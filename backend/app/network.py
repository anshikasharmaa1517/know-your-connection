"""People, organisations, sources and facts inside one user's graph.

Graph shape (per user):

    (:Me)-[:KNOWS {strength, via, since}]->(:Person)
    (:Person)-[:KNOWS {relation, source_id}]->(:Person)     tie heard about in a memo
    (:Person)-[:WORKS_AT {position}]->(:Org)
    (:Source {kind: 'linkedin'|'memo'})-[:IMPORTED]->(:Person)
    (:Source)-[:MENTIONS]->(:Person)
    (:Source)-[:REPORTED]->(:Fact {kind: fact|intent|promise})-[:ABOUT]->(:Person)

Every Person and Fact is reachable from at least one Source. That edge is the
"receipt" shown next to anything the app suggests.

Strength is only ever set by the user (a swipe up, or a memo saying they met).
Nothing decays and no one gets a worth score.
"""

from __future__ import annotations

import re

from falkordb import Graph

from .db import one, rows, run
from .llm import get_model
from .util import headline, name_key, new_id, now_ms, org_key

PERSON_FIELDS = "p {.id, .name, .company, .position, .headline, .linkedin_url, .email, .created_at}"


def ensure_me(g: Graph, name: str) -> None:
    run(g, "MERGE (m:Me {id:'me'}) ON CREATE SET m.name = $name, m.created_at = $now", {"name": name, "now": now_ms()})


def people_index(g: Graph) -> list[dict]:
    """Lightweight list of everyone, used for de-duplication."""
    return rows(
        g,
        "MATCH (p:Person) OPTIONAL MATCH (:Me)-[k:KNOWS]->(p) "
        "RETURN p.id AS id, p.name AS name, p.name_key AS name_key, p.linkedin_url AS linkedin_url, "
        "p.company AS company, k.via AS via",
    )


