from conftest import signup
from test_exchange import befriend
from test_find import import_all


def gap_of(client, h, text):
    goal = client.post("/api/goals", headers=h, json={"text": text}).json()
    assert goal["gaps"], goal
    return goal["goal"]["id"], goal["gaps"][0]


def test_gap_runs_friends_agents_without_a_request(client):
    ha, a = signup(client, "Js")
    hb, b = signup(client, "Bhavya")
    import_all(client, hb, "b")
    gid, gap = gap_of(client, ha, "SDE")

    # No friends yet: nothing to search, and it says so.
    alone = client.post(f"/api/goals/{gid}/gaps/{gap['id']}/ask", headers=ha).json()
    assert alone["friends"] == [] and alone["people"] == []

    befriend(client, ha, hb, b)
    found = client.post(f"/api/goals/{gid}/gaps/{gap['id']}/ask", headers=ha).json()
    names = {p["name"] for p in found["people"]}
    assert {"Nisha Arora", "Rajat Khanna"} <= names
    assert "Hrithik Saxena" not in names  # hardware, not software
    row = next(p for p in found["people"] if p["name"] == "Nisha Arora")
    assert row["paths"] == [[{"name": "Bhavya", "kind": "friend", "handle": "bhavya"}]] and row["reason"] and row["score"] > 0
    # Nothing was posted to the shared graph and Bhavya was not asked to approve anything.
    assert client.get("/api/exchange/inbox", headers=hb).json()["requests"] == []


def test_people_you_know_are_left_to_the_ranking(client):
    ha, a = signup(client, "Js")
    hb, b = signup(client, "Bhavya")
    import_all(client, ha, "a")  # Js knows Aarav Shah, SDE II
    import_all(client, hb, "b")  # so does Bhavya
    befriend(client, ha, hb, b)
    goal = client.post("/api/goals", headers=ha, json={"text": "find a product manager"}).json()
    gap = {"id": None}
    if not goal["gaps"]:
        return
    gap = goal["gaps"][0]
    found = client.post(f"/api/goals/{goal['goal']['id']}/gaps/{gap['id']}/ask", headers=ha).json()
    assert "Aarav Shah" not in {p["name"] for p in found["people"]}


def test_unknown_gap_is_404(client):
    h, _ = signup(client, "Js")
    gid, _ = gap_of(client, h, "SDE")
    assert client.post(f"/api/goals/{gid}/gaps/nope/ask", headers=h).status_code == 404


def test_other_needs_of_the_goal_are_listed_after_the_gap(client):
    from app import llm
    from app.llm import GoalPlan, HeuristicModel, Need

    class TwoNeeds(HeuristicModel):
        def plan_goal(self, text):
            return GoalPlan(summary=text, needs=[
                Need(label="career coach", description="x", keywords=["coach", "career"]),
                Need(label="software engineer at a startup", description="x", keywords=["software", "engineer", "developer", "sde"]),
            ])

    llm.set_model(TwoNeeds())
    try:
        ha, a = signup(client, "Anshika")
        hb, b = signup(client, "Jasmeet")
        import_all(client, hb, "b")
        befriend(client, ha, hb, b)
        goal = client.post("/api/goals", headers=ha, json={"text": "i want a remote job"}).json()
        coach_gap = next(g for g in goal["gaps"] if g["need"] == "career coach")
        found = client.post(f"/api/goals/{goal['goal']['id']}/gaps/{coach_gap['id']}/ask", headers=ha).json()
        # No coaches in Jasmeet's network, but the engineers who help the goal still show up.
        assert not any(p["for_gap"] for p in found["people"])
        assert "Rajat Khanna" in {p["name"] for p in found["people"]}
    finally:
        llm.set_model(None)
