"""Exercise the OpenAI code path (structured outputs, embeddings, FalkorDB vector
index) with a fake client, so no network or key is needed."""

import hashlib
from types import SimpleNamespace

import pytest

from app import db, llm
from app.config import get_settings
from conftest import signup

DIM = 1536


def fake_vector(text: str) -> list[float]:
    # Bag of hashed words: similar texts share coordinates.
    v = [0.0] * DIM
    for w in llm.words(text):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
    return v


class FakeResponses:
    def __init__(self):
        self.h = llm.HeuristicModel()
        self.calls = []

    def parse(self, *, model, instructions, input, text_format):
        self.calls.append(text_format.__name__)
        import json

        if text_format is llm.MemoExtraction:
            known = json.loads(input.split("\n\nMEMO:\n")[0].removeprefix("KNOWN_PEOPLE:\n"))
            parsed = self.h.extract_memo(input.split("\n\nMEMO:\n", 1)[1], known)
        elif text_format is llm.GoalPlan:
            parsed = self.h.plan_goal(input.removeprefix("GOAL: "))
        else:
            data = json.loads(input)
            plan = llm.GoalPlan.model_validate({"summary": data["goal"], "needs": data["needs"]})
            parsed = self.h.score_people(data["goal"], plan, data["people"])
            # a model might hallucinate evidence; the ranker must drop it
            for s in parsed.scores:
                s.evidence.append("made-up-id")
        return SimpleNamespace(output_parsed=parsed)


class FakeEmbeddings:
    def __init__(self):
        self.calls = 0

    def create(self, *, model, input):
        self.calls += 1
        return SimpleNamespace(data=[SimpleNamespace(embedding=fake_vector(t)) for t in input])


@pytest.fixture()
def model_client(monkeypatch, store):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("CONNECTIONS_RANK_POOL", "6")
    get_settings.cache_clear()
    s = db.Store()
    db.set_store(s)
    model = llm.OpenAIModel()
    model.client = SimpleNamespace(responses=FakeResponses(), embeddings=FakeEmbeddings())
    llm.set_model(model)
    from fastapi.testclient import TestClient

    from app.main import app

    yield TestClient(app), model
    llm.set_model(None)
    monkeypatch.delenv("OPENAI_API_KEY")
    get_settings.cache_clear()


def test_vector_shortlist_and_receipts(model_client, sample_csv):
    client, model = model_client
    h, _ = signup(client, "Js")
    up = client.post("/api/imports/linkedin", headers=h, files={"file": ("Connections.csv", sample_csv, "text/csv")}).json()
    sid = up["import"]["id"]
    client.post(f"/api/imports/{sid}/bulk", headers=h, json={"action": "keep"})

    goal = client.post("/api/goals", headers=h, json={"text": "climate fund investor"}).json()
    assert model.client.embeddings.calls >= 1, "people and needs should be embedded"
    assert goal["goal"]["pool"] > 6 and goal["goal"]["shortlist"] <= 6
    assert goal["ranking"], goal
    for r in goal["ranking"]:
        assert all(rc.get("source_kind") == "linkedin" for rc in r["receipts"])
    assert "GoalPlan" in model.client.responses.calls and "Scores" in model.client.responses.calls


def test_extraction_goes_through_model(model_client):
    client, model = model_client
    h, _ = signup(client, "Js")
    out = client.post("/api/capture", headers=h, json={"text": "Met Priya Raman at the climate meetup. I'll send Priya my deck by Friday."}).json()
    assert out["people"][0]["name"] == "Priya Raman"
    assert any(f["kind"] == "promise" for f in out["facts"])
    assert "MemoExtraction" in model.client.responses.calls
