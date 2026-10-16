"""LinkedIn import with swipe-to-confirm.

Uploading an export never adds people to your network. Each row becomes a
pending (:Candidate) and nothing else. A person is created only when the user
swipes:

    right  keep           -> (:Person) + (Me)-[:KNOWS {strength:'known', via:['linkedin']}]
    up     keep as close  -> same, strength 'close'
    left   skip           -> candidate marked skipped, nothing created

If a row looks like someone already in the graph (same LinkedIn URL, or same
name as a person from a memo), the card says so and keeping it merges into
that person instead of creating a duplicate. Every kept person gets an
(:Source {kind:'linkedin'})-[:IMPORTED]->(:Person) edge, which is the receipt.

Re-importing a newer export only shows the connections you haven't triaged.
"""

from __future__ import annotations

from collections import Counter

from falkordb import Graph

from .db import one, rows, run
from .linkedin import ParsedExport
from .llm import keyword_hits
from .network import connect_me, create_person, link_org, mark_stale, people_index
from .util import headline, new_id, now_ms, org_key

CARD_FIELDS = (
    "c {.id, .row, .name, .first_name, .last_name, .company, .position, .headline, .url, .email, "
    ".connected_on, .status, .match_person_id, .match_reason, .person_id, .strength, .decided_at}"
)
DECIDED = ["kept", "merged", "skipped"]


class ImportError_(ValueError):
    pass


def create_import(g: Graph, parsed: ParsedExport) -> dict:
    existing = people_index(g)
    by_url = {p["linkedin_url"]: p for p in existing if p.get("linkedin_url")}
    by_key: dict[str, list[dict]] = {}
    for p in existing:
        by_key.setdefault(p["name_key"], []).append(p)

    # Candidates already triaged or pending in earlier imports.
    earlier = rows(
        g,
        "MATCH (c:Candidate) RETURN c.id AS id, c.url AS url, c.name_key AS name_key, c.status AS status",
    )
    earlier_by = {}
    for c in earlier:
        earlier_by[c["url"] or c["name_key"]] = c

    sid = new_id()
    now = now_ms()
    payload = []
    known = 0
    superseded: list[str] = []
    for order, row in enumerate(parsed.rows):
        status = "pending"
        match_id = None
        match_reason = None
        prior = earlier_by.get(row.url or row.name_key)
        if row.url and row.url in by_url:
            person = by_url[row.url]
            if "linkedin" in (person.get("via") or []):
                status = "known"
                known += 1
            else:
                match_id, match_reason = person["id"], "Same LinkedIn profile"
        elif prior and prior["status"] == "skipped":
            # They skipped this person in an earlier import; don't ask again.
            status = "known"
            known += 1
        elif len(by_key.get(row.name_key, [])) == 1:
            person = by_key[row.name_key][0]
            match_id = person["id"]
            match_reason = "Same name as someone already in your graph"
        if prior and prior["status"] == "pending":
            superseded.append(prior["id"])
        payload.append(
            {
                "id": new_id(),
                "row": row.row,
                "order": order,
                "name": row.name,
                "first_name": row.first_name,
                "last_name": row.last_name,
                "name_key": row.name_key,
                "url": row.url,
                "email": row.email,
                "company": row.company,
                "position": row.position,
                "headline": headline(row.position, row.company),
                "connected_on": row.connected_on or "",
                "status": status,
                "match_person_id": match_id or "",
                "match_reason": match_reason or "",
            }
        )

    run(
        g,
        "CREATE (:Source {id:$id, kind:'linkedin', title:'LinkedIn export', filename:$filename, "
        "created_at:$now, total:$total, duplicates_in_file:$dups, skipped_blank:$blank})",
        {
            "id": sid,
            "filename": parsed.filename,
            "now": now,
            "total": len(parsed.rows),
            "dups": parsed.duplicates_in_file,
            "blank": parsed.skipped_blank,
        },
    )
    for start in range(0, len(payload), 500):
        run(
            g,
            "UNWIND $rows AS r CREATE (c:Candidate) SET c = r, c.import_id = $sid, c.created_at = $now",
            {"rows": payload[start : start + 500], "sid": sid, "now": now},
        )
    if superseded:
        run(g, "UNWIND $ids AS id MATCH (c:Candidate {id:id}) SET c.status = 'superseded'", {"ids": superseded})
    return {"import": import_summary(g, sid), "already_known": known}


