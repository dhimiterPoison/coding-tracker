"""SQLite storage. One file, local only."""
from __future__ import annotations

import json
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
  prompt_times_json TEXT DEFAULT '[]',        -- [iso, ...] every real user prompt (attention signal)
  marks_json TEXT DEFAULT '[]',               -- [{t, k: 'interrupt'|'reject'}] moments you stopped Claude
  parser_version INTEGER DEFAULT 0,           -- bumped when the parser changes; stale rows are re-parsed
  family TEXT,                                -- uuid of the first record: forks/resumes of one chat share it
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

CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  why TEXT NOT NULL DEFAULT '',
  metric TEXT NOT NULL DEFAULT 'manual',      -- 'manual' or a key of goals.METRICS (auto-tracked)
  target_per_week INTEGER NOT NULL DEFAULT 3,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

-- One row per day you closed: your reflection plus a frozen copy of the review as it stood then.
CREATE TABLE IF NOT EXISTS days (
  day TEXT PRIMARY KEY,                       -- YYYY-MM-DD (local)
  closed_at TEXT NOT NULL,
  feeling INTEGER,                            -- self-rated 1..5
  win TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  next_steps_json TEXT NOT NULL DEFAULT '{}', -- {project: first step for tomorrow}
  goal_answers_json TEXT NOT NULL DEFAULT '{}', -- {goal_id: {status, note}} for self-reported goals
  goal_results_json TEXT NOT NULL DEFAULT '{}', -- {goal_id: {status, detail}} for every goal, at close time
  narrative TEXT NOT NULL DEFAULT '',         -- optional written review (e.g. by Claude via /diary day)
  snapshot_json TEXT NOT NULL DEFAULT '{}'
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
    "prompt_times_json", "marks_json", "parser_version", "family",
    "src_path", "src_size", "src_mtime", "updated_at",
]

# Columns added after the first release: CREATE TABLE IF NOT EXISTS never alters an existing table.
_ADDED_COLUMNS = {
    "prompt_times_json": "TEXT DEFAULT '[]'",
    "marks_json": "TEXT DEFAULT '[]'",
    "parser_version": "INTEGER DEFAULT 0",
    "family": "TEXT",
}


def _migrate(conn: sqlite3.Connection) -> None:
    have = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    for col, decl in _ADDED_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {decl}")


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
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


_DAY_JSON = (("next_steps_json", "next_steps"), ("goal_answers_json", "goal_answers"),
             ("goal_results_json", "goal_results"), ("snapshot_json", "snapshot"))


def get_closed_day(conn: sqlite3.Connection, day: str) -> dict | None:
    """The closed-day record for a local date, JSON columns decoded, or None if it is still open."""
    row = conn.execute("SELECT * FROM days WHERE day=?", (day,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for col, name in _DAY_JSON:
        try:
            d[name] = json.loads(d.pop(col) or "{}") or {}
        except ValueError:
            d[name] = {}
    return d
