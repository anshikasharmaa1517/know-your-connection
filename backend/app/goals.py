"""Aim and find the gap.

A goal is planned into 1-4 needs (kinds of people). The ranker walks the
user's graph out to two hops:

    (:Me)-[:KNOWS]->(p)                          direct
    (:Me)-[:KNOWS]->(via)-[:KNOWS]-(p)           warm path through someone you know

narrows the pool with keyword hits and (when embeddings are on) FalkorDB's
vector index, then asks the model to score fit with evidence. Evidence must be
a real Fact id on that person or their profile; anything else is dropped, so
every suggestion has a receipt.

Results are stored as (:Person)-[:RELEVANT_TO {score, reason, need, evidence}]->(:Goal).
A need nobody scores well on becomes (:Goal)-[:HAS_GAP]->(:Gap).
Closing a goal deletes its ranking and gaps.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from falkordb import Graph

from .config import get_settings
from . import web
from .db import get_store, one, rows, run
from .llm import GoalPlan, keyword_hits, get_model
from .network import refresh_embeddings
from .util import new_id, now_ms


class GoalError(ValueError):
    pass


def _with_about(g: Graph, text: str) -> str:
    """The planner reads who you are too, so 'internship' means the right kind of internship."""
    from . import smart

    about = smart.about_of(g)
    return f"{text}\nABOUT ME: {about}" if about else text


def with_company_question(plan: GoalPlan, text: str) -> GoalPlan:
    """Some goals always turn on a company fact, whatever the planner said."""
    if not (plan.company_question or "").strip():
        t = text.lower()
        if re.search(r"\bremote\b", t):
            plan.company_question = "remote internships" if re.search(r"\bintern", t) else "remote work policy"
        elif re.search(r"\bvisa\b|sponsor", t):
            plan.company_question = "visa sponsorship"
    return plan


def _plan_from(goal: dict) -> GoalPlan:
    plan = GoalPlan.model_validate(
        {"summary": goal.get("summary") or goal["text"], "needs": json.loads(goal["needs"]), "company_question": goal.get("company_question")}
    )
    return with_company_question(plan, goal.get("text") or "")


def _keywords(plan: GoalPlan) -> list[str]:
    out: list[str] = []
    for need in plan.needs:
        out.extend(k.lower() for k in need.keywords)
    return list(dict.fromkeys(out))


def create_goal(g: Graph, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise GoalError("goal is empty")
    same = one(g, "MATCH (goal:Goal {status:'open'}) WHERE toLower(goal.text) = toLower($t) RETURN goal.id AS id", {"t": text})
    if same:  # the same goal again: open the one you already have
        return get_goal(g, same["id"])
    plan = with_company_question(get_model().plan_goal(_with_about(g, text)), text)
    plan.summary = plan.summary if plan.summary and len(plan.summary) < 200 else text
    gid = new_id()
    run(
        g,
        "CREATE (:Goal {id:$id, text:$text, summary:$summary, needs:$needs, keywords:$kw, company_question:$cq, status:'open', "
        "created_at:$now, updated_at:$now})",
        {
            "id": gid,
            "text": text,
            "summary": plan.summary,
            "needs": json.dumps([n.model_dump() for n in plan.needs]),
            "kw": _keywords(plan),
            "cq": plan.company_question,
            "now": now_ms(),
        },
    )
    rank(g, gid)
    return get_goal(g, gid)


def update_goal(g: Graph, gid: str, text: str) -> dict:
    goal = _goal(g, gid)
    if goal["status"] != "open":
        raise GoalError("goal is closed")
    plan = with_company_question(get_model().plan_goal(_with_about(g, text)), text)
    plan.summary = plan.summary if plan.summary and len(plan.summary) < 200 else text
    run(
        g,
        "MATCH (goal:Goal {id:$id}) SET goal.text = $text, goal.summary = $summary, goal.needs = $needs, "
        "goal.keywords = $kw, goal.company_question = $cq, goal.updated_at = $now",
        {
            "id": gid,
            "text": text.strip(),
            "summary": plan.summary,
            "needs": json.dumps([n.model_dump() for n in plan.needs]),
            "kw": _keywords(plan),
            "cq": plan.company_question,
            "now": now_ms(),
        },
    )
    rank(g, gid)
    return get_goal(g, gid)


def close_goal(g: Graph, gid: str) -> dict:
    _goal(g, gid)
    _clear_ranking(g, gid)
    run(g, "MATCH (goal:Goal {id:$id}) SET goal.status = 'closed', goal.closed_at = $now", {"id": gid, "now": now_ms()})
    return get_goal(g, gid)


def reopen_goal(g: Graph, gid: str) -> dict:
    _goal(g, gid)
    run(g, "MATCH (goal:Goal {id:$id}) SET goal.status = 'open', goal.closed_at = NULL", {"id": gid})
    rank(g, gid)
    return get_goal(g, gid)


def _goal(g: Graph, gid: str) -> dict:
    found = one(g, "MATCH (goal:Goal {id:$id}) RETURN goal AS goal", {"id": gid})
    if not found:
        raise GoalError("goal not found")
    return found["goal"]


def _clear_ranking(g: Graph, gid: str) -> None:
    run(g, "MATCH (:Person)-[r:RELEVANT_TO]->(:Goal {id:$id}) DELETE r", {"id": gid})
    run(g, "MATCH (:Goal {id:$id})-[:HAS_GAP]->(gap:Gap) DETACH DELETE gap", {"id": gid})


def list_goals(g: Graph) -> list[dict]:
    return rows(
        g,
        "MATCH (goal:Goal) OPTIONAL MATCH (p:Person)-[:RELEVANT_TO]->(goal) "
        "OPTIONAL MATCH (goal)-[:HAS_GAP]->(gap:Gap) "
        "RETURN goal {.id, .text, .summary, .status, .created_at, .ranked_at} AS goal, "
        "count(DISTINCT p) AS people, count(DISTINCT gap) AS gaps "
        "ORDER BY CASE goal.status WHEN 'open' THEN 0 ELSE 1 END, goal.created_at DESC",
    )


# --------------------------------------------------------------------------
# Ranking


def _pool(g: Graph) -> dict[str, dict]:
    direct = rows(
        g,
        "MATCH (:Me)-[k:KNOWS]->(p:Person) "
        "RETURN p.id AS id, p.name AS name, p.headline AS headline, p.company AS company, "
        "p.position AS position, p.linkedin_url AS url, k.strength AS strength, k.since AS since",
    )
    pool = {d["id"]: {**d, "tie": "direct", "via_id": None, "via_name": None} for d in direct}
    second = rows(
        g,
        "MATCH (:Me)-[:KNOWS]->(v:Person)-[t:KNOWS]-(p:Person) WHERE NOT (:Me)-[:KNOWS]->(p) "
        "RETURN p.id AS id, p.name AS name, p.headline AS headline, p.company AS company, "
        "p.position AS position, p.linkedin_url AS url, v.id AS via_id, v.name AS via_name, t.relation AS relation",
    )
    for s in second:
        if s["id"] in pool:
            continue
        rel = f" ({s['relation']})" if s.get("relation") else ""
        pool[s["id"]] = {**s, "strength": None, "tie": f"via {s['via_name']}{rel}"}
    facts = rows(
        g,
        "MATCH (f:Fact)-[:ABOUT]->(p:Person) RETURN p.id AS pid, collect({id:f.id, kind:f.kind, text:f.text}) AS facts",
    )
    for f in facts:
        if f["pid"] in pool:
            pool[f["pid"]]["facts"] = f["facts"][:15]
    for p in pool.values():
        p.setdefault("facts", [])
    attach_statuses(list(pool.values()))
    return pool


def attach_statuses(people: list[dict]) -> None:
    """Add what app users wrote about themselves as a 'status' fact, matched by LinkedIn profile."""
    from .users import li_key, statuses_for

    keys = {li_key(p.get("url")): p for p in people if p.get("url")}
    keys.pop("", None)
    for k, st in statuses_for(get_store(), list(keys)).items():
        p = keys[k]
        p["facts"] = [f for f in p.get("facts", []) if f.get("kind") != "status"] + [
            {"id": f"status:{st['id']}", "kind": "status", "text": f"{st['name']} wrote on Connections: {st['status']}"}
        ]


def _shortlist(g: Graph, plan: GoalPlan, pool: dict[str, dict], size: int) -> list[str]:
    if len(pool) <= size:
        return list(pool)
    keywords = _keywords(plan)
    kw_score: dict[str, int] = {}
    for pid, p in pool.items():
        text = " ".join([p.get("headline") or "", p.get("company") or ""] + [f["text"] for f in p["facts"]])
        kw_score[pid] = len(keyword_hits(keywords, text))
    vec_score: dict[str, float] = {}
    model = get_model()
    if get_settings().llm_enabled:
        refresh_embeddings(g)
        queries = [f"{n.label}. {n.description}. {', '.join(n.keywords)}" for n in plan.needs]
        vectors = model.embed(queries) or []
        for v in vectors:
            hits = rows(
                g,
                "CALL db.idx.vector.queryNodes('Person', 'embedding', $k, vecf32($v)) YIELD node, score "
                "RETURN node.id AS id, score",
                {"k": size, "v": v},
            )
            for h in hits:
                sim = 1.0 - float(h["score"])
                vec_score[h["id"]] = max(vec_score.get(h["id"], 0.0), sim)
    ranked = sorted(pool, key=lambda pid: (kw_score.get(pid, 0) + 3 * vec_score.get(pid, 0.0)), reverse=True)
    picked = [pid for pid in ranked if kw_score.get(pid, 0) or vec_score.get(pid, 0) > 0.2]
    return picked[:size]


_NEED_SKIP = {"a", "an", "the", "at", "in", "of", "for", "to", "and", "or", "with", "who", "on", "someone", "person"}


def _need_words(label: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", (label or "").lower()) if w not in _NEED_SKIP}


def match_need(label: str, plan: GoalPlan) -> str:
    """Map the need a model names back to one of the plan's needs, tolerating rewording
    ("HR leader at a remote-first company" vs "HR leader at remote-first company")."""
    if not plan.needs:
        return label
    if len(plan.needs) == 1:
        return plan.needs[0].label
    want = _need_words(label)
    best, best_overlap = None, 0.0
    for need in plan.needs:
        have = _need_words(need.label)
        if have == want:
            return need.label
        if not have or not want:
            continue
        overlap = len(have & want) / min(len(have), len(want))
        if overlap > best_overlap:
            best, best_overlap = need.label, overlap
    return best if best and best_overlap >= 0.5 else label


_GENERIC_HEADS = {"manager", "engineer", "lead", "partner", "officer", "director", "specialist", "consultant", "executive", "associate", "leader", "developer"}
_FILLER_HEADS = {"role", "roles", "job", "jobs", "position", "positions", "opportunity", "opportunities", "opening", "openings", "internship", "internships", "referral", "referrals"}
_LINKER_RE = re.compile(r"\b(for|at|in|with|who|from|on|of)\b")


def head_phrase(label: str) -> str:
    """The role a need names: 'remote work recruiter' -> 'recruiter',
    'career coach for remote work' -> 'coach', 'remote-first hiring manager' -> 'hiring manager'."""
    core = _LINKER_RE.split((label or "").lower())[0]
    words = [w for w in re.findall(r"[a-z0-9]+", core) if w]
    while len(words) > 1 and words[-1] in _FILLER_HEADS:
        words.pop()
    if not words:
        return ""
    if words[-1].rstrip("s") in _GENERIC_HEADS and len(words) > 1:
        return " ".join(words[-2:])
    return words[-1]


def _has_phrase(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    pattern = r"(?<![a-z0-9])" + r"[\s\-]*".join(re.escape(w) for w in phrase.split(" ")) + r"s?(?![a-z0-9])"
    return re.search(pattern, text.lower()) is not None


def covers(need, person: dict, reason: str) -> bool:
    """Does this person plainly fit a need: their title names the role, or the title,
    ranking reason and notes hit enough of the need's keywords."""
    title = " ".join([person.get("headline") or "", person.get("position") or ""])
    if _has_phrase(title, head_phrase(need.label)):
        return True
    text = " ".join([title, person.get("company") or "", reason] + [f["text"] for f in person.get("facts", []) or []])
    hits = keyword_hits([k.lower() for k in need.keywords], text)
    return len(hits) >= (1 if len(need.keywords) < 4 else 2)


