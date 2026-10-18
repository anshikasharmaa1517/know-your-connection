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

from .config import get_settings
from .db import Store, rows
from .llm import STOPWORDS, get_model
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


def _by_profile(g: Graph, keys: list[str]) -> list[dict]:
    """People in a graph whose LinkedIn profile is one of these normalised keys."""
    from .users import li_key

    found = rows(
        g,
        "MATCH (:Me)-[k:KNOWS]->(p:Person) WHERE p.linkedin_url <> '' AND any(x IN $keys WHERE toLower(p.linkedin_url) CONTAINS x) "
        f"RETURN {_PROFILE}, k.strength AS strength",
        {"keys": keys},
    )
    return [r for r in found if li_key(r.get("url")) in keys]


def _add_statuses(c, store: Store, uid: str, net: dict, own: Graph, friend_graphs, stems: list[str], *, third: bool) -> None:
    from .users import li_key, statuses_matching

    hits = [u for u in statuses_matching(store, stems) if u["id"] != uid and u["discoverable"] and _note_match(stems, [u["status"]])]
    if not hits:
        return
    friends = {f["id"] for f in net["friends"]}
    for u in hits:  # a friend's own status: they are someone you know
        if u["id"] in friends:
            c.add_status(
                {"id": None, "name": u["name"], "headline": "Your friend on Connections", "url": u.get("linkedin_url") or "", "key": "user:" + u["id"]},
                1, [], u["status"],
            )
    by_key = {u["li_key"]: u for u in hits if u["li_key"]}
    if not by_key:
        return
    keys = list(by_key)
    for r in _by_profile(own, keys):
        c.add_status(r, 1, [], by_key[li_key(r["url"])]["status"], own=True)
    for f, g in friend_graphs:
        for r in _by_profile(g, keys):
            c.add_status(r, 2, [{"name": f["name"], "kind": "friend", "handle": f["handle"]}], by_key[li_key(r["url"])]["status"])
    if third:
        for ff in net["fof"]:
            for r in _by_profile(store.user(ff["id"]), keys):
                c.add_status(r, 3, [{"name": ff["through"][0], "kind": "friend"}, {"name": ff["name"], "kind": "friend"}], by_key[li_key(r["url"])]["status"])


def _attach_statuses(c, store: Store) -> None:
    """Show the status of anyone found by title too, when they wrote one."""
    from .users import li_key, statuses_for

    keys = {li_key(r["url"]): r for r in c.found.values() if r.get("url")}
    for k, st in statuses_for(store, list(keys)).items():
        keys[k].setdefault("status", st["status"])
        if not keys[k].get("status"):
            keys[k]["status"] = st["status"]


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


# Notes ------------------------------------------------------------------------
# Your own notes ("hiring a summer intern") are searched word by word, so "hiring intern" finds
# Priya even though her title says Associate. Only your graph: a friend's notes never leave theirs.

_NOTE_SKIP = STOPWORDS | LINKERS | {"people", "person", "looking", "someone", "anyone", "who", "s"}


