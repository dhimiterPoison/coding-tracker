"""SQLite storage. One file, local only."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL DEFAULT 'claude',      -- 'claude' | 'manual'
  project TEXT NOT NULL,
  cwd TEXT,
  branch TEXT,
  title TEXT,
  day TEXT NOT NULL,                          -- local date of start, YYYY-MM-DD
  started_at TEXT NOT NULL,                   -- ISO-8601 UTC
  ended_at TEXT NOT NULL,
  wall_s INTEGER DEFAULT 0,
  active_s INTEGER DEFAULT 0,
  flow_s INTEGER DEFAULT 0,
  longest_block_s INTEGER DEFAULT 0,
  blocks_json TEXT DEFAULT '[]',              -- [[startIso, endIso], ...] active blocks
  idle_gaps INTEGER DEFAULT 0,
  prompts INTEGER DEFAULT 0,
  interruptions INTEGER DEFAULT 0,            -- user pressed Esc / interrupted
  rejections INTEGER DEFAULT 0,               -- user declined a tool call
  errors INTEGER DEFAULT 0,                   -- tool calls that errored
  tool_calls INTEGER DEFAULT 0,
  edits INTEGER DEFAULT 0,
  tests_run INTEGER DEFAULT 0,
  area_switches INTEGER DEFAULT 0,            -- jumps between code areas while editing
  topic_shifts INTEGER DEFAULT 0,             -- prompts unrelated to the previous one
  commits_json TEXT DEFAULT '[]',
  files_json TEXT DEFAULT '[]',
  tool_counts_json TEXT DEFAULT '{}',
  prompts_json TEXT DEFAULT '[]',             -- [{t, text}] truncated
  last_prompt TEXT,
  last_assistant TEXT,
  tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0,
  models TEXT DEFAULT '',
  work_type TEXT DEFAULT 'other',
  work_scores_json TEXT DEFAULT '{}',
  -- user-owned fields: never overwritten by re-import
  type_override TEXT,
  rating INTEGER,                             -- self-rated focus 1..5
  notes TEXT,
  hidden INTEGER DEFAULT 0,
  src_path TEXT,
  src_size INTEGER,
  src_mtime REAL,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions(day);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);

CREATE TABLE IF NOT EXISTS projects (
  name TEXT PRIMARY KEY,
  category TEXT NOT NULL DEFAULT 'personal',  -- 'work' | 'personal'
  path TEXT,
  notes TEXT
);
"""

# Columns written by the importer; the rest are user-owned and survive re-imports.
PARSED_COLUMNS = [
    "source", "project", "cwd", "branch", "title", "day", "started_at", "ended_at",
    "wall_s", "active_s", "flow_s", "longest_block_s", "blocks_json", "idle_gaps",
    "prompts", "interruptions", "rejections", "errors", "tool_calls", "edits",
    "tests_run", "area_switches", "topic_shifts", "commits_json", "files_json",
    "tool_counts_json", "prompts_json", "last_prompt", "last_assistant",
    "tokens_in", "tokens_out", "models", "work_type", "work_scores_json",
    "src_path", "src_size", "src_mtime", "updated_at",
]


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_parsed(conn: sqlite3.Connection, sid: str, row: dict) -> None:
    cols = ["id"] + PARSED_COLUMNS
    vals = [sid] + [row.get(c) for c in PARSED_COLUMNS]
    placeholders = ",".join("?" * len(cols))
    updates = ",".join(f"{c}=excluded.{c}" for c in PARSED_COLUMNS)
    conn.execute(
        f"INSERT INTO sessions ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        vals,
    )


def ensure_project(conn: sqlite3.Connection, name: str, path: str | None, default_cat: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO projects (name, category, path) VALUES (?,?,?)",
        (name, default_cat, path),
    )
