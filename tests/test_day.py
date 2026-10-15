import json

from tests.conftest import make_transcript
from tests.test_accuracy import _local_day, _repo


def _day_review(day=None):
    from tracker import day as day_mod, db, sync
    sync.sync_all()
    with db.session() as conn:
        return day_mod.review(conn, day or _local_day(0))


def _busy_day(home):
    one, two = _repo(home, "alpha"), _repo(home, "beta")
    make_transcript(home / "claude" / "p" / "a.jsonl", one, [
        (0, "prompt", "Add pagination to the users endpoint and update the tests"),
        (10, "prompt", "Also add sorting to the users endpoint response"),
        (12, "tool", ("Edit", {"file_path": one + "/api/users.py"})),
        (14, "tool", ("Bash", {"command": 'git commit -m "Add pagination"'})),
        (14, "result", ("t4", "ok", False)),
        (40, "prompt", "Add the filter parameter for the users endpoint list"),
        (45, "interrupt", None), (46, "interrupt", None), (46, "interrupt", None),
        (48, "assistant", "ok"),
    ], session_id="a")
    make_transcript(home / "claude" / "p" / "b.jsonl", two, [
        (25, "prompt", "Fix the typo in the pricing page headline"),
        (27, "assistant", "ok"),
    ], session_id="b")


def test_empty_day(home):
    r = _day_review("2026-01-01")
    assert r["empty"] and "No sessions" in r["markdown"]


def test_review_builds_stretches_and_what_ended_them(home):
    _busy_day(home)
    r = _day_review()
    assert not r["empty"]
    assert [s["project"] for s in r["stretches"]] == ["alpha", "beta", "alpha"]
    assert r["stretches"][0]["ended_by"] == "switch to beta"
    assert r["stretches"][1]["ended_by"] == "switch to alpha"
    assert r["stretches"][0]["prompts"] == 2
    assert r["day_stats"]["switches"] == 2
    assert [c["msg"] for c in r["commits"]] == ["Add pagination"]


def test_review_flags_interruption_burst_and_renders_markdown(home):
    _busy_day(home)
    r = _day_review()
    assert r["interruption_bursts"] and r["interruption_bursts"][0]["count"] == 3
    md = r["markdown"]
    for heading in ("## At a glance", "## Timeline", "## Where flow broke", "## Shipped",
                    "## Where you left off", "## Coach"):
        assert heading in md
    assert any("interruptions in a row" in f["text"] for f in r["findings"])
    json.dumps({k: v for k, v in r.items() if k != "markdown"}, default=str)   # --json must serialise


def test_cli_day_runs(home, capsys):
    _busy_day(home)
    from tracker import cli
    assert cli.main(["day", "--date", _local_day(0)]) == 0
    assert "# Day review" in capsys.readouterr().out
    assert cli.main(["day", "--date", "nope"]) == 2
