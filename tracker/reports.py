"""Meeting-prep reports: weekly refinement and sprint retrospective. Output is structured
sections (for the UI) plus markdown (for pasting into notes or a retro board)."""
from __future__ import annotations

import sqlite3
from collections import defaultdict

from . import goals, insights, journal
from .insights import _h


def _md(title: str, sections: list[dict]) -> str:
    lines = [f"# {title}", ""]
    for sec in sections:
        lines += [f"## {sec['title']}", ""]
        for it in sec["items"]:
            lines.append(f"- {it}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _per_project(sessions: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = defaultdict(list)
    for s in sessions:
        g[s["project"]].append(s)
    return g


def refinement(conn: sqlite3.Connection, start: str, end: str, category: str | None = "work") -> dict:
    """What moved per project, where each one stands, what to bring up."""
    sess = insights.load_sessions(conn, start, end, category)
    summ = insights.summarize(sess)
    steps: dict[str, str] = {}     # latest next step you wrote per project, from closed days
    for c in journal.reflections(conn, start, end):
        steps.update({p: t for p, t in c["next_steps"].items() if str(t).strip()})
    sections: list[dict] = []
    T = summ["totals"]
    sections.append({"title": "At a glance", "items": [
        f"{_h(T['clock_s'])} on the clock ({_h(T['active_s'])} of session time) across {T['sessions']} sessions "
        f"in {T['projects']} project(s), {start} to {end}.",
        f"{T['commits']} commit(s) made; tests run in {sum(1 for s in sess if s['tests_run'])} session(s).",
    ]})
    for name, rows in sorted(_per_project(sess).items(), key=lambda kv: -sum(r["active_s"] for r in kv[1])):
        items = []
        types = defaultdict(int)
        for r in rows:
            types[r["type"]] += r["active_s"]
        mix = ", ".join(f"{k} {_h(v)}" for k, v in sorted(types.items(), key=lambda kv: -kv[1])[:3])
        items.append(f"Time: {_h(sum(r['active_s'] for r in rows))} ({mix})")
        shipped = [c for r in rows for c in r["commits"] if c["msg"] != "(commit)"]
        if shipped:
            items += [f"Shipped: {c['msg']}" for c in shipped[:6]]
            if len(shipped) > 6:
                items.append(f"…and {len(shipped) - 6} more commits")
        worked = []
        for r in rows:
            if r["title"] and r["title"] not in worked and r["active_s"] >= 300:
                worked.append(r["title"])
        items += [f"Worked on: {w}" for w in worked[:6]]
        hot = defaultdict(int)
        for r in rows:
            for f in r["files"][:10]:
                hot[f] += 1
        if hot:
            items.append("Hot files: " + ", ".join(f for f, _ in sorted(hot.items(), key=lambda kv: -kv[1])[:4]))
        last = rows[-1]
        items.append(f"Left off ({last['day']}): {last['last_prompt'] or last['title']}")
        if last["last_assistant"]:
            items.append(f"Last state: {last['last_assistant']}")
        if name in steps:
            items.append(f"Next step (your note): {steps[name]}")
        sections.append({"title": f"{name}", "items": items})
    asks = []
    for name, rows in _per_project(sess).items():
        if sum(r["errors"] for r in rows) > 30 or any(r["interruptions"] >= 5 for r in rows):
            asks.append(f"{name}: lots of friction (errors/interruptions). Is something unclear or blocked? Ask the team.")
        if rows and not any(r["commits"] for r in rows) and sum(r["active_s"] for r in rows) > 3600:
            asks.append(f"{name}: over an hour of work but nothing committed. Status check: what is the next shippable slice?")
    if asks:
        sections.append({"title": "Things to raise", "items": asks})
    title = f"Refinement prep ({start} to {end})"
    return {"title": title, "sections": sections, "markdown": _md(title, sections)}


def retro(conn: sqlite3.Connection, start: str, end: str, category: str | None = "work") -> dict:
    ov = insights.overview(conn, start, end, category)
    summ, prev, T = ov["summary"], ov["previous"], ov["summary"]["totals"]

    def delta(key: str, fmt=lambda x: str(x)) -> str:
        a, b = T[key], prev.get(key, 0)
        if not b:
            return fmt(a)
        pct = round((a - b) / b * 100)
        return f"{fmt(a)} ({'+' if pct >= 0 else ''}{pct}% vs previous)"

    sections = [
        {"title": "The numbers", "items": [
            f"Time on the clock: {delta('clock_s', _h)}",
            f"Deep flow: {round(T['flow_ratio']*100)}% of active time (longest block {_h(T['longest_block_s'])})",
            f"Sessions: {delta('sessions')} across {T['projects']} project(s) on {T['active_days']} day(s)",
            f"Project switches per active day: {T['switches_per_day']}",
            f"Interruptions per active hour: {T['interruptions_per_hour']}",
            f"Commits: {delta('commits')}",
        ]},
        {"title": "What went well", "items": [c["text"] for c in ov["coach"] if c["kind"] == "win"]
         or ["Nothing stood out statistically; add your own wins from memory."]},
        {"title": "What to improve", "items": [c["text"] for c in ov["coach"] if c["kind"] == "watch"]
         or ["No red flags in the data."]},
        {"title": "Ideas to try next sprint", "items": [c["text"] for c in ov["coach"] if c["kind"] == "tip"]
         or ["Keep the current habits."]},
    ]
    gsum = goals.range_summary(conn, start, end)
    if gsum:
        sections.append({"title": "Goals", "items": [
            f"{g['title']}: met on {g['met']} of {g['judged']} judged day(s)"
            + (f", partly on {g['partly']}" if g["partly"] else "")
            + f" (target about {g['target']})" for g in gsum]})
    refl = journal.reflections(conn, start, end)
    wins = [f"{c['day']}: {c['win']}" for c in refl if c.get("win")]
    if wins:
        sections.append({"title": "Wins you noted", "items": wins})
    feels = [c["feeling"] for c in refl if c.get("feeling")]
    if feels:
        sections[0]["items"].append(
            f"How the days felt: {sum(feels) / len(feels):.1f}/5 average over {len(feels)} closed day(s)")
    days = [d for d in summ["by_day"] if d["active_s"] > 0]
    if days:
        best = max(days, key=lambda d: d["flow_s"])
        worst = max(days, key=lambda d: d.get("switches", 0))
        notable = [f"Best focus: {best['day']} ({_h(best['flow_s'])} in deep flow, {', '.join(best['projects'])})"]
        if worst.get("switches", 0) > 0:
            notable.append(f"Most fragmented: {worst['day']} ({worst['switches']} project switches across "
                           f"{', '.join(worst['projects'])})")
        sections.append({"title": "Notable days", "items": notable})
    sections.append({"title": "Time by project", "items": [
        f"{p['project']}: {_h(p['active_s'])} ({p['sessions']} sessions, {p['commits']} commits)"
        for p in summ["by_project"][:10]]})
    title = f"Sprint retrospective ({start} to {end})"
    return {"title": title, "sections": sections, "markdown": _md(title, sections)}
