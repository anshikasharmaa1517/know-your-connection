"""Model calls: memo extraction, goal planning, relevance scoring, embeddings,
speech-to-text.

`OpenAIModel` uses the Responses API with structured outputs, so every call
returns a typed pydantic object instead of free text. `HeuristicModel` is a
keyword-based stand-in used when no OPENAI_API_KEY is set (and as a fallback if
a call fails), so the whole app runs and tests pass offline. The heuristics are
deliberately simple; real quality comes from the model.
"""

from __future__ import annotations

import json
import logging
import re
import types
import typing
from typing import Literal

from pydantic import BaseModel, Field

from .config import get_settings
from .util import name_key, words

log = logging.getLogger("connections.llm")


# --------------------------------------------------------------------------
# Typed outputs


class ExtractedPerson(BaseModel):
    ref: str = Field(description="Short handle for this person within the memo, like p1, p2.")
    name: str = Field(description="Full name as said in the memo.")
    existing_id: str | None = Field(
        description="id from KNOWN_PEOPLE if this is clearly the same person, else null."
    )
    company: str | None
    position: str | None
    met: bool = Field(
        description="True if the memo's author personally met or talked to this person; "
        "false if they were only mentioned by someone else."
    )


class ExtractedFact(BaseModel):
    kind: Literal["fact", "intent", "promise"] = Field(
        description="fact: something true about them. intent: something they are trying to do or "
        "looking for. promise: something someone committed to do."
    )
    text: str = Field(description="One self-contained sentence in third person. Use names, not pronouns.")
    about: list[str] = Field(description="refs of the people this is about.")
    owner: Literal["me", "them"] | None = Field(
        description="For promises: 'me' if the memo author promised it, 'them' if the other person did."
    )
    due: str | None = Field(description="For promises: when it is due, as said (e.g. 'Friday'), else null.")


class ExtractedTie(BaseModel):
    a: str = Field(description="ref of one person")
    b: str = Field(description="ref of the other person")
    relation: str = Field(description="How they are connected, e.g. 'co-founder', 'manager', 'friend'.")


class MemoExtraction(BaseModel):
    title: str = Field(description="4-8 word title for this memo.")
    people: list[ExtractedPerson]
    facts: list[ExtractedFact]
    ties: list[ExtractedTie] = Field(description="Relationships between two people in the memo (not the author).")


class Need(BaseModel):
    label: str = Field(description="Short noun phrase for a kind of person, e.g. 'climate-tech VC'.")
    description: str = Field(description="One sentence: who would help and why.")
    keywords: list[str] = Field(
        description="5-12 lowercase words or short phrases likely to appear in such a person's job "
        "title, company or notes about them."
    )


class GoalPlan(BaseModel):
    summary: str = Field(description="The goal restated in one short sentence.")
    needs: list[Need] = Field(description="1 to 3 distinct kinds of people who can directly open the door for this goal.")


class Score(BaseModel):
    person_id: str
    score: int = Field(description="0-100 fit to the goal. Not a judgement of the person.")
    need: str = Field(description="label of the need this person serves best.")
    reason: str = Field(description="One sentence citing the concrete detail that makes them relevant.")
    evidence: list[str] = Field(
        description="ids of the facts that back the reason, and/or 'profile' if it comes from their "
        "title or company. Never invent ids."
    )


class Scores(BaseModel):
    scores: list[Score]


class RoleQuery(BaseModel):
    label: str = Field(description="The role restated in a few words, e.g. 'Software development engineer'.")
    phrases: list[str] = Field(
        description="8-20 lowercase job-title words or phrases that mean this role on a LinkedIn profile, "
        "including abbreviations and level variants (e.g. 'sde', 'sde ii', 'software engineer'). "
        "No generic words that also fit unrelated roles, like 'engineer' or 'manager' on their own."
    )


# --------------------------------------------------------------------------
# Prompts

EXTRACT_INSTRUCTIONS = """You turn a short voice memo, recorded right after meeting someone, into graph data for a personal CRM.

Rules:
- Only extract what the memo says. Never guess companies, titles or facts.
- The memo's author is "me"; do not create a person for them.
- If a person matches someone in KNOWN_PEOPLE (same name, consistent context), set existing_id.
- Facts are one self-contained third-person sentence each, using names, not pronouns.
- intent = something a person is trying to do or find (hiring, raising, looking for a mentor).
- promise = a commitment. owner 'me' if the author promised it.
- ties = relationships between two people in the memo, like co-founders or colleagues.
"""

