"""Runtime settings, read from environment variables.

Everything has a default so `uvicorn app.main:app` works against a local
FalkorDB with no configuration. Without OPENAI_API_KEY the app runs on the
offline heuristics in `llm.py`, which is enough to demo the flows.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote, unquote


def normalise_falkordb_url(raw: str) -> str:
    """Turn what people paste into a dashboard into a URL redis-py accepts.

    Handles surrounding whitespace and quotes, a pasted `FALKORDB_URL=` prefix,
    a bare `host:port` with no scheme, `falkor://` schemes, and passwords with
    characters like # / ? @ that would otherwise break URL parsing.
    """
    url = (raw or "").strip().strip("\"'").strip()
    if url.upper().startswith("FALKORDB_URL="):
        url = url.split("=", 1)[1].strip().strip("\"'").strip()
    if not url:
        return "redis://localhost:6379"
    if "://" not in url:
        url = "redis://" + url
    scheme, rest = url.split("://", 1)
    scheme = {"falkor": "redis", "falkors": "rediss"}.get(scheme.lower(), scheme.lower())
    if scheme == "unix":
        return f"{scheme}://{rest}"
    if "@" in rest:
        creds, hostpart = rest.rsplit("@", 1)
        user, _, password = creds.partition(":") if ":" in creds else ("", "", creds)
        rest = f"{quote(unquote(user), safe='')}:{quote(unquote(password), safe='')}@{hostpart}"
    return f"{scheme}://{rest}"


def resolve_falkordb_url(env: Mapping[str, str] = os.environ) -> str:
    """FALKORDB_HOST / PORT / USERNAME / PASSWORD win over FALKORDB_URL when set."""
    host = (env.get("FALKORDB_HOST") or "").strip()
    if host:
        host = re.sub(r"^[a-z]+://", "", host).rstrip("/")
        port = (env.get("FALKORDB_PORT") or "").strip()
        if ":" in host and not port:
            host, port = host.rsplit(":", 1)
        user = (env.get("FALKORDB_USERNAME") or env.get("FALKORDB_USER") or "").strip()
        password = env.get("FALKORDB_PASSWORD") or ""
        tls = (env.get("FALKORDB_TLS") or "").strip().lower() in ("1", "true", "yes")
        creds = f"{quote(user, safe='')}:{quote(password, safe='')}@" if (user or password) else ""
        return f"{'rediss' if tls else 'redis'}://{creds}{host}:{port or '6379'}"
    return normalise_falkordb_url(env.get("FALKORDB_URL") or "redis://localhost:6379")


def describe_falkordb_url(url: str) -> str:
    """The URL with the password hidden, safe for logs."""
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, hostpart = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{unquote(user)}:***@{hostpart}" if ":" in creds else f"{scheme}://***@{hostpart}"


@dataclass(frozen=True)
class Settings:
    falkordb_url: str
    openai_api_key: str | None
    openai_model: str
    embed_model: str
    embed_dim: int
    transcribe_model: str
    graph_prefix: str
    public_url: str
    static_dir: str
    rank_pool: int
    relevance_floor: int
    gap_threshold: int

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    here = os.path.dirname(os.path.abspath(__file__))
    default_static = os.path.normpath(os.path.join(here, "..", "..", "frontend", "dist"))
    return Settings(
        falkordb_url=resolve_falkordb_url(),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
        embed_model=os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
        embed_dim=int(os.getenv("OPENAI_EMBED_DIM", "1536")),
        transcribe_model=os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
        graph_prefix=os.getenv("CIRQL_GRAPH_PREFIX", "cirql"),
        public_url=os.getenv("CIRQL_PUBLIC_URL", "").rstrip("/"),
        static_dir=os.getenv("CIRQL_STATIC_DIR", default_static),
        # How many people the ranker sends to the LLM for one goal.
        rank_pool=int(os.getenv("CIRQL_RANK_POOL", "40")),
        # Scores below this are not shown as relevant at all.
        relevance_floor=int(os.getenv("CIRQL_RELEVANCE_FLOOR", "35")),
        # A need with no person scoring at least this much becomes a gap.
        gap_threshold=int(os.getenv("CIRQL_GAP_THRESHOLD", "60")),
    )
