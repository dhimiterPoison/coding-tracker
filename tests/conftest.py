import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Point the whole app at a throwaway data folder and reload modules that cache paths."""
    monkeypatch.setenv("CODING_TRACKER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(tmp_path / "claude"))
    import tracker.config as config
    importlib.reload(config)
    for name in ("db", "sync", "insights", "reports", "server"):
        importlib.reload(importlib.import_module(f"tracker.{name}"))
    return tmp_path


def make_transcript(path, cwd, events, session_id="sess-1", title=None):
    """events: list of (minutes_from_start, kind, payload). kinds: prompt, assistant, tool, result, interrupt."""
    t0 = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
    lines = []
    if title:
        lines.append({"type": "custom-title", "customTitle": title, "sessionId": session_id})
    n = 0
    for minutes, kind, payload in events:
        ts = (t0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        base = {"timestamp": ts, "sessionId": session_id, "cwd": cwd, "gitBranch": "main", "isSidechain": False}
        n += 1
        if kind == "prompt":
            lines.append({**base, "type": "user", "message": {"role": "user", "content": payload}})
        elif kind == "interrupt":
            lines.append({**base, "type": "user", "message": {"role": "user", "content": [
                {"type": "text", "text": "[Request interrupted by user]"}]}})
        elif kind == "assistant":
            lines.append({**base, "type": "assistant", "message": {"role": "assistant", "model": "claude-x",
                          "content": [{"type": "text", "text": payload}], "usage": {"input_tokens": 10, "output_tokens": 5}}})
        elif kind == "tool":
            name, inp = payload
            lines.append({**base, "type": "assistant", "message": {"role": "assistant", "model": "claude-x",
                          "content": [{"type": "tool_use", "id": f"t{n}", "name": name, "input": inp}], "usage": {}}})
        elif kind == "result":
            tid, text, err = payload
            lines.append({**base, "type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": err}]}})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return path