def open_gaps(plan: GoalPlan, ranking: list[dict], gaps: list[dict]) -> list[dict]:
    """Drop stored gaps that the current ranking already covers."""
    threshold = get_settings().gap_threshold
    needs = {n.label.lower(): n for n in plan.needs}
    out = []
    for gap in gaps:
        need = needs.get((gap.get("need") or "").lower())
        if need and any(
            (r.get("score") or 0) >= threshold
            and ((r.get("need") or "").lower() == need.label.lower() or covers(need, r["person"], r.get("reason") or ""))
            for r in ranking
        ):
            continue
        out.append(gap)
    return out


def attach_web_facts(plan: GoalPlan, people: list[dict]) -> bool:
    """Search the web once per company for the goal's company question and add the results to each
    person's facts as kind 'web'. Returns True when web evidence is in play (some company had results)."""
    question = (getattr(plan, "company_question", None) or "").strip()
    if not question or web.provider() == "off":
        return False
    found = web.company_facts(get_store(), [p.get("company") or "" for p in people], question)
    any_found = False
    for p in people:
        facts = found.get((p.get("company") or "").strip(), [])
        if facts:
            any_found = True
        p["facts"] = [f for f in p.get("facts", []) if f.get("kind") != "web"] + [
            {**f, "kind": "web", "text": f"[source: {urlparse(f['url']).netloc.removeprefix('www.')}] {f['text'][:260]}"} for f in facts
        ]
    return any_found