def import_summary(g: Graph, sid: str) -> dict | None:
    src = one(
        g,
        "MATCH (s:Source {id:$id, kind:'linkedin'}) RETURN s {.id, .filename, .created_at, .total} AS source",
        {"id": sid},
    )
    if not src:
        return None
    counts = rows(
        g, "MATCH (c:Candidate {import_id:$id}) RETURN c.status AS status, count(c) AS n", {"id": sid}
    )
    tally = {c["status"]: c["n"] for c in counts}
    close = one(
        g,
        "MATCH (c:Candidate {import_id:$id}) WHERE c.status IN ['kept', 'merged'] AND c.strength = 'close' "
        "RETURN count(c) AS n",
        {"id": sid},
    )
    return {
        "close": (close or {}).get("n", 0),
        **src["source"],
        "pending": tally.get("pending", 0),
        "kept": tally.get("kept", 0),
        "merged": tally.get("merged", 0),
        "skipped": tally.get("skipped", 0),
        "known": tally.get("known", 0) + tally.get("superseded", 0),
    }


def list_imports(g: Graph) -> list[dict]:
    ids = rows(g, "MATCH (s:Source {kind:'linkedin'}) RETURN s.id AS id ORDER BY s.created_at DESC")
    return [import_summary(g, i["id"]) for i in ids]


# --------------------------------------------------------------------------
# Deck


def _open_goals(g: Graph) -> list[dict]:
    return rows(g, "MATCH (goal:Goal {status:'open'}) RETURN goal.id AS id, goal.text AS text, goal.keywords AS keywords")


def deck(g: Graph, sid: str, limit: int = 12) -> dict:
    """Pending cards, ordered so the ones that matter for your open goals come first."""
    summary = import_summary(g, sid)
    if summary is None:
        raise ImportError_("import not found")
    pending = rows(
        g,
        f"MATCH (c:Candidate {{import_id:$id, status:'pending'}}) RETURN {CARD_FIELDS} AS card, c.order AS ord",
        {"id": sid},
    )
    goals = _open_goals(g)

    # How many people you've already kept at each organisation.
    org_counts: Counter[str] = Counter()
    for p in people_index(g):
        if p.get("company") and p.get("via"):
            org_counts[org_key(p["company"])] += 1

    cards = []
    for item in pending:
        card = item["card"]
        text = " ".join([card.get("headline") or "", card.get("company") or ""])
        relevance = []
        for goal in goals:
            hits = keyword_hits(goal.get("keywords") or [], text)
            if hits:
                relevance.append({"goal_id": goal["id"], "goal": goal["text"], "hits": hits[:4]})
        score = max((len(r["hits"]) for r in relevance), default=0)
        context = []
        same_org = org_counts.get(org_key(card.get("company") or ""), 0) if card.get("company") else 0
        if same_org:
            context.append(f"You already know {same_org} other{'s' if same_org > 1 else ''} at {card['company']}")
        card["relevance"] = relevance
        card["context"] = context
        cards.append((-score, 0 if card.get("match_person_id") else 1, _neg_date(card.get("connected_on")), item["ord"], card))

    cards.sort(key=lambda t: t[:4])
    top = [c[-1] for c in cards[:limit]]
    for card in top:
        if card.get("match_person_id"):
            card["match"] = one(
                g,
                "MATCH (p:Person {id:$id}) OPTIONAL MATCH (s:Source)-[:MENTIONS|IMPORTED]->(p) "
                "RETURN p.id AS id, p.name AS name, p.headline AS headline, collect(s.title)[0] AS seen_in",
                {"id": card["match_person_id"]},
            )
    return {"import": summary, "cards": top, "goals": [{"id": g_["id"], "text": g_["text"]} for g_ in goals]}


