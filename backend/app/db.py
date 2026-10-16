"""FalkorDB access.

Isolation model: every user gets their own FalkorDB graph
(`<prefix>_user_<id>`), and request handlers only ever open the graph of the
signed-in user. The single shared graph (`<prefix>_shared`) holds accounts,
friendships and the Exchange protocol objects. Nothing in a user graph is
reachable from another user's graph because they are separate keys, not
separate subgraphs of one big graph.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

from falkordb import FalkorDB, Graph
from falkordb.edge import Edge
from falkordb.node import Node

from .config import get_settings

log = logging.getLogger("cirql.db")

_USER_ID_RE = re.compile(r"^[a-f0-9]{12}$")

USER_INDEXES = [
    "CREATE INDEX FOR (n:Me) ON (n.id)",
    "CREATE INDEX FOR (n:Person) ON (n.id)",
    "CREATE INDEX FOR (n:Person) ON (n.name_key)",
    "CREATE INDEX FOR (n:Person) ON (n.linkedin_url)",
    "CREATE INDEX FOR (n:Org) ON (n.key)",
    "CREATE INDEX FOR (n:Source) ON (n.id)",
    "CREATE INDEX FOR (n:Fact) ON (n.id)",
    "CREATE INDEX FOR (n:Goal) ON (n.id)",
    "CREATE INDEX FOR (n:Gap) ON (n.id)",
    "CREATE INDEX FOR (n:Candidate) ON (n.id)",
    "CREATE INDEX FOR (n:Candidate) ON (n.import_id)",
    "CALL db.idx.fulltext.createNodeIndex('Person', 'name', 'company', 'position', 'headline')",
]

SHARED_INDEXES = [
    "CREATE INDEX FOR (n:User) ON (n.id)",
    "CREATE INDEX FOR (n:User) ON (n.handle)",
    "CREATE INDEX FOR (n:User) ON (n.token)",
    "CREATE INDEX FOR (n:Request) ON (n.id)",
    "CREATE INDEX FOR (n:Reply) ON (n.id)",
    "CREATE INDEX FOR (n:Card) ON (n.id)",
    "CREATE INDEX FOR (n:Card) ON (n.optin_token)",
]


def _plain(value: Any) -> Any:
    if isinstance(value, Node):
        props = dict(value.properties)
        props.pop("embedding", None)
        return props
    if isinstance(value, Edge):
        props = dict(value.properties)
        props["_type"] = value.relation
        return props
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if k != "embedding"}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def rows(graph: Graph, cypher: str, params: dict | None = None) -> list[dict]:
    """Run a query and return each row as a dict keyed by the RETURN aliases."""
    result = graph.query(cypher, params or {})
    if not result.header:
        return []
    names = [h[1] for h in result.header]
    return [dict(zip(names, (_plain(v) for v in row))) for row in result.result_set]


def one(graph: Graph, cypher: str, params: dict | None = None) -> dict | None:
    found = rows(graph, cypher, params)
    return found[0] if found else None


def run(graph: Graph, cypher: str, params: dict | None = None) -> None:
    graph.query(cypher, params or {})


class Store:
    def __init__(self, url: str | None = None, prefix: str | None = None):
        settings = get_settings()
        self.settings = settings
        self.db = FalkorDB.from_url(url or settings.falkordb_url)
        self.prefix = prefix or settings.graph_prefix
        self._ready: set[str] = set()
        self._lock = threading.Lock()

    # Graph handles -------------------------------------------------------

    def user_graph_name(self, user_id: str) -> str:
        if not _USER_ID_RE.match(user_id or ""):
            raise ValueError("bad user id")
        return f"{self.prefix}_user_{user_id}"

    def user(self, user_id: str) -> Graph:
        name = self.user_graph_name(user_id)
        graph = self.db.select_graph(name)
        self._ensure(name, graph, self._user_schema)
        return graph

    def shared(self) -> Graph:
        name = f"{self.prefix}_shared"
        graph = self.db.select_graph(name)
        self._ensure(name, graph, SHARED_INDEXES)
        return graph

    @property
    def _user_schema(self) -> list[str]:
        schema = list(USER_INDEXES)
        if self.settings.llm_enabled:
            dim = self.settings.embed_dim
            schema.append(
                "CREATE VECTOR INDEX FOR (p:Person) ON (p.embedding) "
                f"OPTIONS {{dimension:{dim}, similarityFunction:'cosine'}}"
            )
        return schema

    def _ensure(self, name: str, graph: Graph, statements: list[str]) -> None:
        if name in self._ready:
            return
        with self._lock:
            if name in self._ready:
                return
            for stmt in statements:
                try:
                    graph.query(stmt)
                except Exception as exc:  # index already exists
                    if "already" not in str(exc).lower():
                        log.warning("schema statement failed on %s: %s (%s)", name, stmt, exc)
            self._ready.add(name)

    # Maintenance ---------------------------------------------------------

    def graph_names(self) -> list[str]:
        return [g for g in self.db.list_graphs() if g.startswith(self.prefix + "_")]

    def drop_all(self) -> None:
        """Delete every graph under this prefix. Used by tests and the seed script."""
        for name in self.graph_names():
            try:
                self.db.select_graph(name).delete()
            except Exception:
                pass
        self._ready.clear()


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def set_store(store: Store | None) -> None:
    global _store
    _store = store