def role_need(plan: GoalPlan, person: dict, label: str) -> str | None:
    """The need this person's own title fits: the model's pick if the title backs it, else another need the title
    backs, else None (a software manager is not a 'recruiter', whatever a web page says about their company)."""
    from .search import ROLE_ALIASES

    # What the person does: their title, plus what you or they wrote (notes, status). Never a web page.
    own = {
        "headline": person.get("headline"),
        "position": person.get("position"),
        "company": person.get("company"),
        "facts": [f for f in person.get("facts", []) or [] if f.get("kind") != "web"],
    }
    title = " ".join(filter(None, [person.get("headline"), person.get("position")]))
    by_label = {n.label.lower(): n for n in plan.needs}
    first = by_label.get((label or "").lower())
    for need in ([first] if first else []) + [n for n in plan.needs if n is not first]:
        if covers(need, own, ""):
            return need.label
        head = head_phrase(need.label)
        aliases = [a for group in ROLE_ALIASES if head in group for a in group]
        if any(_has_phrase(title, a) for a in aliases):
            return need.label
    return None


def _since_ms(since) -> int | None:
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(str(since)[:10]).timestamp() * 1000) if since else None
    except ValueError:
        return None


VERIFY_TOP = 12


def score_against(g: Graph, plan: GoalPlan, text: str, direct_only: bool = False, goal_id: str | None = None):
    """Rank people in this graph against a plan, in steps:
    1. leave out people you marked wrong for this goal,
    2. keep only people whose own title, notes or status fit a need (role filter),
    3. check their companies on the web when the goal turns on a company fact,
    4. score with the model, which also sees ABOUT ME and your feedback as examples,
    5. add graph signals (closeness, notes, recency, your thumbs up),
    6. have the model verify the top results against the evidence before they are shown.
    Returns (pool, shortlist, ordered, model_name); ordered is [(person_id, {score, reason, need, evidence, web})]."""
    from . import smart

    settings = get_settings()
    fb = smart.feedback(g, goal_id) if goal_id else {"own": {}}
    votes = fb["own"]
    goal_text = smart.context(g, goal_id, text)
    pool = _pool(g)
    if direct_only:
        pool = {k: v for k, v in pool.items() if v["tie"] == "direct"}
    for pid, vote in votes.items():
        if vote == "wrong":
            pool.pop(pid, None)
    shortlist = _shortlist(g, plan, pool, settings.rank_pool)
    for pid, vote in votes.items():  # people you liked always get looked at again
        if vote == "good" and pid in pool and pid not in shortlist:
            shortlist.append(pid)
    shortlist = [pid for pid in shortlist if role_need(plan, pool[pid], "") is not None]
    attach_web_facts(plan, [pool[pid] for pid in shortlist])
    payload = [
        {
            "id": pid,
            "name": pool[pid]["name"],
            "headline": pool[pid].get("headline") or "",
            "tie": pool[pid]["tie"],
            "strength": pool[pid].get("strength"),
            "facts": [{"id": f["id"], "kind": f["kind"], "text": f["text"]} for f in pool[pid]["facts"]],
        }
        for pid in shortlist
    ]
    model = get_model()
    scores = model.score_people(goal_text, plan, payload).scores if payload else []

    kept: dict[str, dict] = {}
    for s in scores:
        person = pool.get(s.person_id)
        if not person or s.person_id not in shortlist:
            continue
        valid = {f["id"] for f in person["facts"]}
        evidence = [e for e in s.evidence if e in valid or e == "profile"]
        if "profile" in evidence and not (person.get("headline") or person.get("company")):
            evidence.remove("profile")
        score = max(0, min(100, int(s.score)))
        if not evidence or score < settings.relevance_floor:
            continue
        need = role_need(plan, person, match_need(s.need, plan))
        if need is None:
            continue
        person["since_ms"] = _since_ms(person.get("since"))
        score = max(0, min(100, score + smart.own_bonus(person, votes.get(s.person_id))))
        web_cited = [{k: f[k] for k in ("id", "text", "url")} for f in person["facts"] if f.get("kind") == "web" and f["id"] in evidence]
        backed = any(e != "profile" for e in evidence)  # a web source, their status, or your note
        if (plan.company_question or "").strip() and not backed and votes.get(s.person_id) != "good":
            # The goal turns on a company fact and nothing backed it: unknown, so it cannot rank high.
            score = min(score, 40)
            if score < settings.relevance_floor:
                continue
        if s.person_id not in kept or kept[s.person_id]["score"] < score:
            kept[s.person_id] = {"score": score, "reason": s.reason.strip(), "need": need, "evidence": evidence, "web": web_cited}
    ordered = sorted(kept.items(), key=lambda kv: kv[1]["score"], reverse=True)

    # A second, separate pass over what would be shown first.
    top = ordered[:VERIFY_TOP]
    verdicts = model.verify(
        goal_text,
        [
            {
                "id": pid,
                "name": pool[pid]["name"],
                "headline": pool[pid].get("headline") or "",
                "facts": [f["text"] for f in pool[pid]["facts"]][:6],
                "score": r["score"],
                "reason": r["reason"],
            }
            for pid, r in top
        ],
    ) if top else None
    if verdicts is not None:
        by_id = {v.person_id: v for v in verdicts}
        for pid, r in top:
            v = by_id.get(pid)
            if v is None:
                continue  # no verdict: leave as scored
            if not v.keep and votes.get(pid) != "good":
                kept.pop(pid, None)
                continue
            r["score"] = max(0, min(r["score"] + 5, int(v.score)))
            if v.reason.strip():
                r["reason"] = v.reason.strip()
        ordered = sorted(kept.items(), key=lambda kv: kv[1]["score"], reverse=True)
    return pool, shortlist, ordered, model.name


