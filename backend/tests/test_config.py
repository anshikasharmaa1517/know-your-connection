import pytest
from redis.connection import parse_url

from app.config import describe_falkordb_url, normalise_falkordb_url, resolve_falkordb_url

HOST = "r-abc.instance-x.hc-y.ap-south-1.aws.f2e0.cloud"


@pytest.mark.parametrize(
    "raw",
    [
        f"{HOST}:61155",
        f"FALKORDB_URL=redis://falkordb:pw@{HOST}:61155",
        f'  "redis://falkordb:pw@{HOST}:61155"  ',
        f"falkor://falkordb:pw@{HOST}:61155",
        f"redis://falkordb:p#w/x?y@z@{HOST}:61155",
    ],
)
def test_pasted_urls_parse(raw):
    url = normalise_falkordb_url(raw)
    parsed = parse_url(url)
    assert parsed["host"] == HOST
    assert parsed["port"] == 61155


def test_special_password_survives():
    parsed = parse_url(normalise_falkordb_url(f"redis://falkordb:p#w/x?y@z@{HOST}:61155"))
    assert parsed["username"] == "falkordb"
    assert parsed["password"] == "p#w/x?y@z"


def test_already_encoded_password_is_not_double_encoded():
    parsed = parse_url(normalise_falkordb_url(f"redis://falkordb:a%23b@{HOST}:61155"))
    assert parsed["password"] == "a#b"


def test_separate_fields_win():
    env = {
        "FALKORDB_URL": "garbage",
        "FALKORDB_HOST": HOST,
        "FALKORDB_PORT": "61155",
        "FALKORDB_USERNAME": "falkordb",
        "FALKORDB_PASSWORD": "s3cr#t/!",
    }
    parsed = parse_url(resolve_falkordb_url(env))
    assert (parsed["host"], parsed["port"], parsed["username"], parsed["password"]) == (HOST, 61155, "falkordb", "s3cr#t/!")


def test_host_with_port_in_one_field():
    parsed = parse_url(resolve_falkordb_url({"FALKORDB_HOST": f"{HOST}:61155", "FALKORDB_PASSWORD": "pw"}))
    assert parsed["host"] == HOST and parsed["port"] == 61155


def test_password_hidden_in_logs():
    shown = describe_falkordb_url(normalise_falkordb_url(f"redis://falkordb:topsecret@{HOST}:61155"))
    assert "topsecret" not in shown and HOST in shown and "falkordb" in shown


def test_health_reports_unreachable_without_leaking(monkeypatch):
    from fastapi.testclient import TestClient

    from app import db
    from app.config import get_settings
    from app.main import app

    monkeypatch.setenv("FALKORDB_URL", "redis://falkordb:topsecret@127.0.0.1:1")
    get_settings.cache_clear()
    db.set_store(None)
    try:
        r = TestClient(app).get("/api/health")
        assert r.status_code == 503
        assert "topsecret" not in r.text
        assert r.json()["error"]
    finally:
        db.set_store(None)
        get_settings.cache_clear()
