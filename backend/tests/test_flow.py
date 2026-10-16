"""End-to-end flows through the HTTP API against a real FalkorDB."""

from conftest import signup


def upload(client, headers, data, name="Connections.csv"):
    r = client.post("/api/imports/linkedin", headers=headers, files={"file": (name, data, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def test_upload_creates_candidates_not_people(client, sample_csv):
    h, _ = signup(client, "Js")
    out = upload(client, h, sample_csv)
    imp = out["import"]
    assert imp["pending"] == out["parsed"]["rows"]
    # Nothing is in the network until a swipe.
    assert client.get("/api/people", headers=h).json()["people"] == []


def test_swipe_keep_close_skip_and_receipts(client, sample_csv):
    h, _ = signup(client, "Js")
    sid = upload(client, h, sample_csv)["import"]["id"]
    deck = client.get(f"/api/imports/{sid}/deck?limit=3", headers=h).json()
    a, b, c = deck["cards"]

    r = client.post(f"/api/imports/{sid}/cards/{a['id']}", headers=h, json={"action": "keep"})
    assert r.json()["status"] == "kept"
    r = client.post(f"/api/imports/{sid}/cards/{b['id']}", headers=h, json={"action": "close"})
    pid_b = r.json()["person_id"]
    r = client.post(f"/api/imports/{sid}/cards/{c['id']}", headers=h, json={"action": "skip"})
    assert r.json()["import"]["skipped"] == 1

    people = client.get("/api/people", headers=h).json()["people"]
    assert {p["person"]["name"] for p in people} == {a["name"], b["name"]}
    detail = client.get(f"/api/people/{pid_b}", headers=h).json()
    assert detail["strength"] == "close"
    assert detail["receipts"][0]["source"]["id"] == sid  # receipt back to the import

    # deciding twice is refused
    r = client.post(f"/api/imports/{sid}/cards/{a['id']}", headers=h, json={"action": "keep"})
    assert r.status_code == 400


def test_undo_restores_card_and_removes_person(client, sample_csv):
    h, _ = signup(client, "Js")
    sid = upload(client, h, sample_csv)["import"]["id"]
    card = client.get(f"/api/imports/{sid}/deck?limit=1", headers=h).json()["cards"][0]
    client.post(f"/api/imports/{sid}/cards/{card['id']}", headers=h, json={"action": "keep"})
    assert len(client.get("/api/people", headers=h).json()["people"]) == 1
    r = client.post(f"/api/imports/{sid}/undo", headers=h).json()
    assert r["card"]["id"] == card["id"] and r["card"]["status"] == "pending"
    assert client.get("/api/people", headers=h).json()["people"] == []


def test_memo_person_is_offered_as_merge(client, sample_csv):
    h, _ = signup(client, "Js")
    memo = "Met Priya Raman at the climate meetup. She is raising a new fund and wants to hire an intern next summer."
    cap = client.post("/api/capture", headers=h, json={"text": memo}).json()
    priya_id = cap["people"][0]["id"]
    sid = upload(client, h, sample_csv)["import"]["id"]
    deck = client.get(f"/api/imports/{sid}/deck?limit=50", headers=h).json()
    card = next(c for c in deck["cards"] if c["name"] == "Priya Raman")
    assert card["match"]["id"] == priya_id
    r = client.post(f"/api/imports/{sid}/cards/{card['id']}", headers=h, json={"action": "keep"}).json()
    assert r["status"] == "merged" and r["person_id"] == priya_id
    detail = client.get(f"/api/people/{priya_id}", headers=h).json()
    assert detail["person"]["company"] == "Monsoon Climate Fund"
    assert sorted(detail["via"]) == ["linkedin", "memo"]
    # undo a merge keeps the memo person but drops what the import added
    client.post(f"/api/imports/{sid}/undo", headers=h)
    detail = client.get(f"/api/people/{priya_id}", headers=h).json()
    assert detail["person"]["company"] == ""
    assert detail["via"] == ["memo"]


def test_reimport_only_shows_new_people(client, sample_csv):
    h, _ = signup(client, "Js")
    sid = upload(client, h, sample_csv)["import"]["id"]
    client.post(f"/api/imports/{sid}/bulk", headers=h, json={"action": "keep"})
    newer = sample_csv.decode() + "Zara,Patel,https://www.linkedin.com/in/zara-patel,,Kestrel Capital,Associate,16 Oct 2026\n"
    second = upload(client, h, newer.encode())
    assert second["import"]["pending"] == 1
    deck = client.get(f"/api/imports/{second['import']['id']}/deck", headers=h).json()
    assert [c["name"] for c in deck["cards"]] == ["Zara Patel"]


def test_goal_ranks_with_receipts_and_reorders_deck(client, sample_csv):
    h, _ = signup(client, "Js")
    sid = upload(client, h, sample_csv)["import"]["id"]
    goal = client.post("/api/goals", headers=h, json={"text": "Summer 2027 VC internship"}).json()
    # No one kept yet, so the whole goal is a gap.
    assert goal["ranking"] == [] and goal["gaps"]

    deck = client.get(f"/api/imports/{sid}/deck?limit=5", headers=h).json()
    assert deck["cards"][0]["relevance"], "goal-relevant connections should come first"
    for card in deck["cards"]:
        client.post(f"/api/imports/{sid}/cards/{card['id']}", headers=h, json={"action": "keep"})

    ranked = client.post(f"/api/goals/{goal['goal']['id']}/rerank", headers=h).json()
    assert ranked["ranking"], ranked
    top = ranked["ranking"][0]
    assert top["receipts"] and top["receipts"][0]["source_kind"] == "linkedin"
    assert ranked["gaps"] == []

    closed = client.post(f"/api/goals/{goal['goal']['id']}/close", headers=h).json()
    assert closed["ranking"] == [] and closed["goal"]["status"] == "closed"


def test_two_hop_warm_path(client):
    h, _ = signup(client, "Js")
    memo = (
        "Had coffee with Kabir Singh from Tessellate Labs. "
        "Kabir said his cofounder Leela Varma is a hardware mentor who loves firmware."
    )
    client.post("/api/capture", headers=h, json={"text": memo})
    goal = client.post("/api/goals", headers=h, json={"text": "find a hardware mentor"}).json()
    names = {r["person"]["name"]: r for r in goal["ranking"]}
    assert "Leela Varma" in names
    assert names["Leela Varma"]["via_name"] == "Kabir Singh"


def test_delete_memo_removes_what_it_said(client):
    h, _ = signup(client, "Js")
    cap = client.post("/api/capture", headers=h, json={"text": "Met Omar Siddiqui at DevFest. I'll send Omar my Kubernetes talk by Friday."}).json()
    assert client.get("/api/promises", headers=h).json()["promises"]
    client.delete(f"/api/sources/{cap['source']['id']}", headers=h)
    assert client.get("/api/people", headers=h).json()["people"] == []
    assert client.get("/api/promises", headers=h).json()["promises"] == []


def test_graphs_are_isolated(client, sample_csv):
    ha, _ = signup(client, "Js")
    hb, _ = signup(client, "Bhavya")
    sid = upload(client, ha, sample_csv)["import"]["id"]
    assert client.get(f"/api/imports/{sid}/deck", headers=hb).status_code == 400
    assert client.get("/api/people", headers=hb).json()["people"] == []
    assert client.get("/api/people").status_code == 401