def rank(g: Graph, gid: str) -> dict:
    settings = get_settings()
    goal = _goal(g, gid)
    plan = _plan_from(goal)
    pool, shortlist, ordered, model_name = score_against(g, plan, goal["text"], goal_id=gid)

    _clear_ranking(g, gid)
    # One write for the whole ranking: FalkorDB may be far from the app server, so round trips add up.
    batch = [
        {
            "pid": pid,
            "score": r["score"],
            "reason": r["reason"],
            "need": r["need"],
            "evidence": r["evidence"],
            "rank": position,
            "via_id": pool[pid].get("via_id") or "",
            "via_name": pool[pid].get("via_name") or "",
            "tie": pool[pid]["tie"],
            "web": json.dumps(r.get("web") or [], ensure_ascii=False),
        }
        for position, (pid, r) in enumerate(ordered, start=1)
    ]
    if batch:
        run(
            g,
            "MATCH (goal:Goal {id:$gid}) UNWIND $rows AS row MATCH (p:Person {id:row.pid}) "
            "CREATE (p)-[:RELEVANT_TO {score:row.score, reason:row.reason, need:row.need, evidence:row.evidence, "
            "rank:row.rank, via_id:row.via_id, via_name:row.via_name, tie:row.tie, web:row.web}]->(goal)",
            {"gid": gid, "rows": batch},
        )

    gaps = []
    for need in plan.needs:
        # A need is covered by anyone ranked for it, or ranked for another need who plainly fits
        # this one too (one person can serve two needs; the model only names one).
        best = max(
            (
                r["score"]
                for pid, r in ordered
                if r["need"].lower() == need.label.lower() or covers(need, pool[pid], r["reason"])
            ),
            default=0,
        )
        if best < settings.gap_threshold:
            gap_id = new_id()
            text = f"No warm path to {need.label}" if best == 0 else f"Only a weak path to {need.label}"
            run(
                g,
                "MATCH (goal:Goal {id:$gid}) CREATE (goal)-[:HAS_GAP]->(:Gap {id:$id, need:$need, "
                "description:$desc, keywords:$kw, text:$text, best_score:$best, created_at:$now})",
                {
                    "gid": gid,
                    "id": gap_id,
                    "need": need.label,
                    "desc": need.description,
                    "kw": [k.lower() for k in need.keywords],
                    "text": text,
                    "best": best,
                    "now": now_ms(),
                },
            )
            gaps.append(gap_id)

    run(
        g,
        "MATCH (goal:Goal {id:$id}) SET goal.ranked_at = $now, goal.pool = $pool, goal.shortlist = $short, goal.model = $m",
        {"id": gid, "now": now_ms(), "pool": len(pool), "short": len(shortlist), "m": model_name},
    )
    return {"ranked": len(ordered), "gaps": len(gaps), "pool": len(pool), "shortlist": len(shortlist)}


