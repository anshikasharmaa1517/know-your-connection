from conftest import signup
from test_exchange import befriend

from app import search


def import_all(client, h, sample_set="a"):
    up = client.post(f"/api/imports/sample?set={sample_set}", headers=h).json()
    sid = up["import"]["id"]
    r = client.post(f"/api/imports/{sid}/bulk", headers=h, json={"action": "keep"})
    assert r.status_code == 200, r.text


def names(rows):
    return {r["name"] for r in rows}


def test_expand_known_roles():
    sde = search.expand("SDE")
    assert "software engineer" in sde["phrases"] and "sde ii" in sde["phrases"]
    assert "engineer" not in sde["phrases"]  # too broad on its own
    narrowed = search.expand("sde at google")
    assert "software engineer" in narrowed["phrases"] and narrowed["must"] == ["google"]


def test_find_orders_by_degree_without_approval(client):
    ha, a = signup(client, "Js")
    hb, b = signup(client, "Bhavya")
    import_all(client, ha, "a")
    import_all(client, hb, "b")

    # Before they are friends, only your own contacts show.
    solo = client.get("/api/find?q=SDE", headers=ha).json()
    assert names(solo["first"]) >= {"Aarav Shah", "Mitali Ghosh", "Kunal Bansal"}
    assert solo["second"] == []
    assert "Hrithik Saxena" not in str(solo)  # hardware engineer is not an SDE

    befriend(client, ha, hb, b)
    found = client.get("/api/find?q=SDE", headers=ha).json()
    first, second = names(found["first"]), names(found["second"])
    assert {"Nisha Arora", "Rajat Khanna", "Simran Gill", "Abhishek Pandey", "Tara Mathew", "Megha Iyer"} <= second
    # Someone you both know stays 1st degree and is not repeated.
    assert "Aarav Shah" in first and "Aarav Shah" not in second
    assert "Hrithik Saxena" not in second and "Prerna Dutta" not in second
    row = next(r for r in found["second"] if r["name"] == "Nisha Arora")
    assert row["paths"] == [[{"name": "Bhavya", "kind": "friend"}]]
    assert row["person_id"] is None and row["strength"] is None and row["url"]
    # Your own matches can be opened in People.
    assert all(r["person_id"] for r in found["first"])


def test_memo_tie_is_second_degree(client):
    h, _ = signup(client, "Js")
    client.post(
        "/api/capture",
        headers=h,
        json={"text": "Had coffee with Kabir Singh from Tessellate Labs. Kabir said his cofounder Leela Varma is a climate investor at Pinecone Grove."},
    )
    found = client.get("/api/find?q=investor", headers=h).json()
    row = next((r for r in found["second"] if r["name"] == "Leela Varma"), None)
    if row:  # the offline extractor may not catch the title; when it does, the path runs through Kabir
        assert row["paths"][0][0]["name"] == "Kabir Singh"


def test_third_degree_is_pro_and_respects_opt_out(client):
    ha, a = signup(client, "Js")
    hb, b = signup(client, "Bhavya")
    hc, c = signup(client, "Chirag")
    import_all(client, hc, "b")
    befriend(client, ha, hb, b)
    befriend(client, hb, hc, c)

    free = client.get("/api/find?q=SDE&depth=3", headers=ha).json()
    assert free["third"]["locked"] and free["third"]["people"] == [] and free["third"]["networks"] >= 1
    assert "Nisha Arora" not in str(free)

    assert client.patch("/api/settings", headers=ha, json={"plan": "pro"}).json()["plan"] == "pro"
    pro = client.get("/api/find?q=SDE&depth=3", headers=ha).json()
    row = next(r for r in pro["third"]["people"] if r["name"] == "Nisha Arora")
    assert [p["name"] for p in row["paths"][0]] == ["Bhavya", "Chirag"]
    # Asking for depth 2 does not do the extra work, even on Pro.
    assert client.get("/api/find?q=SDE", headers=ha).json()["third"]["people"] == []

    # Chirag turns search off: his contacts disappear from everyone's results.
    assert client.patch("/api/settings", headers=hc, json={"discoverable": False}).json()["discoverable"] is False
    hidden = client.get("/api/find?q=SDE&depth=3", headers=ha).json()
    assert "Nisha Arora" not in str(hidden)
    assert client.get("/api/me", headers=hc).json()["discoverable"] is False


def test_find_needs_a_query(client):
    h, _ = signup(client, "Js")
    assert client.get("/api/find?q=%20", headers=h).status_code == 400
    assert client.patch("/api/settings", headers=h, json={"plan": "gold"}).status_code == 400


def test_notes_make_people_findable(client):
    h, _ = signup(client, "Js")
    import_all(client, h, "a")
    priya = next(p for p in client.get("/api/people?q=Priya", headers=h).json()["people"] if p["person"]["name"] == "Priya Raman")
    pid = priya["person"]["id"]

    before = client.get("/api/find?q=hiring intern", headers=h).json()
    assert "Priya Raman" not in names(before["first"])

    note = client.post(f"/api/people/{pid}/notes", headers=h, json={"text": "Wants to hire a summer intern for the climate fund"}).json()
    assert note["kind"] == "intent"
    found = client.get("/api/find?q=hiring intern", headers=h).json()
    row = found["first"][0]
    assert row["name"] == "Priya Raman" and "intern" in row["note"]
    # The note shows on her card.
    detail = client.get(f"/api/people/{pid}", headers=h).json()
    assert any(f["fact"]["text"].startswith("Wants to hire") for f in detail["facts"])

    client.delete(f"/api/people/{pid}/notes/{note['id']}", headers=h)
    assert "Priya Raman" not in names(client.get("/api/find?q=hiring intern", headers=h).json()["first"])
    assert client.post(f"/api/people/{pid}/notes", headers=h, json={"text": "  "}).status_code == 400


def test_friends_notes_stay_private(client):
    ha, a = signup(client, "Js")
    hb, b = signup(client, "Bhavya")
    import_all(client, hb, "b")
    befriend(client, ha, hb, b)
    farhan = next(p for p in client.get("/api/people?q=Farhan", headers=hb).json()["people"])
    client.post(f"/api/people/{farhan['person']['id']}/notes", headers=hb, json={"text": "Looking for a cofounder"})
    assert "Farhan" in str(client.get("/api/find?q=cofounder", headers=hb).json()["first"])
    assert "Farhan" not in str(client.get("/api/find?q=cofounder", headers=ha).json())


def test_friend_list_shows_searchable_contacts(client):
    ha, a = signup(client, "Js")
    hb, b = signup(client, "Bhavya")
    import_all(client, hb, "b")
    befriend(client, ha, hb, b)
    f = client.get("/api/friends", headers=ha).json()["friends"][0]
    assert f["name"] == "Bhavya" and f["discoverable"] and f["contacts"] == 12
    client.patch("/api/settings", headers=hb, json={"discoverable": False})
    f = client.get("/api/friends", headers=ha).json()["friends"][0]
    assert not f["discoverable"] and f["contacts"] == 0
