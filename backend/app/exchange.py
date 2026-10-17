"""Ask the network: the cross-user Exchange.

    1. PostRequest    A turns a gap into a redacted request (a need, no names) sent to A's friends.
    2. Answer         Each friend's agent scores *the friend's own graph* and shares only a count
                      and the strongest tie strength. Matching names stay in the friend's graph.
    3. AskForIntro    A picks a friend who has matches and asks for an intro.
    4. Approve        The friend privately picks one contact. A Card is created with an opt-in link,
                      and it is still empty: no name crosses accounts.
    5. OptIn          The contact opens the link, writes their own card and says yes (or no).
    6. Reveal         A sees the card the contact wrote, plus a three-way intro draft.

Boundary, enforced here: `answer_as(store, user_id, ...)` is the only function that runs on
behalf of someone other than the caller, and it opens exactly one user graph, `user_id`'s.
It writes nothing to the shared graph except count and strength. The shared graph never holds a
person from anyone's network until that person writes their own card.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from .auth import Viewer, viewer
from .config import get_settings
from .db import Store, get_store, one, rows, run
from .goals import score_against
from .llm import GoalPlan, Need, expand_keywords
from .network import create_person
from .users import friend_ids, user_by_id
from .util import new_id, new_token, now_ms

log = logging.getLogger("connections.exchange")


class ExchangeError(ValueError):
    pass


# --------------------------------------------------------------------------
# Protocol


def post_request(
    store: Store, uid: str, *, need: str, description: str, keywords: list[str], note: str, goal_id: str | None, gap_id: str | None
) -> dict:
    need = need.strip()
    if not need:
        raise ExchangeError("say what kind of person you're looking for")
    friends = friend_ids(store, uid)
    if not friends:
        raise ExchangeError("add a friend first, their agent is who you ask")
    me = user_by_id(store, uid)
    rid = new_id()
    shared = store.shared()
    run(
        shared,
        "MATCH (u:User {id:$uid}) CREATE (u)-[:POSTED]->(:Request {id:$id, from_id:$uid, from_name:$name, need:$need, "
        "description:$desc, keywords:$kw, note:$note, status:'open', created_at:$now})",
        {
            "uid": uid,
            "id": rid,
            "name": me["name"],
            "need": need,
            "desc": description.strip(),
            "kw": [k.lower() for k in keywords] or expand_keywords(need + " " + description),
            "note": note.strip(),
            "now": now_ms(),
        },
    )
    run(
        shared,
        "MATCH (r:Request {id:$rid}) UNWIND $friends AS fid MATCH (f:User {id:fid}) CREATE (r)-[:ASKED]->(f)",
        {"rid": rid, "friends": friends},
    )
    if gap_id:
        run(store.user(uid), "MATCH (gap:Gap {id:$gid}) SET gap.request_id = $rid", {"gid": gap_id, "rid": rid})
    return {"id": rid, "asked": len(friends)}


def _request(store: Store, rid: str) -> dict:
    found = one(store.shared(), "MATCH (r:Request {id:$id}) RETURN r AS r", {"id": rid})
    if not found:
        raise ExchangeError("request not found")
    return found["r"]


def answer_as(store: Store, user_id: str, rid: str) -> dict:
    """Run `user_id`'s agent on `user_id`'s own graph for one incoming request."""
    shared = store.shared()
    asked = one(shared, "MATCH (r:Request {id:$rid})-[:ASKED]->(u:User {id:$uid}) RETURN r AS r", {"rid": rid, "uid": user_id})
    if not asked:
        raise ExchangeError("that request wasn't sent to you")
    req = asked["r"]
    existing = one(shared, "MATCH (rep:Reply {request_id:$rid, from_id:$uid}) RETURN rep.id AS id", {"rid": rid, "uid": user_id})

    own = store.user(user_id)  # the only graph this function opens
    plan = GoalPlan(
        summary=req["need"],
        needs=[Need(label=req["need"], description=req.get("description") or req["need"], keywords=req.get("keywords") or [])],
    )
    _, _, ordered, _ = score_against(own, plan, f"{req['need']}. {req.get('description') or ''}", direct_only=True)

    # Private: keep the matches in the answering user's own graph.
    run(own, "MATCH (i:Incoming {request_id:$rid}) DETACH DELETE i", {"rid": rid})
    run(
        own,
        "CREATE (:Incoming {request_id:$rid, from_name:$from_name, need:$need, created_at:$now})",
        {"rid": rid, "from_name": req["from_name"], "need": req["need"], "now": now_ms()},
    )
    for pid, r in ordered[:10]:
        run(
            own,
            "MATCH (p:Person {id:$pid}), (i:Incoming {request_id:$rid}) "
            "CREATE (p)-[:MATCHES {score:$score, reason:$reason, evidence:$ev}]->(i)",
            {"pid": pid, "rid": rid, "score": r["score"], "reason": r["reason"], "ev": r["evidence"]},
        )
    strengths = [
        one(own, "MATCH (:Me)-[k:KNOWS]->(:Person {id:$pid}) RETURN k.strength AS s", {"pid": pid}) or {}
        for pid, _ in ordered[:10]
    ]
    strength = "close" if any(s.get("s") == "close" for s in strengths) else ("known" if ordered else "none")

    # Public: only a count and a strength.
    count = len(ordered[:10])
    if existing:
        run(
            shared,
            "MATCH (rep:Reply {id:$id}) WHERE rep.status IN ['answered', 'no_match'] "
            "SET rep.count = $count, rep.strength = $strength, rep.status = $status, rep.answered_at = $now",
            {"id": existing["id"], "count": count, "strength": strength, "status": "answered" if count else "no_match", "now": now_ms()},
        )
        return {"id": existing["id"], "count": count, "strength": strength}
    me = user_by_id(store, user_id)
    rep_id = new_id()
    run(
        shared,
        "MATCH (r:Request {id:$rid}), (u:User {id:$uid}) "
        "CREATE (u)-[:WROTE]->(rep:Reply {id:$id, request_id:$rid, from_id:$uid, from_name:$name, count:$count, "
        "strength:$strength, status:$status, answered_at:$now})-[:ANSWERS]->(r)",
        {
            "rid": rid,
            "uid": user_id,
            "id": rep_id,
            "name": me["name"],
            "count": count,
            "strength": strength,
            "status": "answered" if count else "no_match",
            "now": now_ms(),
        },
    )
    return {"id": rep_id, "count": count, "strength": strength}


def answer_all(store: Store, rid: str) -> None:
    """Background: wake each asked friend's agent. Each call stays inside that friend's graph."""
    for f in rows(store.shared(), "MATCH (:Request {id:$rid})-[:ASKED]->(u:User) RETURN u.id AS id", {"rid": rid}):
        try:
            answer_as(store, f["id"], rid)
        except Exception as exc:  # one friend failing shouldn't block the rest
            log.warning("agent for %s failed on %s: %s", f["id"], rid, exc)


def _reply(store: Store, rep_id: str) -> dict:
    found = one(store.shared(), "MATCH (rep:Reply {id:$id}) RETURN rep AS rep", {"id": rep_id})
    if not found:
        raise ExchangeError("reply not found")
    return found["rep"]


def ask_for_intro(store: Store, uid: str, rep_id: str) -> dict:
    rep = _reply(store, rep_id)
    req = _request(store, rep["request_id"])
    if req["from_id"] != uid:
        raise ExchangeError("not your request")
    if rep["status"] != "answered":
        raise ExchangeError(f"reply is {rep['status']}")
    run(store.shared(), "MATCH (rep:Reply {id:$id}) SET rep.status = 'intro_requested', rep.asked_at = $now", {"id": rep_id, "now": now_ms()})
    return {"status": "intro_requested"}


def approve(store: Store, uid: str, rep_id: str, person_id: str, note: str) -> dict:
    rep = _reply(store, rep_id)
    if rep["from_id"] != uid:
        raise ExchangeError("not your reply")
    if rep["status"] != "intro_requested":
        raise ExchangeError("they haven't asked for an intro yet")
    own = store.user(uid)
    match = one(
        own,
        "MATCH (p:Person {id:$pid})-[m:MATCHES]->(:Incoming {request_id:$rid}) RETURN p.name AS name, p.email AS email",
        {"pid": person_id, "rid": rep["request_id"]},
    )
    if not match:
        raise ExchangeError("pick one of your matches")
    req = _request(store, rep["request_id"])
    token = new_token()
    card_id = new_id()
    run(
        store.shared(),
        "MATCH (rep:Reply {id:$rep}) SET rep.status = 'approved', rep.approved_at = $now "
        "CREATE (rep)-[:OFFERS]->(:Card {id:$id, request_id:$rid, reply_id:$rep, optin_token:$token, status:'awaiting_optin', "
        "broker_id:$uid, broker_name:$broker, requester_id:$req_uid, requester_name:$req_name, need:$need, "
        "broker_note:$note, created_at:$now})",
        {
            "rep": rep_id,
            "id": card_id,
            "rid": rep["request_id"],
            "token": token,
            "uid": uid,
            "broker": rep["from_name"],
            "req_uid": req["from_id"],
            "req_name": req["from_name"],
            "need": req["need"],
            "note": note.strip(),
            "now": now_ms(),
        },
    )
    # Which contact was chosen is recorded only in the broker's graph.
    run(own, "MATCH (i:Incoming {request_id:$rid}) SET i.chosen_person_id = $pid, i.card_id = $cid",
        {"rid": rep["request_id"], "pid": person_id, "cid": card_id})
    link = f"{get_settings().public_url}/optin/{token}"
    first = match["name"].split(" ")[0]
    draft = (
        f"Hi {first}, {req['from_name']} is looking for {req['need']}"
        + (f" ({req['description']})" if req.get("description") else "")
        + ". I think you'd be a great person for them to talk to. "
        f"If you're open to it, write a short card here and I'll make the intro: {link}"
        + (f"\n\n{note.strip()}" if note.strip() else "")
    )
    return {"card_id": card_id, "optin_link": link, "optin_path": f"/optin/{token}", "message": draft, "contact_email": match.get("email")}


def decline(store: Store, uid: str, rep_id: str) -> dict:
    rep = _reply(store, rep_id)
    if rep["from_id"] != uid:
        raise ExchangeError("not your reply")
    run(store.shared(), "MATCH (rep:Reply {id:$id}) SET rep.status = 'declined'", {"id": rep_id})
    return {"status": "declined"}


def optin_view(store: Store, token: str) -> dict:
    card = one(store.shared(), "MATCH (c:Card {optin_token:$t}) RETURN c AS c", {"t": token})
    if not card:
        raise ExchangeError("this link isn't valid")
    c = card["c"]
    return {
        "status": c["status"],
        "requester_name": c["requester_name"],
        "broker_name": c["broker_name"],
        "need": c["need"],
        "broker_note": c.get("broker_note") or "",
    }


def optin_submit(store: Store, token: str, accept: bool, name: str, line: str, contact: str) -> dict:
    card = one(store.shared(), "MATCH (c:Card {optin_token:$t}) RETURN c AS c", {"t": token})
    if not card:
        raise ExchangeError("this link isn't valid")
    c = card["c"]
    if c["status"] != "awaiting_optin":
        raise ExchangeError("this card was already answered")
    if not accept:
        run(store.shared(), "MATCH (c:Card {optin_token:$t}) SET c.status = 'declined', c.answered_at = $now", {"t": token, "now": now_ms()})
        return {"status": "declined"}
    if not name.strip() or not contact.strip():
        raise ExchangeError("add your name and how to reach you")
    run(
        store.shared(),
        "MATCH (c:Card {optin_token:$t}) SET c.status = 'shared', c.name = $name, c.line = $line, c.contact = $contact, "
        "c.answered_at = $now",
        {"t": token, "name": name.strip()[:80], "line": line.strip()[:240], "contact": contact.strip()[:160], "now": now_ms()},
    )
    return {"status": "shared"}


def _intro_draft(card: dict) -> str:
    first = (card.get("name") or "").split(" ")[0]
    broker = (card.get("broker_name") or "").split(" ")[0]
    return (
        f"Hi {broker} and {first},\n\n"
        f"Thank you both. {first}, {broker} mentioned you might be up for a quick chat about {card['need']}. "
        f"I'd love 20 minutes whenever suits you this week or next.\n\n"
        f"{broker}, moving you to BCC. Thanks for the intro!"
    )


def outbox(store: Store, uid: str) -> list[dict]:
    reqs = rows(
        store.shared(),
        "MATCH (:User {id:$uid})-[:POSTED]->(r:Request) "
        "OPTIONAL MATCH (r)-[:ASKED]->(f:User) WITH r, count(f) AS asked "
        "RETURN r {.id, .need, .description, .note, .status, .created_at} AS request, asked ORDER BY r.created_at DESC",
        {"uid": uid},
    )
    for r in reqs:
        r["replies"] = rows(
            store.shared(),
            "MATCH (rep:Reply {request_id:$rid}) OPTIONAL MATCH (rep)-[:OFFERS]->(c:Card) "
            "RETURN rep {.id, .from_name, .count, .strength, .status, .answered_at} AS reply, "
            "c {.id, .status, .name, .line, .contact, .broker_name, .need, .answered_at, .saved} AS card "
            "ORDER BY rep.count DESC",
            {"rid": r["request"]["id"]},
        )
        for rep in r["replies"]:
            card = rep.get("card")
            if card and card.get("status") != "shared":
                rep["card"] = {"id": card["id"], "status": card["status"]}  # nothing to reveal yet
            elif card:
                card["intro_draft"] = _intro_draft(card)
    return reqs


def inbox(store: Store, uid: str, refresh: bool = True) -> list[dict]:
    shared = store.shared()
    reqs = rows(
        shared,
        "MATCH (r:Request)-[:ASKED]->(:User {id:$uid}) OPTIONAL MATCH (rep:Reply {request_id:r.id, from_id:$uid}) "
        "OPTIONAL MATCH (rep)-[:OFFERS]->(c:Card) "
        "RETURN r {.id, .from_name, .need, .description, .note, .status, .created_at} AS request, "
        "rep {.id, .count, .strength, .status} AS reply, c {.id, .status} AS card ORDER BY r.created_at DESC",
        {"uid": uid},
    )
    own = store.user(uid)
    for r in reqs:
        if refresh and not r.get("reply"):
            answer_as(store, uid, r["request"]["id"])
            r["reply"] = one(shared, "MATCH (rep:Reply {request_id:$rid, from_id:$uid}) RETURN rep {.id, .count, .strength, .status} AS reply",
                             {"rid": r["request"]["id"], "uid": uid})["reply"]
        # Names only from the viewer's own graph.
        r["matches"] = rows(
            own,
            "MATCH (p:Person)-[m:MATCHES]->(i:Incoming {request_id:$rid}) "
            "RETURN p {.id, .name, .headline} AS person, m.score AS score, m.reason AS reason, "
            "i.chosen_person_id = p.id AS chosen ORDER BY m.score DESC",
            {"rid": r["request"]["id"]},
        )
    return reqs


def save_card(store: Store, uid: str, card_id: str) -> dict:
    card = one(store.shared(), "MATCH (c:Card {id:$id}) RETURN c AS c", {"id": card_id})
    if not card or card["c"]["requester_id"] != uid:
        raise ExchangeError("card not found")
    c = card["c"]
    if c["status"] != "shared":
        raise ExchangeError("they haven't shared a card")
    g = store.user(uid)
    sid = new_id()
    run(
        g,
        "CREATE (:Source {id:$id, kind:'card', title:$title, created_at:$now})",
        {"id": sid, "title": f"Card from intro via {c['broker_name']}", "now": now_ms()},
    )
    email = c["contact"] if "@" in c["contact"] else ""
    url = c["contact"] if "linkedin.com" in c["contact"].lower() else ""
    pid = create_person(g, name=c["name"], position=c.get("line") or "", email=email, linkedin_url=url)
    run(g, "MATCH (s:Source {id:$sid}), (p:Person {id:$pid}) CREATE (s)-[:IMPORTED]->(p)", {"sid": sid, "pid": pid})
    run(
        g,
        "MATCH (s:Source {id:$sid}), (p:Person {id:$pid}) "
        "CREATE (s)-[:REPORTED]->(:Fact {id:$fid, kind:'fact', text:$text, owner:'', due:'', done:false, created_at:$now})-[:ABOUT]->(p)",
        {"sid": sid, "pid": pid, "fid": new_id(), "text": f"{c['name']} was introduced by {c['broker_name']} for {c['need']}. Card: {c.get('line') or ''}", "now": now_ms()},
    )
    run(store.shared(), "MATCH (c:Card {id:$id}) SET c.saved = true", {"id": card_id})
    return {"person_id": pid}


# --------------------------------------------------------------------------
# Routes

router = APIRouter()


class RequestIn(BaseModel):
    need: str
    description: str = ""
    keywords: list[str] = []
    note: str = ""
    goal_id: str | None = None
    gap_id: str | None = None


class ApproveIn(BaseModel):
    person_id: str
    note: str = ""


class OptInIn(BaseModel):
    accept: bool
    name: str = ""
    line: str = ""
    contact: str = ""


@router.post("/api/exchange/requests")
def http_post_request(body: RequestIn, background: BackgroundTasks, v: Viewer = Depends(viewer)):
    store = get_store()
    out = post_request(
        store, v.id, need=body.need, description=body.description, keywords=body.keywords,
        note=body.note, goal_id=body.goal_id, gap_id=body.gap_id,
    )
    background.add_task(answer_all, store, out["id"])
    return out


@router.get("/api/exchange/outbox")
def http_outbox(v: Viewer = Depends(viewer)):
    return {"requests": outbox(get_store(), v.id)}


@router.get("/api/exchange/inbox")
def http_inbox(v: Viewer = Depends(viewer)):
    return {"requests": inbox(get_store(), v.id)}


@router.post("/api/exchange/replies/{rep_id}/ask")
def http_ask(rep_id: str, v: Viewer = Depends(viewer)):
    return ask_for_intro(get_store(), v.id, rep_id)


@router.post("/api/exchange/replies/{rep_id}/approve")
def http_approve(rep_id: str, body: ApproveIn, v: Viewer = Depends(viewer)):
    return approve(get_store(), v.id, rep_id, body.person_id, body.note)


@router.post("/api/exchange/replies/{rep_id}/decline")
def http_decline(rep_id: str, v: Viewer = Depends(viewer)):
    return decline(get_store(), v.id, rep_id)


@router.post("/api/exchange/cards/{card_id}/save")
def http_save_card(card_id: str, v: Viewer = Depends(viewer)):
    return save_card(get_store(), v.id, card_id)


@router.get("/api/optin/{token}")
def http_optin_view(token: str):
    try:
        return optin_view(get_store(), token)
    except ExchangeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/optin/{token}")
def http_optin_submit(token: str, body: OptInIn):
    return optin_submit(get_store(), token, body.accept, body.name, body.line, body.contact)