PLAN_INSTRUCTIONS = """You help someone work out which kinds of people in their own network could move a goal forward.
Return 1 to 3 distinct needs. Each need is a kind of person (role, sector, seniority), not a named person.
Only include people who can directly open the door: someone who can hire, refer, invest, buy, decide, or is already doing
exactly what the goal describes and can vouch for them. For a job goal that means recruiters, hiring managers and people
working in that kind of role or company.
Do not add supporting or advisory roles (career coaches, consultants, resume writers, mentors, trainers) unless the goal
itself asks for advice, coaching or mentorship. Fewer, sharper needs are better than padding.
Keywords should be words that would literally appear in a LinkedIn job title, company name or a note about such a person."""

SCORE_INSTRUCTIONS = """You rank people from the user's own network against one goal.

Rules:
- Score only fit to this goal (0-100). This is never a judgement of a person's worth.
- Every score needs evidence: ids from that person's facts, or 'profile' when the reason comes from their title or company. Do not invent ids.
- The reason is one sentence and must cite the concrete detail (company, role, what they said).
- People reached 'via' someone are second-degree: still list them if they fit, the via is the warm path.
- Leave out anyone with no real connection to the goal (score below 30). An empty list is fine.
"""

ROLE_INSTRUCTIONS = """You expand a short people search, typed by someone looking through their network, into job-title phrases.
The search names a kind of person (a role, maybe with a sector or seniority), like 'SDE', 'PM at a fintech' or 'VC'.
Return phrases that would literally appear in a LinkedIn position or headline for that kind of person.
Keep them specific to the role: an 'SDE' search must not match hardware or civil engineers."""


# --------------------------------------------------------------------------
# Heuristic model


SYNONYM_GROUPS: list[list[str]] = [
    ["vc", "venture", "investor", "investing", "capital", "fund", "partner", "principal", "associate"],
    ["angel", "seed", "pre-seed", "investor", "check", "funding", "raise", "raising"],
    ["intern", "internship", "hiring", "recruiter", "talent", "recruiting"],
    ["hardware", "firmware", "embedded", "electronics", "robotics", "pcb", "iot", "semiconductor"],
    ["mentor", "mentorship", "advisor", "adviser", "senior", "principal", "staff"],
    ["climate", "climate-tech", "cleantech", "clean", "energy", "sustainability", "carbon", "solar", "ev"],
    ["sre", "reliability", "devops", "platform", "infrastructure", "kubernetes", "cloud"],
    ["founder", "co-founder", "cofounder", "startup", "ceo", "cto"],
    ["ai", "ml", "llm", "genai", "machine", "learning", "research"],
    ["health", "healthcare", "medical", "doctor", "hospital", "clinic", "pharma"],
    ["design", "designer", "ux", "ui", "product"],
    ["job", "role", "position", "hiring", "manager", "lead"],
]

STOPWORDS = set(
    "a an the and or for to of in on at by with from my me i want need get find land some someone summer "
    "winter spring fall next this year new first good great help into about who that".split()
)

_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:[\s-][A-Z][a-z]+){1,2})\b")
_NOT_NAMES = set(
    "I Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May June July "
    "August September October November December Today Tomorrow Yesterday The This That Met Just Also She He "
    "They We Her His Their Our".split()
)
_ORG_WORDS = set(
    "Fund Capital Ventures Partners Labs Lab Robotics Energy Health Cloud Networks Studio Systems Technologies "
    "Tech Inc Ltd Bank Group University Institute College School Foundation Silicon Batteries Talent AI DevFest "
    "Meetup Summit Conference Hackathon Google Microsoft Amazon".split()
)
_ORG_PREFIX_RE = re.compile(r"\b(at|from|of|joined|for|founded|runs|leads)\s+(the\s+)?$", re.I)
_PROMISE_RE = re.compile(r"\b(i'll|i will|i promised|promised to|i owe|i need to send|i should send)\b", re.I)
_THEIR_PROMISE_RE = re.compile(r"\b(she'll|he'll|they'll|will send me|will intro|offered to)\b", re.I)
_INTENT_RE = re.compile(r"\b(looking for|wants to|want to|hiring|raising|trying to|needs? (a|an|some))\b", re.I)
_MET_RE = re.compile(r"\b(met|talked|spoke|chatted|coffee|lunch|dinner|call with|sat next to)\b", re.I)
_TIE_RE = re.compile(
    r"\b(co-?founder|cofounder|colleague|works with|manager|boss|reports to|friend|brother|sister|"
    r"partner|teammate|roommate|classmate)\b",
    re.I,
)
_AT_RE = re.compile(r"\b(?:at|from|with)\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})")
_ROLE_RE = re.compile(
    r"\b(?:is an?|works as an?|an?)\s+((?:[a-z\-]+\s){0,3}(?:engineer|partner|founder|investor|associate|"
    r"manager|designer|researcher|recruiter|principal|director|analyst|lead|intern|student|professor|cto|ceo))\b",
    re.I,
)
_DUE_RE = re.compile(r"\bby\s+((?:next\s+)?[A-Za-z]+day|tomorrow|tonight|next week|end of (?:the )?week|\w+ \d{1,2})", re.I)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def expand_keywords(text: str) -> list[str]:
    tokens = [w for w in words(text) if w not in STOPWORDS and not w.isdigit()]
    out: list[str] = []
    for tok in tokens:
        for group in SYNONYM_GROUPS:
            if tok in group or tok.rstrip("s") in group:
                out.extend(group)
        out.append(tok)
    seen: set[str] = set()
    return [k for k in out if not (k in seen or seen.add(k))][:16]


