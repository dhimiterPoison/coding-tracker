"""Goals, closing the day and the next-morning card."""
import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_transcript

H = {"X-Tracker": "1"}
DAY = "2026-10-12"          # make_transcript starts sessions at 09:00 UTC on this date


def _busy_day(home, tests=True):
    """One long single-project session with edits, a test run and a commit, plus a short one on another project."""
    repo, other = home / "repo", home / "other"
    for r in (repo, other):
        (r / ".git").mkdir(parents=True)
    ev = [(0, "prompt", "Add a settings page for notifications")]
    for i in range(1, 7):
        ev.append((i * 5, "tool", ("Edit", {"file_path": str(repo / "src" / f"f{i}.py")})))
    if tests:
        ev.append((35, "tool", ("Bash", {"command": "pytest -q"})))
    ev.append((40, "tool", ("Bash", {"command": 'git commit -m "Add settings page"'})))
    ev.append((40, "result", (f"t{len(ev)}", "ok", False)))
    ev.append((45, "assistant", "Done."))
    make_transcript(home / "claude" / "a" / "a.jsonl", str(repo), ev, session_id="a")
    make_transcript(home / "claude" / "b" / "b.jsonl", str(other), [
        (50, "prompt", "Check why the other build is red"), (52, "assistant", "Looking.")], session_id="b")


@pytest.fixture()
def client(home):
    from tracker import server, sync
    _busy_day(home)
    sync.sync_all()
    with TestClient(server.app, base_url="http://127.0.0.1:8765") as c:
        yield c


def _goal(client, **kw):
    r = client.post("/api/goals", headers=H, json=kw)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_auto_goals_are_judged_from_the_day(client):
    focus = _goal(client, title="Focus", metric="focus_block", target_per_week=3)
    tests = _goal(client, title="Tests", metric="tests")
    commits = _goal(client, title="Commits", metric="small_commits")   # only 1 commit -> missed
    day = client.get(f"/api/day/{DAY}").json()
    by = {g["id"]: g for g in day["goals"]}
    assert by[focus]["status"] == "met"
    assert by[tests]["status"] == "met" and "1 of 1" in by[tests]["detail"]
    assert by[commits]["status"] == "missed"
    assert any(f["text"].startswith("Goal") for f in day["findings"])
    assert "## Goals" in day["markdown"]


def test_goal_validation(client):
    assert client.post("/api/goals", headers=H, json={"title": "x", "metric": "nope"}).status_code == 422
    assert client.post("/api/goals", headers=H, json={"title": "x", "target_per_week": 9}).status_code == 422
    gid = _goal(client, title="Old one")
    assert client.patch(f"/api/goals/{gid}", headers=H, json={"active": False}).status_code == 200
    assert all(g["id"] != gid or not g["active"] for g in client.get("/api/goals").json()["goals"])
    assert client.patch("/api/goals/999", headers=H, json={"title": "z"}).status_code == 404


def test_close_the_day_saves_reflection_and_a_frozen_review(client):
    manual_goal = _goal(client, title="Explain a decision", metric="manual")
    step_goal = _goal(client, title="Next step", metric="next_step")
    r = client.post(f"/api/day/{DAY}/close", headers=H, json={
        "feeling": 4, "win": "Settings page shipped", "notes": "good flow",
        "next_steps": {"repo": "Write the settings tests", "other": "Find the red build cause"},
        "goal_answers": {str(manual_goal): {"status": "partly", "note": "wrote half of it"}}})
    assert r.status_code == 200, r.text
    c = r.json()["closed"]
    assert c["feeling"] == 4 and c["win"] == "Settings page shipped" and not c["stale"]
    res = c["goal_results"]
    assert res[str(manual_goal)]["status"] == "partly"
    assert res[str(step_goal)]["status"] == "met"            # both projects got a step
    assert "## Your reflection" in r.json()["markdown"]
    days = {d["day"]: d for d in client.get(f"/api/days?start={DAY}&end={DAY}").json()}
    assert days[DAY]["closed"] and days[DAY]["feeling"] == 4


def test_closing_again_keeps_what_you_did_not_change(client):
    client.post(f"/api/day/{DAY}/close", headers=H, json={"feeling": 5, "win": "first", "next_steps": {"repo": "a"}})
    r = client.post(f"/api/day/{DAY}/close", headers=H, json={"notes": "later thought"}).json()["closed"]
    assert (r["feeling"], r["win"], r["notes"], r["next_steps"]) == (5, "first", "later thought", {"repo": "a"})


def test_cannot_close_an_empty_day(client):
    assert client.post("/api/day/2026-01-01/close", headers=H, json={"feeling": 3}).status_code == 422
    assert client.get("/api/day/not-a-date").status_code == 422


def test_missing_next_step_is_reported(client):
    gid = _goal(client, title="Next step", metric="next_step")
    r = client.post(f"/api/day/{DAY}/close", headers=H, json={"next_steps": {"repo": "x"}}).json()
    assert r["closed"]["goal_results"][str(gid)]["status"] == "partly"
    assert "other" in r["closed"]["goal_results"][str(gid)]["detail"]


def test_morning_card_uses_your_next_steps(client):
    client.post(f"/api/day/{DAY}/close", headers=H, json={"next_steps": {"repo": "Write the settings tests"}})
    card = client.get("/api/morning").json()
    assert card["source"] == "you"
    assert card["steps"][0]["project"] == "repo" and card["steps"][0]["text"] == "Write the settings tests"
    assert card["from_day"] == DAY


def test_morning_card_falls_back_to_where_you_stopped(home):
    from tracker import db, journal, sync
    _busy_day(home)
    sync.sync_all()
    with db.session() as conn:
        card = journal.morning_card(conn, today="2026-10-13")
    # No closed day. Recency of the fallback is measured against the real clock, so only assert the shape.
    assert card["source"] in ("last prompt", "none") and isinstance(card["steps"], list)


def test_goal_lines_reach_the_overview_coach(client):
    _goal(client, title="Focus", metric="focus_block", target_per_week=1)
    ov = client.get(f"/api/overview?start={DAY}&end={DAY}").json()
    assert any(c["text"].startswith("Goal") for c in ov["coach"])


def test_retro_includes_goals_and_wins(client):
    _goal(client, title="Focus", metric="focus_block")
    client.post(f"/api/day/{DAY}/close", headers=H, json={"win": "Shipped the settings page", "feeling": 4})
    md = client.get(f"/api/report/retro?start={DAY}&end={DAY}").json()["markdown"]
    assert "Goals" in md and "Shipped the settings page" in md and "felt" in md


def test_cli_close_and_goals(home, capsys):
    from tracker import cli, sync
    _busy_day(home)
    sync.sync_all()
    assert cli.main(["goals", "add", "Tests first", "--metric", "tests", "--target", "4"]) == 0
    assert cli.main(["goals"]) == 0
    assert "Tests first" in capsys.readouterr().out
    assert cli.main(["day", "--date", DAY, "--close", "--feeling", "4", "--win", "ok", "--next", "repo=do x"]) == 0
    out = capsys.readouterr().out
    assert "Your reflection" in out and "do x" in out
    assert cli.main(["goals", "add", "Bad", "--metric", "nope"]) == 2
    assert cli.main(["day", "--date", DAY, "--close", "--next", "no-equals-sign"]) == 2
