from __future__ import annotations

import re
import secrets
import time
import unicodedata

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️]+",
    flags=re.UNICODE,
)
# Credentials people put after their name on LinkedIn ("Priya Shah, CFA").
_CREDENTIAL_RE = re.compile(r",\s*[A-Z][A-Za-z.\-]{1,10}(\s*,\s*[A-Z][A-Za-z.\-]{1,10})*\s*$")


def new_id() -> str:
    return secrets.token_hex(6)


def new_token() -> str:
    return secrets.token_urlsafe(24)


def now_ms() -> int:
    return int(time.time() * 1000)


def clean_name(raw: str) -> str:
    """Display name: strip emoji, credentials and stray whitespace."""
    name = _EMOJI_RE.sub("", raw or "")
    name = _CREDENTIAL_RE.sub("", name.strip())
    name = re.sub(r"\s+", " ", name).strip(" ,|-")
    return name


def name_key(raw: str) -> str:
    """Normalised key used to spot the same person across sources."""
    name = clean_name(raw)
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-zA-Z0-9 ]+", " ", name).lower()
    return re.sub(r"\s+", " ", name).strip()


def org_key(raw: str) -> str:
    key = name_key(raw)
    key = re.sub(r"\b(inc|llc|ltd|pvt|private|limited|corp|corporation|co)\b", "", key)
    return re.sub(r"\s+", " ", key).strip()


def headline(position: str | None, company: str | None) -> str:
    position = (position or "").strip()
    company = (company or "").strip()
    if position and company:
        return f"{position} at {company}"
    return position or company


_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")


def words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())