def keyword_hits(keywords: list[str], text: str) -> list[str]:
    text_words = set(words(text))
    # "SDE-1" should hit "sde": also index the parts of hyphenated words.
    text_words |= {part for w in list(text_words) if "-" in w for part in w.split("-") if part}
    low = (text or "").lower()
    hits = []
    for kw in keywords:
        kw = kw.lower().strip()
        if not kw:
            continue
        if " " in kw or "-" in kw:
            if kw in low:
                hits.append(kw)
        elif kw in text_words or (len(kw) > 4 and kw + "s" in text_words):
            hits.append(kw)
    return hits


class HeuristicModel:
    name = "offline-heuristics"
    provider = "offline"
    can_transcribe = False

    def extract_memo(self, text: str, known: list[dict]) -> MemoExtraction:
        sentences = _sentences(text)
        known_by_key = {name_key(k["name"]): k for k in known}
        known_by_first: dict[str, list[dict]] = {}
        for k in known:
            first = name_key(k["name"]).split(" ")[0]
            known_by_first.setdefault(first, []).append(k)

        people: dict[str, ExtractedPerson] = {}
        order: list[str] = []

        def add(raw_name: str, sentence: str, start: int | None = None, allow_single: bool = False) -> str | None:
            tokens = raw_name.split()
            while tokens and tokens[0] in _NOT_NAMES:
                tokens.pop(0)
            if len(tokens) < (1 if allow_single else 2):
                return None
            if start is not None and len(tokens) < len(raw_name.split()):
                start = None  # the match began with a verb like "Met", so it isn't after "at"/"from"
            raw_name = " ".join(tokens)
            key = name_key(raw_name)
            if not key:
                return None
            if any(w in _ORG_WORDS for w in raw_name.split()):
                return None
            if start is not None and _ORG_PREFIX_RE.search(sentence[:start]):
                return None
            match = known_by_key.get(key)
            if match is None and " " not in key and len(known_by_first.get(key, [])) == 1:
                match = known_by_first[key][0]
            canonical = name_key(match["name"]) if match else key
            if canonical in people:
                return people[canonical].ref
            ref = f"p{len(people) + 1}"
            company = None
            role = None
            at = _AT_RE.search(sentence[sentence.find(raw_name):] if raw_name in sentence else sentence)
            if at:
                company = at.group(1).strip().rstrip(".,;:!?")
                if company.split()[0] in _NOT_NAMES or company.lower() in ("devfest", "meetup"):
                    company = None
            rm = _ROLE_RE.search(sentence)
            if rm:
                role = rm.group(1).strip()
            people[canonical] = ExtractedPerson(
                ref=ref,
                name=match["name"] if match else raw_name,
                existing_id=match["id"] if match else None,
                company=company,
                position=role,
                met=bool(_MET_RE.search(sentence)) or not people,
            )
            order.append(canonical)
            return ref

        sentence_refs: list[list[str]] = []
        for sentence in sentences:
            refs: list[str] = []
            for m in _NAME_RE.finditer(sentence):
                ref = add(m.group(1), sentence, m.start())
                if ref and ref not in refs:
                    refs.append(ref)
            # first names of people already known
            for w in re.findall(r"\b[A-Z][a-z]+\b", sentence):
                key = name_key(w)
                for canonical, person in people.items():
                    if canonical.split(" ")[0] == key and person.ref not in refs:
                        refs.append(person.ref)
                if key in known_by_first and len(known_by_first[key]) == 1 and not any(
                    p.existing_id == known_by_first[key][0]["id"] for p in people.values()
                ):
                    ref = add(known_by_first[key][0]["name"], sentence, allow_single=True)
                    if ref and ref not in refs:
                        refs.append(ref)
            sentence_refs.append(refs)

        facts: list[ExtractedFact] = []
        ties: list[ExtractedTie] = []
        last_refs: list[str] = []
        for sentence, refs in zip(sentences, sentence_refs):
            about = refs or last_refs[:1]
            if not about:
                continue
            last_refs = refs or last_refs
            if _PROMISE_RE.search(sentence):
                kind, owner = "promise", "me"
            elif _THEIR_PROMISE_RE.search(sentence):
                kind, owner = "promise", "them"
            elif _INTENT_RE.search(sentence):
                kind, owner = "intent", None
            else:
                kind, owner = "fact", None
            due = None
            if kind == "promise":
                dm = _DUE_RE.search(sentence)
                due = dm.group(1) if dm else None
            facts.append(ExtractedFact(kind=kind, text=sentence.rstrip("."), about=about, owner=owner, due=due))
            if len(refs) >= 2:
                tm = _TIE_RE.search(sentence)
                if tm:
                    ties.append(ExtractedTie(a=refs[0], b=refs[1], relation=tm.group(1).lower()))

        names = [people[c].name for c in order]
        title = "Met " + ", ".join(names[:2]) if names else (sentences[0][:48] if sentences else "Memo")
        return MemoExtraction(title=title, people=[people[c] for c in order], facts=facts, ties=ties)

    def expand_role(self, query: str) -> RoleQuery:
        label = re.sub(r"\s+", " ", query.strip())
        return RoleQuery(label=label, phrases=[label.lower()] if label else [])

    def plan_goal(self, text: str) -> GoalPlan:
        label = re.sub(r"\s+", " ", text.strip()).rstrip(".")
        return GoalPlan(
            summary=label,
            needs=[Need(label=label, description=f"People who can help with: {label}", keywords=expand_keywords(text))],
        )

    def score_people(self, goal: str, plan: GoalPlan, people: list[dict]) -> Scores:
        scores: list[Score] = []
        for person in people:
            best: Score | None = None
            profile = " ".join(filter(None, [person.get("headline"), person.get("company"), person.get("position")]))
            for need in plan.needs:
                kws = [k.lower() for k in need.keywords] or expand_keywords(need.label)
                prof_hits = keyword_hits(kws, profile)
                evidence: list[str] = ["profile"] if prof_hits else []
                fact_hits: list[str] = []
                for fact in person.get("facts", []):
                    h = keyword_hits(kws, fact["text"])
                    if h:
                        evidence.append(fact["id"])
                        fact_hits.extend(h)
                hits = list(dict.fromkeys(prof_hits + fact_hits))
                if not hits:
                    continue
                score = min(92, 34 + 14 * len(hits) + (6 if fact_hits else 0))
                shown = [f"“{h}”" for h in hits[:3]]
                listed = shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]
                where = "title and notes" if prof_hits and fact_hits else "title" if prof_hits else "notes"
                verb = "mention" if where == "notes" or " and " in where else "mentions"
                reason = f"Their {where} {verb} {listed}, which fits {need.label}."
                cand = Score(person_id=person["id"], score=score, need=need.label, reason=reason, evidence=evidence)
                if best is None or cand.score > best.score:
                    best = cand
            if best:
                scores.append(best)
        return Scores(scores=scores)

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None

    def transcribe(self, filename: str, data: bytes) -> str:
        raise RuntimeError("Voice capture needs OPENAI_API_KEY. Type the memo instead.")


