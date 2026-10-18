"""Accounts and friendships, kept in the shared graph.

This is hackathon-grade identity: creating an account returns a random bearer
token that the browser keeps. There are no passwords. Friendships need both
sides: one person sends a request with the other's handle, the other accepts.
"""

from __future__ import annotations

import re

from .db import Store, one, rows, run
from .network import ensure_me
from .util import new_id, new_token, now_ms


class UserError(ValueError):
    pass


def _handle_base(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return base[:24] or "user"


def create_user(store: Store, name: str) -> dict:
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        raise UserError("name is required")
    shared = store.shared()
    base = _handle_base(name)
    handle = base
    n = 1
    while one(shared, "MATCH (u:User {handle:$h}) RETURN u.id AS id", {"h": handle}):
        n += 1
        handle = f"{base}-{n}"
    uid = new_id()
    token = new_token()
    run(
        shared,
        "CREATE (:User {id:$id, name:$name, handle:$handle, token:$token, plan:'free', discoverable:true, created_at:$now})",
        {"id": uid, "name": name, "handle": handle, "token": token, "now": now_ms()},
    )
    ensure_me(store.user(uid), name)
    return {"user": {"id": uid, "name": name, "handle": handle}, "token": token}


def user_by_token(store: Store, token: str) -> dict | None:
    if not token:
        return None
    return one(
        store.shared(),
        "MATCH (u:User {token:$t}) RETURN u.id AS id, u.name AS name, u.handle AS handle",
        {"t": token},
    )


def user_by_id(store: Store, uid: str) -> dict | None:
    return one(store.shared(), "MATCH (u:User {id:$id}) RETURN u.id AS id, u.name AS name, u.handle AS handle", {"id": uid})


def friends(store: Store, uid: str) -> dict:
    shared = store.shared()
    accepted = rows(
        shared,
        "MATCH (:User {id:$id})-[:FRIEND]->(f:User) RETURN f.id AS id, f.name AS name, f.handle AS handle ORDER BY f.name",
        {"id": uid},
    )
    incoming = rows(
        shared,
        "MATCH (f:User)-[:FRIEND_REQUEST]->(:User {id:$id}) RETURN f.id AS id, f.name AS name, f.handle AS handle",
        {"id": uid},
    )
    outgoing = rows(
        shared,
        "MATCH (:User {id:$id})-[:FRIEND_REQUEST]->(f:User) RETURN f.id AS id, f.name AS name, f.handle AS handle",
        {"id": uid},
    )
    return {"friends": accepted, "incoming": incoming, "outgoing": outgoing}


def request_friend(store: Store, uid: str, handle: str) -> dict:
    shared = store.shared()
    other = one(shared, "MATCH (u:User {handle:$h}) RETURN u.id AS id, u.name AS name", {"h": (handle or "").strip().lower()})
    if not other:
        raise UserError("no one with that handle")
    if other["id"] == uid:
        raise UserError("that's you")
    if one(shared, "MATCH (:User {id:$a})-[:FRIEND]->(:User {id:$b}) RETURN 1 AS x", {"a": uid, "b": other["id"]}):
        return {"status": "already friends"}
    # If they already asked us, this accepts.
    if one(shared, "MATCH (:User {id:$b})-[:FRIEND_REQUEST]->(:User {id:$a}) RETURN 1 AS x", {"a": uid, "b": other["id"]}):
        return accept_friend(store, uid, other["id"])
    run(
        shared,
        "MATCH (a:User {id:$a}), (b:User {id:$b}) MERGE (a)-[:FRIEND_REQUEST {at:$now}]->(b)",
        {"a": uid, "b": other["id"], "now": now_ms()},
    )
    return {"status": "requested", "to": other["name"]}


def accept_friend(store: Store, uid: str, other_id: str) -> dict:
    shared = store.shared()
    req = one(shared, "MATCH (:User {id:$b})-[r:FRIEND_REQUEST]->(:User {id:$a}) RETURN 1 AS x", {"a": uid, "b": other_id})
    if not req:
        raise UserError("no request from them")
    run(
        shared,
        "MATCH (b:User {id:$b})-[r:FRIEND_REQUEST]->(a:User {id:$a}) DELETE r "
        "WITH a, b MERGE (a)-[:FRIEND {since:$now}]->(b) MERGE (b)-[:FRIEND {since:$now}]->(a)",
        {"a": uid, "b": other_id, "now": now_ms()},
    )
    return {"status": "friends"}


def are_friends(store: Store, a: str, b: str) -> bool:
    return bool(one(store.shared(), "MATCH (:User {id:$a})-[:FRIEND]->(:User {id:$b}) RETURN 1 AS x", {"a": a, "b": b}))


def friend_ids(store: Store, uid: str) -> list[str]:
    return [r["id"] for r in rows(store.shared(), "MATCH (:User {id:$id})-[:FRIEND]->(f:User) RETURN f.id AS id", {"id": uid})]


# --------------------------------------------------------------------------
# Plan and search settings

PLANS = ("free", "pro")


def settings_of(store: Store, uid: str) -> dict:
    found = one(store.shared(), "MATCH (u:User {id:$id}) RETURN u.plan AS plan, u.discoverable AS discoverable", {"id": uid}) or {}
    return {
        "plan": found.get("plan") if found.get("plan") in PLANS else "free",
        # Accounts made before this setting existed are searchable, the same as new ones.
        "discoverable": found.get("discoverable") is not False,
    }


def update_settings(store: Store, uid: str, *, plan: str | None = None, discoverable: bool | None = None) -> dict:
    if plan is not None:
        if plan not in PLANS:
            raise UserError("unknown plan")
        run(store.shared(), "MATCH (u:User {id:$id}) SET u.plan = $plan", {"id": uid, "plan": plan})
    if discoverable is not None:
        run(store.shared(), "MATCH (u:User {id:$id}) SET u.discoverable = $d", {"id": uid, "d": bool(discoverable)})
    return settings_of(store, uid)


def reach(store: Store, uid: str) -> dict:
    """Who a search can look through: friends (2nd degree) and their friends (3rd degree).

    Only accounts that leave "friends can find my contacts" on are included.
    """
    shared = store.shared()
    friends_ = rows(
        shared,
        "MATCH (:User {id:$id})-[:FRIEND]->(f:User) WHERE coalesce(f.discoverable, true) "
        "RETURN f.id AS id, f.name AS name, f.handle AS handle ORDER BY f.name",
        {"id": uid},
    )
    fof = rows(
        shared,
        "MATCH (me:User {id:$id})-[:FRIEND]->(f:User)-[:FRIEND]->(ff:User) "
        "WHERE ff.id <> $id AND coalesce(ff.discoverable, true) "
        "OPTIONAL MATCH (me)-[d:FRIEND]->(ff) WITH ff, f, d WHERE d IS NULL "
        "RETURN ff.id AS id, ff.name AS name, collect(DISTINCT f.name) AS through ORDER BY ff.name LIMIT 60",
        {"id": uid},
    )
    return {"friends": friends_, "fof": fof}
