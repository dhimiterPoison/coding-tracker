"""Local dashboard API. Binds to 127.0.0.1 only; refuses foreign Host headers (DNS-rebinding)
and requires a custom header on writes so other websites cannot POST to it from the browser."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import classify, config, db, goals, insights, journal, manual, reports, sync

STATIC = Path(__file__).parent / "static"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}
SYNC_EVERY_S = 300


async def _periodic_sync():
    while True:
        try:
            await asyncio.to_thread(sync.sync_all)
        except Exception:  # keep the loop alive
            pass
        await asyncio.sleep(SYNC_EVERY_S)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.ensure_dirs()
    task = asyncio.create_task(_periodic_sync())
    yield
    task.cancel()


app = FastAPI(title="Coding Tracker", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    raw = request.headers.get("host") or ""
    host = raw.split("]")[0] + "]" if raw.startswith("[") else raw.split(":")[0]
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "forbidden host"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-tracker") != "1":
        return JSONResponse({"detail": "missing X-Tracker header"}, status_code=403)
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"
    return resp


# ───────────── helpers ─────────────

def _range(start: str | None, end: str | None, days: int = 7) -> tuple[str, str]:
    e = date.fromisoformat(end) if end else date.today()
    s = date.fromisoformat(start) if start else e - timedelta(days=days - 1)
    return s.isoformat(), e.isoformat()


def _slim(s: dict) -> dict:
    keep = ("id", "source", "project", "branch", "title", "day", "started_at", "ended_at", "wall_s",
            "active_s", "flow_s", "longest_block_s", "prompts", "interruptions", "rejections", "errors",
            "edits", "tests_run", "area_switches", "topic_shifts", "idle_gaps", "type", "work_type",
            "type_override", "rating", "notes", "category", "focus", "focus_label", "tool_calls",
            "tokens_out", "models", "hidden")
    d = {k: s.get(k) for k in keep}
    d["commit_count"] = len(s["commits"])
    return d


# ───────────── read API ─────────────

@app.get("/api/config")
def get_config():
    cfg = config.load()
    return {"work_types": classify.WORK_TYPES, "flow_block_minutes": cfg["flow_block_minutes"],
            "idle_gap_minutes": cfg["idle_gap_minutes"], "sprint_length_days": cfg["sprint_length_days"],
            "refinement_length_days": cfg["refinement_length_days"], "data_dir": str(config.DATA_DIR)}


@app.get("/api/overview")
def api_overview(start: str | None = None, end: str | None = None, category: str = "all"):
    s, e = _range(start, end)
    with db.session() as conn:
        return insights.overview(conn, s, e, category)


@app.get("/api/sessions")
def api_sessions(start: str | None = None, end: str | None = None, category: str = "all",
                 project: str | None = None, type: str | None = None, q: str | None = None,
                 include_hidden: bool = False):
    s, e = _range(start, end, 30)
    with db.session() as conn:
        rows = insights.load_sessions(conn, s, e, category, project, include_hidden)
    if type:
        rows = [r for r in rows if r["type"] == type]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in (r["title"] or "").lower() or ql in (r["notes"] or "").lower()
                or ql in (r["last_prompt"] or "").lower() or any(ql in p["text"].lower() for p in r["prompt_list"])]
    return [_slim(r) for r in reversed(rows)]


@app.get("/api/sessions/{sid}")
def api_session(sid: str):
    with db.session() as conn:
        row = conn.execute(
            "SELECT s.*, COALESCE(p.category,'personal') AS category FROM sessions s "
            "LEFT JOIN projects p ON p.name=s.project WHERE s.id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "session not found")
    s = insights.decorate(dict(row))
    out = _slim(s)
    out.update({"commits": s["commits"], "files": s["files"], "tool_counts": s["tool_counts"],
                "prompt_list": s["prompt_list"], "last_prompt": s["last_prompt"],
                "last_assistant": s["last_assistant"], "work_scores": s["work_scores"],
                "blocks": s["blocks"], "cwd": s["cwd"]})
    return out


@app.get("/api/projects")
def api_projects(category: str = "all"):
    with db.session() as conn:
        return insights.projects_overview(conn, category)


@app.get("/api/report/{kind}")
def api_report(kind: str, start: str | None = None, end: str | None = None, category: str = "all"):
    cfg = config.load()
    if kind == "refinement":
        s, e = _range(start, end, cfg["refinement_length_days"])
        fn = reports.refinement
    elif kind == "retro":
        s, e = _range(start, end, cfg["sprint_length_days"])
        fn = reports.retro
    else:
        raise HTTPException(404, "unknown report")
    with db.session() as conn:
        return fn(conn, s, e, category)


@app.get("/api/day/{day}")
def api_day(day: str, category: str = "all"):
    try:
        with db.session() as conn:
            return journal.full_review(conn, day, category)
    except journal.JournalError as e:
        raise HTTPException(422, str(e))


@app.get("/api/days")
def api_days(start: str | None = None, end: str | None = None, category: str = "all"):
    s, e = _range(start, end, 14)
    with db.session() as conn:
        return journal.list_days(conn, s, e, category)


@app.get("/api/morning")
def api_morning():
    with db.session() as conn:
        return journal.morning_card(conn)


@app.get("/api/goals")
def api_goals(window: int = 7):
    with db.session() as conn:
        return {"goals": goals.progress(conn, goals.list_goals(conn, active_only=False), date.today().isoformat(),
                                        max(1, min(window, 90))),
                "templates": goals.TEMPLATES, "metrics": goals.METRICS}


# ───────────── write API ─────────────

class GoalAnswer(BaseModel):
    status: Literal["met", "partly", "missed"]
    note: str = Field("", max_length=500)


class CloseDay(BaseModel):
    feeling: int | None = Field(None, ge=1, le=5)
    win: str | None = Field(None, max_length=1000)
    notes: str | None = Field(None, max_length=5000)
    next_steps: dict[str, str] | None = None
    goal_answers: dict[str, GoalAnswer] | None = None
    narrative: str | None = Field(None, max_length=20000)


@app.post("/api/day/{day}/close")
def api_close_day(day: str, body: CloseDay, category: str = "all"):
    answers = {k: v.model_dump() for k, v in (body.goal_answers or {}).items()}
    try:
        with db.session() as conn:
            journal.close_day(conn, day, feeling=body.feeling, win=body.win, notes=body.notes,
                              next_steps=body.next_steps, goal_answers=answers or None,
                              narrative=body.narrative, category=category)
            return journal.full_review(conn, day, category)
    except journal.JournalError as e:
        raise HTTPException(422, str(e))


@app.delete("/api/day/{day}/close")
def api_reopen_day(day: str):
    try:
        with db.session() as conn:
            journal.reopen_day(conn, day)
    except journal.JournalError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


class NewGoal(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    why: str = Field("", max_length=1000)
    metric: str = "manual"
    target_per_week: int = Field(3, ge=1, le=7)


class GoalPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    why: str | None = Field(None, max_length=1000)
    target_per_week: int | None = Field(None, ge=1, le=7)
    active: bool | None = None


@app.post("/api/goals")
def api_add_goal(body: NewGoal):
    try:
        with db.session() as conn:
            return {"id": goals.create(conn, body.title, body.why, body.metric, body.target_per_week)}
    except goals.GoalError as e:
        raise HTTPException(422, str(e))


@app.patch("/api/goals/{gid}")
def api_patch_goal(gid: int, body: GoalPatch):
    try:
        with db.session() as conn:
            goals.update(conn, gid, **body.model_dump(exclude_unset=True))
    except goals.GoalError as e:
        raise HTTPException(404 if "not found" in str(e) else 422, str(e))
    return {"ok": True}


@app.delete("/api/goals/{gid}")
def api_delete_goal(gid: int):
    with db.session() as conn:
        goals.delete(conn, gid)
    return {"ok": True}


class SessionPatch(BaseModel):
    type_override: str | None = None
    rating: int | None = Field(None, ge=1, le=5)
    notes: str | None = Field(None, max_length=5000)
    hidden: bool | None = None


@app.patch("/api/sessions/{sid}")
def patch_session(sid: str, body: SessionPatch):
    fields = body.model_dump(exclude_unset=True)
    if "type_override" in fields and fields["type_override"] not in (None, "") + tuple(classify.WORK_TYPES):
        raise HTTPException(422, "unknown work type")
    if fields.get("type_override") == "":
        fields["type_override"] = None
    if "hidden" in fields:
        fields["hidden"] = int(bool(fields["hidden"]))
    if not fields:
        return {"ok": True}
    sets = ",".join(f"{k}=?" for k in fields)
    with db.session() as conn:
        cur = conn.execute(f"UPDATE sessions SET {sets} WHERE id=?", [*fields.values(), sid])
        if not cur.rowcount:
            raise HTTPException(404, "session not found")
    return {"ok": True}


class ManualSession(BaseModel):
    project: str = Field(..., min_length=1, max_length=120)
    day: str
    start_time: str = "09:00"          # local HH:MM
    minutes: int = Field(..., ge=1, le=24 * 60)
    title: str = Field(..., min_length=1, max_length=200)
    type: str = "other"
    rating: int | None = Field(None, ge=1, le=5)
    notes: str = Field("", max_length=5000)
    interruptions: int = Field(0, ge=0, le=500)
    context_switches: int = Field(0, ge=0, le=500)


@app.post("/api/sessions")
def add_manual(body: ManualSession):
    try:
        with db.session() as conn:
            sid = manual.create(
                conn, project=body.project, day=body.day, start_time=body.start_time, minutes=body.minutes,
                title=body.title, type=body.type, rating=body.rating, notes=body.notes,
                interruptions=body.interruptions, context_switches=body.context_switches)
    except manual.ManualError as e:
        raise HTTPException(422, str(e))
    return {"id": sid}


@app.delete("/api/sessions/{sid}")
def delete_manual(sid: str):
    if not sid.startswith("manual-"):
        raise HTTPException(400, "Imported sessions can only be hidden, not deleted (they would be re-imported).")
    with db.session() as conn:
        conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
    return {"ok": True}


class ProjectPatch(BaseModel):
    category: str | None = Field(None, pattern="^(work|personal)$")
    notes: str | None = Field(None, max_length=5000)


@app.patch("/api/projects/{name}")
def patch_project(name: str, body: ProjectPatch):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return {"ok": True}
    sets = ",".join(f"{k}=?" for k in fields)
    with db.session() as conn:
        cur = conn.execute(f"UPDATE projects SET {sets} WHERE name=?", [*fields.values(), name])
        if not cur.rowcount:
            raise HTTPException(404, "project not found")
    return {"ok": True}


@app.post("/api/sync")
def api_sync():
    return sync.sync_all()


# ───────────── static dashboard ─────────────

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
