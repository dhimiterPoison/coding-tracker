"""Turn one Claude Code transcript (.jsonl) into a diary entry.

Everything is derived from what Claude Code already records locally: timestamps, prompts,
tool calls, cwd/branch and token usage. Nothing is sent anywhere.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import classify, config

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob", "NotebookRead"}
_SKIP_PROMPT_PREFIXES = (
    "<local-command", "<system-reminder", "<task-notification", "<bash-", "Caveat:",
    "<user-prompt-submit-hook", "<ide_", "[Image", "This session is being continued",
)
# `git commit` as a command in its own right, not merely the text inside grep/echo arguments.
_GIT_COMMIT_RX = re.compile(r"(?:^|&&|\|\||;|\n|\()\s*git\s+(?:-\S+(?:\s+[^-\s]\S*)?\s+)*commit(?!\w)")
_COMMIT_MSG_RX =re.compile(r"""-m\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)')""", re.S)
_HEREDOC_RX = re.compile(r"""<<-?\s*['"]?(\w+)['"]?\s*\n(.*?)\n\s*\1""", re.S)
_WORKTREE_RX = re.compile(r"[\\/]\.claude[\\/]worktrees[\\/][^\\/]+.*$", re.I)
_STOPWORDS = set(
    "the a an and or to of in on for with is it this that i we you be do can as at by from "
    "please just now then also make let lets let's want need should would could not no yes "
    "ok okay so if but my our your its".split())


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def project_for_cwd(cwd: str) -> tuple[str, str]:
    """Map a working directory to (project name, project root path).

    Claude Code worktrees live under <repo>/.claude/worktrees/<name>; they belong to <repo>.
    Otherwise walk up to the nearest git root, falling back to the directory itself.
    """
    if not cwd:
        return "unknown", ""
    base = _WORKTREE_RX.sub("", cwd) or cwd
    p = Path(base)
    try:
        for cand in [p, *p.parents]:
            if (cand / ".git").exists():
                return cand.name, str(cand)
    except OSError:
        pass
    return (p.name or str(p)), str(p)


def _commit_message(cmd: str) -> str:
    """Best-effort first line of the commit message from a `git commit` command line."""
    m = _HEREDOC_RX.search(cmd)
    if m and re.search(r"\s(?:-\w*[mF]|--message|--file)\b", cmd[: m.start()]):
        first = m.group(2).strip().splitlines()[0] if m.group(2).strip() else ""
    else:
        mm = _COMMIT_MSG_RX.search(cmd)
        first = ((mm.group(1) or mm.group(2) or "") if mm else "").strip().splitlines()[0:1]
        first = first[0] if first else ""
    return _clip(first, 140) or "(commit)"


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
    return "\n".join(parts)


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text.lower()) if w not in _STOPWORDS}


def _area(path: str, cwd: str) -> str:
    p = path.replace("\\", "/")
    c = (cwd or "").replace("\\", "/").rstrip("/")
    if c and p.lower().startswith(c.lower() + "/"):
        p = p[len(c) + 1:]
    parts = [x for x in p.split("/") if x]
    return "/".join(parts[:2]) if len(parts) > 2 else "/".join(parts[:-1]) or "."


def _rel(path: str, cwd: str) -> str:
    p = path.replace("\\", "/")
    c = (cwd or "").replace("\\", "/").rstrip("/")
    if c and p.lower().startswith(c.lower() + "/"):
        return p[len(c) + 1:]
    return p