# --------------------------------------------------------------------------
# Hosted models (OpenAI or AWS Bedrock)


def inline_schema(model: type[BaseModel]) -> dict:
    """JSON schema with every $ref expanded in place, for APIs that don't resolve $defs."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def expand(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return expand(dict(defs[node["$ref"].split("/")[-1]]))
            node = {k: expand(v) for k, v in node.items() if k != "title"}
            return _nullable_as_type_list(node)
        if isinstance(node, list):
            return [expand(v) for v in node]
        return node

    return expand(schema)


def _nullable_as_type_list(node: dict) -> dict:
    """Rewrite {"anyOf": [{"type": "string"}, {"type": "null"}]} as {"type": ["string", "null"]}.
    Both mean the same; the type-list form is what Amazon Nova's tool docs use."""
    options = node.get("anyOf")
    if not isinstance(options, list) or len(options) != 2:
        return node
    nulls = [o for o in options if o == {"type": "null"}]
    others = [o for o in options if o != {"type": "null"}]
    if len(nulls) != 1 or len(others) != 1 or not isinstance(others[0].get("type"), str):
        return node
    other = dict(others[0])
    if other["type"] in ("object", "array"):
        return node
    merged = {k: v for k, v in node.items() if k != "anyOf"}
    merged.update(other)
    merged["type"] = [other["type"], "null"]
    if "enum" in merged and None not in merged["enum"]:
        merged["enum"] = list(merged["enum"]) + [None]
    return merged


