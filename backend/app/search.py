"""Find: search your network for a kind of person, ordered by degree.

    1st degree  people you know: (:Me)-[:KNOWS]->(p) in your own graph.
    2nd degree  people your friends know, from each friend's own graph, plus people you heard
                about through one of your contacts (a memo tie in your own graph).
    3rd degree  people your friends' friends know, plus memo ties in your friends' graphs.
                Pro plan only, because it walks many more graphs.

No approval round trip: a friend's contacts are searchable as long as that friend leaves
"friends can find my contacts" on (the default). What crosses accounts is limited to the
profile a LinkedIn connection already shows (name, title, company, profile link) plus who the
path runs through. Notes, memo facts and tie strength in someone else's graph never leave it.
"""

from __future__ import annotations

import re
import time

from falkordb import Graph

from .db import Store, rows
from .llm import get_model
from .users import reach, settings_of

# Common searches resolved without a model call. Each group is one role; a query that equals
# any phrase in a group searches the whole group.
ROLE_ALIASES: list[list[str]] = [
    [
        "sde", "sde 1", "sde 2", "sde 3", "sde i", "sde ii", "sde iii", "swe", "software engineer",
        "software development engineer", "software developer", "software dev", "backend engineer",
        "backend developer", "frontend engineer", "frontend developer", "front end engineer",
        "full stack", "fullstack", "full stack developer", "full stack engineer", "web developer",
        "mobile developer", "android developer", "ios developer", "application developer",
        "member of technical staff", "mts", "programmer", "developer",
    ],
    ["pm", "product manager", "apm", "associate product manager", "product lead", "product owner",
     "group product manager", "senior product manager", "head of product", "product management"],
    ["designer", "product designer", "ux designer", "ui designer", "ux", "ui/ux", "interaction designer",
     "visual designer", "design lead"],
    ["data scientist", "data science", "ml engineer", "machine learning engineer", "machine learning",
     "ai engineer", "applied scientist", "research scientist", "deep learning"],
    ["data engineer", "analytics engineer", "data platform", "etl"],
    ["data analyst", "business analyst", "analyst", "bi analyst", "analytics"],
    ["devops", "sre", "site reliability", "site reliability engineer", "platform engineer",
     "infrastructure engineer", "cloud engineer", "devops engineer"],
    ["qa", "sdet", "test engineer", "quality assurance", "qa engineer", "automation tester", "tester"],
    ["recruiter", "talent acquisition", "technical recruiter", "hr", "human resources", "talent partner",
     "people partner", "hiring"],
    ["vc", "venture capital", "venture capitalist", "investor", "venture partner", "general partner",
     "angel investor", "angel"],
    ["founder", "co-founder", "cofounder", "ceo", "founding"],
    ["cto", "chief technology officer", "vp engineering", "vp of engineering", "head of engineering",
     "engineering manager", "director of engineering"],
    ["marketing", "marketer", "growth", "growth marketer", "brand manager", "content marketer", "seo"],
    ["sales", "account executive", "business development", "bdr", "sdr", "account manager", "partnerships"],
    ["hardware engineer", "hardware", "embedded", "firmware", "electronics engineer", "vlsi", "pcb"],
    ["security engineer", "security", "infosec", "cybersecurity", "appsec", "penetration tester"],
]

# Single words too broad to search on unless the person typed exactly that word.
GENERIC = set(
    "engineer engineering manager lead senior junior staff associate intern head director specialist consultant "
    "principal partner officer executive member team".split()
)

# Words that join a role to a qualifier: "sde at google", "pm in fintech".
LINKERS = set("at in from for with who works work working a an the of and or".split())

THIRD_DEGREE_PLAN = "pro"

_cache: dict[str, tuple[float, dict]] = {}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#/ ]+", " ", (text or "").lower())).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None