def parse_transcript(path: Path, cfg: dict | None = None) -> dict | None:
    """Parse a transcript file into a row dict (with 'id' and 'project_path'), or None if not worth keeping."""
    cfg = cfg or config.load()
    idle = cfg["idle_gap_minutes"] * 60
    flow_min = cfg["flow_block_minutes"] * 60

    events: list[datetime] = []
    prompts: list[tuple[datetime, str]] = []
    interruptions = rejections = errors = tool_calls = edits = tests_run = 0
    tool_counts: dict[str, int] = {}
    files: dict[str, int] = {}
    edit_areas: list[str] = []
    commits: list[dict] = []
    models: dict[str, int] = {}
    tokens_in = tokens_out = 0
    cwd = branch = session_id = title = None
    last_assistant = ""
    pending_commits: dict[str, str] = {}   # tool_use_id -> commit message

    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t = r.get("type")
            if t in ("custom-title", "ai-title", "agent-name"):
                title = r.get("customTitle") or r.get("aiTitle") or r.get("agentName") or title
                continue
            if t not in ("user", "assistant") or "timestamp" not in r:
                continue
            try:
                ts = _ts(r["timestamp"])
            except ValueError:
                continue
            events.append(ts)
            side = bool(r.get("isSidechain"))
            session_id = session_id or r.get("sessionId")
            if not side:
                cwd = cwd or r.get("cwd")
                branch = r.get("gitBranch") or branch
            msg = r.get("message") or {}
            content = msg.get("content")

            if t == "assistant":
                u = msg.get("usage") or {}
                tokens_in += (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
                tokens_out += u.get("output_tokens") or 0
                if msg.get("model") and not str(msg["model"]).startswith("<"):
                    models[msg["model"]] = models.get(msg["model"], 0) + 1
                if not isinstance(content, list):
                    continue
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and not side and b.get("text", "").strip():
                        last_assistant = b["text"]
                    elif b.get("type") == "tool_use":
                        name = b.get("name", "?")
                        inp = b.get("input") or {}
                        tool_calls += 1
                        tool_counts[name] = tool_counts.get(name, 0) + 1
                        fp = inp.get("file_path") or inp.get("notebook_path")
                        if name in EDIT_TOOLS and fp:
                            edits += 1
                            rel = _rel(fp, cwd or "")
                            files[rel] = files.get(rel, 0) + 1
                            edit_areas.append(_area(fp, cwd or ""))
                        elif name == "Bash":
                            cmd = inp.get("command", "")
                            # Text after a heredoc marker is data (often a script editing docs), not a command.
                            if _GIT_COMMIT_RX.search(cmd.split("<<", 1)[0]) and "--dry-run" not in cmd:
                                pending_commits[b.get("id", "")] = _commit_message(cmd)
                            if classify.is_test_command(cmd):
                                tests_run += 1
                continue

            # ---- user records ----
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        body = b.get("content")
                        text = body if isinstance(body, str) else _text_of(body)
                        if b.get("is_error"):
                            if "doesn't want to proceed" in text or "tool use was rejected" in text:
                                rejections += 1
                            else:
                                errors += 1
                        msg_c = pending_commits.pop(b.get("tool_use_id", ""), None)
                        if (msg_c is not None and not b.get("is_error")
                                and "nothing to commit" not in text and "no changes added" not in text):
                            commits.append({"msg": msg_c, "t": _iso(ts)})
            if side or r.get("isMeta") or r.get("isCompactSummary"):
                continue
            text = _text_of(content).strip()
            if not text:
                continue
            if text.startswith("[Request interrupted by user"):
                interruptions += 1
                continue
            if text.startswith("<command-name>"):
                m = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
                a = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
                text = ((m.group(1) if m else "") + " " + (a.group(1) if a else "")).strip()
                if not text:
                    continue
            elif text.startswith(_SKIP_PROMPT_PREFIXES):
                continue
            prompts.append((ts, text))

    if len(prompts) < cfg["min_prompts"] or not events:
        return None
    cwd = cwd or ""
    if any(s in cwd for s in cfg["ignore_cwd_contains"]):
        return None

    events.sort()
    started, ended = events[0], events[-1]

    # ---- active blocks & flow ----
    blocks: list[list[datetime]] = []
    idle_gaps = 0
    cur = [events[0], events[0]]
    for prev, nxt in zip(events, events[1:]):
        if (nxt - prev).total_seconds() > idle:
            idle_gaps += 1
            blocks.append(cur)
            cur = [nxt, nxt]
        else:
            cur[1] = nxt
    blocks.append(cur)
    active_s = int(sum((b[1] - b[0]).total_seconds() for b in blocks))
    flow_s = int(sum((b[1] - b[0]).total_seconds() for b in blocks if (b[1] - b[0]).total_seconds() >= flow_min))
    longest = int(max((b[1] - b[0]).total_seconds() for b in blocks))

    # ---- context switching signals ----
    area_switches = sum(1 for a, b in zip(edit_areas, edit_areas[1:]) if a != b)
    topic_shifts = 0
    prev_kw: set[str] = set()
    for _, text in prompts:
        kw = _keywords(text)
        if prev_kw and len(kw) >= 4 and len(prev_kw) >= 4:
            overlap = len(kw & prev_kw) / len(kw | prev_kw)
            if overlap < 0.05:
                topic_shifts += 1
        if len(kw) >= 4:
            prev_kw = kw

    prompt_texts = [p[1] for p in prompts]
    top_files = [f for f, _ in sorted(files.items(), key=lambda kv: -kv[1])][:40]
    work_type, scores = classify.classify(
        prompt_texts, top_files, edits, {"tests_run": tests_run, **tool_counts})

    project, root = project_for_cwd(cwd)
    first = _clip(prompt_texts[0], 100)
    sid = session_id or path.stem
    st = path.stat()
    return {
        "id": sid,
        "source": "claude",
        "project": project,
        "project_path": root,
        "cwd": cwd,
        "branch": branch or "",
        "title": _clip(title, 100) if title else first,
        "day": started.astimezone().strftime("%Y-%m-%d"),
        "started_at": _iso(started),
        "ended_at": _iso(ended),
        "wall_s": int((ended - started).total_seconds()),
        "active_s": active_s,
        "flow_s": flow_s,
        "longest_block_s": longest,
        "blocks_json": json.dumps([[_iso(a), _iso(b)] for a, b in blocks]),
        "idle_gaps": idle_gaps,
        "prompts": len(prompts),
        "interruptions": interruptions,
        "rejections": rejections,
        "errors": errors,
        "tool_calls": tool_calls,
        "edits": edits,
        "tests_run": tests_run,
        "area_switches": area_switches,
        "topic_shifts": topic_shifts,
        "commits_json": json.dumps(commits),
        "files_json": json.dumps(top_files),
        "tool_counts_json": json.dumps(tool_counts),
        "prompts_json": json.dumps([{"t": _iso(ts), "text": _clip(tx, 280)} for ts, tx in prompts[:60]]),
        "last_prompt": _clip(prompt_texts[-1], 400),
        "last_assistant": _clip(last_assistant, 500),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "models": ",".join(sorted(models, key=models.get, reverse=True)[:3]),
        "work_type": work_type,
        "work_scores_json": json.dumps(scores),
        "src_path": str(path),
        "src_size": st.st_size,
        "src_mtime": st.st_mtime,
        "updated_at": _iso(datetime.now(timezone.utc)),
    }
