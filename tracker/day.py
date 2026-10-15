"""End-of-day review: what happened today, where flow held, where it broke, what to do differently.

Everything is derived from the same session rows as the dashboard. The unit here is the *stretch*:
a run of consecutive prompts (across all your sessions) that go to the same project. A stretch ends
when your next prompt goes to another project (a switch) or after an idle break. That is a better
picture of attention than per-session numbers, because you run several sessions at once.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import config, insights
from .insights import _h, _local

SHORT_STRETCH_S = 10 * 60        # a stretch shorter than this barely gets you loaded into the task
EPISODE_GAP_S = 20 * 60          # switches closer together than this belong to one fragmentation episode
EPISODE_MIN_SWITCHES = 3
BURST_GAP_S = 15 * 60            # interruptions closer together than this belong to one burst
BURST_MIN = 3
LATE_HOUR = 21                   # activity after this local hour is called out
BASELINE_DAYS = 14


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _block_end(blocks: list[tuple[datetime, datetime]], t: datetime) -> datetime:
    """End of the active block that contains t (the moment Claude/you went quiet after it)."""
    for a, b in blocks:
        if a <= t <= b:
            return b
    return t


def build_stretches(sessions: list[dict], idle_s: float) -> list[dict]:
    claude = [s for s in sessions if s["source"] == "claude"]
    blocks = {s["id"]: [(_local(a), _local(b)) for a, b in s["blocks"]] for s in claude}
    events = sorted(((_local(pt), s["project"], s["id"], s["title"]) for s in claude for pt in s["prompt_times"]),
                    key=lambda e: e[0])
    marks = [(_local(m["t"]), s["project"], m["k"]) for s in claude for m in s["marks"]]
    gap = timedelta(seconds=idle_s)

    out: list[dict] = []
    cur: dict | None = None

    def close(end: datetime, reason: str) -> None:
        nonlocal cur
        cur["end"], cur["ended_by"] = end, reason
        cur["interruptions"] = sum(1 for mt, mp, _ in marks if mp == cur["project"] and cur["start"] <= mt <= end)
        out.append(cur)
        cur = None

    for t, proj, sid, title in events:
        if cur is not None:
            last_end = _block_end(blocks[cur["_sid"]], cur["_last"])
            if proj != cur["project"]:
                close(min(last_end, t), f"switch to {proj}")
            elif t - last_end > gap:
                close(last_end, "idle break")
        if cur is None:
            cur = {"project": proj, "start": t, "prompts": 0, "titles": [], "_sid": sid, "_last": t}
        cur["prompts"] += 1
        cur["_sid"], cur["_last"] = sid, t
        if title and title not in cur["titles"]:
            cur["titles"].append(title)
    if cur is not None:
        close(_block_end(blocks[cur["_sid"]], cur["_last"]), "end of day")
    for st in out:
        st["seconds"] = int((st["end"] - st["start"]).total_seconds())
        del st["_sid"], st["_last"]
    return out


def fragmentation_episodes(sessions: list[dict]) -> list[dict]:
    """Bursts of rapid project switching: 3+ switches with no more than 20 min between them."""
    events = sorted(((_local(pt), s["project"]) for s in sessions if s["source"] == "claude"
                     for pt in s["prompt_times"]), key=lambda e: e[0])
    switches = [(b[0], a[1], b[1]) for a, b in zip(events, events[1:]) if a[1] != b[1]]
    episodes, group = [], []
    for sw in switches + [None]:
        if group and (sw is None or (sw[0] - group[-1][0]).total_seconds() > EPISODE_GAP_S):
            if len(group) >= EPISODE_MIN_SWITCHES:
                projects = []
                for _, a, b in group:
                    for p in (a, b):
                        if p not in projects:
                            projects.append(p)
                episodes.append({"start": group[0][0], "end": group[-1][0], "switches": len(group),
                                 "projects": projects})
            group = []
        if sw is not None:
            group.append(sw)
    return episodes


def interruption_bursts(sessions: list[dict]) -> list[dict]:
    marks = sorted(((_local(m["t"]), s["project"]) for s in sessions if s["source"] == "claude" for m in s["marks"]),
                   key=lambda m: m[0])
    bursts, group = [], []
    for m in marks + [None]:
        if group and (m is None or (m[0] - group[-1][0]).total_seconds() > BURST_GAP_S or m[1] != group[-1][1]):
            if len(group) >= BURST_MIN:
                bursts.append({"start": group[0][0], "end": group[-1][0], "count": len(group),
                               "project": group[0][1]})
            group = []
        if m is not None:
            group.append(m)
    return bursts


def baseline(conn: sqlite3.Connection, day: str, category: str | None) -> dict | None:
    """Averages over your active days in the two weeks before `day`, for 'compared with usual'."""
    d0 = date.fromisoformat(day)
    prev = insights.summarize(insights.load_sessions(
        conn, (d0 - timedelta(days=BASELINE_DAYS)).isoformat(), (d0 - timedelta(days=1)).isoformat(), category))
    days = [d for d in prev["by_day"] if d["clock_s"] >= 1800]
    if len(days) < 3:
        return None
    n = len(days)
    return {"days": n, **{k: sum(d[k] for d in days) / n
                          for k in ("clock_s", "flow_clock_s", "switches", "interruptions", "prompts")}}


def review(conn: sqlite3.Connection, day: str | None = None, category: str | None = None) -> dict:
    day = day or date.today().isoformat()
    cfg = config.load()
    flow_s = cfg["flow_block_minutes"] * 60
    sessions = insights.load_sessions(conn, day, day, category)
    summ = insights.summarize(sessions)
    T = summ["totals"]
    d = next((x for x in summ["by_day"] if x["day"] == day), None)
    out: dict = {"day": day, "empty": d is None or T["sessions"] == 0}
    if out["empty"]:
        out["markdown"] = f"# Day review: {day}\n\nNo sessions recorded for this day.\n"
        return out

    stretches = build_stretches(sessions, cfg["idle_gap_minutes"] * 60)
    episodes = fragmentation_episodes(sessions)
    bursts = interruption_bursts(sessions)
    base = baseline(conn, day, category)
    starts = [_local(s["started_at"]) for s in sessions]
    ends = [_local(s["ended_at"]) for s in sessions]
    longest = max(stretches, key=lambda s: s["seconds"], default=None)
    focus = [s for s in stretches if s["seconds"] >= flow_s]
    short = [s for s in stretches if s["seconds"] < SHORT_STRETCH_S]
    commits = sorted(({"t": c["t"], "project": s["project"], "msg": c["msg"]}
                      for s in sessions for c in s["commits"] if c["msg"] != "(commit)"), key=lambda c: c["t"])

    # where each project stands at the end of the day
    left_off: dict[str, dict] = {}
    for s in sessions:
        if s["source"] != "claude":
            continue
        ended_today = _local(s["ended_at"]).strftime("%Y-%m-%d") == day and not s.get("clipped")
        cand = {"title": s["title"], "ended": s["ended_at"],
                "last_prompt": (s["prompt_list"][-1]["text"] if s["prompt_list"] else s["last_prompt"]),
                "state": s["last_assistant"] if ended_today else ""}
        if s["project"] not in left_off or cand["ended"] > left_off[s["project"]]["ended"]:
            left_off[s["project"]] = cand

    findings = _findings(sessions, summ, d, stretches, focus, short, episodes, bursts, base, longest, flow_s)

    out.update({
        "totals": T, "by_project": summ["by_project"], "by_type": summ["by_type"], "day_stats": d,
        "span": {"start": _hm(min(starts)), "end": _hm(max(ends))},
        "stretches": [_ser_stretch(s) for s in stretches],
        "focus_stretches": len(focus), "longest_stretch": _ser_stretch(longest) if longest else None,
        "episodes": [{"start": _hm(e["start"]), "end": _hm(e["end"]), "switches": e["switches"],
                      "projects": e["projects"]} for e in episodes],
        "interruption_bursts": [{"start": _hm(b["start"]), "end": _hm(b["end"]), "count": b["count"],
                                 "project": b["project"]} for b in bursts],
        "commits": [{"time": _hm(_local(c["t"])), "project": c["project"], "msg": c["msg"]} for c in commits],
        "left_off": left_off, "baseline": base, "findings": findings,
        "notes": [{"project": s["project"], "title": s["title"], "notes": s["notes"], "rating": s["rating"]}
                  for s in sessions if s.get("notes") or s.get("rating")],
    })
    out["markdown"] = _markdown(out)
    return out


def _ser_stretch(s: dict) -> dict:
    return {"start": _hm(s["start"]), "end": _hm(s["end"]), "project": s["project"], "minutes": round(s["seconds"] / 60),
            "prompts": s["prompts"], "interruptions": s["interruptions"], "ended_by": s["ended_by"],
            "title": s["titles"][0] if s["titles"] else ""}


def _findings(sessions, summ, d, stretches, focus, short, episodes, bursts, base, longest, flow_s) -> list[dict]:
    out: list[dict] = []
    T = summ["totals"]

    def add(kind: str, text: str) -> None:
        out.append({"kind": kind, "text": text})

    if focus:
        best = max(focus, key=lambda s: s["seconds"])
        add("win", f"{len(focus)} focus stretch(es) of {flow_s // 60}+ min on one project. Best: {_hm(best['start'])}"
                   f"–{_hm(best['end'])} on {best['project']} ({_h(best['seconds'])}, {best['prompts']} prompts).")
    elif longest:
        add("watch", f"No focus stretch reached {flow_s // 60} min. Longest was {_h(longest['seconds'])} on "
                     f"{longest['project']} at {_hm(longest['start'])}.")
    for e in sorted(episodes, key=lambda e: -e["switches"])[:2]:
        add("watch", f"Flow broke {_hm(e['start'])}–{_hm(e['end'])}: {e['switches']} project switches "
                     f"({' ↔ '.join(e['projects'][:4])}). Next time park one of them in a note and finish the other first.")
    if base and d["switches"] >= 8 and d["switches"] >= 1.5 * base["switches"]:
        add("watch", f"{d['switches']} project switches today vs {base['switches']:.0f} on your usual day.")
    elif base and d["switches"] <= 0.5 * base["switches"] and base["switches"] >= 6:
        add("win", f"Only {d['switches']} project switches vs {base['switches']:.0f} on your usual day.")
    if len(stretches) >= 6 and len(short) / len(stretches) > 0.5:
        add("watch", f"{len(short)} of {len(stretches)} stretches were under {SHORT_STRETCH_S // 60} min. "
                     "Lots of quick pokes; a written next step per project makes it easier to stay put.")
    for b in bursts[:2]:
        add("watch", f"{b['count']} interruptions in a row on {b['project']} ({_hm(b['start'])}–{_hm(b['end'])}). "
                     "Usually a sign the task was unclear: stop, restate the goal and acceptance criteria, then continue.")
    meaty = [s for s in sessions if s["source"] == "claude" and s["edits"] >= 5]
    no_tests = [s for s in meaty if not s["tests_run"]]
    if len(meaty) >= 2 and len(no_tests) / len(meaty) > 0.6:
        add("tip", f"{len(no_tests)} of {len(meaty)} sessions with real edits never ran tests.")
    edited_no_commit = defaultdict(int)
    committed = {s["project"] for s in sessions if s["commits"]}
    for s in sessions:
        if s["source"] == "claude" and s["edits"] >= 10 and s["project"] not in committed:
            edited_no_commit[s["project"]] += s["edits"]
    for proj, n in edited_no_commit.items():
        add("tip", f"{proj}: {n} edits and nothing committed today. Leave a one-line note on where it stands.")
    if T["commits"] >= 3:
        add("win", f"{T['commits']} commits made today.")
    late = [s for s in sessions if s["source"] == "claude"
            for a, b in s["blocks"] if _local(b).hour >= LATE_HOUR and (_local(b) - _local(a)).total_seconds() >= 1800]
    if late:
        add("tip", f"Working past {LATE_HOUR}:00 for 30+ min. Fine occasionally; make sure tomorrow's first step is written down.")
    if base and T["clock_s"] >= 1.4 * base["clock_s"] and T["clock_s"] > 4 * 3600:
        add("tip", f"{_h(T['clock_s'])} on the clock vs {_h(base['clock_s'])} usual. Heavy day: protect tomorrow.")
    order = {"watch": 0, "tip": 1, "win": 2}
    return sorted(out, key=lambda x: order[x["kind"]])[:9]


def _markdown(r: dict) -> str:
    T, d, base = r["totals"], r["day_stats"], r["baseline"]
    dt = date.fromisoformat(r["day"])
    lines = [f"# Day review: {dt.strftime('%a')} {r['day']}", "", "## At a glance", ""]
    par = f" ({_h(T['parallel_s'])} ran in parallel)" if T["parallel_s"] > 600 else ""
    lines.append(f"- {_h(T['clock_s'])} on the clock between {r['span']['start']} and {r['span']['end']}; "
                 f"{_h(T['active_s'])} of session time{par}.")
    lines.append(f"- {T['sessions']} session(s) across {T['projects']} project(s), {d['prompts']} prompts, "
                 f"{T['commits']} commit(s).")
    lines.append(f"- Deep focus: {r['focus_stretches']} stretch(es) of {config.load()['flow_block_minutes']}+ min on one project"
                 + (f"; longest {_h(r['longest_stretch']['minutes'] * 60)} on {r['longest_stretch']['project']}."
                    if r["longest_stretch"] else "."))
    sw = f"- Project switches: {d['switches']}"
    if base:
        sw += f" (usual: {base['switches']:.0f}/day over {base['days']} recent days)"
    lines.append(sw + f"; interruptions: {d['interruptions']}.")
    lines += ["", "## Timeline", ""]
    for s in r["stretches"]:
        end = {"end of day": "", "idle break": " (then a break)"}.get(s["ended_by"], f" (then {s['ended_by']})")
        flag = " ⚑ " + f"{s['interruptions']} interruptions" if s["interruptions"] >= 2 else ""
        lines.append(f"- {s['start']}–{s['end']} **{s['project']}** · {s['minutes']}m · {s['prompts']} prompts{flag}: "
                     f"{s['title']}{end}")
    if r["episodes"] or r["interruption_bursts"]:
        lines += ["", "## Where flow broke", ""]
        lines += [f"- {e['start']}–{e['end']}: {e['switches']} switches ({' ↔ '.join(e['projects'][:4])})" for e in r["episodes"]]
        lines += [f"- {b['start']}–{b['end']}: {b['count']} interruptions on {b['project']}" for b in r["interruption_bursts"]]
    if r["commits"]:
        lines += ["", "## Shipped", ""]
        lines += [f"- {c['time']} [{c['project']}] {c['msg']}" for c in r["commits"]]
    lines += ["", "## Time by project", ""]
    lines += [f"- {p['project']}: {_h(p['active_s'])} session time, {p['commits']} commit(s)" for p in r["by_project"][:8]]
    lines += ["", "## Where you left off", ""]
    for proj, lo in r["left_off"].items():
        lines.append(f"- **{proj}**: {lo['last_prompt']}" + (f"  \n  State: {lo['state']}" if lo["state"] else ""))
    if r["notes"]:
        lines += ["", "## Your notes", ""]
        lines += [f"- [{n['project']}] {n['title']}: {n['notes'] or ''}{' ' + '★' * n['rating'] if n['rating'] else ''}".rstrip()
                  for n in r["notes"]]
    lines += ["", "## Coach", ""]
    lines += [f"- [{f['kind']}] {f['text']}" for f in r["findings"]] or ["- Nothing stood out today."]
    return "\n".join(lines) + "\n"
