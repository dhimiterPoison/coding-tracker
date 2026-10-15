from tracker import parser
from tests.conftest import make_transcript


def _session(tmp_path):
    return make_transcript(
        tmp_path / "p" / "s.jsonl", str(tmp_path / "repo"),
        [
            (0, "prompt", "Fix the login bug where the token expires early"),
            (1, "tool", ("Edit", {"file_path": str(tmp_path / "repo" / "src" / "auth.py")})),
            (2, "tool", ("Bash", {"command": "pytest -q"})),
            (3, "prompt", "[ignored later]"),
            (4, "interrupt", None),
            (5, "tool", ("Bash", {"command": 'git add -A && git commit -m "Fix token expiry"'})),
            (5, "result", ("t6", "[main abc1234] Fix token expiry", False)),
            (6, "tool", ("Bash", {"command": 'grep -rn "git commit" docs'})),
            (6, "result", ("t8", "docs/x.md: use git commit", False)),
            (7, "assistant", "Fixed and committed. Next: add a regression test."),
            (40, "prompt", "Now add a regression test for the refresh path"),  # after a 33 min idle gap
            (45, "assistant", "Done."),
        ],
        title="Token expiry fix",
    )


def test_parse_basics(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    row = parser.parse_transcript(_session(tmp_path))
    assert row["project"] == "repo"
    assert row["title"] == "Token expiry fix"
    assert row["prompts"] == 3            # includes the "[ignored later]" text prompt, excludes the interrupt
    assert row["interruptions"] == 1
    assert row["edits"] == 1
    assert row["tests_run"] == 1
    assert row["idle_gaps"] == 1          # the 33 minute silence
    assert [c["msg"] for c in __import__("json").loads(row["commits_json"])] == ["Fix token expiry"]
    assert row["work_type"] == "bugfix"
    assert row["last_assistant"] == "Done."


def test_active_time_excludes_idle_gap(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    row = parser.parse_transcript(_session(tmp_path))
    assert row["wall_s"] == 45 * 60
    assert row["active_s"] == (7 + 5) * 60   # 0-7 min and 40-45 min; the 33 min gap is not counted


def test_worktree_maps_to_parent_repo(tmp_path):
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)
    wt = repo / ".claude" / "worktrees" / "feature-x"
    wt.mkdir(parents=True)
    assert parser.project_for_cwd(str(wt))[0] == "myrepo"


def test_empty_or_toolonly_sessions_are_skipped(tmp_path):
    f = make_transcript(tmp_path / "e.jsonl", str(tmp_path), [(0, "tool", ("Read", {"file_path": "x"}))])
    assert parser.parse_transcript(f) is None


def test_ignored_cwd(tmp_path):
    f = make_transcript(tmp_path / "a.jsonl", "C:\\Users\\x\\AppData\\Roaming\\Claude\\scratch", [(0, "prompt", "hi there")])
    assert parser.parse_transcript(f) is None
