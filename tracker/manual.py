"""Manual diary entries and session notes (shared by the dashboard and the CLI)."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from . import classify, config, db


class ManualError(ValueError):
    pass


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create(conn: sqlite3.Connection, *, project: str, day: str, start_time: str, minutes: int, title: str,
           type: str = "other", rating: int | None = None, notes: str = "", interruptions: int = 0,
           context_switches: int = 0) -> str:
    if type not in classify.WORK_TYPES:
        raise ManualError(f"unknown work type '{type}' (choose from {', '.join(classify.WORK_TYPES)})")
    try:
        start = datetime.fromisoformat(f"{day}T{start_time}").astimezone()
    except ValueError:
        raise ManualError("bad date or time (use YYYY-MM-DD and HH:MM)")
    cfg = config.load()
    secs = minutes * 60
    end = start + timedelta(minutes=minutes)
    row = {
        "source": "manual", "project": project.strip(), "cwd": "", "branch": "", "title": title.strip(),
        "day": day, "started_at": _iso(start), "ended_at": _iso(end), "wall_s": secs, "active_s": secs,
        "flow_s": secs if secs >= cfg["flow_block_minutes"] * 60 and interruptions == 0 else 0,
        "longest_block_s": secs, "blocks_json": json.dumps([[_iso(start), _iso(end)]]), "idle_gaps": 0,
        "prompts": 0, "interruptions": interruptions, "rejections": 0, "errors": 0, "tool_calls": 0, "edits": 0,
        "tests_run": 0, "area_switches": context_switches, "topic_shifts": 0, "commits_json": "[]",
        "files_json": "[]", "tool_counts_json": "{}", "prompts_json": "[]", "last_prompt": "",
        "last_assistant": "", "tokens_in": 0, "tokens_out": 0, "models": "", "work_type": type,
        "work_scores_json": "{}", "src_path": None, "src_size": None, "src_mtime": None,
        "updated_at": _iso(datetime.now(timezone.utc)),
    }
    sid = f"manual-{uuid.uuid4().hex[:12]}"
    db.ensure_project(conn, row["project"], None, cfg.get("default_category", "personal"))
    db.upsert_parsed(conn, sid, row)
    conn.execute("UPDATE sessions SET rating=?, notes=? WHERE id=?", (rating, notes, sid))
    return sid


def add_note(conn: sqlite3.Connection, cwd: str, text: str) -> dict | None:
    """Append a note to the most recent imported session whose working directory matches `cwd`."""
    norm = cwd.replace("\\", "/").rstrip("/").lower()
    rows = conn.execute(
        "SELECT id, title, notes, cwd FROM sessions WHERE source='claude' ORDER BY started_at DESC LIMIT 200").fetchall()
    for r in rows:
        c = (r["cwd"] or "").replace("\\", "/").rstrip("/").lower()
        if c and (c == norm or norm.startswith(c + "/") or c.startswith(norm + "/")):
            merged = (r["notes"] + "\n\n" if r["notes"] else "") + text.strip()
            conn.execute("UPDATE sessions SET notes=? WHERE id=?", (merged, r["id"]))
            return {"id": r["id"], "title": r["title"]}
    return None
