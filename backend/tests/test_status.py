"""Status lines: what someone writes about themselves surfaces them where they are in your network."""

from conftest import signup
from test_exchange import befriend
from test_find import import_all

from app.users import li_key


def test_li_key_normalises():
    assert li_key("https://www.linkedin.com/in/Tanvi-Joshi/?trk=x") == "linkedin.com/in/tanvi-joshi"
    assert li_key("in.linkedin.com/in/tanvi-joshi") == "linkedin.com/in/tanvi-joshi"
    assert li_key("https://example.com/me") == ""


def test_status_surfaces_people_in_find_and_goals(client):
    ha, a = signup(client, "Anshika")
    import_all(client, ha, "a")  # Anshika knows Tanvi Joshi (Technical Recruiter) from her LinkedIn export
    hr, _ = signup(client, "Tanvi Joshi")
    # Tanvi uses the app too, and links her LinkedIn profile.
    bad = client.patch("/api/profile", headers=hr, json={"linkedin_url": "https://example.com/tanvi"})
    assert bad.status_code == 400
    me = client.patch("/api/profile", headers=hr, json={"status": "Hiring remote SDE interns this winter", "linkedin_url": "https://www.linkedin.com/in/tanvi-joshi-talent/"}).json()
    assert me["status"].startswith("Hiring")

    found = client.get("/api/find?q=hiring interns", headers=ha).json()
    tanvi = next(r for r in found["first"] if r["name"] == "Tanvi Joshi")
    assert tanvi["status"] == "Hiring remote SDE interns this winter" and tanvi["person_id"]

    goal = client.post("/api/goals", headers=ha, json={"text": "remote internship"}).json()
    assert "Tanvi Joshi" in {r["person"]["name"] for r in goal["ranking"]}

    # Turning search off hides the status from others' searches.
    client.patch("/api/settings", headers=hr, json={"discoverable": False})
    hidden = client.get("/api/find?q=hiring interns", headers=ha).json()
    assert not any(r.get("status") for r in hidden["first"])


def test_friends_status_shows_in_find_and_friend_list(client):
    ha, a = signup(client, "Anshika")
    hb, b = signup(client, "Jasmeet")
    befriend(client, ha, hb, b)
    client.patch("/api/profile", headers=hb, json={"status": "Looking for a cofounder for a fintech idea"})
    assert client.get("/api/friends", headers=ha).json()["friends"][0]["status"].startswith("Looking for a cofounder")
    found = client.get("/api/find?q=cofounder", headers=ha).json()
    assert any(r["name"] == "Jasmeet" and r["status"] for r in found["first"])
    assert client.get("/api/me", headers=hb).json()["profile"]["status"].startswith("Looking")