def _neg_date(iso: str | None) -> str:
    # Sort newest connections first without parsing: invert each digit.
    if not iso:
        return "z"
    return "".join(chr(ord("9") - ord(ch) + ord("0")) if ch.isdigit() else ch for ch in iso)


# --------------------------------------------------------------------------
# Decisions


def _candidate(g: Graph, sid: str, cid: str) -> dict:
    found = one(g, "MATCH (c:Candidate {id:$cid, import_id:$sid}) RETURN c AS c", {"cid": cid, "sid": sid})
    if not found:
        raise ImportError_("card not found")
    return found["c"]


def decide(g: Graph, sid: str, cid: str, action: str) -> dict:
    if action not in ("keep", "close", "skip"):
        raise ImportError_("action must be keep, close or skip")
    c = _candidate(g, sid, cid)
    if c["status"] != "pending":
        raise ImportError_(f"card already {c['status']}")
    now = now_ms()

    if action == "skip":
        run(g, "MATCH (c:Candidate {id:$id}) SET c.status = 'skipped', c.decided_at = $now", {"id": cid, "now": now})
        return {"status": "skipped", "import": import_summary(g, sid)}

    strength = "close" if action == "close" else "known"
    person = None
    if c.get("match_person_id"):
        person = one(g, "MATCH (p:Person {id:$id}) RETURN p AS p", {"id": c["match_person_id"]})
    prev = None
    if person:
        p = person["p"]
        pid = p["id"]
        filled = [f for f in ("company", "position", "email") if not p.get(f) and c.get(f)]
        if not p.get("linkedin_url") and c.get("url"):
            filled.append("linkedin_url")
        updates = {f: (c["url"] if f == "linkedin_url" else c[f]) for f in filled}
        if updates:
            sets = ", ".join(f"p.{f} = $u_{f}" for f in updates)
            params = {"id": pid, "now": now, **{f"u_{f}": v for f, v in updates.items()}}
            run(g, f"MATCH (p:Person {{id:$id}}) SET {sets}, p.updated_at = $now", params)
            new_company = updates.get("company", p.get("company"))
            new_position = updates.get("position", p.get("position"))
            run(g, "MATCH (p:Person {id:$id}) SET p.headline = $h", {"id": pid, "h": headline(new_position, new_company)})
            if "company" in updates:
                link_org(g, pid, updates["company"], new_position)
        prev = one(g, "MATCH (:Me)-[k:KNOWS]->(:Person {id:$id}) RETURN k.strength AS strength, k.via AS via", {"id": pid})
        created, status = False, "merged"
    else:
        pid = create_person(
            g,
            name=c["name"],
            first_name=c.get("first_name"),
            last_name=c.get("last_name"),
            company=c.get("company"),
            position=c.get("position"),
            linkedin_url=c.get("url"),
            email=c.get("email"),
        )
        filled, created, status = [], True, "kept"

    knows_created = connect_me(g, pid, via="linkedin", strength=strength, since=c.get("connected_on") or None)
    run(
        g,
        "MATCH (s:Source {id:$sid}), (p:Person {id:$pid}) CREATE (s)-[:IMPORTED {candidate_id:$cid}]->(p)",
        {"sid": sid, "pid": pid, "cid": cid},
    )
    mark_stale(g, [pid])
    run(
        g,
        "MATCH (c:Candidate {id:$cid}) SET c.status = $status, c.decided_at = $now, c.person_id = $pid, "
        "c.strength = $strength, c.created_person = $created, c.knows_created = $kc, c.filled = $filled, "
        "c.prev_strength = $ps, c.prev_via = $pv",
        {
            "cid": cid,
            "status": status,
            "now": now,
            "pid": pid,
            "strength": strength,
            "created": created,
            "kc": knows_created,
            "filled": filled,
            "ps": (prev or {}).get("strength") or "",
            "pv": (prev or {}).get("via") or [],
        },
    )
    return {"status": status, "person_id": pid, "import": import_summary(g, sid)}


