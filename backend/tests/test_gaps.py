"""A gap must not claim "no warm path" when someone ranked just below plainly fits that need."""

from conftest import signup
from test_find import import_all

from app import llm
from app.llm import GoalPlan, HeuristicModel, Need, Score, Scores

PLAN = GoalPlan(
    summary="Land a remote job",
    needs=[
        Need(label="Hiring manager at a remote company", description="x", keywords=["engineering manager", "director", "remote"]),
        Need(label="HR leader at remote-first company", description="x", keywords=["hr", "talent", "recruiter", "people", "remote"]),
    ],
)


class OneNeedModel(HeuristicModel):
    """Files every match under the first need, reworded, like real models sometimes do."""

    def plan_goal(self, text):
        return PLAN

    def score_people(self, goal, plan, people):
        out = []
        for p in people:
            if "Talent" in (p["headline"] or "") or "Recruit" in (p["headline"] or ""):
                out.append(Score(person_id=p["id"], score=85, need="hiring manager at remote company",
                                 reason=f"{p['name']} runs talent hiring, often for remote roles.", evidence=["profile"]))
        return Scores(scores=out)


def test_need_labels_match_loosely():
    assert llm and PLAN
    from app.goals import match_need

    assert match_need("HR leader at a remote-first company", PLAN) == "HR leader at remote-first company"
    assert match_need("hiring managers at remote companies", PLAN) == "Hiring manager at a remote company"
    assert match_need("astronaut", PLAN) == "astronaut"


def test_no_false_gap_when_someone_fits(client):
    llm.set_model(OneNeedModel())
    try:
        h, _ = signup(client, "Js")
        import_all(client, h, "a")
        goal = client.post("/api/goals", headers=h, json={"text": "remote job"}).json()
        assert goal["ranking"], goal
        needs_with_gaps = {g["need"] for g in goal["gaps"]}
        # Tanvi (Technical Recruiter) and Pooja (Head of Talent) plainly cover the HR need.
        assert "HR leader at remote-first company" not in needs_with_gaps
        # And their need label was mapped back to the plan's wording, so that need is covered too.
        assert "Hiring manager at a remote company" not in needs_with_gaps
    finally:
        llm.set_model(None)


def test_reopen_goal_reranks(client):
    h, _ = signup(client, "Js")
    import_all(client, h, "a")
    g = client.post("/api/goals", headers=h, json={"text": "Summer 2027 VC internship"}).json()
    gid = g["goal"]["id"]
    closed = client.post(f"/api/goals/{gid}/close", headers=h).json()
    assert closed["goal"]["status"] == "closed" and closed["ranking"] == []
    back = client.post(f"/api/goals/{gid}/reopen", headers=h).json()
    assert back["goal"]["status"] == "open" and back["ranking"]