def get_goal(g: Graph, gid: str) -> dict:
    goal = _goal(g, gid)
    ranking = rows(
        g,
        "MATCH (p:Person)-[r:RELEVANT_TO]->(:Goal {id:$id}) "
        "OPTIONAL MATCH (:Me)-[k:KNOWS]->(p) "
        "RETURN p {.id, .name, .headline, .company, .linkedin_url} AS person, r.score AS score, r.reason AS reason, "
        "r.need AS need, r.evidence AS evidence, r.web AS web, r.tie AS tie, r.via_id AS via_id, r.via_name AS via_name, "
        "k.strength AS strength ORDER BY r.rank",
        {"id": gid},
    )
    fact_ids = list({e for r in ranking for e in (r["evidence"] or []) if e != "profile"})
    facts = {
        f["id"]: f
        for f in rows(
            g,
            "UNWIND $ids AS fid MATCH (s:Source)-[:REPORTED]->(f:Fact {id:fid}) "
            "RETURN f.id AS id, f.text AS text, f.kind AS kind, s.id AS source_id, s.title AS source, "
            "s.kind AS source_kind, s.created_at AS at",
            {"ids": fact_ids},
        )
    }
    pids = [r["person"]["id"] for r in ranking]
    profile_sources = {
        r["pid"]: r
        for r in rows(
            g,
            "UNWIND $ids AS pid MATCH (s:Source)-[:IMPORTED|MENTIONS]->(p:Person {id:pid}) "
            "WITH pid, s ORDER BY s.created_at "
            "WITH pid, collect(s)[0] AS s RETURN pid, s.id AS source_id, s.title AS source, s.kind AS source_kind, "
            "s.created_at AS at",
            {"ids": pids},
        )
    }
    for r in ranking:
        receipts = []
        web_facts = {w["id"]: w for w in _load_agents(r.pop("web", None)) or []}
        for e in r.pop("evidence") or []:
            if e in web_facts:
                w = web_facts[e]
                receipts.append({"kind": "web", "text": w["text"], "url": w["url"], "source": urlparse(w["url"]).netloc.removeprefix("www."), "source_kind": "web"})
                continue
            if e == "profile":
                src = profile_sources.get(r["person"]["id"], {})
                receipts.append(
                    {
                        "kind": "profile",
                        "text": r["person"].get("headline") or r["person"].get("company") or "",
                        "source_id": src.get("source_id"),
                        "source": src.get("source"),
                        "source_kind": src.get("source_kind"),
                        "at": src.get("at"),
                    }
                )
            elif e in facts:
                f = facts[e]
                receipts.append({"kind": f["kind"], **{k: f[k] for k in ("id", "text", "source_id", "source", "source_kind", "at")}})
        r["receipts"] = receipts
    gaps = rows(
        g,
        "MATCH (:Goal {id:$id})-[:HAS_GAP]->(gap:Gap) "
        "RETURN gap {.id, .need, .description, .text, .best_score, .request_id, .agents} AS gap",
        {"id": gid},
    )
    return {
        "goal": {k: goal.get(k) for k in ("id", "text", "summary", "status", "created_at", "ranked_at", "pool", "shortlist", "model")}
        | {"agents": _load_agents(goal.get("agents"))}
        | {"needs": json.loads(goal["needs"])},
        "ranking": ranking,
        "votes": _votes(g, gid),
        "quality": _quality(g, gid),
        "gaps": open_gaps(_plan_from(goal), ranking, [x["gap"] | {"agents": _load_agents(x["gap"].get("agents"))} for x in gaps]),
    }


# --------------------------------------------------------------------------
# Friends' agent results, kept on the goal so they survive leaving the page.


def _load_agents(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def save_agents(g: Graph, gid: str, result: dict, gap_id: str | None = None) -> dict:
    """Store the latest agent run on the goal (for the map) and on the gap that asked."""
    result = {**result, "at": now_ms()}
    blob = json.dumps(result, ensure_ascii=False)
    run(g, "MATCH (goal:Goal {id:$gid}) SET goal.agents = $j", {"gid": gid, "j": blob})
    if gap_id:
        run(g, "MATCH (:Goal {id:$gid})-[:HAS_GAP]->(gap:Gap {id:$id}) SET gap.agents = $j", {"gid": gid, "id": gap_id, "j": blob})
    return result


def _votes(g: Graph, gid: str) -> dict:
    from . import smart

    fb = smart.feedback(g, gid)
    return {**fb["own"], **fb["friends"]}


def _quality(g: Graph, gid: str) -> dict:
    from . import smart

    return smart.quality(g, gid)
