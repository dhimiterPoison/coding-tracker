"""The numbers have to survive real-life shapes: forks, sessions spanning days, parallel sessions."""
import sqlite3
from datetime import datetime, timedelta, timezone

from tests.conftest import make_transcript


def _repo(home, name):
    r = home / name
    (r / ".git").mkdir(parents=True, exist_ok=True)
    return str(r)


def _load(start, end):
    from tracker import db, insights
    with db.session() as conn:
        return insights.load_sessions(conn, start, end)


def _local_day(minutes):
    """Local calendar day of the fixture clock (conftest starts at 2026-10-12 09:00 UTC) plus N minutes."""
    from tracker import insights
    t = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)
    return insights._local(t.strftime("%Y-%m-%dT%H:%M:%SZ")).strftime("%Y-%m-%d")


def test_fork_history_is_counted_once(home):
    from tracker import sync
    repo = _repo(home, "app")
    shared = [(0, "prompt", "Add the export button to the report page", "u-1"),
              (2, "assistant", "Done, committed.", "u-2"),
              (4, "prompt", "Now wire up the csv download endpoint", "u-3"),
              (6, "assistant", "Wired.", "u-4")]
    make_transcript(home / "claude" / "p" / "a.jsonl", repo, shared, session_id="a")
    make_transcript(home / "claude" / "p" / "b.jsonl", repo,
                    shared + [(30, "prompt", "Fork: try a different layout for the toolbar", "b-5"),
                              (32, "assistant", "Tried.", "b-6")], session_id="b")
    sync.sync_all()
    rows = {s["id"]: s for s in _load("2026-10-01", "2026-10-31")}
    assert rows["a"]["prompts"] == 2
    assert rows["b"]["prompts"] == 1                      # only what the fork did itself
    assert sum(s["prompts"] for s in rows.values()) == 3  # not 5


def test_fork_imported_before_its_parent_is_fixed_up(home):
    """The later member was imported first (parent not on disk yet): importing the parent redoes it."""
    from tracker import sync
    repo = _repo(home, "app")
    shared = [(0, "prompt", "Investigate the slow dashboard query please", "u-1"), (3, "assistant", "ok", "u-2")]
    make_transcript(home / "claude" / "p" / "b.jsonl", repo,
                    shared + [(20, "prompt", "Fork: add an index on the table", "b-3")], session_id="b")
    sync.sync_all()
    assert sum(s["prompts"] for s in _load("2026-10-01", "2026-10-31")) == 2
    make_transcript(home / "claude" / "p" / "a.jsonl", repo, shared, session_id="a")
    sync.sync_all()
    assert sum(s["prompts"] for s in _load("2026-10-01", "2026-10-31")) == 2   # 1 + 1, not 1 + 2


def test_session_over_several_days_is_split_by_day(home):
    from tracker import db, insights, sync
    repo = _repo(home, "app")
    make_transcript(home / "claude" / "p" / "s.jsonl", repo, [
        (0, "prompt", "Start the migration of the billing tables now"),
        (5, "assistant", "Started."),
        (36 * 60, "prompt", "Continue the migration with the invoice tables"),
        (36 * 60 + 10, "assistant", "Continued."),
    ])
    sync.sync_all()
    d1, d2 = _local_day(0), _local_day(36 * 60)
    assert d1 != d2
    first, second = _load(d1, d1), _load(d2, d2)
    assert [s["active_s"] for s in first] == [5 * 60]
    assert [s["active_s"] for s in second] == [10 * 60]
    assert first[0]["prompts"] == 1 and second[0]["prompts"] == 1
    with db.session() as conn:
        both = insights.summarize(insights.load_sessions(conn, d1, d2))
    assert both["totals"]["active_s"] == 15 * 60
    assert {d["day"]: d["active_s"] for d in both["by_day"]} == {d1: 5 * 60, d2: 10 * 60}


def test_parallel_sessions_count_once_on_the_clock(home):
    from tracker import db, insights, sync
    make_transcript(home / "claude" / "p" / "a.jsonl", _repo(home, "one"), [
        (0, "prompt", "Refactor the settings module to use dataclasses"),
        (5, "assistant", "a"), (10, "assistant", "b"), (15, "assistant", "c"), (20, "assistant", "ok")], session_id="a")
    make_transcript(home / "claude" / "p" / "b.jsonl", _repo(home, "two"), [
        (10, "prompt", "Write the changelog entry for the release"),
        (15, "assistant", "a"), (20, "assistant", "b"), (25, "assistant", "c"), (30, "assistant", "ok")], session_id="b")
    sync.sync_all()
    day = _local_day(0)
    with db.session() as conn:
        T = insights.summarize(insights.load_sessions(conn, day, day))["totals"]
    assert T["active_s"] == 40 * 60       # 20 + 20 minutes of session time
    assert T["clock_s"] == 30 * 60        # 0-30 on the clock
    assert T["parallel_s"] == 10 * 60


def test_switches_follow_your_prompts_across_sessions(home):
    from tracker import db, insights, sync
    make_transcript(home / "claude" / "p" / "a.jsonl", _repo(home, "one"), [
        (0, "prompt", "Add pagination to the users endpoint"), (1, "assistant", "ok"),
        (8, "prompt", "Also add sorting to that users endpoint"), (9, "assistant", "ok")], session_id="a")
    make_transcript(home / "claude" / "p" / "b.jsonl", _repo(home, "two"), [
        (4, "prompt", "Fix the typo in the pricing page headline"), (5, "assistant", "ok")], session_id="b")
    sync.sync_all()
    day = _local_day(0)
    with db.session() as conn:
        T = insights.summarize(insights.load_sessions(conn, day, day))["totals"]
    assert T["project_switches"] == 2     # one -> two -> one


def test_topic_shift_ignores_follow_ups_and_short_replies(tmp_path):
    from tracker import parser
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    f = make_transcript(tmp_path / "t.jsonl", str(tmp_path / "repo"), [
        (0, "prompt", "Add pagination to the users endpoint and update the tests accordingly"),
        (2, "prompt", "push"),
        (3, "prompt", "Now the pagination needs a default page size in the endpoint config"),
        (5, "prompt", "yes do it"),
        (6, "prompt", "Totally different: rewrite the onboarding email copy for newsletter subscribers"),
    ])
    assert parser.parse_transcript(f)["topic_shifts"] == 1


def test_old_database_is_migrated(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("CODING_TRACKER_HOME", str(tmp_path / "home"))
    import tracker.config as config
    importlib.reload(config)
    db = importlib.reload(importlib.import_module("tracker.db"))
    config.ensure_dirs()
    old = sqlite3.connect(config.DB_PATH)
    old.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, project TEXT NOT NULL, day TEXT NOT NULL, "
                "started_at TEXT NOT NULL, ended_at TEXT NOT NULL)")
    old.commit()
    old.close()
    with db.session() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert {"prompt_times_json", "marks_json", "parser_version", "family"} <= cols
