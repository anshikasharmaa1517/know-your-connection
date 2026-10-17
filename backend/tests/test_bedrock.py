"""The AWS Bedrock provider, exercised with a fake bedrock-runtime client against a real FalkorDB."""

import hashlib
import io
import json

import pytest
from botocore.exceptions import ClientError

from app import db, llm
from app.config import detect_provider, get_settings
from conftest import signup

DIM = 1024


def fake_vector(text: str) -> list[float]:
    v = [0.0] * DIM
    for w in llm.words(text):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
    return v


def drop_nones(value):
    """Mimic a model that leaves optional fields out instead of sending null."""
    if isinstance(value, dict):
        return {k: drop_nones(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_nones(v) for v in value]
    return value


class FakeBedrock:
    def __init__(self, fail_embeddings=False, error=None, refuse=()):
        self.h = llm.HeuristicModel()
        self.converse_calls = []
        self.embed_calls = 0
        self.fail_embeddings = fail_embeddings
        self.error = error
        self.refuse = set(refuse)

    def converse(self, *, modelId, system, messages, toolConfig, inferenceConfig):
        if self.error:
            raise self.error
        if modelId in self.refuse:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": f"no access to {modelId}"}}, "Converse")
        assert "anyOf" not in json.dumps(toolConfig), "nullable fields should be type lists"
        self.converse_calls.append({"model": modelId, "toolConfig": toolConfig})
        tool = toolConfig["tools"][0]["toolSpec"]["name"]
        assert toolConfig["toolChoice"] == {"tool": {"name": tool}}
        text = messages[0]["content"][0]["text"]
        if tool == "record_memo_extraction":
            known_raw, memo = text.split("\n\nMEMO:\n", 1)
            result = self.h.extract_memo(memo, json.loads(known_raw.removeprefix("KNOWN_PEOPLE:\n")))
        elif tool == "record_goal_plan":
            result = self.h.plan_goal(text.removeprefix("GOAL: "))
        elif tool == "record_scores":
            data = json.loads(text)
            plan = llm.GoalPlan.model_validate({"summary": data["goal"], "needs": data["needs"]})
            result = self.h.score_people(data["goal"], plan, data["people"])
        else:  # pragma: no cover
            raise AssertionError(tool)
        return {
            "stopReason": "tool_use",
            "output": {"message": {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": "t1", "name": tool, "input": drop_nones(result.model_dump())}}
            ]}},
        }

    def invoke_model(self, *, modelId, body, contentType, accept):
        self.embed_calls += 1
        if self.fail_embeddings:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no titan"}}, "InvokeModel")
        payload = json.loads(body)
        assert payload["dimensions"] == DIM and payload["normalize"] is True
        return {"body": io.BytesIO(json.dumps({"embedding": fake_vector(payload["inputText"])}).encode())}


@pytest.fixture()
def bedrock(monkeypatch, store):
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("CONNECTIONS_RANK_POOL", "6")
    get_settings.cache_clear()
    db.set_store(db.Store())
    fake = FakeBedrock()
    llm.set_model(llm.BedrockModel(client=fake))
    from fastapi.testclient import TestClient

    from app.main import app

    yield TestClient(app), fake
    llm.set_model(None)
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    get_settings.cache_clear()


class NoFallback:
    def __getattr__(self, name):
        raise AssertionError(f"fell back to heuristics for {name}")