def expand(query: str) -> dict:
    """Turn 'SDE' into the job-title phrases that mean it.

    Returns label, phrases (any one must match) and must (extra words that all have to appear,
    from searches like 'sde at google').
    """
    q = _norm(query)
    if not q:
        return {"label": "", "phrases": [], "must": []}
    hit = _cache.get(q)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]

    phrases: list[str] = []
    must: list[str] = []
    label = query.strip()
    exact = [g for g in ROLE_ALIASES if q in g or q.rstrip("s") in g]
    if exact:
        for g in exact:
            phrases.extend(g)
    else:
        # A known role inside a longer search: the role picks the group, the rest narrows it.
        inner = [(p, g) for g in ROLE_ALIASES for p in g if " " in q and _contains_phrase(q, p)]
        if inner:
            best = max(len(p) for p, _ in inner)
            role = [p for p, _ in inner if len(p) == best][0]
            for p, g in inner:
                if p == role:
                    phrases.extend(g)
            rest = _norm(q.replace(role, " "))
            must = [w for w in rest.split(" ") if w and w not in LINKERS]
        else:
            found = get_model().expand_role(query)
            label = found.label or label
            phrases.extend(found.phrases)
            # A model phrase that names a known role pulls in that role's aliases too.
            for g in ROLE_ALIASES:
                if any(_norm(p) in g for p in found.phrases):
                    phrases.extend(g)
            phrases.append(q)

    seen: set[str] = set()
    out: list[str] = []
    for p in phrases:
        p = _norm(p)
        if not p or p in seen or (p in GENERIC and p != q) or len(p) < 2:
            continue
        seen.add(p)
        out.append(p)
    result = {"label": label, "phrases": out[:40], "must": must}
    _cache[q] = (time.time(), result)
    return result


def _pattern(phrases: list[str]) -> re.Pattern:
    parts = []
    for p in sorted(phrases, key=len, reverse=True):
        words = [re.escape(w) for w in p.split(" ")]
        parts.append(r"[\s\-_]?".join(words) + "s?")
    return re.compile(r"(?<![a-z0-9])(" + "|".join(parts) + r")(?![a-z0-9])", re.I)


def _prefilter_terms(phrases: list[str]) -> list[str]:
    """Substrings for a cheap CONTAINS filter in Cypher; the regex does the exact match after."""
    terms = set()
    for p in phrases:
        longest = max(re.split(r"[\s/]+", p), key=len)
        terms.add(longest)
    return sorted(terms)


_PROFILE = (
    "p.id AS id, p.name AS name, p.position AS position, p.company AS company, p.headline AS headline, "
    "p.linkedin_url AS url, p.name_key AS key"
)
_TEXT = "toLower(coalesce(p.position,'') + ' ' + coalesce(p.headline,'') + ' ' + coalesce(p.company,''))"


# Note: the term filter sits in the WHERE right after MATCH. FalkorDB drops matches when a
# list predicate over a $parameter runs after a WITH.
_MATCHES = f"any(t IN $terms WHERE {_TEXT} CONTAINS t)"


def _direct(g: Graph, terms: list[str]) -> list[dict]:
    return rows(
        g,
        f"MATCH (:Me)-[k:KNOWS]->(p:Person) WHERE {_MATCHES} "
        f"RETURN {_PROFILE}, k.strength AS strength LIMIT 1000",
        {"terms": terms},
    )


def _via_ties(g: Graph, terms: list[str]) -> list[dict]:
    """People you heard about through one of your contacts, but don't know yourself."""
    return rows(
        g,
        f"MATCH (m:Me)-[:KNOWS]->(via:Person)-[:KNOWS]-(p:Person) WHERE {_MATCHES} "
        "OPTIONAL MATCH (m)-[d:KNOWS]->(p) WITH p, via, d WHERE d IS NULL "
        f"RETURN {_PROFILE}, collect(DISTINCT via.name) AS via LIMIT 500",
        {"terms": terms},
    )


def _key(r: dict) -> str:
    url = (r.get("url") or "").strip().lower().rstrip("/")
    if url:
        return "u:" + url.split("?")[0]
    return "n:" + (r.get("key") or _norm(r.get("name") or ""))


