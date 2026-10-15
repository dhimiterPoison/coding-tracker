"""Goals: what you are working toward, checked against what the diary can actually see.

A goal is a *practice* (write tests, protect a focus block, leave a next step), not an outcome.
Most practices can be checked automatically from your sessions; the rest are self-reported when you
close the day. Everything is judged per day, then summed over a week against your own target, so a
quiet day or a day off simply does not count (status "na") instead of counting against you.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import config, db, insights
from . import day as day_review
from .insights import _h

STATUSES = ("met", "partly", "missed")
MIN_CLOCK_S = 30 * 60        # less than this on the clock: not enough of a day to judge
TESTS_SHARE = 0.6            # share of substantial sessions that ran tests
PLAN_SHARE = 0.10            # share of session time spent exploring / planning / reviewing
COMMITS_MIN = 3

METRICS = {
    "manual": "You report it when you close the day",
    "focus_block": "A deep-focus stretch on one project",
    "tests": "Tests ran in substantial sessions",
    "small_commits": "Work committed in small pieces (3+ commits)",
    "plan_first": "10%+ of time spent exploring, planning or reviewing",
    "low_switches": "Few project switches",
    "next_step": "A next step written for every project you touched",
}

TEMPLATES = [
    {"title": "Protect a deep-focus block", "metric": "focus_block", "target_per_week": 3,
     "why": "Hard problems need an uninterrupted stretch. Senior work is mostly the hard problems."},
    {"title": "Tests with every change", "metric": "tests", "target_per_week": 4,
     "why": "Validate before calling it done; it is the cheapest way to be the colleague nobody has to double-check."},
    {"title": "Ship in small commits", "metric": "small_commits", "target_per_week": 4,
     "why": "Small steps are easier to review, revert and explain in refinement."},
    {"title": "Plan before building", "metric": "plan_first", "target_per_week": 2,
     "why": "Time spent understanding and scoping first prevents the mid-flight corrections later."},
    {"title": "Keep project switching low", "metric": "low_switches", "target_per_week": 3,
     "why": "Every switch costs re-load time; fewer, longer visits to each repo."},
    {"title": "Leave a next step before stopping", "metric": "next_step", "target_per_week": 4,
     "why": "Tomorrow-you starts in minutes instead of reconstructing context across many repos."},
    {"title": "Write down a decision and why", "metric": "manual", "target_per_week": 2,
     "why": "Seniority is largely judgement you can explain. Notes make it visible."},
    {"title": "Review or unblock a teammate", "metric": "manual", "target_per_week": 2,
     "why": "Reliability as a colleague: your work multiplies when it helps others move."},
]

_HINTS = {
    "focus_block": "Block 45 minutes tomorrow, one repo, notifications off.",
    "tests": "Ask for a test run (or a failing test first) before calling the work done.",
    "small_commits": "Commit after each working step, not at the end.",
    "plan_first": "Spend the first 10 minutes writing the goal and acceptance criteria.",
    "low_switches": "Pick one repo for the next block and park the others in a note.",
    "next_step": "Write one line per repo before you close the laptop.",
}


class GoalError(ValueError):
    pass


# ───────────────────────────── CRUD ─────────────────────────────

def list_goals(conn: sqlite3.Connection, active_only: bool = True) -> list[dict]:
    q = "SELECT * FROM goals" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
    return [dict(r) for r in conn.execute(q)]


def create(conn: sqlite3.Connection, title: str, why: str = "", metric: str = "manual",
           target_per_week: int = 3) -> int:
    title = (title or "").strip()
    if not title:
        raise GoalError("a goal needs a title")
    if metric not in METRICS:
        raise GoalError(f"unknown metric '{metric}' (choose from {', '.join(METRICS)})")
    if not 1 <= int(target_per_week) <= 7:
        raise GoalError("target per week must be between 1 and 7")
    cur = conn.execute(
        "INSERT INTO goals (title, why, metric, target_per_week, created_at) VALUES (?,?,?,?,?)",
        (title[:200], (why or "").strip()[:1000], metric, int(target_per_week),
         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
    return cur.lastrowid


def update(conn: sqlite3.Connection, gid: int, **fields) -> None:
    allowed = {"title", "why", "target_per_week", "active"}
    fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "target_per_week" in fields and not 1 <= int(fields["target_per_week"]) <= 7:
        raise GoalError("target per week must be between 1 and 7")
    if "title" in fields and not str(fields["title"]).strip():
        raise GoalError("a goal needs a title")
    if "active" in fields:
        fields["active"] = int(bool(fields["active"]))
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    cur = conn.execute(f"UPDATE goals SET {sets} WHERE id=?", [*fields.values(), gid])
    if not cur.rowcount:
        raise GoalError("goal not found")


def delete(conn: sqlite3.Connection, gid: int) -> None:
    conn.execute("DELETE FROM goals WHERE id=?", (gid,))


# ───────────────────────────── evaluation ─────────────────────────────

def _facts(conn: sqlite3.Connection, day: str) -> dict:
    cfg = config.load()
    sessions = insights.load_sessions(conn, day, day)
    summ = insights.summarize(sessions)
    d = next((x for x in summ["by_day"] if x["day"] == day), None)
    stretches = day_review.build_stretches(sessions, cfg["idle_gap_minutes"] * 60)
    flow_s = cfg["flow_block_minutes"] * 60
    claude = [s for s in sessions if s["source"] == "claude"]
    tot = sum(summ["by_type"].values()) or 1
    think = sum(summ["by_type"].get(k, 0) for k in ("exploration", "planning", "review"))
    return {
        "sessions": sessions, "totals": summ["totals"], "day": d,
        "clock_s": d["clock_s"] if d else 0,
        "stretches": stretches, "focus": [s for s in stretches if s["seconds"] >= flow_s],
        "meaty": [s for s in claude if s["edits"] >= 5],
        "commits": sum(len(s["commits"]) for s in sessions),
        "edits": sum(s["edits"] for s in sessions),
        "think_share": think / tot,
        "projects": sorted(p["project"] for p in summ["by_project"] if p["active_s"] > 0),
        "calm": cfg["calm_switches_per_day"], "switchy": cfg["switchy_switches_per_day"],
        "flow_min": cfg["flow_block_minutes"],
    }


def _result(status: str, detail: str, auto: bool = True) -> dict:
    return {"status": status, "detail": detail, "auto": auto}


def _evaluate(goal: dict, f: dict, closed: dict | None) -> dict:
    m = goal["metric"]
    if m == "manual":
        a = ((closed or {}).get("goal_answers") or {}).get(str(goal["id"]))
        if a and a.get("status") in STATUSES:
            return _result(a["status"], a.get("note", ""), auto=False)
        return _result("na", "Answer when you close the day", auto=False)
    if m == "next_step":
        if not closed:
            return _result("na", "Close the day to check this")
        steps = {k: v for k, v in (closed.get("next_steps") or {}).items() if str(v).strip()}
        missing = [p for p in f["projects"] if p not in steps]
        if not f["projects"]:
            return _result("na", "No projects today")
        if not missing:
            return _result("met", f"Next step written for {len(f['projects'])} project(s)")
        status = "partly" if steps else "missed"
        return _result(status, "No next step for " + ", ".join(missing[:3]))
    if f["clock_s"] < MIN_CLOCK_S:
        return _result("na", "Under 30 minutes of activity")
    if m == "focus_block":
        if f["focus"]:
            best = max(f["focus"], key=lambda s: s["seconds"])
            return _result("met", f"{_h(best['seconds'])} on {best['project']} at {day_review._hm(best['start'])}")
        longest = max((s["seconds"] for s in f["stretches"]), default=0)
        return _result("missed", f"No stretch reached {f['flow_min']} min (longest {_h(longest)})")
    if m == "tests":
        if not f["meaty"]:
            return _result("na", "No substantial coding sessions")
        n = sum(1 for s in f["meaty"] if s["tests_run"])
        ok = n / len(f["meaty"]) >= TESTS_SHARE
        return _result("met" if ok else "missed", f"Tests ran in {n} of {len(f['meaty'])} substantial sessions")
    if m == "small_commits":
        if f["edits"] < 5:
            return _result("na", "Hardly any edits")
        c = f["commits"]
        return _result("met" if c >= COMMITS_MIN else "missed", f"{c} commit(s)")
    if m == "plan_first":
        if f["totals"]["active_s"] < 3600:
            return _result("na", "Under an hour of session time")
        share = round(f["think_share"] * 100)
        return _result("met" if f["think_share"] >= PLAN_SHARE else "missed",
                       f"{share}% of session time on exploring, planning or reviewing")
    if m == "low_switches":
        d = f["day"]
        if not d or d["prompts"] < 2:
            return _result("na", "Too few prompts")
        sw = d["switches"]
        status = "met" if sw <= f["calm"] else "partly" if sw < f["switchy"] else "missed"
        return _result(status, f"{sw} project switch(es)")
    return _result("na", "Unknown metric")


def evaluate_day(conn: sqlite3.Connection, day: str, closed: dict | None = None,
                 goals: list[dict] | None = None) -> dict[str, dict]:
    """{str(goal_id): {status: met|partly|missed|na, detail, auto}} for one local day."""
    goals = list_goals(conn) if goals is None else goals
    if not goals:
        return {}
    if closed is None:
        closed = db.get_closed_day(conn, day)
    f = _facts(conn, day)
    return {str(g["id"]): _evaluate(g, f, closed) for g in goals}


def _days(end: str, window: int) -> list[str]:
    e = date.fromisoformat(end)
    return [(e - timedelta(days=i)).isoformat() for i in range(window - 1, -1, -1)]


def progress(conn: sqlite3.Connection, goals: list[dict], end: str, window: int = 7) -> list[dict]:
    """Each goal with how it went over the `window` days ending `end`: dots (one per judged day), met count, target."""
    if not goals:
        return []
    per_day = {d: evaluate_day(conn, d, goals=goals) for d in _days(end, window)}
    out = []
    for g in goals:
        dots = [{"day": d, "status": r[str(g["id"])]["status"]} for d, r in per_day.items()
                if r[str(g["id"])]["status"] != "na"]
        out.append({**g, "window": window, "dots": dots,
                    "met": sum(1 for x in dots if x["status"] == "met"),
                    "partly": sum(1 for x in dots if x["status"] == "partly"),
                    "judged": len(dots),
                    "target": max(1, round(g["target_per_week"] * window / 7))})
    return out


# ───────────────────────────── coach hooks ─────────────────────────────

def day_findings(results: dict[str, dict], progress_by_id: dict[int, dict], goals: list[dict]) -> list[dict]:
    """Coach lines tied to your goals, for the day review."""
    out = []
    for g in goals:
        r = results.get(str(g["id"]))
        if not r or r["status"] == "na":
            continue
        p = progress_by_id.get(g["id"])
        week = f" ({p['met']} of target {p['target']} this week)" if p else ""
        line = f"Goal “{g['title']}”: {r['detail']}.{week}"
        if r["status"] == "met":
            out.append({"kind": "win", "text": line})
        else:
            hint = _HINTS.get(g["metric"], "")
            out.append({"kind": "tip", "text": f"{line} {hint}".strip()})
    return out


def period_lines(progress_list: list[dict]) -> list[dict]:
    """Coach lines for the overview: where each goal stands this week."""
    out = []
    for p in progress_list:
        if not p["judged"]:
            continue
        line = (f"Goal “{p['title']}”: met on {p['met']} of {p['judged']} judged day(s) this week "
                f"(target {p['target']}).")
        if p["met"] >= p["target"]:
            out.append({"kind": "win", "text": line})
        else:
            hint = _HINTS.get(p["metric"], "")
            out.append({"kind": "tip", "text": f"{line} {hint}".strip()})
    return out


def range_summary(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """Per goal over an arbitrary range (used by the retro): days met / judged."""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    return progress(conn, list_goals(conn), end, (e - s).days + 1)