def test_ranking_and_capture_go_through_bedrock(bedrock, sample_csv):
    client, fake = bedrock
    llm.get_model().fallback = NoFallback()  # any parse failure now fails the test
    h, _ = signup(client, "Js")
    sid = client.post("/api/imports/linkedin", headers=h, files={"file": ("Connections.csv", sample_csv, "text/csv")}).json()["import"]["id"]
    client.post(f"/api/imports/{sid}/bulk", headers=h, json={"action": "keep"})

    goal = client.post("/api/goals", headers=h, json={"text": "climate fund investor"}).json()
    assert goal["ranking"], goal
    assert goal["goal"]["shortlist"] <= 6 < goal["goal"]["pool"]
    assert fake.embed_calls > 6, "people and needs are embedded with Titan"
    assert all(rc["source_kind"] == "linkedin" for r in goal["ranking"] for rc in r["receipts"])

    out = client.post("/api/capture", headers=h, json={"text": "Met Priya Raman at the climate meetup. I'll send Priya my deck by Friday."}).json()
    assert out["people"][0]["name"] == "Priya Raman"
    assert any(f["kind"] == "promise" for f in out["facts"])

    tools = {c["toolConfig"]["tools"][0]["toolSpec"]["name"] for c in fake.converse_calls}
    assert tools == {"record_goal_plan", "record_scores", "record_memo_extraction"}
    assert all(c["model"] == "global.anthropic.claude-haiku-5-5" for c in fake.converse_calls)
    for c in fake.converse_calls:
        assert "$ref" not in json.dumps(c["toolConfig"])


def test_health_reports_bedrock(bedrock):
    client, _ = bedrock
    body = client.get("/api/health").json()
    assert body["provider"] == "bedrock" and body["llm"] is True and body["voice"] is False
    check = client.get("/api/health/model").json()
    assert check["ok"] is True and check["sample"]


def test_model_check_explains_a_rejected_key(bedrock):
    client, fake = bedrock
    fake.error = ClientError({"Error": {"Code": "UnrecognizedClientException", "Message": "bad token"}}, "Converse")
    r = client.get("/api/health/model")
    assert r.status_code == 503
    assert r.json()["error"] == "UnrecognizedClientException" and "key" in r.json()["hint"]
    # The app keeps working on the fallback.
    h, _ = signup(client, "Js")
    assert client.post("/api/goals", headers=h, json={"text": "hardware mentor"}).status_code == 200


def test_embedding_failure_turns_embeddings_off_once():
    fake = FakeBedrock(fail_embeddings=True)
    model = llm.BedrockModel(client=fake)
    assert model.embed(["a", "b"]) is None
    calls = fake.embed_calls
    assert model.embed(["c"]) is None and fake.embed_calls == calls


def test_inline_schema_and_fill_missing():
    schema = llm.inline_schema(llm.MemoExtraction)
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    data = {"title": "t", "people": [{"ref": "p1", "name": "A B", "met": True}], "facts": [], "ties": []}
    parsed = llm.MemoExtraction.model_validate(llm.fill_missing(data, llm.MemoExtraction))
    assert parsed.people[0].existing_id is None and parsed.people[0].company is None


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, "offline"),
        ({"OPENAI_API_KEY": "sk-x"}, "openai"),
        ({"AWS_BEARER_TOKEN_BEDROCK": "abc"}, "bedrock"),
        ({"AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "b"}, "bedrock"),
        ({"OPENAI_API_KEY": " "}, "offline"),
        ({"LLM_PROVIDER": "bedrock", "OPENAI_API_KEY": "sk-x"}, "bedrock"),
    ],
)
def test_detect_provider(env, expected):
    assert detect_provider(env) == expected


def test_bedrock_key_under_other_names_is_picked_up():
    from app.config import apply_env_aliases, detect_provider, related_env_names

    env = {" bedrock_api_key ": '"Bearer abc123"', "AWS_REGION": "Mumbai", "OPENAI_API_KEY": ""}
    notes = apply_env_aliases(env)
    assert env["AWS_BEARER_TOKEN_BEDROCK"] == "abc123"
    assert env["AWS_REGION"] == "ap-south-1"
    assert detect_provider(env) == "bedrock"
    assert notes and all("abc123" not in n for n in notes)
    seen = related_env_names(env)
    assert "OPENAI_API_KEY (empty)" in seen and all("abc123" not in s for s in seen)


def test_correct_names_are_left_alone():
    from app.config import apply_env_aliases

    env = {"AWS_BEARER_TOKEN_BEDROCK": "abc", "AWS_REGION": "us-east-1"}
    assert apply_env_aliases(env) == [] and env == {"AWS_BEARER_TOKEN_BEDROCK": "abc", "AWS_REGION": "us-east-1"}


