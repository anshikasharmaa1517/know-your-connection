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


def test_head_phrase_and_title_cover():
    from app.goals import covers, head_phrase

    assert head_phrase("remote work recruiter") == "recruiter"
    assert head_phrase("career coach for remote work") == "coach"
    assert head_phrase("remote-first hiring manager") == "hiring manager"
    assert head_phrase("senior remote software engineer") == "software engineer"
    need = Need(label="remote work recruiter", description="x", keywords=["recruiter", "talent", "remote", "staffing", "placement"])
    assert covers(need, {"headline": "Technical Recruiter at Brightpath Talent"}, "")
    assert covers(need, {"headline": "Senior Recruiters Lead"}, "")
    assert not covers(need, {"headline": "Hiring Manager at EarlyJobs"}, "early-stage remote roles")
    coach = Need(label="career coach for remote work", description="x", keywords=["coach", "career", "remote", "mentor", "resume"])
    assert not covers(coach, {"headline": "Hiring Manager at EarlyJobs"}, "He hires for early-stage remote roles.")


def test_stale_gaps_are_hidden_on_read():
    from app.goals import open_gaps

    plan = PLAN
    ranking = [{"person": {"headline": "Technical Recruiter at X"}, "score": 80, "reason": "Leads recruiting on the talent team", "need": "something else"}]
    gaps = [{"id": "1", "need": "HR leader at remote-first company"}, {"id": "2", "need": "Hiring manager at a remote company"}]
    assert [g["id"] for g in open_gaps(plan, ranking, gaps)] == ["2"]


def test_goal_map_has_everyone_ranked_even_in_big_networks(client, monkeypatch):
    from app import network

    h, _ = signup(client, "Js")
    import_all(client, h, "a")
    g = client.post("/api/goals", headers=h, json={"text": "Summer 2027 VC internship"}).json()
    ranked = {r["person"]["id"] for r in g["ranking"]}
    assert ranked
    # Simulate a network bigger than the full snapshot's limit.
    real = network.snapshot
    monkeypatch.setattr(network, "snapshot", lambda graph, limit=400: real(graph, limit=2))
    full = client.get("/api/graph", headers=h).json()
    assert len(full["people"]) == 2
    m = client.get(f"/api/graph?goal={g['goal']['id']}", headers=h).json()
    assert ranked <= {p["id"] for p in m["people"]}
    assert {r["id"] for r in m["relevant"]} == ranked
