"""Aggregation, scoring and the rule-based 'coach' feedback. Pure functions over diary rows."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from . import config

SHORT_SESSION_S = 10 * 60
# Project switches per active day (calibrated on real history: median ~6, top quartile 13+).
SWITCHY_PER_DAY = 10
CALM_PER_DAY = 3
TOTAL_KEYS = ("sessions", "active_s", "flow_s", "wall_s", "prompts", "interruptions", "rejections", "errors",
              "edits", "tests_run", "tokens_in", "tokens_out", "idle_gaps", "area_switches", "topic_shifts")
# (db column, decoded key, empty value). 'prompts' stays the integer count.
_JSON_FIELDS = [
    ("blocks_json", "blocks", list), ("commits_json", "commits", list), ("files_json", "files", list),
    ("tool_counts_json", "tool_counts", dict), ("prompts_json", "prompt_list", list),
    ("work_scores_json", "work_scores", dict), ("marks_json", "marks", list),
    ("prompt_times_json", "prompt_times", list),
]
# Counters with no timestamp of their own: when a session is cut to a range they are scaled by its time share.
_SCALED_COUNTERS = ("tool_calls", "edits", "tests_run", "area_switches", "topic_shifts", "errors",
                    "tokens_in", "tokens_out", "idle_gaps")


# ───────────────────────────── loading ─────────────────────────────

def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bounds(start: str, end: str) -> tuple[datetime, datetime]:
    """Local-time [lo, hi) covering the days start..end inclusive."""
    lo = datetime.fromisoformat(start).astimezone()
    hi = (datetime.fromisoformat(end) + timedelta(days=1)).astimezone()
    return lo, hi


def load_sessions(conn: sqlite3.Connection, start: str, end: str, category: str | None = None,
                  project: str | None = None, include_hidden: bool = False) -> list[dict]:
    """Sessions with activity in start..end (local days). A session that runs over several days is
    cut to the part inside the range, so every day gets only the time actually spent on it."""
    lo, hi = _bounds(start, end)
    q = ("SELECT s.*, COALESCE(p.category,'personal') AS category FROM sessions s "
         "LEFT JOIN projects p ON p.name = s.project WHERE "
         "((s.source = 'manual' AND s.day >= ? AND s.day <= ?) OR "
         "(s.source != 'manual' AND s.ended_at >= ? AND s.started_at < ?))")
    args: list = [start, end, _iso_utc(lo), _iso_utc(hi)]
    if not include_hidden:
        q += " AND s.hidden = 0"
    if category and category != "all":
        q += " AND COALESCE(p.category,'personal') = ?"
        args.append(category)
    if project:
        q += " AND s.project = ?"
        args.append(project)
    q += " ORDER BY s.started_at"
    flow_min = config.load()["flow_block_minutes"] * 60
    out = []
    for r in conn.execute(q, args):
        s = clip_session(decorate(dict(r)), lo, hi, flow_min)
        if s is not None:
            out.append(s)
    return out


def clip_session(s: dict, lo: datetime, hi: datetime, flow_min: float) -> dict | None:
    """Limit a decorated session to the local window [lo, hi); None if nothing happened inside it."""
    if s.get("source") == "manual" or not s["blocks"]:
        return s
    spans = []
    for b0, b1 in s["blocks"]:
        l0, l1 = _local(b0), _local(b1)
        spans.append((l0, l1, (l1 - l0).total_seconds() >= flow_min))   # flow is decided on the whole block
    if min(x[0] for x in spans) >= lo and max(x[1] for x in spans) <= hi:
        return s
    kept = [(max(a, lo), min(b, hi), f) for a, b, f in spans if max(a, lo) < min(b, hi)]
    if not kept:
        return None
    secs = [(b - a).total_seconds() for a, b, _ in kept]
    orig = s.get("active_s") or 0
    frac = sum(secs) / orig if orig else 1.0
    out = dict(s)
    out["clipped"] = True
    out["active_s"] = int(sum(secs))
    out["flow_s"] = int(sum(sec for sec, (_, _, f) in zip(secs, kept) if f))
    out["longest_block_s"] = int(max(secs))
    out["blocks"] = [[_iso_utc(a), _iso_utc(b)] for a, b, _ in kept]
    out["started_at"], out["ended_at"] = out["blocks"][0][0], out["blocks"][-1][1]
    out["wall_s"] = int((kept[-1][1] - kept[0][0]).total_seconds())
    out["day"] = kept[0][0].strftime("%Y-%m-%d")

    def inside(iso: str) -> bool:
        return lo <= _local(iso) < hi

    if s["prompt_times"]:
        out["prompt_times"] = [t for t in s["prompt_times"] if inside(t)]
        out["prompts"] = len(out["prompt_times"])
        out["marks"] = [m for m in s["marks"] if inside(m["t"])]
        out["interruptions"] = sum(1 for m in out["marks"] if m["k"] == "interrupt")
        out["rejections"] = sum(1 for m in out["marks"] if m["k"] == "reject")
    else:   # row from before prompt times were stored
        for k in ("prompts", "interruptions", "rejections"):
            out[k] = round((s.get(k) or 0) * frac)
    out["commits"] = [c for c in s["commits"] if inside(c["t"])]
    out["prompt_list"] = [p for p in s["prompt_list"] if inside(p["t"])]
    for k in _SCALED_COUNTERS:
        out[k] = round((s.get(k) or 0) * frac)
    out["focus"] = focus_score(out)
    out["focus_label"] = focus_label(out["focus"])
    return out


def decorate(s: dict) -> dict:
    s["type"] = s.get("type_override") or s.get("work_type") or "other"
    for col, name, default in _JSON_FIELDS:
        try:
            s[name] = json.loads(s.get(col) or "null") or default()
        except ValueError:
            s[name] = default()
    s["focus"] = focus_score(s)
    s["focus_label"] = focus_label(s["focus"])
    return s


def focus_score(s: dict) -> int | None:
    """0-100, explainable: 40% flow share, 25% longest uninterrupted block, 20% low interruptions,
    15% low topic-hopping. Manual entries use the user's own 1-5 rating."""
    if s.get("source") == "manual":
        return s["rating"] * 20 if s.get("rating") else None
    active = s.get("active_s") or 0
    if active < 5 * 60:
        return None
    flow_min = config.load()["flow_block_minutes"] * 60
    prompts = max(s.get("prompts") or 1, 1)
    flow = min(1.0, (s.get("flow_s") or 0) / active)
    steady = min(1.0, (s.get("longest_block_s") or 0) / flow_min)
    calm = 1 - min(1.0, ((s.get("interruptions") or 0) + (s.get("rejections") or 0)) / prompts)
    focused = 1 - min(1.0, (s.get("topic_shifts") or 0) / prompts)
    return round(100 * (0.40 * flow + 0.25 * steady + 0.20 * calm + 0.15 * focused))


