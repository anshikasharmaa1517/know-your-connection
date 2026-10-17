import os
import pathlib

import pytest

# Tests run against a real FalkorDB (default redis://localhost:6379) with the
# offline heuristic model, under their own graph prefix.
os.environ["CONNECTIONS_GRAPH_PREFIX"] = "connectionstest"
os.environ.pop("OPENAI_API_KEY", None)
# Never call a real model from tests, even if AWS or OpenAI credentials are in the environment.
os.environ["LLM_PROVIDER"] = "offline"
os.environ["CONNECTIONS_STATIC_DIR"] = "/nonexistent"

from fastapi.testclient import TestClient  # noqa: E402

from app import db, llm  # noqa: E402
from app.config import get_settings  # noqa: E402

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"


@pytest.fixture()
def store():
    get_settings.cache_clear()
    llm.set_model(None)
    s = db.Store()
    s.drop_all()
    db.set_store(s)
    yield s
    s.drop_all()


@pytest.fixture()
def client(store):
    from app.main import app

    return TestClient(app)


def signup(client, name):
    r = client.post("/api/session", json={"name": name})
    assert r.status_code == 200, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["user"]


@pytest.fixture()
def sample_csv():
    return (SAMPLES / "Connections.csv").read_bytes()
