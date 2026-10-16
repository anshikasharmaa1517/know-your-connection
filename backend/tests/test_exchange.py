from conftest import signup


def befriend(client, ha, hb, b_user):
    client.post("/api/friends", headers=ha, json={"handle": b_user["handle"]})
    a_id = client.get("/api/me", headers=ha).json()["user"]["id"]
    r = client.post(f"/api/friends/{a_id}/accept", headers=hb)
    assert r.json()["status"] == "friends"


def test_full_exchange(client):
    ha, a = signup(client, "Js")
    hb, b = signup(client, "Bhavya")
    befriend(client, ha, hb, b)

    # Bhavya knows a climate VC.
    client.post(
        "/api/capture",
        headers=hb,
        json={"text": "Met Priya Raman, a partner at Monsoon Climate Fund. She invests in climate-tech startups."},
    )

    # Js asks the network.
    req = client.post(
        "/api/exchange/requests",
        headers=ha,
        json={"need": "climate-tech VC", "description": "an investor in climate startups", "keywords": ["climate", "fund", "investor", "partner"]},
    ).json()
    assert req["asked"] == 1

    out = client.get("/api/exchange/outbox", headers=ha).json()["requests"][0]
    reply = out["replies"][0]["reply"]
    assert reply["count"] == 1 and reply["from_name"] == "Bhavya"
    # No names cross: the reply has a count and a strength only.
    assert "Priya" not in str(out)

    # Bhavya sees the name, in her own inbox, from her own graph.
    inbox = client.get("/api/exchange/inbox", headers=hb).json()["requests"][0]
    assert inbox["matches"][0]["person"]["name"] == "Priya Raman"

    # Bhavya can't approve before Js asks.
    pid = inbox["matches"][0]["person"]["id"]
    assert client.post(f"/api/exchange/replies/{reply['id']}/approve", headers=hb, json={"person_id": pid}).status_code == 400

    client.post(f"/api/exchange/replies/{reply['id']}/ask", headers=ha)
    approved = client.post(f"/api/exchange/replies/{reply['id']}/approve", headers=hb, json={"person_id": pid}).json()
    token = approved["optin_path"].rsplit("/", 1)[-1]
    assert "Priya" in approved["message"]

    # Still nothing revealed to Js.
    assert "Priya" not in str(client.get("/api/exchange/outbox", headers=ha).json())

    view = client.get(f"/api/optin/{token}").json()
    assert view["requester_name"] == "Js" and view["broker_name"] == "Bhavya"
    client.post(f"/api/optin/{token}", json={"accept": True, "name": "Priya Raman", "line": "Climate VC, happy to chat", "contact": "priya@example.com"})

    out = client.get("/api/exchange/outbox", headers=ha).json()["requests"][0]
    card = out["replies"][0]["card"]
    assert card["name"] == "Priya Raman" and card["contact"] == "priya@example.com"
    assert "Bhavya" in card["intro_draft"]

    saved = client.post(f"/api/exchange/cards/{card['id']}/save", headers=ha).json()
    detail = client.get(f"/api/people/{saved['person_id']}", headers=ha).json()
    assert detail["receipts"][0]["source"]["kind"] == "card"


def test_cannot_answer_for_someone_else(client):
    ha, a = signup(client, "Js")
    hb, b = signup(client, "Bhavya")
    hc, c = signup(client, "Chirag")
    befriend(client, ha, hb, b)
    req = client.post("/api/exchange/requests", headers=ha, json={"need": "hardware mentor"}).json()
    # Chirag wasn't asked: his inbox is empty and he can't see the request.
    assert client.get("/api/exchange/inbox", headers=hc).json()["requests"] == []
    out = client.get("/api/exchange/outbox", headers=ha).json()["requests"][0]
    rep = out["replies"][0]["reply"]
    assert rep["status"] == "no_match"
    assert client.post(f"/api/exchange/replies/{rep['id']}/approve", headers=hc, json={"person_id": "x"}).status_code == 400
    assert req["asked"] == 1


def test_contact_can_say_no(client):
    ha, a = signup(client, "Js")
    hb, b = signup(client, "Bhavya")
    befriend(client, ha, hb, b)
    client.post("/api/capture", headers=hb, json={"text": "Met Neha Kulkarni, a firmware lead at Voltgrid Robotics who mentors hardware students."})
    client.post("/api/exchange/requests", headers=ha, json={"need": "hardware mentor", "keywords": ["hardware", "firmware", "mentor"]})
    rep = client.get("/api/exchange/outbox", headers=ha).json()["requests"][0]["replies"][0]["reply"]
    client.post(f"/api/exchange/replies/{rep['id']}/ask", headers=ha)
    pid = client.get("/api/exchange/inbox", headers=hb).json()["requests"][0]["matches"][0]["person"]["id"]
    token = client.post(f"/api/exchange/replies/{rep['id']}/approve", headers=hb, json={"person_id": pid}).json()["optin_path"].rsplit("/", 1)[-1]
    client.post(f"/api/optin/{token}", json={"accept": False})
    card = client.get("/api/exchange/outbox", headers=ha).json()["requests"][0]["replies"][0]["card"]
    assert card == {"id": card["id"], "status": "declined"}