def undo(g: Graph, sid: str) -> dict:
    last = one(
        g,
        "MATCH (c:Candidate {import_id:$sid}) WHERE c.status IN $decided "
        "RETURN c AS c ORDER BY c.decided_at DESC LIMIT 1",
        {"sid": sid, "decided": DECIDED},
    )
    if not last:
        raise ImportError_("nothing to undo")
    c = last["c"]
    cid = c["id"]
    if c["status"] in ("kept", "merged"):
        pid = c["person_id"]
        run(
            g,
            "MATCH (:Source {id:$sid})-[r:IMPORTED {candidate_id:$cid}]->(:Person) DELETE r",
            {"sid": sid, "cid": cid},
        )
        other = one(
            g,
            "MATCH (p:Person {id:$pid}) RETURN size([(s:Source)-[:IMPORTED|MENTIONS]->(p) | s]) AS n",
            {"pid": pid},
        )
        if c.get("created_person") and other and other["n"] == 0:
            run(g, "MATCH (p:Person {id:$pid}) DETACH DELETE p", {"pid": pid})
            run(g, "MATCH (o:Org) WHERE NOT (o)<-[:WORKS_AT]-() DELETE o")
        else:
            for f in c.get("filled") or []:
                run(g, f"MATCH (p:Person {{id:$pid}}) SET p.{f} = ''", {"pid": pid})
            if c.get("filled"):
                p = one(g, "MATCH (p:Person {id:$pid}) RETURN p.position AS position, p.company AS company", {"pid": pid})
                run(g, "MATCH (p:Person {id:$pid}) SET p.headline = $h", {"pid": pid, "h": headline(p["position"], p["company"])})
            if c.get("knows_created"):
                k = one(g, "MATCH (:Me)-[k:KNOWS]->(:Person {id:$pid}) RETURN k.via AS via", {"pid": pid})
                via = [v for v in (k or {}).get("via") or [] if v != "linkedin"]
                if via:
                    run(g, "MATCH (:Me)-[k:KNOWS]->(:Person {id:$pid}) SET k.via = $via", {"pid": pid, "via": via})
                else:
                    run(g, "MATCH (:Me)-[k:KNOWS]->(:Person {id:$pid}) DELETE k", {"pid": pid})
            else:
                run(
                    g,
                    "MATCH (:Me)-[k:KNOWS]->(:Person {id:$pid}) SET k.strength = $s, k.via = $via",
                    {"pid": pid, "s": c.get("prev_strength") or "known", "via": c.get("prev_via") or []},
                )
            mark_stale(g, [pid])
    run(
        g,
        "MATCH (c:Candidate {id:$cid}) SET c.status = 'pending', c.decided_at = NULL, c.person_id = NULL, "
        "c.strength = NULL, c.created_person = NULL, c.knows_created = NULL, c.filled = NULL, "
        "c.prev_strength = NULL, c.prev_via = NULL",
        {"cid": cid},
    )
    card = one(g, f"MATCH (c:Candidate {{id:$cid}}) RETURN {CARD_FIELDS} AS card", {"cid": cid})["card"]
    card["relevance"], card["context"] = [], []
    return {"card": card, "undone": c["status"], "import": import_summary(g, sid)}


def bulk(g: Graph, sid: str, action: str) -> dict:
    """Keep or skip every card still pending in this import."""
    if action not in ("keep", "skip"):
        raise ImportError_("bulk action must be keep or skip")
    pending = rows(g, "MATCH (c:Candidate {import_id:$sid, status:'pending'}) RETURN c.id AS id", {"sid": sid})
    if action == "skip":
        run(
            g,
            "MATCH (c:Candidate {import_id:$sid, status:'pending'}) SET c.status = 'skipped', c.decided_at = $now",
            {"sid": sid, "now": now_ms()},
        )
    else:
        for p in pending:
            decide(g, sid, p["id"], "keep")
    return {"changed": len(pending), "import": import_summary(g, sid)}
