import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_transcript

H = {"X-Tracker": "1"}


@pytest.fixture()
def client(home):
    from tracker import server, sync
    repo = home / "repo"
    (repo / ".git").mkdir(parents=True)
    make_transcript(home / "claude" / "p" / "s1.jsonl", str(repo), [
        (0, "prompt", "Add a settings page for notifications"),
        (1, "tool", ("Write", {"file_path": str(repo / "src" / "settings.tsx")})),
        (30, "assistant", "Added."),
        (31, "prompt", "Looks good, commit it"),
        (32, "tool", ("Bash", {"command": 'git commit -m "Add settings page"'})),
        (32, "result", ("t5", "ok", False)),
    ], session_id="s1")
    sync.sync_all()
    with TestClient(server.app, base_url="http://127.0.0.1:8765") as c:
        yield c


def test_sync_is_idempotent(home, client):
    from tracker import sync
    assert sync.sync_all()["imported"] == 0
    assert sync.sync_all(force=True)["imported"] == 1
    assert len(client.get("/api/sessions?start=2026-10-01&end=2026-10-31").json()) == 1


def test_overview_and_detail(client):
    ov = client.get("/api/overview?start=2026-10-12&end=2026-10-12").json()
    assert ov["summary"]["totals"]["sessions"] == 1
    assert ov["summary"]["totals"]["commits"] == 1
    sid = client.get("/api/sessions?start=2026-10-12&end=2026-10-12").json()[0]["id"]
    d = client.get(f"/api/sessions/{sid}").json()
    assert d["prompt_list"][0]["text"].startswith("Add a settings page")


def test_user_edits_survive_reimport(home, client):
    from tracker import sync
    sid = client.get("/api/sessions?start=2026-10-12&end=2026-10-12").json()[0]["id"]
    assert client.patch(f"/api/sessions/{sid}", json={"type_override": "refactor", "rating": 4, "notes": "n"}, headers=H).status_code == 200
    sync.sync_all(force=True)
    d = client.get(f"/api/sessions/{sid}").json()
    assert (d["type"], d["rating"], d["notes"]) == ("refactor", 4, "n")


def test_manual_session_roundtrip(client):
    r = client.post("/api/sessions", headers=H, json={"project": "side", "day": "2026-10-13", "start_time": "10:00",
                                                       "minutes": 90, "title": "Paired on auth", "type": "feature", "rating": 5})
    assert r.status_code == 200
    rows = client.get("/api/sessions?start=2026-10-13&end=2026-10-13").json()
    assert rows[0]["source"] == "manual" and rows[0]["active_s"] == 5400 and rows[0]["focus"] == 100
    assert client.delete(f"/api/sessions/{r.json()['id']}", headers=H).status_code == 200


def test_reports_render_even_when_empty(client):
    for kind in ("refinement", "retro"):
        r = client.get(f"/api/report/{kind}?start=2026-01-01&end=2026-01-07")
        assert r.status_code == 200 and r.json()["markdown"].startswith("# ")
    r = client.get("/api/report/refinement?start=2026-10-12&end=2026-10-12").json()
    assert "Add settings page" in r["markdown"]


def test_writes_need_header_and_host_is_checked(client):
    assert client.post("/api/sync").status_code == 403
    assert client.post("/api/sync", headers=H).status_code == 200
    assert client.get("/api/config", headers={"Host": "evil.example.com"}).status_code == 403