def test_offline_model_check_lists_setting_names(client, monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_IDX", "typo")
    body = client.get("/api/health/model").json()
    assert body["ok"] is False and body["provider"] == "offline"
    assert "BEDROCK_MODEL_IDX" in body["settings_seen"]
    assert "typo" not in str(body)


def test_refused_claude_falls_back_to_nova(bedrock):
    client, fake = bedrock
    fake.refuse = {"global.anthropic.claude-haiku-5-5"}
    model = llm.get_model()
    model._fallbacks = [m for m in model._fallbacks if not m.startswith("mantle:")]
    llm.get_model().fallback = NoFallback()  # the fallback model must answer, not the keyword heuristics
    check = client.get("/api/health/model").json()
    assert check["ok"] is True
    assert check["model"] == "global.amazon.nova-2-lite-v1:0"
    assert check["fell_back_from"] == {"global.anthropic.claude-haiku-5-5": "AccessDeniedException"}
    h, _ = signup(client, "Js")
    client.post("/api/capture", headers=h, json={"text": "Met Priya Raman at the climate meetup."})
    assert {c["model"] for c in fake.converse_calls} == {"global.amazon.nova-2-lite-v1:0"}


def test_all_models_refused_reports_each(bedrock):
    client, fake = bedrock
    fake.refuse = {"global.anthropic.claude-haiku-5-5", "global.amazon.nova-2-lite-v1:0", "us.amazon.nova-2-lite-v1:0"}
    model = llm.get_model()
    model._fallbacks = [m for m in model._fallbacks if not m.startswith("mantle:")]
    r = client.get("/api/health/model")
    assert r.status_code == 503 and r.json()["error"] == "AccessDeniedException"
    assert set(r.json()["refused"]) == {"global.anthropic.claude-haiku-5-5", "global.amazon.nova-2-lite-v1:0"}


@pytest.mark.parametrize(
    "region,expected",
    [
        ("us-east-1", ("mantle:anthropic.claude-haiku-4-5", "mantle:anthropic.claude-sonnet-5", "mantle:anthropic.claude-haiku-5-5",
                       "global.amazon.nova-2-lite-v1:0", "us.amazon.nova-2-lite-v1:0")),
        ("ap-south-1", ("mantle:anthropic.claude-haiku-4-5", "mantle:anthropic.claude-sonnet-5", "mantle:anthropic.claude-haiku-5-5",
                        "mantle:us-east-1/anthropic.claude-haiku-4-5", "mantle:us-east-1/anthropic.claude-sonnet-5",
                        "global.amazon.nova-2-lite-v1:0")),
    ],
)
def test_fallback_list_by_region(region, expected):
    from app.config import bedrock_fallbacks

    assert bedrock_fallbacks({"AWS_REGION": region}) == expected
    assert bedrock_fallbacks({"AWS_REGION": region, "BEDROCK_FALLBACK_MODEL_IDS": "none"}) == ()


def test_mantle_is_tried_after_runtime_refuses(bedrock, monkeypatch):
    client, fake = bedrock
    fake.refuse = {"global.anthropic.claude-haiku-5-5"}
    model = llm.get_model()
    model.fallback = NoFallback()
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "mantle-key")
    calls = []

    def fake_post(url, body, key):
        calls.append((url, body["model"], key, body["tool_choice"]))
        plan = llm.HeuristicModel().plan_goal("hardware mentor")
        return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": body["tool_choice"]["name"], "input": plan.model_dump()}]}

    monkeypatch.setattr(model, "_mantle_post", fake_post)
    check = client.get("/api/health/model").json()
    assert check["ok"] is True and check["model"] == "mantle:anthropic.claude-haiku-4-5"
    url, mid, key, choice = calls[0]
    assert url == "https://bedrock-mantle.us-east-1.api.aws/anthropic/v1/messages"
    assert mid == "anthropic.claude-haiku-4-5" and key == "mantle-key" and choice["type"] == "tool"