def _allows_none(annotation) -> bool:
    return annotation is None or type(None) in typing.get_args(annotation)


def _fill_value(value, ann):
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return fill_missing(value, ann)
    origin = typing.get_origin(ann)
    if origin is list and isinstance(value, list):
        args = typing.get_args(ann)
        return [_fill_value(v, args[0]) for v in value] if args else value
    if origin in (typing.Union, types.UnionType):
        for arg in typing.get_args(ann):
            if arg is type(None):
                continue
            is_model = isinstance(arg, type) and issubclass(arg, BaseModel) and isinstance(value, dict)
            is_list = typing.get_origin(arg) is list and isinstance(value, list)
            if is_model or is_list:
                return _fill_value(value, arg)
    return value


def fill_missing(data, model: type[BaseModel]):
    """Set nullable fields a model left out to None, recursing into nested models and lists."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for name, field in model.model_fields.items():
        if name not in out:
            if _allows_none(field.annotation):
                out[name] = None
            continue
        out[name] = _fill_value(out[name], field.annotation)
    return out


class HostedModel:
    """Shared logic. Subclasses implement _parse, embed and transcribe."""

    provider = "hosted"
    can_transcribe = False

    def __init__(self):
        self.settings = get_settings()
        self.fallback = HeuristicModel()

    def _parse(self, instructions: str, payload: str, schema: type[BaseModel]):  # pragma: no cover
        raise NotImplementedError

    def check(self) -> GoalPlan:
        """One real call with no fallback, so configuration errors surface."""
        return self._parse(PLAN_INSTRUCTIONS, "GOAL: find a hardware mentor", GoalPlan)

    def extract_memo(self, text: str, known: list[dict]) -> MemoExtraction:
        payload = "KNOWN_PEOPLE:\n" + json.dumps(known[:150]) + "\n\nMEMO:\n" + text
        try:
            return self._parse(EXTRACT_INSTRUCTIONS, payload, MemoExtraction)
        except Exception as exc:
            log.warning("extract_memo fell back to heuristics: %s: %s", type(exc).__name__, exc)
            return self.fallback.extract_memo(text, known)

    def plan_goal(self, text: str) -> GoalPlan:
        try:
            plan = self._parse(PLAN_INSTRUCTIONS, "GOAL: " + text, GoalPlan)
            for need in plan.needs:
                need.keywords = [k.lower().strip() for k in need.keywords if k.strip()]
            if not plan.needs:
                raise RuntimeError("model returned no needs")
            plan.needs = plan.needs[:3]
            return plan
        except Exception as exc:
            log.warning("plan_goal fell back to heuristics: %s: %s", type(exc).__name__, exc)
            return self.fallback.plan_goal(text)

    def expand_role(self, query: str) -> RoleQuery:
        try:
            found = self._parse(ROLE_INSTRUCTIONS, "SEARCH: " + query, RoleQuery)
            found.phrases = [p.lower().strip() for p in found.phrases if p.strip()]
            return found
        except Exception as exc:
            log.warning("expand_role fell back to heuristics: %s: %s", type(exc).__name__, exc)
            return self.fallback.expand_role(query)

    def score_people(self, goal: str, plan: GoalPlan, people: list[dict]) -> Scores:
        out: list[Score] = []
        for start in range(0, len(people), 20):
            chunk = people[start : start + 20]
            payload = json.dumps(
                {"goal": goal, "needs": [n.model_dump() for n in plan.needs], "people": chunk},
                ensure_ascii=False,
            )
            try:
                out.extend(self._parse(SCORE_INSTRUCTIONS, payload, Scores).scores)
            except Exception as exc:
                log.warning("score_people chunk fell back to heuristics: %s: %s", type(exc).__name__, exc)
                out.extend(self.fallback.score_people(goal, plan, chunk).scores)
        return Scores(scores=out)

    def embed(self, texts: list[str]) -> list[list[float]] | None:  # pragma: no cover
        return None

    def transcribe(self, filename: str, data: bytes) -> str:
        raise RuntimeError("This server can't transcribe audio. Use dictation in the browser or type the memo.")


class OpenAIModel(HostedModel):
    provider = "openai"
    can_transcribe = True

    def __init__(self):
        super().__init__()
        from openai import OpenAI

        self.client = OpenAI(api_key=self.settings.openai_api_key)
        self.name = self.settings.openai_model

    def _parse(self, instructions: str, payload: str, schema: type[BaseModel]):
        response = self.client.responses.parse(
            model=self.settings.openai_model,
            instructions=instructions,
            input=payload,
            text_format=schema,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("model returned no structured output")
        return parsed

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not texts:
            return []
        try:
            vectors: list[list[float]] = []
            for start in range(0, len(texts), 256):
                resp = self.client.embeddings.create(
                    model=self.settings.embed_model, input=texts[start : start + 256]
                )
                vectors.extend(d.embedding for d in resp.data)
            return vectors
        except Exception as exc:
            log.warning("embedding failed: %s", exc)
            return None

    def transcribe(self, filename: str, data: bytes) -> str:
        resp = self.client.audio.transcriptions.create(
            model=self.settings.transcribe_model, file=(filename or "memo.webm", data)
        )
        return resp.text


class BedrockModel(HostedModel):
    """AWS Bedrock. Structured output comes from forcing a single tool call whose
    input schema is the pydantic model; embeddings come from Titan Text v2.

    Auth is whatever boto3 finds: a Bedrock API key in AWS_BEARER_TOKEN_BEDROCK,
    or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.
    """

    provider = "bedrock"

    # Errors that mean "this account can't use this model", where another model may work.
    SWITCH_ON = {"AccessDeniedException", "ResourceNotFoundException", "ValidationException"}

    def __init__(self, client=None):
        super().__init__()
        import threading

        self.name = self.settings.bedrock_model
        self.embed_model = self.settings.bedrock_embed_model
        self._embed_ok = bool(self.embed_model)
        self._fallbacks = [m for m in self.settings.bedrock_fallback_models if m != self.name]
        self.refused: dict[str, str] = {}  # model id -> error code, for /api/health/model
        self._switch_lock = threading.Lock()
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "bedrock-runtime",
                region_name=self.settings.aws_region,
                config=Config(retries={"max_attempts": 4, "mode": "adaptive"}, read_timeout=90),
            )
        self.client = client

    def _parse(self, instructions: str, payload: str, schema: type[BaseModel]):
        tool = "record_" + re.sub(r"(?<!^)(?=[A-Z])", "_", schema.__name__).lower()
        response = self._converse(
            system=[{"text": instructions}],
            messages=[{"role": "user", "content": [{"text": payload}]}],
            toolConfig={
                "tools": [
                    {
                        "toolSpec": {
                            "name": tool,
                            "description": f"Record the result as {schema.__name__}. Always call this tool.",
                            "inputSchema": {"json": inline_schema(schema)},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": tool}},
            },
            inferenceConfig={"maxTokens": 4096},
        )
        for block in response.get("output", {}).get("message", {}).get("content", []):
            if "toolUse" in block:
                data = block["toolUse"].get("input") or {}
                return schema.model_validate(fill_missing(data, schema))
        raise RuntimeError(f"model did not return {schema.__name__} (stopReason={response.get('stopReason')})")

    def _converse(self, **request):
        while True:
            model = self.name
            try:
                if model.startswith("mantle:"):
                    return self._mantle(model.removeprefix("mantle:"), request)
                return self.client.converse(modelId=model, **request)
            except Exception as exc:
                resp = getattr(exc, "response", None)
                code = resp.get("Error", {}).get("Code") if isinstance(resp, dict) else None
                if code not in self.SWITCH_ON:
                    raise
                with self._switch_lock:
                    if self.name == model:  # another thread may have switched already
                        if not self._fallbacks:
                            raise
                        self.refused[model] = code
                        self.name = self._fallbacks.pop(0)
                        log.warning("Bedrock refused %s (%s: %s). Switching to %s.", model, code, exc, self.name)

    def _mantle_post(self, url: str, body: dict, key: str) -> dict:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            code = {400: "ValidationException", 401: "AccessDeniedException", 403: "AccessDeniedException",
                    404: "ResourceNotFoundException", 429: "ThrottlingException"}.get(exc.code, "ServiceUnavailableException")
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": code, "Message": f"mantle HTTP {exc.code}: {detail}"}}, "Messages") from None

    def _mantle(self, model: str, request: dict) -> dict:
        """Call Claude through the bedrock-mantle endpoint (Anthropic Messages API) and return a
        Converse-shaped response, so the rest of the code doesn't care which endpoint answered."""
        import os

        key = (os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or "").strip()
        if not key:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no Bedrock API key for mantle"}}, "Messages")
        spec = request["toolConfig"]["tools"][0]["toolSpec"]
        body = {
            "model": model,
            "max_tokens": request.get("inferenceConfig", {}).get("maxTokens", 4096),
            "system": " ".join(b["text"] for b in request.get("system", [])),
            "messages": [
                {"role": m["role"], "content": " ".join(c["text"] for c in m["content"] if "text" in c)}
                for m in request["messages"]
            ],
            "tools": [{"name": spec["name"], "description": spec["description"], "input_schema": spec["inputSchema"]["json"]}],
            "tool_choice": {"type": "tool", "name": spec["name"]},
        }
        region = self.settings.aws_region
        if "/" in model:  # "us-east-1/anthropic.claude-haiku-4-5"
            region, model = model.split("/", 1)
            body["model"] = model
        url = f"https://bedrock-mantle.{region}.api.aws/anthropic/v1/messages"
        data = self._mantle_post(url, body, key)
        content = [{"toolUse": {"name": b.get("name"), "input": b.get("input") or {}}}
                   for b in data.get("content", []) if b.get("type") == "tool_use"]
        return {"stopReason": data.get("stop_reason"), "output": {"message": {"role": "assistant", "content": content}}}

    def _embed_one(self, text: str) -> list[float]:
        body = json.dumps({"inputText": text[:8000] or " ", "dimensions": self.settings.embed_dim, "normalize": True})
        resp = self.client.invoke_model(
            modelId=self.embed_model, body=body, contentType="application/json", accept="application/json"
        )
        return json.loads(resp["body"].read())["embedding"]

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not texts:
            return []
        if not self._embed_ok:
            return None
        from concurrent.futures import ThreadPoolExecutor

        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                return list(pool.map(self._embed_one, texts))
        except Exception as exc:
            # Don't retry on every ranking: without embeddings, ranking uses keywords plus the model.
            self._embed_ok = False
            log.warning("Bedrock embeddings disabled for this run (%s: %s)", type(exc).__name__, exc)
            return None


