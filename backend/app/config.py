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


BEDROCK_KEY_NAMES = (
    "AWS_BEARER_TOKEN_BEDROCK", "BEDROCK_API_KEY", "AWS_BEDROCK_API_KEY", "BEDROCK_KEY", "AWS_BEDROCK_KEY",
    "BEDROCK_TOKEN", "AWS_BEDROCK_TOKEN", "BEDROCK_BEARER_TOKEN", "AWS_BEARER_TOKEN",
)
REGION_NAMES = ("AWS_REGION", "AWS_DEFAULT_REGION", "BEDROCK_REGION", "AWS_BEDROCK_REGION", "REGION")
REGION_ALIASES = {
    "n. virginia": "us-east-1", "north virginia": "us-east-1", "virginia": "us-east-1", "us east (n. virginia)": "us-east-1",
    "ohio": "us-east-2", "us east (ohio)": "us-east-2", "oregon": "us-west-2", "us west (oregon)": "us-west-2",
    "mumbai": "ap-south-1", "asia pacific (mumbai)": "ap-south-1", "singapore": "ap-southeast-1",
    "asia pacific (singapore)": "ap-southeast-1", "tokyo": "ap-northeast-1", "sydney": "ap-southeast-2",
    "frankfurt": "eu-central-1", "ireland": "eu-west-1", "london": "eu-west-2", "paris": "eu-west-3",
}
RELATED_PREFIXES = ("AWS", "BEDROCK", "OPENAI", "LLM", "FALKOR", "REGION", "CONNECTIONS")


def _clean_secret(value: str) -> str:
    value = (value or "").strip().strip("\"'").strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value


def _lookup(env: Mapping[str, str], names: tuple[str, ...]) -> tuple[str, str] | None:
    """Find a variable by any accepted name, forgiving case and stray spaces in the name."""
    wanted = {n: i for i, n in enumerate(names)}
    found = []
    for key, value in env.items():
        norm = key.strip().upper().replace(" ", "_").replace("-", "_")
        if norm in wanted and (value or "").strip():
            found.append((wanted[norm], key, value))
    if not found:
        return None
    _, key, value = min(found)
    return key, value


def apply_env_aliases(env=os.environ) -> list[str]:
    """Copy a Bedrock key or region set under another name to the names boto3 and the app read.
    Returns notes for the startup log. Never returns values."""
    notes = []
    hit = _lookup(env, BEDROCK_KEY_NAMES)
    if hit:
        key, value = hit
        cleaned = _clean_secret(value)
        if key != "AWS_BEARER_TOKEN_BEDROCK" or cleaned != value:
            env["AWS_BEARER_TOKEN_BEDROCK"] = cleaned
            notes.append(f"Using Bedrock key from {key.strip()!r}")
    region = _lookup(env, REGION_NAMES)
    if region:
        key, value = region
        code = REGION_ALIASES.get(value.strip().lower(), value.strip().lower())
        if key != "AWS_REGION" or code != value:
            env["AWS_REGION"] = code
            notes.append(f"Using region {code} from {key.strip()!r}")
    return notes


def related_env_names(env: Mapping[str, str] = os.environ) -> list[str]:
    """Names (never values) of settings that look relevant, for diagnosing a misconfigured deploy."""
    out = []
    for key, value in env.items():
        norm = key.strip().upper()
        if norm.startswith(RELATED_PREFIXES) and norm not in ("AWS_CA_BUNDLE",):
            out.append(f"{key}{'' if (value or '').strip() else ' (empty)'}")
    return sorted(out)