def _stem(word: str) -> str:
    for suffix in ("ing", "ers", "er", "ed", "es", "s", "e"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def note_stems(query: str) -> list[str]:
    words = _norm(query.replace("-", "")).split(" ")
    return [_stem(w) for w in words if w and w not in _NOTE_SKIP and len(w) > 1]


def _note_match(stems: list[str], notes: list[str]) -> str | None:
    """The note that contains every search word, if any."""
    for note in notes:
        words = {_stem(w) for w in _norm(note.replace("-", "")).split(" ") if w}
        if all(any(w.startswith(s) for w in words) for s in stems):
            return note
    return None


def _noted(g: Graph, stems: list[str]) -> list[dict]:
    return rows(
        g,
        "MATCH (:Me)-[k:KNOWS]->(p:Person)<-[:ABOUT]-(f:Fact) "
        "WHERE f.kind <> 'promise' AND any(t IN $stems WHERE toLower(replace(f.text, '-', '')) CONTAINS t) "
        f"RETURN {_PROFILE}, k.strength AS strength, collect(f.text) AS notes LIMIT 500",
        {"stems": stems},
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
                "note": None,
            }

    def add_notes(self, found: list[dict], stems: list[str]):
        for r in found:
            note = _note_match(stems, r.get("notes") or [])
            if not note:
                continue
            key = _key(r)
            existing = self.found.get(key)
            if existing:
                if existing["degree"] == 1:
                    existing["note"] = note
                continue
            self.found[key] = {
                "key": key,
                "person_id": r["id"],
                "name": r.get("name") or "",
                "position": r.get("position") or "",
                "company": r.get("company") or "",
                "headline": r.get("headline") or "",
                "url": r.get("url") or "",
                "degree": 1,
                "strength": r.get("strength"),
                "paths": [],
                "matched": [],
                "note": note,
            }

    def add_status(self, r: dict, degree: int, path: list[dict], status: str, *, own: bool = False):
        key = _key(r)
        existing = self.found.get(key)
        if existing and existing["degree"] <= degree:
            existing["status"] = status
            return
        self.found[key] = {
            "key": key,
            "person_id": r.get("id") if own else None,
            "name": r.get("name") or "",
            "position": r.get("position") or "",
            "company": r.get("company") or "",
            "headline": r.get("headline") or "",
            "url": r.get("url") or "",
            "degree": degree,
            "strength": r.get("strength") if own else None,
            "paths": [path] if path else [],
            "matched": [],
            "note": None,
            "status": status,
        }

    def by_degree(self, degree: int) -> list[dict]:
        out = [r for r in self.found.values() if r["degree"] == degree]
        out.sort(
            key=lambda r: (r["note"] is None and not r.get("status"), r["strength"] != "close", -len(r["paths"]), -len(r["matched"]), r["name"].lower())
        )
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
    stems = note_stems(query)
    if not phrases and not stems:
        return result

    pattern = _pattern(phrases) if phrases else re.compile(r"(?!x)x")
    terms = _prefilter_terms(phrases) if phrases else []
    c = _Collector(pattern, spec["must"])

    # 1st degree: your own contacts, by title and by your notes about them.
    own = store.user(uid)
    if terms:
        c.add(_direct(own, terms), 1, [], own=True)
    if stems:
        c.add_notes(_noted(own, stems), stems)

    # 2nd degree: memo ties in your graph, then each friend's contacts.
    for r in _via_ties(own, terms):
        for via in r["via"][:3]:
            c.add([r], 2, [{"name": via, "kind": "contact"}])
    friend_graphs: list[tuple[dict, Graph]] = []
    for f in net["friends"]:
        g = store.user(f["id"])  # read only: profile fields, never their notes or facts
        friend_graphs.append((f, g))
        c.add(_direct(g, terms), 2, [{"name": f["name"], "kind": "friend", "handle": f["handle"]}])

    # 3rd degree, on request and on the Pro plan.
    if depth >= 3 and third_allowed:
        for f, g in friend_graphs:
            for r in _via_ties(g, terms):
                for via in r["via"][:2]:
                    c.add([r], 3, [{"name": f["name"], "kind": "friend", "handle": f["handle"]}, {"name": via, "kind": "contact"}])
        for ff in net["fof"]:
            g = store.user(ff["id"])
            path = [{"name": ff["through"][0], "kind": "friend"}, {"name": ff["name"], "kind": "friend"}]
            c.add(_direct(g, terms), 3, path)
        result["scanned"]["fof"] = len(net["fof"])
        result["third"]["people"] = c.by_degree(3)

    # Statuses people wrote about themselves ("Hiring SDE interns"), matched to your network by LinkedIn profile.
    if stems:
        _add_statuses(c, store, uid, net, own, friend_graphs, stems, third=depth >= 3 and third_allowed)
    _attach_statuses(c, store)
    if depth >= 3 and third_allowed:
        result["third"]["people"] = c.by_degree(3)

    result["first"] = c.by_degree(1)
    result["second"] = c.by_degree(2)
    result["ms"] = int((time.time() - started) * 1000)
    return result



# --------------------------------------------------------------------------
# Ask your network: friends' agents search their owners' graphs for one need.
#
# Triggered from a gap on a goal. No form and no approval: each friend who leaves
# "friends can find my contacts" on is searched directly, candidates are scored by the
# model against the need, and only profile fields (name, title, company, link) are used,
# never a friend's notes or memos.

AGENT_POOL = 60


def _need_phrases(need) -> tuple[list[str], set[str]]:
    """All phrases for one need, and the role phrases that name it outright."""
    from .goals import head_phrase

    head = head_phrase(need.label)
    spec = expand(head) if head else {"phrases": [], "must": []}
    role = {_norm(p) for p in (spec["phrases"] or [head]) if p}
    out: list[str] = []
    for p in [*spec["phrases"], head, *[k.lower() for k in need.keywords]]:
        p = _norm(p)
        if p and len(p) > 1 and p not in GENERIC and p not in out:
            out.append(p)
    return out, role


def ask_network(store: Store, uid: str, *, plan, gap_need: str, goal: str, depth: int = 2) -> dict:
    """Friends' agents search their own graphs for everything a goal needs, starting with
    the gap that was clicked. People for that gap come first; anyone else in their
    networks who helps with the goal is listed after."""
    from .goals import covers, match_need

    settings = settings_of(store, uid)
    net = reach(store, uid)
    third_allowed = settings["plan"] == THIRD_DEGREE_PLAN
    result = {
        "need": gap_need,
        "plan": settings["plan"],
        "friends": [{"name": f["name"], "handle": f["handle"]} for f in net["friends"]],
        "scanned": {"friends": len(net["friends"]), "fof": 0},
        "third": {"locked": not third_allowed, "networks": len(net["fof"])},
        "people": [],
    }
    gap = next((n for n in plan.needs if n.label.lower() == gap_need.lower()), None) or (plan.needs[0] if plan.needs else None)
    needs = sorted(plan.needs, key=lambda n: n is not gap)  # the clicked gap first
    phrases: list[str] = []
    gap_roles: set[str] = set()
    any_roles: set[str] = set()
    scoring = plan.model_copy(deep=True)  # same needs, keywords widened with role aliases
    for n, sn in zip(plan.needs, scoring.needs):
        sn.keywords = list(dict.fromkeys([*sn.keywords, *_need_phrases(n)[0]]))[:24]
    for n in needs:
        ps, roles = _need_phrases(n)
        any_roles |= roles
        if n is gap:
            gap_roles = roles
        phrases.extend(p for p in ps if p not in phrases)
    if not phrases or not net["friends"]:
        return result

    pattern = _pattern(phrases)
    terms = _prefilter_terms(phrases)
    c = _Collector(pattern, [])
    # Your own contacts first, so anyone you already know is left to the goal's ranking.
    c.add(_direct(store.user(uid), terms), 1, [], own=True)
    for f in net["friends"]:
        g = store.user(f["id"])  # read only: profile fields, never their notes or facts
        c.add(_direct(g, terms), 2, [{"name": f["name"], "kind": "friend", "handle": f["handle"]}])
    if depth >= 3 and third_allowed:
        for ff in net["fof"]:
            g = store.user(ff["id"])
            c.add(_direct(g, terms), 3, [{"name": ff["through"][0], "kind": "friend"}, {"name": ff["name"], "kind": "friend"}])
        result["scanned"]["fof"] = len(net["fof"])

    candidates = [r for r in c.found.values() if r["degree"] > 1]
    candidates.sort(
        key=lambda r: (
            r["degree"],
            -sum(m in gap_roles for m in r["matched"]),
            -sum(m in any_roles for m in r["matched"]),
            -len(r["matched"]),
        )
    )
    candidates = candidates[:AGENT_POOL]
    if not candidates:
        return result

    from .goals import attach_statuses, attach_web_facts

    attach_statuses(candidates)  # what people wrote about themselves in the app
    web_on = attach_web_facts(plan, candidates)  # company checks on the web, cached in FalkorDB
    payload = [
        {
            "id": r["key"],
            "name": r["name"],
            "headline": r["headline"] or ", ".join(filter(None, [r["position"], r["company"]])),
            "tie": "friend",
            "strength": None,
            "facts": [{"id": f["id"], "kind": f["kind"], "text": f["text"]} for f in r.get("facts", [])],
        }
        for r in candidates
    ]
    scores = {s.person_id: s for s in get_model().score_people(goal, scoring, payload).scores}
    floor = get_settings().relevance_floor
    people = []
    for r in candidates:
        s = scores.get(r["key"])
        if not s or s.score < floor:
            continue
        from .goals import role_need

        need = role_need(plan, {"headline": r["headline"], "position": r["position"], "company": r["company"]}, match_need(s.need, plan))
        if need is None:
            continue
        cited = [{"text": f["text"], "url": f["url"]} for f in r.get("facts", []) if f.get("kind") == "web" and f["id"] in s.evidence]
        score = max(0, min(100, int(s.score)))
        backed = any(f["id"] in s.evidence for f in r.get("facts", []))
        if (plan.company_question or "").strip() and not backed:
            score = min(score, 40)
            if score < floor:
                continue
        for_gap = bool(gap) and (need.lower() == gap.label.lower() or covers(gap, {"headline": r["headline"], "position": r["position"]}, s.reason))
        row = {k: v for k, v in r.items() if k != "facts"}
        people.append({**row, "score": score, "reason": s.reason.strip(), "need": need, "for_gap": for_gap, "sources": cited})
    people.sort(key=lambda r: (not r["for_gap"], r["degree"], -r["score"]))
    result["people"] = people
    return result
