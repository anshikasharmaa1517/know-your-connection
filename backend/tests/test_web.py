"""Company checks on the web: sources back the ranking, unknowns cannot rank high."""

from conftest import signup
from test_find import import_all

from app import llm, web
from app.llm import GoalPlan, HeuristicModel, Need, Score, Scores


class RemoteJobModel(HeuristicModel):
    def plan_goal(self, text):
        return GoalPlan(
            summary=text,
            needs=[Need(label="recruiter", description="x", keywords=["recruiter", "talent", "recruiting"])],
            company_question="remote work policy",
        )

    def score_people(self, goal, plan, people):
        out = []
        for p in people:
            web_ids = [f["id"] for f in p["facts"] if f["kind"] == "web"]
            # A model that is sure of itself: 85 for every recruiter, citing web facts when it has them.
            if "Recruit" in (p["headline"] or "") or "Talent" in (p["headline"] or ""):
                out.append(Score(person_id=p["id"], score=85, need="recruiter", reason="recruits", evidence=["profile", *web_ids]))
        return Scores(scores=out)


def test_web_sources_back_the_ranking(client, monkeypatch):
    calls = []

    def fake_search(q):
        calls.append(q)
        if "Brightpath Talent" in q:
            return [{"title": "Brightpath Talent careers", "url": "https://brightpath.example/careers", "snippet": "Brightpath Talent is a fully remote company hiring across India."}]
        return []

    monkeypatch.setenv("CONNECTIONS_WEB_SEARCH", "on")
    monkeypatch.setattr(web, "search", fake_search)
    llm.set_model(RemoteJobModel())
    try:
        h, _ = signup(client, "Anshika")
        import_all(client, h, "a")
        goal = client.post("/api/goals", headers=h, json={"text": "i want a remote job"}).json()
        by_name = {r["person"]["name"]: r for r in goal["ranking"]}
        tanvi = by_name["Tanvi Joshi"]  # Technical Recruiter at Brightpath Talent
        assert tanvi["score"] >= 85  # 85 from the model, plus graph signals (direct, recent connection)
        web_receipt = next(rc for rc in tanvi["receipts"] if rc["kind"] == "web")
        assert web_receipt["url"] == "https://brightpath.example/careers" and web_receipt["source"] == "brightpath.example"
        # A recruiter whose company the web says nothing about is capped as unknown.
        others = [r for n, r in by_name.items() if n != "Tanvi Joshi"]
        assert others and all(r["score"] <= 40 for r in others)
        # Each company is searched once, then served from FalkorDB.
        first = len(calls)
        client.post(f"/api/goals/{goal['goal']['id']}/rerank", headers=h)
        assert len(calls) == first
    finally:
        llm.set_model(None)
