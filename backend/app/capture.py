"""Capture: a short memo after meeting someone becomes people, facts,
intents, promises and ties in the user's graph. Everything written points back
at the (:Source {kind:'memo'}) node, so deleting the memo removes what it said.
"""

from __future__ import annotations

import re

from falkordb import Graph

from .db import one, run
from .llm import get_model
from .network import connect_me, create_person, link_org, mark_stale, people_index
from .util import headline, name_key, new_id, now_ms


def _known_for(text: str, index: list[dict]) -> list[dict]:
    """People already in the graph whose name (or first name) appears in the memo."""
    low = text.lower()
    tokens = set(re.findall(r"[a-z]+", low))
    out = []
    for p in index:
        key = p.get("name_key") or ""
        first = key.split(" ")[0] if key else ""
        if key and (key in low or first in tokens):
            out.append({"id": p["id"], "name": p["name"], "company": p.get("company") or ""})
    return out[:150]


def capture(g: Graph, text: str, title: str | None = None) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("memo is empty")
    model = get_model()
    index = people_index(g)
    by_id = {p["id"]: p for p in index}
    by_key: dict[str, list[dict]] = {}
    for p in index:
        by_key.setdefault(p["name_key"], []).append(p)

    ex = model.extract_memo(text, _known_for(text, index))

    sid = new_id()
    now = now_ms()
    run(
        g,
        "CREATE (:Source {id:$id, kind:'memo', title:$title, text:$text, created_at:$now})",
        {"id": sid, "title": title or ex.title, "text": text, "now": now},
    )

    ref_to_pid: dict[str, str] = {}
    people_out = []
    for person in ex.people:
        name = person.name.strip()
        if not name:
            continue
        pid = None
        if person.existing_id and person.existing_id in by_id:
            pid = person.existing_id
        elif len(by_key.get(name_key(name), [])) == 1:
            pid = by_key[name_key(name)][0]["id"]
        created = pid is None
        if created:
            pid = create_person(g, name=name, company=person.company, position=person.position)
            by_key.setdefault(name_key(name), []).append({"id": pid, "name": name})
        else:
            current = one(g, "MATCH (p:Person {id:$id}) RETURN p.company AS company, p.position AS position", {"id": pid})
            fill = {}
            if person.company and not (current or {}).get("company"):
                fill["company"] = person.company
            if person.position and not (current or {}).get("position"):
                fill["position"] = person.position
            if fill:
                company = fill.get("company", (current or {}).get("company"))
                position = fill.get("position", (current or {}).get("position"))
                run(
                    g,
                    "MATCH (p:Person {id:$id}) SET p.company = $c, p.position = $pos, p.headline = $h, p.updated_at = $now",
                    {"id": pid, "c": company or "", "pos": position or "", "h": headline(position, company), "now": now},
                )
                if "company" in fill:
                    link_org(g, pid, company, position)
        run(g, "MATCH (s:Source {id:$sid}), (p:Person {id:$pid}) MERGE (s)-[:MENTIONS]->(p)", {"sid": sid, "pid": pid})
        if person.met:
            connect_me(g, pid, via="memo")
        ref_to_pid[person.ref] = pid
        people_out.append({"id": pid, "name": name, "created": created, "met": person.met})

    facts_out = []
    for fact in ex.facts:
        about = list(dict.fromkeys(ref_to_pid[r] for r in fact.about if r in ref_to_pid))
        if not about or not fact.text.strip():
            continue
        fid = new_id()
        run(
            g,
            "MATCH (s:Source {id:$sid}) "
            "CREATE (s)-[:REPORTED]->(f:Fact {id:$id, kind:$kind, text:$text, owner:$owner, due:$due, "
            "done:false, created_at:$now}) "
            "WITH f UNWIND $about AS pid MATCH (p:Person {id:pid}) CREATE (f)-[:ABOUT]->(p)",
            {
                "sid": sid,
                "id": fid,
                "kind": fact.kind,
                "text": fact.text.strip(),
                "owner": fact.owner or "",
                "due": fact.due or "",
                "about": about,
                "now": now,
            },
        )
        facts_out.append({"id": fid, "kind": fact.kind, "text": fact.text, "about": about, "due": fact.due})

    ties_out = []
    for tie in ex.ties:
        a, b = ref_to_pid.get(tie.a), ref_to_pid.get(tie.b)
        if not a or not b or a == b:
            continue
        run(
            g,
            "MATCH (a:Person {id:$a}), (b:Person {id:$b}) "
            "MERGE (a)-[t:KNOWS]->(b) SET t.relation = $rel, t.source_id = $sid",
            {"a": a, "b": b, "rel": tie.relation, "sid": sid},
        )
        ties_out.append({"a": a, "b": b, "relation": tie.relation})

    mark_stale(g, list(ref_to_pid.values()))
    return {
        "source": {"id": sid, "title": title or ex.title, "created_at": now},
        "people": people_out,
        "facts": facts_out,
        "ties": ties_out,
        "model": model.name,
    }
