"""Bedrock Mantle (OpenAI-compatible) provider with a fake client."""

import json
from types import SimpleNamespace

import pytest

from app import db, llm
from app.config import detect_provider, get_settings
from conftest import signup


class FakeMantle:
    def __init__(self, base_url, models, strict=False, list_ok_prefix="/v1"):
        self.base_url = base_url
        self._models = models
        self.strict = strict
        self.list_ok = base_url.endswith(list_ok_prefix)
        self.calls = []
        h = llm.HeuristicModel()
        fake = self

        class Models:
            def list(self_inner):
                if not fake.list_ok:
                    raise RuntimeError("404")
                return [SimpleNamespace(id=m) for m in fake._models]

        class Responses:
            def parse(self_inner, *, model, instructions, input, text_format):
                fake.calls.append(("parse", model))
                if not fake.strict:
                    raise RuntimeError("json_schema not supported")
                raise AssertionError("not used in tests")

            def create(self_inner, *, model, instructions, input):
                fake.calls.append(("create", model))
                assert "JSON schema" in instructions
                if input.startswith("GOAL: "):
                    out = h.plan_goal(input.removeprefix("GOAL: "))
                elif input.startswith("KNOWN_PEOPLE:"):
                    known_raw, memo = input.split("\n\nMEMO:\n", 1)
                    out = h.extract_memo(memo, json.loads(known_raw.removeprefix("KNOWN_PEOPLE:\n")))
                else:
                    data = json.loads(input)
                    plan = llm.GoalPlan.model_validate({"summary": data["goal"], "needs": data["needs"]})
                    out = h.score_people(data["goal"], plan, data["people"])
                return SimpleNamespace(output_text="Sure:\n" + out.model_dump_json())

        self.models = Models()
        self.responses = Responses()


@pytest.fixture()
def mantle(monkeypatch, store):
    monkeypatch.setenv("LLM_PROVIDER", "mantle")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "k")
    get_settings.cache_clear()
    db.set_store(db.Store())
    made = []

    def factory(url):
        c = FakeMantle(url, ["openai.gpt-oss-20b", "openai.gpt-5.6-terra", "amazon.titan-embed"], list_ok_prefix="/openai/v1")
        made.append(c)
        return c

    model = llm.MantleModel(client_factory=factory)
    model.fallback = SimpleNamespace(**{n: (lambda *a, **k: (_ for _ in ()).throw(AssertionError("fell back"))) for n in ("extract_memo", "plan_goal", "score_people")})
    llm.set_model(model)
    from fastapi.testclient import TestClient

    from app.main import app

    yield TestClient(app), model
    llm.set_model(None)
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    get_settings.cache_clear()


def test_discovers_prefix_and_prefers_gpt(mantle):
    _, model = mantle
    assert model.base_url.endswith("/openai/v1")
    assert model.name == "openai.gpt-5.6-terra"


def test_plain_json_flow_end_to_end(mantle, sample_csv):
    client, model = mantle
    check = client.get("/api/health/model").json()
    assert check["ok"] is True and check["provider"] == "mantle"
    assert "openai.gpt-5.6-terra" in check["available_models"]
    h, _ = signup(client, "Js")
    sid = client.post("/api/imports/linkedin", headers=h, files={"file": ("Connections.csv", sample_csv, "text/csv")}).json()["import"]["id"]
    client.post(f"/api/imports/{sid}/bulk", headers=h, json={"action": "keep"})
    goal = client.post("/api/goals", headers=h, json={"text": "climate fund investor"}).json()
    assert goal["ranking"]
    out = client.post("/api/capture", headers=h, json={"text": "Met Priya Raman at the climate meetup. I'll send Priya my deck by Friday."}).json()
    assert out["people"][0]["name"] == "Priya Raman"


def test_bedrock_key_alone_means_mantle():
    assert detect_provider({"AWS_BEARER_TOKEN_BEDROCK": "k"}) == "mantle"
    assert detect_provider({"AWS_BEARER_TOKEN_BEDROCK": "k", "BEDROCK_MODEL_ID": "x"}) == "bedrock"
    assert detect_provider({"AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "b"}) == "bedrock"


def test_http_error_is_reported_not_crashed(mantle):
    client, model = mantle

    class Err(Exception):
        status_code = 403
        response = object()  # an httpx-style response, not an AWS dict

    def boom(**kw):
        raise Err("forbidden")

    model.client.responses.parse = boom
    model.client.responses.create = boom
    r = client.get("/api/health/model")
    assert r.status_code == 503
    assert r.json()["error"] == "Err (HTTP 403)"
    assert "openai.gpt-5.6-terra" in r.json()["available_models"]
