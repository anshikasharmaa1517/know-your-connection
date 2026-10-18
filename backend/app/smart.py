"""What makes ranking smarter than one model call: who you are, what you told it, and the graph.

About you   (:Me).about in your own graph. Private: no other account can read it.
Feedback    (:Person)-[:RATED {vote, shown_score, at}]->(:Goal) for your own people, and goal.friend_votes
            (JSON) for people from friends' networks. "wrong" people are left out of later rankings; both
            kinds are shown to the model as examples so it generalises.
Signals     Graph facts the model does not weigh well on its own: closeness, notes you wrote, how many
            friends connect you, how far away someone is.
Accuracy    Of the suggestions you rated that were shown as strong, how many you said were right. Measured
            on what you actually saw, so it cannot flatter itself.
"""

from __future__ import annotations

import json
import time

from falkordb import Graph

from .db import one, rows, run
from .util import now_ms

ABOUT_MAX = 600
STRONG = 50


# About you ----------------------------------------------------------------


def about_of(g: Graph) -> str:
    found = one(g, "MATCH (m:Me {id:'me'}) RETURN m.about AS about") or {}
    return found.get("about") or ""


def set_about(g: Graph, text: str) -> str:
    text = " ".join((text or "").split())
    if len(text) > ABOUT_MAX:
        raise ValueError(f"keep it under {ABOUT_MAX} characters")
    run(g, "MATCH (m:Me {id:'me'}) SET m.about = $t, m.about_at = $now", {"t": text, "now": now_ms()})
    return text


# Feedback -----------------------------------------------------------------


def _friend_votes(g: Graph, gid: str) -> dict:
    found = one(g, "MATCH (goal:Goal {id:$id}) RETURN goal.friend_votes AS v", {"id": gid}) or {}
    try:
        return json.loads(found.get("v") or "{}")
    except (TypeError, ValueError):
        return {}


def rate(g: Graph, gid: str, *, vote: str, shown_score: int, person_id: str | None = None, key: str | None = None,
         name: str = "", title: str = "") -> dict:
    if vote not in ("good", "wrong", "clear"):
        raise ValueError("vote is good, wrong or clear")
    if person_id:
        run(g, "MATCH (:Person {id:$pid})-[r:RATED]->(:Goal {id:$gid}) DELETE r", {"pid": person_id, "gid": gid})
        if vote != "clear":
            run(
                g,
                "MATCH (p:Person {id:$pid}), (goal:Goal {id:$gid}) "
                "CREATE (p)-[:RATED {vote:$v, shown_score:$s, at:$now}]->(goal)",
                {"pid": person_id, "gid": gid, "v": vote, "s": int(shown_score or 0), "now": now_ms()},
            )
    elif key:
        votes = _friend_votes(g, gid)
        if vote == "clear":
            votes.pop(key, None)
        else:
            votes[key] = {"vote": vote, "shown_score": int(shown_score or 0), "name": name, "title": title, "at": now_ms()}
        run(g, "MATCH (goal:Goal {id:$id}) SET goal.friend_votes = $v", {"id": gid, "v": json.dumps(votes, ensure_ascii=False)})
        if vote == "wrong":  # also drop them from the saved friends' results, so they stay gone after a reload
            saved = one(g, "MATCH (goal:Goal {id:$id}) RETURN goal.agents AS a", {"id": gid}) or {}
            try:
                agents = json.loads(saved.get("a") or "null")
            except (TypeError, ValueError):
                agents = None
            if agents and isinstance(agents.get("people"), list):
                agents["people"] = [p for p in agents["people"] if p.get("key") != key]
                run(g, "MATCH (goal:Goal {id:$id}) SET goal.agents = $a", {"id": gid, "a": json.dumps(agents, ensure_ascii=False)})
    else:
        raise ValueError("say who you are rating")
    return quality(g, gid)


def feedback(g: Graph, gid: str) -> dict:
    """{"own": {pid: vote}, "friends": {key: vote}, "examples": {"good": [...], "wrong": [...]}}"""
    own = rows(
        g,
        "MATCH (p:Person)-[r:RATED]->(:Goal {id:$gid}) RETURN p.id AS id, r.vote AS vote, r.shown_score AS score, "
        "p.name AS name, coalesce(p.headline, p.position, '') AS title",
        {"gid": gid},
    )
    friends = _friend_votes(g, gid)
    examples: dict[str, list[str]] = {"good": [], "wrong": []}
    for r in own:
        examples[r["vote"]].append(r["title"] or r["name"])
    for v in friends.values():
        examples[v["vote"]].append(v.get("title") or v.get("name") or "")
    return {
        "own": {r["id"]: r["vote"] for r in own},
        "friends": {k: v["vote"] for k, v in friends.items()},
        "examples": {k: [x for x in v if x][:12] for k, v in examples.items()},
        "rated": own + [{"vote": v["vote"], "score": v.get("shown_score", 0)} for v in friends.values()],
    }


def context(g: Graph, gid: str | None, goal_text: str) -> str:
    """The goal as the model sees it: the goal, who you are, and what you said about earlier suggestions."""
    parts = [goal_text]
    about = about_of(g)
    if about:
        parts.append(f"ABOUT ME: {about}")
    if gid:
        ex = feedback(g, gid)["examples"]
        if ex["good"]:
            parts.append("I MARKED THESE AS GOOD FITS (find more like them): " + "; ".join(ex["good"]))
        if ex["wrong"]:
            parts.append("I MARKED THESE AS WRONG (avoid people like them): " + "; ".join(ex["wrong"]))
    return "\n".join(parts)


def quality(g: Graph, gid: str) -> dict:
    """Of the suggestions shown as strong that you rated, the share you said were right."""
    rated = feedback(g, gid)["rated"]
    strong = [r for r in rated if (r.get("score") or 0) >= STRONG]
    good = sum(1 for r in strong if r["vote"] == "good")
    missed = sum(1 for r in rated if (r.get("score") or 0) < STRONG and r["vote"] == "good")
    return {
        "rated": len(rated),
        "strong_rated": len(strong),
        "strong_right": good,
        "accuracy": round(100 * good / len(strong)) if strong else None,
        "weak_but_good": missed,
    }


# Graph signals ----------------------------------------------------------------

YEAR_MS = 365 * 24 * 3600 * 1000


def own_bonus(person: dict, vote: str | None) -> int:
    """Nudges from your own graph: closeness, recent connection, notes you wrote, your earlier thumbs up."""
    b = 0
    if person.get("strength") == "close":
        b += 8
    elif person.get("tie") == "direct":
        b += 2
    else:
        b -= 5  # reached through someone
    since = person.get("since_ms")
    if since and time.time() * 1000 - since < YEAR_MS:
        b += 2
    if any(f.get("kind") in ("fact", "intent") for f in person.get("facts", [])):
        b += 3
    if vote == "good":
        b += 10
    return b


def friend_bonus(hit: dict, vote: str | None) -> int:
    """Nudges for people in friends' networks: more friends who know them is a warmer path."""
    b = 4 * max(0, len(hit.get("paths") or []) - 1)
    if hit.get("degree", 2) >= 3:
        b -= 5
    if vote == "good":
        b += 10
    return b
