"""Company checks on the open web, so rankings rest on sources rather than the model's memory.

A goal can name a company question ("remote work policy"). Before scoring, the top candidates'
companies are searched once each, in parallel, and the snippets go to the model as evidence it
must cite. Results are cached in FalkorDB's shared graph per company and question for a week.

Search providers, first one configured wins:
    TAVILY_API_KEY   Tavily search API
    BRAVE_API_KEY    Brave Search API
    (none)           DuckDuckGo's plain HTML results page, no key needed
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .db import Store, one, run

log = logging.getLogger("connections.web")

CACHE_SECONDS = 7 * 24 * 3600
MAX_COMPANIES = 15
UA = "Mozilla/5.0 (compatible; ConnectionsBot/1.0; +https://github.com/anshikasharmaa1517/know-your-connection)"


def provider() -> str:
    if os.getenv("CONNECTIONS_WEB_SEARCH", "").lower() in ("off", "0", "false", "none"):
        return "off"
    if os.getenv("TAVILY_API_KEY"):
        return "tavily"
    if os.getenv("BRAVE_API_KEY"):
        return "brave"
    return "duckduckgo"


def _tavily(q: str) -> list[dict]:
    r = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": os.environ["TAVILY_API_KEY"], "query": q, "max_results": 4, "search_depth": "basic"},
        timeout=10,
    )
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")} for x in r.json().get("results", [])]


def _brave(q: str) -> list[dict]:
    r = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": q, "count": 4},
        headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"], "Accept": "application/json"},
        timeout=10,
    )
    r.raise_for_status()
    return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("description", "")} for x in r.json().get("web", {}).get("results", [])]


_DDG_RESULT = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S
)


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def _ddg(q: str) -> list[dict]:
    r = httpx.post("https://html.duckduckgo.com/html/", data={"q": q}, headers={"User-Agent": UA}, timeout=10, follow_redirects=True)
    r.raise_for_status()
    out = []
    for href, title, snippet in _DDG_RESULT.findall(r.text)[:4]:
        url = href
        if "uddg=" in href:
            url = unquote(parse_qs(urlparse(href if href.startswith("http") else "https:" + href).query).get("uddg", [href])[0])
        out.append({"title": _strip(title), "url": url, "snippet": _strip(snippet)})
    return out


def search(q: str) -> list[dict]:
    p = provider()
    if p == "off":
        return []
    try:
        return {"tavily": _tavily, "brave": _brave, "duckduckgo": _ddg}[p](q)
    except Exception as exc:  # network, quota, blocked: the ranking goes on without web evidence
        log.warning("web search failed (%s) for %r: %s: %s", p, q, type(exc).__name__, exc)
        return []


def _key(company: str, topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", f"{company}|{topic}".lower()).strip()


def company_facts(store: Store, companies: list[str], topic: str) -> dict[str, list[dict]]:
    """{company: [{id, text, url}]} for each company, from cache or a fresh search."""
    topic = (topic or "").strip()
    names = list(dict.fromkeys(c.strip() for c in companies if c and c.strip() and c.strip().lower() not in ("freelance", "self-employed")))[:MAX_COMPANIES]
    if not topic or not names:
        return {}
    shared = store.shared()
    out: dict[str, list[dict]] = {}
    todo: list[str] = []
    now = time.time()
    for c in names:
        cached = one(shared, "MATCH (w:WebCheck {key:$k}) RETURN w.facts AS facts, w.at AS at", {"k": _key(c, topic)})
        if cached and cached.get("at") and now - cached["at"] < CACHE_SECONDS:
            out[c] = json.loads(cached["facts"] or "[]")
        else:
            todo.append(c)

    def look(c: str) -> tuple[str, list[dict]]:
        results = search(f'"{c}" {topic}')
        facts = []
        words = [w for w in re.findall(r"[a-z0-9]+", c.lower()) if len(w) > 2] or [c.lower()]
        for i, r in enumerate(results):
            text = f"{r['title']}. {r['snippet']}".strip()
            # Keep results that are actually about this company.
            if not any(w in text.lower() or w in r["url"].lower() for w in words):
                continue
            facts.append({"id": f"web:{_key(c, '')[:40].replace(' ', '-')}:{i}", "text": text[:400], "url": r["url"]})
        return c, facts[:3]

    if todo:
        with ThreadPoolExecutor(max_workers=min(6, len(todo))) as pool:
            for c, facts in pool.map(look, todo):
                out[c] = facts
                run(
                    shared,
                    "MERGE (w:WebCheck {key:$k}) SET w.company = $c, w.topic = $t, w.facts = $f, w.at = $at",
                    {"k": _key(c, topic), "c": c, "t": topic, "f": json.dumps(facts, ensure_ascii=False), "at": now},
                )
    return out