def detect_provider(env: Mapping[str, str] = os.environ) -> str:
    """openai, mantle, bedrock or offline. LLM_PROVIDER wins; otherwise whichever credentials are present."""
    chosen = (env.get("LLM_PROVIDER") or "").strip().lower()
    if chosen in ("openai", "bedrock", "mantle", "offline"):
        return chosen
    if (env.get("OPENAI_API_KEY") or "").strip():
        return "openai"
    aws_keys = (env.get("AWS_ACCESS_KEY_ID") or "").strip() and (env.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    if (env.get("AWS_BEARER_TOKEN_BEDROCK") or "").strip() and not (env.get("BEDROCK_MODEL_ID") or "").strip():
        return "mantle"
    if (env.get("AWS_BEARER_TOKEN_BEDROCK") or "").strip() or (env.get("BEDROCK_MODEL_ID") or "").strip() or aws_keys:
        return "bedrock"
    return "offline"


@dataclass(frozen=True)
class Settings:
    falkordb_url: str
    llm_provider: str
    openai_api_key: str | None
    openai_model: str
    embed_model: str
    embed_dim: int
    transcribe_model: str
    aws_region: str
    bedrock_model: str
    bedrock_embed_model: str
    bedrock_fallback_models: tuple[str, ...]
    graph_prefix: str
    public_url: str
    static_dir: str
    rank_pool: int
    relevance_floor: int
    gap_threshold: int

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider in ("openai", "bedrock", "mantle")

    @property
    def model_name(self) -> str:
        return {"openai": self.openai_model, "bedrock": self.bedrock_model, "mantle": "mantle (auto)"}.get(self.llm_provider, "offline heuristics")


def bedrock_fallbacks(env: Mapping[str, str] = os.environ) -> tuple[str, ...]:
    """Models to try, in order, if AWS refuses the main one (for example Anthropic's use-case form
    isn't filled in, or the AWS Marketplace subscription can't be created). Amazon Nova needs neither.
    BEDROCK_FALLBACK_MODEL_IDS overrides this; set it to "none" to turn fallback off."""
    raw = env.get("BEDROCK_FALLBACK_MODEL_IDS")
    if raw is not None:
        if raw.strip().lower() in ("", "none", "off"):
            return ()
        return tuple(m.strip() for m in raw.split(",") if m.strip())
    region = (env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or "us-east-1").strip().lower()
    # Same Claude model through the bedrock-mantle endpoint (Anthropic Messages API), then Amazon Nova.
    mantle = ["anthropic.claude-haiku-4-5", "anthropic.claude-sonnet-5", "anthropic.claude-haiku-5-5"]
    models = [f"mantle:{m}" for m in mantle]
    if region != "us-east-1":
        models += [f"mantle:us-east-1/{m}" for m in mantle[:2]]
    models.append("global.amazon.nova-2-lite-v1:0")
    if region.startswith(("us-", "ca-")):
        models.append("us.amazon.nova-2-lite-v1:0")
    elif region.startswith("eu-"):
        models.append("eu.amazon.nova-2-lite-v1:0")
    elif region == "ap-northeast-1":
        models.append("jp.amazon.nova-2-lite-v1:0")
    return tuple(models)


@lru_cache
def get_settings() -> Settings:
    here = os.path.dirname(os.path.abspath(__file__))
    default_static = os.path.normpath(os.path.join(here, "..", "..", "frontend", "dist"))
    apply_env_aliases()
    provider = detect_provider()
    if provider == "bedrock":
        embed_dim = int(os.getenv("BEDROCK_EMBED_DIM", "1024"))
    else:
        embed_dim = int(os.getenv("OPENAI_EMBED_DIM", "1536"))
    return Settings(
        falkordb_url=resolve_falkordb_url(),
        llm_provider=provider,
        openai_api_key=(os.getenv("OPENAI_API_KEY") or "").strip() or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
        embed_model=os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
        embed_dim=embed_dim,
        transcribe_model=os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
        # boto3 only reads AWS_DEFAULT_REGION on its own, so pass the region explicitly.
        aws_region=(os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1").strip(),
        bedrock_model=(os.getenv("BEDROCK_MODEL_ID") or "global.anthropic.claude-haiku-5-5").strip(),
        # Set BEDROCK_EMBED_MODEL_ID to an empty string to turn embeddings off.
        bedrock_embed_model=os.getenv("BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0").strip(),
        bedrock_fallback_models=bedrock_fallbacks(),
        graph_prefix=os.getenv("CONNECTIONS_GRAPH_PREFIX", "connections"),
        public_url=os.getenv("CONNECTIONS_PUBLIC_URL", "").rstrip("/"),
        static_dir=os.getenv("CONNECTIONS_STATIC_DIR", default_static),
        # How many people the ranker sends to the LLM for one goal.
        rank_pool=int(os.getenv("CONNECTIONS_RANK_POOL", "40")),
        # Scores below this are not shown as relevant at all.
        relevance_floor=int(os.getenv("CONNECTIONS_RELEVANCE_FLOOR", "35")),
        # A need with no person scoring at least this much becomes a gap.
        gap_threshold=int(os.getenv("CONNECTIONS_GAP_THRESHOLD", "60")),
    )
