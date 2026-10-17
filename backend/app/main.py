from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import capture as capture_mod
from . import goals as goals_mod
from . import imports as imports_mod
from . import network, users
from .auth import Viewer, graph_of, viewer
from .config import describe_falkordb_url, get_settings
from .db import get_store, one
from .linkedin import ExportError, parse_upload
from .llm import get_model

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cirql")

app = FastAPI(title="Cirql", version="0.1.0")

_boot = get_settings()
log.info("FalkorDB: %s", describe_falkordb_url(_boot.falkordb_url))
log.info("Model: %s", _boot.openai_model if _boot.llm_enabled else "offline heuristics (no OPENAI_API_KEY)")
if "<" in _boot.falkordb_url or "YOUR_PASSWORD" in _boot.falkordb_url.upper():
    log.warning("FALKORDB_URL still contains a placeholder like <password>. Replace it with the real value.")

MAX_UPLOAD = 60 * 1024 * 1024


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


class NameIn(BaseModel):
    name: str


@app.get("/api/health")
def health():
    settings = get_settings()
    target = describe_falkordb_url(settings.falkordb_url)
    try:
        get_store().shared().query("RETURN 1")
    except Exception as exc:
        # Full reason goes to the server log only; it can contain parts of the URL.
        log.error("FalkorDB check failed for %s: %s: %s", target, type(exc).__name__, exc)
        return JSONResponse(
            status_code=503,
            content={"ok": False, "falkordb": target, "error": type(exc).__name__, "detail": "FalkorDB is unreachable. See the server log."},
        )
    return {"ok": True, "falkordb": target, "model": get_model().name, "llm": settings.llm_enabled}


@app.post("/api/session")
def create_session(body: NameIn):
    return users.create_user(get_store(), body.name)


@app.get("/api/me")
def me(v: Viewer = Depends(viewer)):
    g = graph_of(v)
    counts = one(
        g,
        "OPTIONAL MATCH (:Me)-[k:KNOWS]->(p:Person) WITH count(p) AS direct "
        "OPTIONAL MATCH (f:Fact) WITH direct, count(f) AS facts "
        "OPTIONAL MATCH (goal:Goal {status:'open'}) RETURN direct, facts, count(goal) AS goals",
    )
    return {"user": v.model_dump(), "counts": counts, "llm": get_settings().llm_enabled, "model": get_model().name}


# --------------------------------------------------------------------------
# LinkedIn import


@app.post("/api/imports/linkedin")
async def upload_linkedin(file: UploadFile = File(...), v: Viewer = Depends(viewer)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="file too large")
    try:
        parsed = parse_upload(file.filename or "Connections.csv", data)
    except ExportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = imports_mod.create_import(graph_of(v), parsed)
    result["parsed"] = {
        "rows": len(parsed.rows),
        "duplicates_in_file": parsed.duplicates_in_file,
        "skipped_blank": parsed.skipped_blank,
    }
    return result


SAMPLE_CSV = os.getenv(
    "CIRQL_SAMPLE_CSV",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "samples", "Connections.csv")),
)


@app.post("/api/imports/sample")
def upload_sample(v: Viewer = Depends(viewer)):
    """Import the bundled fictional export, for demos."""
    with open(SAMPLE_CSV, "rb") as fh:
        parsed = parse_upload("Sample Connections.csv", fh.read())
    result = imports_mod.create_import(graph_of(v), parsed)
    result["parsed"] = {"rows": len(parsed.rows), "duplicates_in_file": parsed.duplicates_in_file, "skipped_blank": parsed.skipped_blank}
    return result


@app.get("/api/imports")
def list_imports(v: Viewer = Depends(viewer)):
    return {"imports": imports_mod.list_imports(graph_of(v))}


@app.get("/api/imports/{sid}/deck")
def get_deck(sid: str, limit: int = 12, v: Viewer = Depends(viewer)):
    return imports_mod.deck(graph_of(v), sid, limit=min(max(limit, 1), 50))


class DecisionIn(BaseModel):
    action: str


@app.post("/api/imports/{sid}/cards/{cid}")
def decide(sid: str, cid: str, body: DecisionIn, v: Viewer = Depends(viewer)):
    return imports_mod.decide(graph_of(v), sid, cid, body.action)


@app.post("/api/imports/{sid}/undo")
def undo(sid: str, v: Viewer = Depends(viewer)):
    return imports_mod.undo(graph_of(v), sid)


