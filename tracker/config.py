"""Paths and tunables. Everything lives under one local folder; nothing leaves the machine."""
from __future__ import annotations

import json
import os
from pathlib import Path

HOME = Path(os.environ.get("CODING_TRACKER_HOME", Path.home() / ".coding-tracker"))
DATA_DIR = HOME / "data"
DB_PATH = DATA_DIR / "diary.db"
LOG_PATH = DATA_DIR / "tracker.log"
CONFIG_PATH = DATA_DIR / "config.json"

CLAUDE_PROJECTS_DIR = Path(
    os.environ.get("CLAUDE_PROJECTS_DIR", Path.home() / ".claude" / "projects")
)

DEFAULTS = {
    # A gap longer than this between two events ends an "active block" (reading, thinking
    # about Claude's output and typing a reply is still active; leaving the desk is not).
    "idle_gap_minutes": 10,
    # An uninterrupted active block at least this long counts as flow.
    "flow_block_minutes": 25,
    # Sessions with fewer real prompts than this are ignored (accidental opens).
    "min_prompts": 1,
    # Transcripts whose cwd contains any of these substrings are never imported.
    "ignore_cwd_contains": ["AppData\\Roaming\\Claude", "AppData/Roaming/Claude"],
    # Project name -> "work" | "personal". Unknown projects default to "personal"
    # unless they match a work_path_hint.
    "work_path_hints": [],
    "default_category": "personal",
    "sprint_length_days": 21,
    "refinement_length_days": 7,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