class _Collector:
    def __init__(self, pattern: re.Pattern, must: list[str]):
        self.pattern = pattern
        self.must = must
        self.found: dict[str, dict] = {}

    def add(self, found: list[dict], degree: int, path: list[dict], *, strength: str | None = None, own: bool = False):
        for r in found:
            text = " ".join(filter(None, [r.get("position"), r.get("headline"), r.get("company")]))
            matched = sorted({m.group(1).lower() for m in self.pattern.finditer(text)})
            if not matched:
                continue
            low = _norm(text)
            if any(not _contains_phrase(low, w) for w in self.must):
                continue
            key = _key(r)
            existing = self.found.get(key)
            if existing and existing["degree"] < degree:
                continue
            if existing and existing["degree"] == degree:
                if path and path not in existing["paths"] and len(existing["paths"]) < 5:
                    existing["paths"].append(path)
                continue
            self.found[key] = {
                "key": key,
                # Only your own people carry an id you can open in People.
                "person_id": r["id"] if own else None,
                "name": r.get("name") or "",
                "position": r.get("position") or "",
                "company": r.get("company") or "",
                "headline": r.get("headline") or "",
                "url": r.get("url") or "",
                "degree": degree,
                "strength": strength or r.get("strength") if own and degree == 1 else None,
                "paths": [path] if path else [],
                "matched": matched,
            }

    def by_degree(self, degree: int) -> list[dict]:
        out = [r for r in self.found.values() if r["degree"] == degree]
        out.sort(key=lambda r: (r["strength"] != "close", -len(r["paths"]), -len(r["matched"]), r["name"].lower()))
        return out


def find(store: Store, uid: str, query: str, *, depth: int = 2) -> dict:
    started = time.time()
    spec = expand(query)
    phrases = spec["phrases"]
    settings = settings_of(store, uid)
    net = reach(store, uid)
    third_allowed = settings["plan"] == THIRD_DEGREE_PLAN
    result = {
        "query": query,
        "label": spec["label"],
        "phrases": phrases,
        "must": spec["must"],
        "plan": settings["plan"],
        "scanned": {"own": 1, "friends": len(net["friends"]), "fof": 0},
        "first": [],
        "second": [],
        "third": {"locked": not third_allowed, "networks": len(net["fof"]) + len(net["friends"]), "people": []},
    }
    if not phrases:
        return result

    pattern = _pattern(phrases)
    terms = _prefilter_terms(phrases)
    c = _Collector(pattern, spec["must"])

    # 1st degree: your own contacts.
    own = store.user(uid)
    c.add(_direct(own, terms), 1, [], own=True)

    # 2nd degree: memo ties in your graph, then each friend's contacts.
    for r in _via_ties(own, terms):
        for via in r["via"][:3]:
            c.add([r], 2, [{"name": via, "kind": "contact"}])
    friend_graphs: list[tuple[dict, Graph]] = []
    for f in net["friends"]:
        g = store.user(f["id"])  # read only: the profile fields above, nothing from notes or facts
        friend_graphs.append((f, g))
        c.add(_direct(g, terms), 2, [{"name": f["name"], "kind": "friend"}])

    # 3rd degree, on request and on the Pro plan.
    if depth >= 3 and third_allowed:
        for f, g in friend_graphs:
            for r in _via_ties(g, terms):
                for via in r["via"][:2]:
                    c.add([r], 3, [{"name": f["name"], "kind": "friend"}, {"name": via, "kind": "contact"}])
        for ff in net["fof"]:
            g = store.user(ff["id"])
            path = [{"name": ff["through"][0], "kind": "friend"}, {"name": ff["name"], "kind": "friend"}]
            c.add(_direct(g, terms), 3, path)
        result["scanned"]["fof"] = len(net["fof"])
        result["third"]["people"] = c.by_degree(3)

    result["first"] = c.by_degree(1)
    result["second"] = c.by_degree(2)
    result["ms"] = int((time.time() - started) * 1000)
    return result