@app.post("/api/imports/{sid}/bulk")
def bulk(sid: str, body: DecisionIn, v: Viewer = Depends(viewer)):
    return imports_mod.bulk(graph_of(v), sid, body.action)


# --------------------------------------------------------------------------
# Network


@app.get("/api/people")
def people(q: str | None = None, v: Viewer = Depends(viewer)):
    return {"people": network.list_people(graph_of(v), q=q)}


@app.get("/api/people/{pid}")
def person(pid: str, v: Viewer = Depends(viewer)):
    detail = network.person_detail(graph_of(v), pid)
    if not detail:
        raise HTTPException(status_code=404, detail="not found")
    return detail


@app.get("/api/graph")
def graph_snapshot(v: Viewer = Depends(viewer)):
    return network.snapshot(graph_of(v))


@app.get("/api/sources")
def sources(v: Viewer = Depends(viewer)):
    return {"sources": network.sources(graph_of(v))}


@app.delete("/api/sources/{sid}")
def delete_source(sid: str, v: Viewer = Depends(viewer)):
    return network.delete_source(graph_of(v), sid)


@app.get("/api/promises")
def promises(v: Viewer = Depends(viewer)):
    return {"promises": network.promises(graph_of(v))}


class DoneIn(BaseModel):
    done: bool = True


@app.post("/api/promises/{fid}")
def promise_done(fid: str, body: DoneIn, v: Viewer = Depends(viewer)):
    network.set_promise_done(graph_of(v), fid, body.done)
    return {"ok": True}


# --------------------------------------------------------------------------
# Capture


class MemoIn(BaseModel):
    text: str
    title: str | None = None


@app.post("/api/capture")
def capture(body: MemoIn, v: Viewer = Depends(viewer)):
    return capture_mod.capture(graph_of(v), body.text, body.title)


@app.post("/api/capture/audio")
async def capture_audio(file: UploadFile = File(...), title: str | None = Form(default=None), v: Viewer = Depends(viewer)):
    data = await file.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="recording too long")
    try:
        text = get_model().transcribe(file.filename or "memo.webm", data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not transcribe: {exc}") from exc
    result = capture_mod.capture(graph_of(v), text, title)
    result["transcript"] = text
    return result


# --------------------------------------------------------------------------
# Goals


class GoalIn(BaseModel):
    text: str


@app.get("/api/goals")
def list_goals(v: Viewer = Depends(viewer)):
    return {"goals": goals_mod.list_goals(graph_of(v))}


@app.post("/api/goals")
def create_goal(body: GoalIn, v: Viewer = Depends(viewer)):
    return goals_mod.create_goal(graph_of(v), body.text)


@app.get("/api/goals/{gid}")
def get_goal(gid: str, v: Viewer = Depends(viewer)):
    return goals_mod.get_goal(graph_of(v), gid)


@app.patch("/api/goals/{gid}")
def update_goal(gid: str, body: GoalIn, v: Viewer = Depends(viewer)):
    return goals_mod.update_goal(graph_of(v), gid, body.text)


@app.post("/api/goals/{gid}/rerank")
def rerank(gid: str, v: Viewer = Depends(viewer)):
    goals_mod.rank(graph_of(v), gid)
    return goals_mod.get_goal(graph_of(v), gid)


@app.post("/api/goals/{gid}/close")
def close_goal(gid: str, v: Viewer = Depends(viewer)):
    return goals_mod.close_goal(graph_of(v), gid)


# --------------------------------------------------------------------------
# Friends


class HandleIn(BaseModel):
    handle: str


@app.get("/api/friends")
def get_friends(v: Viewer = Depends(viewer)):
    return users.friends(get_store(), v.id)


@app.post("/api/friends")
def add_friend(body: HandleIn, v: Viewer = Depends(viewer)):
    return users.request_friend(get_store(), v.id, body.handle)


@app.post("/api/friends/{uid}/accept")
def accept_friend(uid: str, v: Viewer = Depends(viewer)):
    return users.accept_friend(get_store(), v.id, uid)


# --------------------------------------------------------------------------
# Exchange (cross-user) lives in its own module.

from .exchange import router as exchange_router  # noqa: E402

app.include_router(exchange_router)


# --------------------------------------------------------------------------
# Frontend

_static = get_settings().static_dir
if os.path.isdir(_static):
    assets = os.path.join(_static, "assets")
    if os.path.isdir(assets):
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = os.path.normpath(os.path.join(_static, path))
        if path and candidate.startswith(_static) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_static, "index.html"))
