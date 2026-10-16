from __future__ import annotations

from fastapi import Header, HTTPException
from pydantic import BaseModel

from . import users
from .db import get_store


class Viewer(BaseModel):
    id: str
    name: str
    handle: str


def viewer(authorization: str | None = Header(default=None)) -> Viewer:
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = users.user_by_token(get_store(), token)
    if not user:
        raise HTTPException(status_code=401, detail="sign in first")
    return Viewer(**user)


def graph_of(v: Viewer):
    # The only way a request reaches a user graph: the viewer's own id.
    return get_store().user(v.id)
