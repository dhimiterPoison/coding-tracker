"""Closing the day: a saved entry per day (your reflection + a frozen copy of the review), the
next-morning "pick up where you left off" card, and the day page that ties in your goals."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import db, goals, insights
from . import day as day_review
from .insights import _h

MAX_STEP = 500


class JournalError(ValueError):
    pass


def _check_day(day: str) -> str:
    try:
        return date.fromisoformat(day).isoformat()
    except (TypeError, ValueError):
        raise JournalError("day must be YYYY-MM-DD")


# ───────────────────────────── the day page ─────────────────────────────

def full_review(conn: sqlite3.Connection, day: str | None = None, category: str | None = None) -> dict:
    """day.review() plus your goals, the closed-day record and goal-aware coach lines."""
    day = _check_day(day or date.today().isoformat())
    r = day_review.review(conn, day, category)
    closed = db.get_closed_day(conn, day)
    active = goals.list_goals(conn)
    r["goals"] = []
    r["closed"] = None
    if closed:
        snap = closed.get("snapshot") or {}
        was = (snap.get("totals") or {}).get("clock_s", 0)
        now = (r.get("totals") or {}).get("clock_s", 0)
        closed["stale"] = abs(now - was) > 300   # sessions changed after you closed the day
        closed.pop("snapshot", None)
        r["closed"] = closed
    if r["empty"]:
        return r

    results = goals.evaluate_day(conn, day, closed=closed, goals=active)
    prog = {p["id"]: p for p in goals.progress(conn, active, day, 7)}
    r["goals"] = [{
        "id": g["id"], "title": g["title"], "why": g["why"], "metric": g["metric"],
        "metric_label": goals.METRICS[g["metric"]], "target_per_week": g["target_per_week"],
        **results[str(g["id"])],
        "week": {k: prog[g["id"]][k] for k in ("met", "partly", "judged", "target", "dots")},
    } for g in active]
    order = {"watch": 0, "tip": 1, "win": 2}
    r["findings"] = sorted(goals.day_findings(results, prog, active) + r["findings"],
                           key=lambda x: order[x["kind"]])[:10]
    md = day_review._markdown(r)
    extra = _goals_md(r) + _reflection_md(r)
    head, sep, tail = md.partition("\n## Coach\n")
    r["markdown"] = head.rstrip("\n") + "\n" + extra + (sep + tail if sep else "")
    return r


_MARK = {"met": "met", "partly": "partly", "missed": "not today", "na": "n/a"}


def _goals_md(r: dict) -> str:
    if not r["goals"]:
        return ""
    lines = ["", "## Goals", ""]
    for g in r["goals"]:
        w = g["week"]
        lines.append(f"- {g['title']}: {_MARK[g['status']]}" + (f" ({g['detail']})" if g["detail"] else "")
                     + f" · week: {w['met']}/{w['target']}")
    return "\n".join(lines) + "\n"


def _reflection_md(r: dict) -> str:
    c = r.get("closed")
    if not c:
        return ""
    lines = ["", "## Your reflection", ""]
    if c.get("feeling"):
        stars = "★" * c["feeling"] + "☆" * (5 - c["feeling"])
        lines.append(f"- Felt: {stars}")
    if c.get("win"):
        lines.append(f"- Win: {c['win']}")
    for proj, step in (c.get("next_steps") or {}).items():
        if str(step).strip():
            lines.append(f"- Next step [{proj}]: {step}")
    if c.get("notes"):
        lines.append(f"- Notes: {c['notes']}")
    if c.get("narrative"):
        lines += ["", c["narrative"].strip()]
    return "\n".join(lines) + "\n"


# ───────────────────────────── closing the day ─────────────────────────────

def close_day(conn: sqlite3.Connection, day: str, *, feeling: int | None = None, win: str | None = None,
              notes: str | None = None, next_steps: dict | None = None, goal_answers: dict | None = None,
              narrative: str | None = None, category: str | None = None) -> dict:
    """Save (or update) the entry for a day. Fields you leave out keep their previous value."""
    day = _check_day(day)
    old = db.get_closed_day(conn, day) or {}
    review = day_review.review(conn, day, category)
    if review["empty"]:
        raise JournalError("No sessions recorded for this day, nothing to close.")
    if feeling is not None and not 1 <= int(feeling) <= 5:
        raise JournalError("feeling must be 1 to 5")

    steps = dict(old.get("next_steps") or {})
    for proj, text in (next_steps or {}).items():
        text = (text or "").strip()[:MAX_STEP]
        if text:
            steps[proj] = text
        else:
            steps.pop(proj, None)
    answers = dict(old.get("goal_answers") or {})
    for gid, a in (goal_answers or {}).items():
        if a.get("status") not in goals.STATUSES:
            raise JournalError(f"goal answer for {gid} must be one of {', '.join(goals.STATUSES)}")
        answers[str(gid)] = {"status": a["status"], "note": (a.get("note") or "")[:500]}

    provisional = {"next_steps": steps, "goal_answers": answers}
    results = goals.evaluate_day(conn, day, closed=provisional)
    keep = lambda new, key: (old.get(key) if new is None else new) or ""
    conn.execute(
        "INSERT OR REPLACE INTO days (day, closed_at, feeling, win, notes, next_steps_json, goal_answers_json, "
        "goal_results_json, narrative, snapshot_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (day, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         feeling if feeling is not None else old.get("feeling"),
         keep(win, "win").strip()[:1000], keep(notes, "notes").strip()[:5000],
         json.dumps(steps), json.dumps(answers), json.dumps(results),
         keep(narrative, "narrative").strip()[:20000], json.dumps(review, default=str)))
    return db.get_closed_day(conn, day)


def reopen_day(conn: sqlite3.Connection, day: str) -> None:
    conn.execute("DELETE FROM days WHERE day=?", (_check_day(day),))


def list_days(conn: sqlite3.Connection, start: str, end: str, category: str | None = None) -> list[dict]:
    """One row per calendar day in the range (for the day strip), newest last."""
    summ = insights.summarize(insights.load_sessions(conn, start, end, category))
    by_day = {d["day"]: d for d in summ["by_day"]}
    closed = {r["day"]: db.get_closed_day(conn, r["day"]) for r in
              conn.execute("SELECT day FROM days WHERE day>=? AND day<=?", (start, end))}
    out = []
    d, e = date.fromisoformat(start), date.fromisoformat(end)
    while d <= e:
        k = d.isoformat()
        b, c = by_day.get(k), closed.get(k)
        res = (c or {}).get("goal_results", {})
        out.append({
            "day": k, "clock_s": b["clock_s"] if b else 0, "flow_clock_s": b["flow_clock_s"] if b else 0,
            "projects": b["projects"] if b else [], "closed": bool(c), "feeling": (c or {}).get("feeling"),
            "win": (c or {}).get("win", ""),
            "goals_met": sum(1 for x in res.values() if x["status"] == "met"),
            "goals_judged": sum(1 for x in res.values() if x["status"] != "na"),
        })
        d += timedelta(days=1)
    return out


def reflections(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """Closed days in a range, oldest first (for the retro: wins and how the days felt)."""
    rows = conn.execute("SELECT day FROM days WHERE day>=? AND day<=? ORDER BY day", (start, end)).fetchall()
    return [db.get_closed_day(conn, r["day"]) for r in rows]


# ───────────────────────────── next morning ─────────────────────────────

def morning_card(conn: sqlite3.Connection, today: str | None = None) -> dict:
    """What to pick up first: the next steps you wrote when you last closed a day, else where each
    recently touched project stopped, plus where your goals stand this week."""
    today = _check_day(today or date.today().isoformat())
    steps, from_day, source = [], None, "none"
    for row in conn.execute("SELECT day FROM days WHERE day<=? ORDER BY day DESC LIMIT 7", (today,)):
        c = db.get_closed_day(conn, row["day"])
        mine = [(p, t) for p, t in (c["next_steps"] or {}).items() if str(t).strip()]
        if mine:
            from_day, source = row["day"], "you"
            for p, t in mine:
                last = conn.execute("SELECT MAX(day) d FROM sessions WHERE project=? AND hidden=0", (p,)).fetchone()["d"]
                steps.append({"project": p, "text": t, "source": "you",
                              "worked_since": bool(last and last > row["day"])})
            break
    if not steps:
        for p in insights.projects_overview(conn)[:4]:
            if p["days_ago"] <= 3 and (p["left_off"]["last_prompt"] or p["left_off"]["title"]):
                steps.append({"project": p["name"], "text": p["left_off"]["last_prompt"] or p["left_off"]["title"],
                              "source": "last prompt", "worked_since": False})
                from_day = from_day or p["last_day"]
        source = "last prompt" if steps else "none"
    active = goals.list_goals(conn)
    return {
        "from_day": from_day, "source": source,
        "days_ago": (date.fromisoformat(today) - date.fromisoformat(from_day)).days if from_day else None,
        "steps": steps,
        "goals": [{k: p[k] for k in ("id", "title", "met", "judged", "target", "dots")}
                  for p in goals.progress(conn, active, today, 7)],
    }