def extract_json(text: str) -> dict:
    """The first JSON object in a model reply, ignoring reasoning tags, code fences and prose."""
    text = re.sub(r"<(think|reasoning)>.*?</\1>", "", text or "", flags=re.S)
    text = text.replace("```json", "```")
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    raise RuntimeError(f"model returned no JSON object; reply began: {text.strip()[:160]!r}")


class MantleModel(OpenAIModel):
    """OpenAI-style models (for example openai.gpt-5.6-terra) through Amazon Bedrock Mantle's
    OpenAI-compatible API, authenticated with a Bedrock API key. Picks the model from what the
    key can list, unless MANTLE_MODEL_ID is set. Uses strict structured output when Mantle
    accepts it, otherwise asks for plain JSON and validates it."""

    provider = "mantle"
    can_transcribe = False
    PREFERRED = (
        "openai.gpt-5.6-terra",
        "openai.gpt-5.6-sol",
        "openai.gpt-5.5",
        "openai.gpt-oss-120b",
        "openai.gpt-oss-20b",
    )

    def __init__(self, client_factory=None):
        HostedModel.__init__(self)
        import os

        self.key = (os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or "").strip()
        self.region = self.settings.aws_region
        self.available: list[str] = []
        self.base_url = ""
        self._factory = client_factory or self._openai_client
        self.route = None
        self.refused: dict[str, str] = {}
        self.name = (os.environ.get("MANTLE_MODEL_ID") or "").strip()
        self.client = None
        self._discover()

    def _openai_client(self, base_url: str):
        from openai import OpenAI

        return OpenAI(api_key=self.key, base_url=base_url, max_retries=2, timeout=90)

    def _discover(self) -> None:
        """Find which URL prefix answers and which models this key can use."""
        last_error = None
        for prefix in ("/v1", "/openai/v1"):
            url = f"https://bedrock-mantle.{self.region}.api.aws{prefix}"
            client = self._factory(url)
            try:
                ids = sorted(m.id for m in client.models.list())
            except Exception as exc:
                last_error = exc
                continue
            self.client, self.base_url, self.available = client, url, ids
            break
        if self.client is None:
            self.base_url = f"https://bedrock-mantle.{self.region}.api.aws/v1"
            self.client = self._factory(self.base_url)
            log.warning("Mantle model list failed (%s); using %s", last_error, self.base_url)
        if not self.name:
            text_models = [m for m in self.available if "embed" not in m and "whisper" not in m]
            chosen = next((m for m in self.PREFERRED if m in self.available), None)
            chosen = chosen or next((m for m in text_models if m.startswith("openai.")), None)
            self.name = chosen or (text_models[0] if text_models else self.PREFERRED[0])
        log.info("Mantle: %s via %s (%d models listed)", self.name, self.base_url, len(self.available))

    BACKUP_MODELS = ("openai.gpt-oss-120b", "openai.gpt-5.5", "openai.gpt-5.4", "openai.gpt-5.6-sol", "qwen.qwen3-235b-a22b-2507", "deepseek.v3.2", "mistral.mistral-large-3-675b-instruct")

    def _routes(self):
        """(base_url, api, model) combinations to try, best first, until one works."""
        prefixes = [self.base_url] + [
            f"https://bedrock-mantle.{self.region}.api.aws{p}" for p in ("/v1", "/openai/v1")
        ]
        bases = list(dict.fromkeys(prefixes))
        models = [self.name] + [m for m in self.BACKUP_MODELS if m != self.name and (not self.available or m in self.available)]
        return [(b, api, m) for m in models for b in bases for api in ("responses", "chat")]

    def _ask(self, base: str, api: str, model: str, instructions: str, payload: str) -> str:
        client = self.client if base == self.base_url else self._factory(base)
        # Mantle ignored the separate instructions field, so everything goes in the one user message.
        message = f"{instructions}\n\n=== INPUT ===\n{payload}\n=== END INPUT ===\n\nReturn the JSON object now."
        if api == "responses":
            response = client.responses.create(model=model, input=message)
            text = getattr(response, "output_text", "") or ""
            if not text.strip():
                parts = []
                for item in getattr(response, "output", None) or []:
                    for c in getattr(item, "content", None) or []:
                        t = getattr(c, "text", None)
                        if t:
                            parts.append(t)
                text = "\n".join(parts)
            return text
        response = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": message}]
        )
        msg = response.choices[0].message
        return (msg.content or "") or (getattr(msg, "reasoning_content", None) or "")

    def _parse(self, instructions: str, payload: str, schema: type[BaseModel]):
        guide = (
            instructions
            + "\n\nReply with ONLY a JSON object matching this JSON schema. No prose, no code fences.\n"
            + json.dumps(inline_schema(schema))
        )
        routes = [self.route] if getattr(self, "route", None) else self._routes()
        last = None
        for base, api, model in routes:
            try:
                text = self._ask(base, api, model, guide, payload)
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status in (400, 401, 403, 404, 405, 422) and len(routes) > 1:
                    last = exc
                    self.refused[f"{model} {api} {base.rsplit('.api.aws', 1)[-1]}"] = f"HTTP {status}"
                    continue
                raise
            if not getattr(self, "route", None):
                self.route = (base, api, model)
                if base != self.base_url:
                    self.client, self.base_url = self._factory(base), base
                self.name = model
                log.info("Mantle: using %s via %s (%s API)", model, base, api)
            return schema.model_validate(fill_missing(extract_json(text), schema))
        raise last or RuntimeError("no Mantle route worked")

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None

    def transcribe(self, filename: str, data: bytes) -> str:
        return HostedModel.transcribe(self, filename, data)


_model = None


def get_model():
    global _model
    if _model is None:
        provider = get_settings().llm_provider
        if provider == "openai":
            _model = OpenAIModel()
        elif provider == "mantle":
            _model = MantleModel()
        elif provider == "bedrock":
            _model = BedrockModel()
        else:
            _model = HeuristicModel()
    return _model


def set_model(model) -> None:
    global _model
    _model = model