def focus_label(score: int | None) -> str:
    if score is None:
        return "too short"
    return "deep flow" if score >= 70 else "steady" if score >= 40 else "scattered"


# ───────────────────────────── aggregation ─────────────────────────────

def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()


def _hour_split(start: datetime, end: datetime):
    """Yield (local_hour, seconds) for an interval, split on hour boundaries."""
    cur = start
    while cur < end:
        nxt = min(end, cur.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        yield cur.hour, (nxt - cur).total_seconds()
        cur = nxt


def _union_s(spans: list[tuple[datetime, datetime]]) -> int:
    """Seconds covered by at least one span: overlapping sessions are counted once."""
    total, cur = 0.0, None
    for a, b in sorted(spans):
        if cur is None:
            cur = [a, b]
        elif a <= cur[1]:
            cur[1] = max(cur[1], b)
        else:
            total += (cur[1] - cur[0]).total_seconds()
            cur = [a, b]
    if cur:
        total += (cur[1] - cur[0]).total_seconds()
    return int(total)


def _day_pieces(start: datetime, end: datetime):
    """Yield (local_day, piece_start, piece_end) for an interval, split at local midnight."""
    cur = start
    while cur < end:
        nxt = min(end, cur.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
        yield cur.strftime("%Y-%m-%d"), cur, nxt
        cur = nxt


def count_switches(prompts: list[tuple[datetime, str]]) -> int:
    """Context switches = times consecutive prompts (across all sessions) go to a different project."""
    ordered = sorted(prompts)
    return sum(1 for x, y in zip(ordered, ordered[1:]) if x[1] != y[1])


def summarize(sessions: list[dict]) -> dict:
    cfg = config.load()
    flow_min = cfg["flow_block_minutes"] * 60
    t = defaultdict(int, {k: 0 for k in TOTAL_KEYS})
    by_type: dict[str, int] = defaultdict(int)
    by_project: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    hours_active = [0.0] * 24
    hours_flow = [0.0] * 24
    day_spans: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    day_flow_spans: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    day_prompts: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    commits = 0
    short = 0
    longest = 0

    def entry(day: str) -> dict:
        return by_day.setdefault(day, {
            "day": day, "active_s": 0, "flow_s": 0, "clock_s": 0, "flow_clock_s": 0, "sessions": set(), "projects": set(),
            "by_type": defaultdict(int), "interruptions": 0, "prompts": 0, "switches": 0})

    for s in sessions:
        for k in TOTAL_KEYS[1:]:
            t[k] += s.get(k) or 0
        t["sessions"] += 1
        commits += len(s["commits"])
        a = s.get("active_s") or 0
        by_type[s["type"]] += a
        if a < SHORT_SESSION_S:
            short += 1
        longest = max(longest, s.get("longest_block_s") or 0)

        p = by_project.setdefault(s["project"], {"project": s["project"], "category": s["category"],
                                                 "active_s": 0, "flow_s": 0, "sessions": 0, "commits": 0})
        p["active_s"] += a
        p["flow_s"] += s.get("flow_s") or 0
        p["sessions"] += 1
        p["commits"] += len(s["commits"])

        if s["source"] == "manual":
            d = entry(s["day"])
            d["active_s"] += a
            d["flow_s"] += s.get("flow_s") or 0
            d["sessions"].add(s["id"])
            d["projects"].add(s["project"])
            d["by_type"][s["type"]] += a
            d["interruptions"] += s.get("interruptions") or 0
            for b0, b1 in s["blocks"]:
                day_spans[s["day"]].append((_local(b0), _local(b1)))
                if s.get("flow_s"):
                    day_flow_spans[s["day"]].append((_local(b0), _local(b1)))
            continue

        for b0, b1 in s["blocks"]:
            l0, l1 = _local(b0), _local(b1)
            is_flow = (l1 - l0).total_seconds() >= flow_min
            for day, p0, p1 in _day_pieces(l0, l1):     # a block that crosses midnight feeds both days
                secs = (p1 - p0).total_seconds()
                d = entry(day)
                d["active_s"] += secs
                d["by_type"][s["type"]] += secs
                d["sessions"].add(s["id"])
                d["projects"].add(s["project"])
                if is_flow:
                    d["flow_s"] += secs
                    day_flow_spans[day].append((p0, p1))
                day_spans[day].append((p0, p1))
            for h, secs in _hour_split(l0, l1):
                hours_active[h] += secs
                if is_flow:
                    hours_flow[h] += secs
        for pt in s["prompt_times"]:
            lt = _local(pt)
            day = lt.strftime("%Y-%m-%d")
            day_prompts[day].append((lt, s["project"]))
            entry(day)["prompts"] += 1
        for m in s["marks"]:
            entry(_local(m["t"]).strftime("%Y-%m-%d"))["interruptions"] += 1

    switches_total = 0
    clock_total = 0
    flow_clock_total = 0
    for day, d in by_day.items():
        d["clock_s"] = _union_s(day_spans.get(day, []))
        d["flow_clock_s"] = _union_s(day_flow_spans.get(day, []))
        flow_clock_total += d["flow_clock_s"]
        d["switches"] = count_switches(day_prompts.get(day, []))
        d["sessions"] = len(d["sessions"])
        d["projects"] = sorted(d["projects"])
        d["by_type"] = {k: round(v) for k, v in d["by_type"].items()}
        d["active_s"] = round(d["active_s"])
        d["flow_s"] = round(d["flow_s"])
        switches_total += d["switches"]
        clock_total += d["clock_s"]

    active_days = [d for d in by_day.values() if d["active_s"] > 0]
    hrs = t["active_s"] / 3600 or 0
    return {
        "totals": {
            **t, "commits": commits, "short_sessions": short, "longest_block_s": longest,
            "clock_s": clock_total, "flow_clock_s": flow_clock_total, "parallel_s": max(0, t["active_s"] - clock_total),
            "flow_ratio": round(t["flow_s"] / t["active_s"], 3) if t["active_s"] else 0,
            "interruptions_per_hour": round((t["interruptions"] + t["rejections"]) / hrs, 2) if hrs else 0,
            "project_switches": switches_total,
            "switches_per_day": round(switches_total / len(active_days), 2) if active_days else 0,
            "active_days": len(active_days),
            "projects": len(by_project),
        },
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "by_project": sorted(by_project.values(), key=lambda p: -p["active_s"]),
        "by_day": sorted(by_day.values(), key=lambda d: d["day"]),
        "by_hour": {"active": [round(x) for x in hours_active], "flow": [round(x) for x in hours_flow]},
    }


# ───────────────────────────── coach feedback ─────────────────────────────

def _h(seconds: float) -> str:
    m = round(seconds / 60)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def coach(sessions: list[dict], summary: dict, prev: dict | None = None) -> list[dict]:
    """Rule-based observations. kind: win | watch | tip. Each is one sentence you can act on."""
    out: list[dict] = []
    T = summary["totals"]
    if T["active_s"] < 3600:
        return [{"kind": "tip", "text": "Not enough activity in this range yet. Pick a longer range or log a session."}]
    claude = [s for s in sessions if s["source"] == "claude"]
    hrs = T["active_s"] / 3600

    # flow
    fr = T["flow_ratio"]
    if fr >= 0.5:
        out.append({"kind": "win", "text": f"{round(fr*100)}% of your active time was deep flow (uninterrupted blocks of "
                    f"{config.load()['flow_block_minutes']}+ min). Longest stretch: {_h(T['longest_block_s'])}."})
    elif fr < 0.2:
        out.append({"kind": "watch", "text": f"Only {round(fr*100)}% of your active time was deep flow. Your day is chopped "
                    "into short bursts; try one protected block with notifications off."})
    # best hour
    fl = summary["by_hour"]["flow"]
    if sum(fl) > 3600 and max(fl) > 0:
        best = max(range(24), key=lambda h: fl[h])
        out.append({"kind": "tip", "text": f"Your deepest focus clusters around {best:02d}:00–{(best+1)%24:02d}:00. "
                    "Protect that slot for hard problems; keep reviews and chores for the rest."})
    # switching
    sw = T["switches_per_day"]
    if sw >= SWITCHY_PER_DAY:
        msg = (f"You switch projects {sw:.1f}× per active day (a switch = your next prompt goes to a different "
               "project). Each one costs re-load time; batch work per repo where you can.")
        if prev and prev["totals"]["switches_per_day"]:
            msg += f" (previous period: {prev['totals']['switches_per_day']:.1f})"
        out.append({"kind": "watch", "text": msg})
    elif T["projects"] > 1 and sw <= CALM_PER_DAY:
        out.append({"kind": "win", "text": f"Low context switching ({sw:.1f} project switches/day) across {T['projects']} projects."})
    # parallel sessions
    if T["active_s"] and T["parallel_s"] / T["active_s"] >= 0.3 and T["clock_s"] >= 3600:
        out.append({"kind": "tip", "text": f"{round(100 * T['parallel_s'] / T['active_s'])}% of session time overlapped with "
                    f"another session ({_h(T['clock_s'])} on the clock vs {_h(T['active_s'])} of session time). "
                    "Parallel threads are fine for agents, but each one needs your attention to be reviewed."})
    # interruptions
    ipr = T["interruptions_per_hour"]
    if ipr >= 1.5:
        out.append({"kind": "watch", "text": f"You stopped or rejected Claude {ipr:.1f}× per active hour. A short plan or "
                    "acceptance criteria up front usually prevents mid-flight corrections (it is also how seniors scope work)."})
    elif ipr <= 0.4 and hrs >= 3:
        out.append({"kind": "win", "text": "Few interruptions: your prompts were clear enough that sessions ran without course corrections."})
    # tests / commits
    meaty = [s for s in claude if s["edits"] >= 5]
    if len(meaty) >= 3:
        no_tests = sum(1 for s in meaty if not s["tests_run"])
        no_commit = sum(1 for s in meaty if not s["commits"])
        if no_tests / len(meaty) > 0.6:
            out.append({"kind": "tip", "text": f"{no_tests} of {len(meaty)} substantial sessions never ran tests. "
                        "Ask for a test run (or a failing test first) before calling work done."})
        elif no_tests / len(meaty) < 0.25:
            out.append({"kind": "win", "text": "You consistently validate changes: tests ran in most substantial sessions."})
        if no_commit / len(meaty) > 0.7:
            out.append({"kind": "tip", "text": f"{no_commit} of {len(meaty)} substantial sessions ended without a commit. "
                        "Smaller, frequent commits make work reviewable and easier to explain in refinement."})
    # work mix
    tot = sum(summary["by_type"].values()) or 1
    share = {k: v / tot for k, v in summary["by_type"].items()}
    if share.get("bugfix", 0) > 0.4:
        out.append({"kind": "watch", "text": f"{round(share['bugfix']*100)}% of time went to bug fixing. Worth asking in the retro: "
                    "is there a recurring root cause (flaky area, missing tests, unclear requirements)?"})
    think = share.get("exploration", 0) + share.get("planning", 0) + share.get("review", 0)
    if think >= 0.15:
        out.append({"kind": "win", "text": f"{round(think*100)}% of time went to exploring, planning and reviewing before/around building. Solid senior habit."})
    elif hrs >= 8 and think < 0.05:
        out.append({"kind": "tip", "text": "Almost no time on exploration/planning. Try a design pass before large features."})
    if hrs >= 10 and share.get("docs", 0) < 0.02:
        out.append({"kind": "tip", "text": "No documentation time this period. A short note on decisions pays off at handover and in retros."})
    # fragmentation / hours
    if T["sessions"] >= 6 and T["short_sessions"] / T["sessions"] > 0.4:
        out.append({"kind": "watch", "text": f"{T['short_sessions']} of {T['sessions']} sessions were under 10 minutes. Lots of quick pokes can be a sign of distraction or unclear next steps."})
    night = sum(summary["by_hour"]["active"][h] for h in (22, 23, 0, 1, 2, 3, 4, 5))
    if T["active_s"] and night / T["active_s"] > 0.25:
        out.append({"kind": "watch", "text": f"{round(100*night/T['active_s'])}% of your coding happened between 22:00 and 06:00. Sustainable pace beats heroics."})
    if any(s["longest_block_s"] > 3 * 3600 for s in claude):
        out.append({"kind": "tip", "text": "At least one block ran 3h+ without a real break. Short breaks keep decision quality up."})
    # trend
    if prev and prev["totals"]["active_s"] > 3600:
        d = (T["active_s"] - prev["totals"]["active_s"]) / prev["totals"]["active_s"]
        if abs(d) >= 0.25:
            out.append({"kind": "tip", "text": f"Active time is {'up' if d > 0 else 'down'} {abs(round(d*100))}% vs the previous period "
                        f"({_h(prev['totals']['active_s'])} → {_h(T['active_s'])})."})
    order = {"watch": 0, "tip": 1, "win": 2}
    return sorted(out, key=lambda x: order[x["kind"]])[:9]


def previous_range(start: str, end: str) -> tuple[str, str]:
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    n = (e - s).days + 1
    return (s - timedelta(days=n)).isoformat(), (s - timedelta(days=1)).isoformat()


def overview(conn: sqlite3.Connection, start: str, end: str, category: str | None = None) -> dict:
    sess = load_sessions(conn, start, end, category)
    summ = summarize(sess)
    ps, pe = previous_range(start, end)
    prev = summarize(load_sessions(conn, ps, pe, category))
    return {"range": {"start": start, "end": end}, "summary": summ, "previous": prev["totals"],
            "coach": coach(sess, summ, prev)}


def projects_overview(conn: sqlite3.Connection, category: str | None = None) -> list[dict]:
    """The 'many repos in my head' view: per project, where you left off."""
    today = date.today()
    out = []
    for p in conn.execute("SELECT * FROM projects ORDER BY name"):
        if category and category != "all" and p["category"] != category:
            continue
        rows = [decorate(dict(r)) for r in conn.execute(
            "SELECT * FROM sessions WHERE project=? AND hidden=0 ORDER BY started_at DESC", (p["name"],))]
        if not rows:
            continue
        last = rows[0]
        d30 = (today - timedelta(days=30)).isoformat()
        recent = [r for r in rows if r["day"] >= d30]
        out.append({
            "name": p["name"], "category": p["category"], "path": p["path"],
            "last_day": last["day"], "days_ago": (today - date.fromisoformat(last["day"])).days,
            "sessions_30d": len(recent), "active_s_30d": sum(r["active_s"] for r in recent),
            "total_sessions": len(rows), "branch": last["branch"],
            "left_off": {"id": last["id"], "title": last["title"], "type": last["type"],
                         "last_prompt": last["last_prompt"], "last_assistant": last["last_assistant"],
                         "commits": last["commits"][-3:]},
            "recent_titles": [{"day": r["day"], "title": r["title"], "type": r["type"]} for r in rows[:5]],
        })
    return sorted(out, key=lambda x: x["days_ago"])
