"""About you, feedback, graph signals and the accuracy measure."""

from conftest import signup
from test_find import import_all

from app import smart


def test_about_is_private_and_reaches_the_model(client):
    ha, _ = signup(client, "Anshika")
    hb, _ = signup(client, "Jasmeet")
    client.put("/api/about", headers=ha, json={"about": "SDE student in Bangalore, Python and Go, want a remote internship"})
    assert client.get("/api/about", headers=ha).json()["about"].startswith("SDE student")
    assert client.get("/api/about", headers=hb).json()["about"] == ""  # each graph is its own
    assert client.put("/api/about", headers=ha, json={"about": "x" * 700}).status_code == 400


def test_feedback_shapes_the_next_ranking_and_is_measured(client, store):
    h, _ = signup(client, "Anshika")
    import_all(client, h, "a")
    goal = client.post("/api/goals", headers=h, json={"text": "Summer 2027 VC internship"}).json()
    gid = goal["goal"]["id"]
    ranked = goal["ranking"]
    assert len(ranked) >= 3
    good, wrong = ranked[0], ranked[1]

    q = client.post(f"/api/goals/{gid}/rate", headers=h, json={"vote": "good", "person_id": good["person"]["id"], "shown_score": good["score"]}).json()
    q = client.post(f"/api/goals/{gid}/rate", headers=h, json={"vote": "wrong", "person_id": wrong["person"]["id"], "shown_score": wrong["score"]}).json()
    strong = sum(1 for r in (good, wrong) if r["score"] >= smart.STRONG)
    assert q["rated"] == 2 and q["strong_rated"] == strong

    again = client.post(f"/api/goals/{gid}/rerank", headers=h).json()
    ids = [r["person"]["id"] for r in again["ranking"]]
    assert wrong["person"]["id"] not in ids  # wrong ones leave later rankings
    assert good["person"]["id"] in ids
    assert again["votes"][good["person"]["id"]] == "good"
    assert again["quality"]["rated"] == 2

    # The model sees both as examples next time.
    from app.auth import Viewer  # noqa: F401
    user_graph = store.user(client.get("/api/me", headers=h).json()["user"]["id"])
    ctx = smart.context(user_graph, gid, "Summer 2027 VC internship")
    assert "GOOD FITS" in ctx and "WRONG" in ctx

    # Clearing a vote brings them back.
    client.post(f"/api/goals/{gid}/rate", headers=h, json={"vote": "clear", "person_id": wrong["person"]["id"]})
    back = client.post(f"/api/goals/{gid}/rerank", headers=h).json()
    assert wrong["person"]["id"] in [r["person"]["id"] for r in back["ranking"]]


def test_graph_signals():
    assert smart.own_bonus({"strength": "close", "tie": "direct", "facts": []}, None) > smart.own_bonus({"strength": "known", "tie": "direct", "facts": []}, None)
    assert smart.own_bonus({"tie": "via Kabir", "facts": []}, None) < 0
    assert smart.friend_bonus({"paths": [[1], [2], [3]], "degree": 2}, None) == 8
    assert smart.friend_bonus({"paths": [[1]], "degree": 3}, None) < 0


def test_friend_results_can_be_rated(client):
    from test_exchange import befriend

    ha, _ = signup(client, "Js")
    hb, b = signup(client, "Bhavya")
    import_all(client, hb, "b")
    befriend(client, ha, hb, b)
    gid = client.post("/api/goals", headers=ha, json={"text": "SDE"}).json()["goal"]["id"]
    found = client.post(f"/api/goals/{gid}/ask", headers=ha).json()
    nisha = next(p for p in found["people"] if p["name"] == "Nisha Arora")
    other = next(p for p in found["people"] if p["name"] != "Nisha Arora")

    q = client.post(f"/api/goals/{gid}/rate", headers=ha, json={"vote": "wrong", "key": nisha["key"], "shown_score": nisha["score"], "name": nisha["name"]}).json()
    assert q["rated"] == 1
    client.post(f"/api/goals/{gid}/rate", headers=ha, json={"vote": "good", "key": other["key"], "shown_score": other["score"], "name": other["name"]})

    # Gone from the saved results right away, and from the next run.
    saved = client.get(f"/api/goals/{gid}", headers=ha).json()
    kept = {p["name"] for p in saved["goal"]["agents"]["people"]}
    assert "Nisha Arora" not in kept and other["name"] in kept
    again = client.post(f"/api/goals/{gid}/ask", headers=ha).json()
    names = [p["name"] for p in again["people"]]
    assert "Nisha Arora" not in names
    assert next(p for p in again["people"] if p["key"] == other["key"])["vote"] == "good"
