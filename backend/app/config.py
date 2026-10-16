"""Runtime settings, read from environment variables.

Everything has a default so `uvicorn app.main:app` works against a local
FalkorDB with no configuration. Without OPENAI_API_KEY the app runs on the
offline heuristics in `llm.py`, which is enough to demo the flows.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


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
        falkordb_url=os.getenv("FALKORDB_URL", "redis://localhost:6379"),
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