def create_person(
    g: Graph,
    *,
    name: str,
    company: str | None = None,
    position: str | None = None,
    linkedin_url: str | None = None,
    email: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> str:
    pid = new_id()
    run(
        g,
        "CREATE (p:Person {id:$id, name:$name, name_key:$key, first_name:$first, last_name:$last, "
        "company:$company, position:$position, headline:$headline, linkedin_url:$url, email:$email, "
        "created_at:$now, updated_at:$now})",
        {
            "id": pid,
            "name": name,
            "key": name_key(name),
            "first": first_name or name.split(" ")[0],
            "last": last_name or " ".join(name.split(" ")[1:]),
            "company": company or "",
            "position": position or "",
            "headline": headline(position, company),
            "url": linkedin_url or "",
            "email": email or "",
            "now": now_ms(),
        },
    )
    if company:
        link_org(g, pid, company, position)
    return pid


def link_org(g: Graph, pid: str, company: str, position: str | None) -> None:
    key = org_key(company)
    if not key:
        return
    run(
        g,
        "MATCH (p:Person {id:$pid}) "
        "MERGE (o:Org {key:$key}) ON CREATE SET o.name = $company "
        "MERGE (p)-[w:WORKS_AT]->(o) SET w.position = $position",
        {"pid": pid, "key": key, "company": company, "position": position or ""},
    )


def connect_me(g: Graph, pid: str, *, via: str, strength: str = "known", since: str | None = None) -> bool:
    """Make sure (Me)-[:KNOWS]->(p) exists. Returns True if the edge was created."""
    existing = one(g, "MATCH (:Me)-[k:KNOWS]->(:Person {id:$pid}) RETURN k.via AS via, k.strength AS strength", {"pid": pid})
    if existing:
        via_list = list(existing.get("via") or [])
        if via not in via_list:
            via_list.append(via)
        new_strength = "close" if strength == "close" or existing.get("strength") == "close" else "known"
        run(
            g,
            "MATCH (:Me)-[k:KNOWS]->(:Person {id:$pid}) SET k.via = $via, k.strength = $strength, "
            "k.since = coalesce(k.since, $since)",
            {"pid": pid, "via": via_list, "strength": new_strength, "since": since},
        )
        return False
    run(
        g,
        "MATCH (m:Me {id:'me'}), (p:Person {id:$pid}) "
        "CREATE (m)-[:KNOWS {strength:$strength, via:$via, since:$since, created_at:$now}]->(p)",
        {"pid": pid, "strength": strength, "via": [via], "since": since, "now": now_ms()},
    )
    return True


def mark_stale(g: Graph, pids: list[str]) -> None:
    if pids:
        run(g, "UNWIND $ids AS id MATCH (p:Person {id:id}) SET p.embedding = NULL", {"ids": pids})


# --------------------------------------------------------------------------
# Reads


def list_people(g: Graph, q: str | None = None, limit: int = 200) -> list[dict]:
    params = {"limit": limit}
    where = ""
    if q:
        where = "WHERE toLower(p.name) CONTAINS $q OR toLower(p.headline) CONTAINS $q"
        params["q"] = q.lower()
    return rows(
        g,
        f"MATCH (p:Person) {where} "
        "OPTIONAL MATCH (:Me)-[k:KNOWS]->(p) "
        "OPTIONAL MATCH (f:Fact)-[:ABOUT]->(p) "
        "WITH p, k, count(DISTINCT f) AS facts "
        "OPTIONAL MATCH (:Me)-[:KNOWS]->(via:Person)-[:KNOWS]-(p) WHERE k IS NULL "
        "WITH p, k, facts, collect(DISTINCT via.name)[0] AS via_name "
        f"RETURN {PERSON_FIELDS} AS person, k.strength AS strength, k.via AS sources, facts, via_name "
        "ORDER BY CASE WHEN k IS NULL THEN 1 ELSE 0 END, facts DESC, p.name LIMIT $limit",
        params,
    )


def person_detail(g: Graph, pid: str) -> dict | None:
    head = one(
        g,
        f"MATCH (p:Person {{id:$pid}}) OPTIONAL MATCH (:Me)-[k:KNOWS]->(p) "
        f"RETURN {PERSON_FIELDS} AS person, k.strength AS strength, k.via AS via, k.since AS since",
        {"pid": pid},
    )
    if not head:
        return None
    facts = rows(
        g,
        "MATCH (s:Source)-[:REPORTED]->(f:Fact)-[:ABOUT]->(:Person {id:$pid}) "
        "RETURN f {.id, .kind, .text, .due, .owner, .done, .created_at} AS fact, "
        "s {.id, .kind, .title, .created_at} AS source ORDER BY f.created_at DESC",
        {"pid": pid},
    )
    receipts = rows(
        g,
        "MATCH (s:Source)-[r:IMPORTED|MENTIONS]->(:Person {id:$pid}) "
        "RETURN s {.id, .kind, .title, .filename, .created_at} AS source, type(r) AS how "
        "ORDER BY s.created_at",
        {"pid": pid},
    )
    ties = rows(
        g,
        "MATCH (:Person {id:$pid})-[t:KNOWS]-(o:Person) "
        "RETURN o {.id, .name, .headline} AS person, t.relation AS relation",
        {"pid": pid},
    )
    relevance = rows(
        g,
        "MATCH (:Person {id:$pid})-[r:RELEVANT_TO]->(goal:Goal {status:'open'}) "
        "RETURN goal {.id, .text} AS goal, r.score AS score, r.reason AS reason, r.need AS need",
        {"pid": pid},
    )
    return {**head, "facts": facts, "receipts": receipts, "ties": ties, "relevance": relevance}


def promises(g: Graph, include_done: bool = False) -> list[dict]:
    cond = "" if include_done else "AND coalesce(f.done, false) = false"
    return rows(
        g,
        f"MATCH (s:Source)-[:REPORTED]->(f:Fact)-[:ABOUT]->(p:Person) WHERE f.kind = 'promise' {cond} "
        "RETURN f {.id, .text, .due, .owner, .done, .created_at} AS fact, collect(p {.id, .name}) AS people, "
        "s {.id, .title, .created_at} AS source ORDER BY fact.created_at DESC",
    )


def set_promise_done(g: Graph, fid: str, done: bool) -> None:
    run(g, "MATCH (f:Fact {id:$id, kind:'promise'}) SET f.done = $done", {"id": fid, "done": done})


def sources(g: Graph) -> list[dict]:
    return rows(
        g,
        "MATCH (s:Source) OPTIONAL MATCH (s)-[r:IMPORTED|MENTIONS]->(p:Person) "
        "OPTIONAL MATCH (s)-[:REPORTED]->(f:Fact) "
        "RETURN s {.id, .kind, .title, .filename, .text, .created_at, .total} AS source, "
        "count(DISTINCT p) AS people, count(DISTINCT f) AS facts ORDER BY source.created_at DESC",
    )


def delete_source(g: Graph, sid: str) -> dict:
    """Delete a source and everything that only it vouched for."""
    src = one(g, "MATCH (s:Source {id:$id}) RETURN s.kind AS kind", {"id": sid})
    if not src:
        return {"deleted": False}
    facts = one(
        g,
        "MATCH (s:Source {id:$id})-[:REPORTED]->(f:Fact) WITH collect(f) AS fs, count(f) AS n "
        "FOREACH (f IN fs | DETACH DELETE f) RETURN n",
        {"id": sid},
    )
    # People whose only provenance was this source go too.
    orphans = rows(
        g,
        "MATCH (s:Source {id:$id})-[:IMPORTED|MENTIONS]->(p:Person) "
        "WITH p, [(o:Source)-[:IMPORTED|MENTIONS]->(p) WHERE o.id <> $id | o.id] AS others "
        "WHERE size(others) = 0 RETURN DISTINCT p.id AS id",
        {"id": sid},
    )
    orphan_ids = [o["id"] for o in orphans]
    if orphan_ids:
        run(g, "UNWIND $ids AS id MATCH (p:Person {id:id}) DETACH DELETE p", {"ids": orphan_ids})
    if src["kind"] == "linkedin":
        # Undo what the import added to people who are also known from elsewhere.
        survivors = rows(g, "MATCH (s:Source {id:$id})-[:IMPORTED]->(p:Person) RETURN p.id AS id", {"id": sid})
        for s in survivors:
            k = one(g, "MATCH (:Me)-[k:KNOWS]->(:Person {id:$pid}) RETURN k.via AS via", {"pid": s["id"]})
            if k and k.get("via"):
                via = [v for v in k["via"] if v != "linkedin"]
                run(g, "MATCH (:Me)-[k:KNOWS]->(:Person {id:$pid}) SET k.via = $via", {"pid": s["id"], "via": via})
        run(g, "MATCH (c:Candidate {import_id:$id}) DETACH DELETE c", {"id": sid})
    run(g, "MATCH (t:Person)-[k:KNOWS {source_id:$id}]-(:Person) DELETE k", {"id": sid})
    run(g, "MATCH (s:Source {id:$id}) DETACH DELETE s", {"id": sid})
    run(g, "MATCH (o:Org) WHERE NOT (o)<-[:WORKS_AT]-() DELETE o")
    return {"deleted": True, "facts": (facts or {}).get("n", 0), "people": len(orphan_ids)}


def snapshot(g: Graph, limit: int = 400) -> dict:
    """Nodes and edges for the network view."""
    people = rows(
        g,
        "MATCH (p:Person) OPTIONAL MATCH (:Me)-[k:KNOWS]->(p) "
        "RETURN p.id AS id, p.name AS name, p.headline AS headline, p.company AS company, "
        "p.position AS position, k.strength AS strength, k IS NOT NULL AS direct, "
        "size([(p)<-[:ABOUT]-(f:Fact) | f]) AS notes LIMIT $limit",
        {"limit": limit},
    )
    ties = rows(g, "MATCH (a:Person)-[t:KNOWS]->(b:Person) RETURN a.id AS a, b.id AS b, t.relation AS relation")
    relevant = rows(
        g,
        "MATCH (p:Person)-[r:RELEVANT_TO]->(goal:Goal {status:'open'}) "
        "RETURN p.id AS id, goal.id AS goal_id, r.score AS score",
    )
    return {"people": people, "ties": ties, "relevant": relevant}


# --------------------------------------------------------------------------
# Embeddings


def profile_text(person: dict, facts: list[str]) -> str:
    parts = [person.get("name", ""), person.get("headline") or "", *facts[:12]]
    return ". ".join(p for p in parts if p)


def refresh_embeddings(g: Graph, limit: int = 2000) -> int:
    """Embed people whose profile changed. No-op without an API key."""
    model = get_model()
    todo = rows(
        g,
        "MATCH (p:Person) WHERE p.embedding IS NULL "
        "OPTIONAL MATCH (f:Fact)-[:ABOUT]->(p) "
        "RETURN p.id AS id, p.name AS name, p.headline AS headline, collect(f.text) AS facts LIMIT $limit",
        {"limit": limit},
    )
    if not todo:
        return 0
    vectors = model.embed([profile_text(t, t["facts"]) for t in todo])
    if not vectors:
        return 0
    payload = [{"id": t["id"], "v": v} for t, v in zip(todo, vectors)]
    for start in range(0, len(payload), 200):
        run(
            g,
            "UNWIND $rows AS r MATCH (p:Person {id:r.id}) SET p.embedding = vecf32(r.v)",
            {"rows": payload[start : start + 200]},
        )
    return len(payload)


# --------------------------------------------------------------------------
# Notes on a person: a one-line Fact with its own Source, written from their card.

_INTENT_RE = re.compile(
    r"\b(hiring|hires?|looking for|wants?|seeking|raising|needs?|open to|searching for|building a team)\b", re.I
)


def add_note(g: Graph, pid: str, text: str) -> dict:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        raise ValueError("write a note first")
    if len(text) > 500:
        raise ValueError("keep a note under 500 characters")
    if not one(g, "MATCH (p:Person {id:$pid}) RETURN p.id AS id", {"pid": pid}):
        raise ValueError("person not found")
    kind = "intent" if _INTENT_RE.search(text) else "fact"
    sid, fid, now = new_id(), new_id(), now_ms()
    run(
        g,
        "MATCH (p:Person {id:$pid}) "
        "CREATE (s:Source {id:$sid, kind:'note', title:'Your note', text:$text, created_at:$now}) "
        "CREATE (s)-[:REPORTED]->(f:Fact {id:$fid, kind:$kind, text:$text, created_at:$now})-[:ABOUT]->(p)",
        {"pid": pid, "sid": sid, "fid": fid, "kind": kind, "text": text, "now": now},
    )
    mark_stale(g, [pid])
    return {"id": fid, "kind": kind, "text": text}


def delete_note(g: Graph, pid: str, fid: str) -> None:
    run(
        g,
        "MATCH (s:Source)-[:REPORTED]->(f:Fact {id:$fid})-[:ABOUT]->(:Person {id:$pid}) "
        "WITH s, f, s.kind = 'note' AS own DETACH DELETE f "
        "WITH s, own WHERE own DETACH DELETE s",
        {"fid": fid, "pid": pid},
    )
    mark_stale(g, [pid])
