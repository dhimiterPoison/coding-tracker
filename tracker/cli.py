"""Command line entry point: `tracker sync | hook | serve | install-hook | today | day | log | note | report | stats`."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import config, db, sync


def _setup_logging() -> None:
    config.ensure_dirs()
    logging.basicConfig(filename=config.LOG_PATH, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")


def cmd_sync(args) -> int:
    stats = sync.sync_all(force=args.force)
    print(f"imported={stats['imported']} unchanged={stats['unchanged']} skipped={stats['skipped']}")
    return 0


def cmd_hook(_args) -> int:
    """Claude Code hook entry (SessionEnd). Reads the hook JSON on stdin; never fails the session."""
    _setup_logging()
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        tp = payload.get("transcript_path")
        if tp:
            with db.session() as conn:
                sync.ingest_file(conn, Path(tp), force=True)
    except Exception:
        logging.getLogger("tracker").exception("hook failed")
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    from .server import app
    print(f"Coding Tracker on http://127.0.0.1:{args.port}  (local only, data in {config.DATA_DIR})")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def cmd_install_hook(args) -> int:
    """Add (or remove) the SessionEnd hook in ~/.claude/settings.json, keeping everything else."""
    settings_path = Path.home() / ".claude" / "settings.json"
    data = json.loads(settings_path.read_text(encoding="utf-8-sig")) if settings_path.exists() else {}
    exe = Path(sys.executable).with_name("tracker.exe" if sys.platform == "win32" else "tracker")
    command = f'"{exe}" hook'
    hooks = data.setdefault("hooks", {})
    groups = hooks.setdefault("SessionEnd", [])
    marker = " hook"
    groups[:] = [g for g in groups
                 if not any("tracker" in h.get("command", "") and h.get("command", "").endswith(marker)
                            for h in g.get("hooks", []))]
    if not args.remove:
        groups.append({"hooks": [{"type": "command", "command": command}]})
    if not groups:
        hooks.pop("SessionEnd")
    if not hooks:
        data.pop("hooks")
    backup = settings_path.with_suffix(".json.bak")
    if settings_path.exists():
        backup.write_text(settings_path.read_text(encoding="utf-8"), encoding="utf-8")
    settings_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(("Removed" if args.remove else "Installed") + f" SessionEnd hook in {settings_path} (backup: {backup.name})")
    if not args.remove:
        print(f"Command: {command}")
    return 0


def cmd_log(args) -> int:
    """Add a manual diary entry (meetings, pairing, offline work, anything Claude did not see)."""
    from datetime import date
    from . import manual
    try:
        with db.session() as conn:
            manual.create(conn, project=args.project, day=args.day or date.today().isoformat(),
                          start_time=args.start, minutes=args.minutes, title=args.title, type=args.type,
                          rating=args.rating, notes=args.notes or "")
    except manual.ManualError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"Logged {args.minutes} min on {args.project}: {args.title}")
    return 0


def cmd_note(args) -> int:
    """Attach a note (e.g. a short progress summary) to the latest session in a directory."""
    from . import manual
    text = args.text or sys.stdin.read()
    if not text.strip():
        print("error: empty note", file=sys.stderr)
        return 2
    sync.sync_all()  # the session in progress may not be imported yet
    with db.session() as conn:
        hit = manual.add_note(conn, args.cwd or str(Path.cwd()), text)
    if not hit:
        print("No imported session found for this directory.", file=sys.stderr)
        return 1
    print(f"Note saved on: {hit['title']}")
    return 0


def cmd_report(args) -> int:
    from datetime import date, timedelta
    from . import reports
    cfg = config.load()
    days = args.days or (cfg["sprint_length_days"] if args.kind == "retro" else cfg["refinement_length_days"])
    end = date.today()
    start = end - timedelta(days=days - 1)
    fn = reports.retro if args.kind == "retro" else reports.refinement
    sync.sync_all()
    with db.session() as conn:
        print(fn(conn, start.isoformat(), end.isoformat(), args.category)["markdown"])
    return 0


def cmd_today(_args) -> int:
    from datetime import date
    from . import insights
    sync.sync_all()
    today = date.today().isoformat()
    with db.session() as conn:
        sess = insights.load_sessions(conn, today, today)
        summ = insights.summarize(sess)
    T = summ["totals"]
    print(f"{today}: {insights._h(T['clock_s'])} on the clock ({insights._h(T['active_s'])} of session time), "
          f"{T['sessions']} session(s), {round(T['flow_ratio'] * 100)}% deep flow, {T['commits']} commit(s)")
    for s in sess:
        print(f"  - [{s['project']}] {s['title']} ({s['type']}, {insights._h(s['active_s'])}, {s['focus_label']})")
    return 0


def cmd_day(args) -> int:
    """End-of-day review: timeline, focus stretches, where flow broke, what shipped, where you left off."""
    from datetime import date
    from . import journal
    try:
        day = date.fromisoformat(args.date).isoformat() if args.date else date.today().isoformat()
    except ValueError:
        print("error: --date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    sync.sync_all()
    try:
        with db.session() as conn:
            if args.close:
                steps = dict(_kv(x) for x in args.next or [])
                answers = {gid: {"status": st} for gid, st in (_kv(x) for x in args.goal or [])}
                narrative = sys.stdin.read() if args.narrative == "-" else args.narrative
                journal.close_day(conn, day, feeling=args.feeling, win=args.win, notes=args.notes,
                                  next_steps=steps or None, goal_answers=answers or None,
                                  narrative=narrative, category=args.category)
                print(f"Day {day} closed.", file=sys.stderr)
            r = journal.full_review(conn, day, args.category)
    except (journal.JournalError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.json:
        r.pop("markdown", None)
        print(json.dumps(r, indent=2, default=str))
    else:
        print(r["markdown"])
    return 0


def _kv(text: str) -> tuple[str, str]:
    key, sep, val = text.partition("=")
    if not sep or not key.strip():
        raise ValueError(f"expected KEY=VALUE, got '{text}'")
    return key.strip(), val.strip()


def cmd_morning(_args) -> int:
    from . import journal
    sync.sync_all()
    with db.session() as conn:
        card = journal.morning_card(conn)
    if not card["steps"]:
        print("Nothing to pick up yet. Close a day with `tracker day --close` to leave yourself next steps.")
    else:
        when = "yesterday" if card["days_ago"] == 1 else f"{card['from_day']}"
        print(f"Pick up where you left off ({when}, {card['source']}):")
        for s in card["steps"]:
            print(f"  - [{s['project']}] {s['text']}" + ("  (worked on since)" if s["worked_since"] else ""))
    for g in card["goals"]:
        print(f"  goal: {g['title']}: {g['met']}/{g['target']} this week")
    return 0


def cmd_goals(args) -> int:
    from datetime import date
    from . import goals
    with db.session() as conn:
        try:
            if args.action == "add":
                gid = goals.create(conn, args.title, args.why or "", args.metric, args.target)
                print(f"Added goal {gid}: {args.title}")
            elif args.action in ("archive", "restore"):
                goals.update(conn, int(args.title), active=(args.action == "restore"))
                print(f"Goal {args.title} {'restored' if args.action == 'restore' else 'archived'}.")
            elif args.action == "templates":
                for i, t in enumerate(goals.TEMPLATES, 1):
                    print(f"{i}. {t['title']}  [{t['metric']}, {t['target_per_week']}/week]\n   {t['why']}")
                print("\nAdd one: tracker goals add \"<title>\" --metric <metric> --target <n>")
            else:
                prog = goals.progress(conn, goals.list_goals(conn), date.today().isoformat(), 7)
                if not prog:
                    print("No goals yet. See `tracker goals templates`.")
                for p in prog:
                    dots = "".join({"met": "●", "partly": "◐", "missed": "○"}[d["status"]] for d in p["dots"])
                    print(f"{p['id']}. {p['title']}  [{p['metric']}]  week: {p['met']}/{p['target']} {dots}")
        except (goals.GoalError, ValueError, TypeError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    return 0


def cmd_stats(_args) -> int:
    with db.session() as conn:
        n = conn.execute("SELECT COUNT(*) c, COUNT(DISTINCT project) p, MIN(day) a, MAX(day) b FROM sessions").fetchone()
    print(f"{n['c']} sessions, {n['p']} projects, {n['a']} → {n['b']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):   # piped output on Windows is cp1252 and chokes on arrows/stars
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="tracker", description="Local diary of your coding sessions.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync", help="import new/changed Claude Code transcripts")
    p.add_argument("--force", action="store_true", help="re-parse everything")
    p.set_defaults(fn=cmd_sync)
    sub.add_parser("hook", help="Claude Code hook entry point (reads JSON on stdin)").set_defaults(fn=cmd_hook)
    p = sub.add_parser("serve", help="start the dashboard on localhost")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("install-hook", help="register the SessionEnd hook in ~/.claude/settings.json")
    p.add_argument("--remove", action="store_true")
    p.set_defaults(fn=cmd_install_hook)
    sub.add_parser("stats", help="print a one-line summary of the diary").set_defaults(fn=cmd_stats)
    sub.add_parser("today", help="print today's sessions").set_defaults(fn=cmd_today)
    p = sub.add_parser("day", help="end-of-day review (timeline, where flow broke, what to improve)")
    p.add_argument("--date", help="YYYY-MM-DD (default today)")
    p.add_argument("--category", default="all", choices=["all", "work", "personal"])
    p.add_argument("--json", action="store_true", help="structured output instead of Markdown")
    p.add_argument("--close", action="store_true", help="close the day: save your reflection and a frozen review")
    p.add_argument("--feeling", type=int, choices=range(1, 6), help="how the day felt, 1-5 (with --close)")
    p.add_argument("--win", help="one win from the day (with --close)")
    p.add_argument("--notes", help="free notes (with --close)")
    p.add_argument("--next", action="append", metavar="PROJECT=STEP", help="first step for tomorrow (repeatable)")
    p.add_argument("--goal", action="append", metavar="ID=met|partly|missed", help="answer a self-reported goal (repeatable)")
    p.add_argument("--narrative", help="written review to save with the day; '-' reads stdin")
    p.set_defaults(fn=cmd_day)
    sub.add_parser("morning", help="what to pick up first today").set_defaults(fn=cmd_morning)
    p = sub.add_parser("goals", help="your goals: list | templates | add | archive | restore")
    p.add_argument("action", nargs="?", default="list", choices=["list", "templates", "add", "archive", "restore"])
    p.add_argument("title", nargs="?", help="goal title (add) or goal id (archive/restore)")
    p.add_argument("--why", help="why it matters to you")
    p.add_argument("--metric", default="manual", help="manual, focus_block, tests, small_commits, plan_first, low_switches, next_step")
    p.add_argument("--target", type=int, default=3, help="days per week you aim to meet it")
    p.set_defaults(fn=cmd_goals)
    p = sub.add_parser("log", help="add a manual session")
    p.add_argument("--project", required=True)
    p.add_argument("--minutes", type=int, required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--type", default="other")
    p.add_argument("--day", help="YYYY-MM-DD (default today)")
    p.add_argument("--start", default="09:00", help="HH:MM local time")
    p.add_argument("--rating", type=int, choices=range(1, 6), help="felt focus 1-5")
    p.add_argument("--notes")
    p.set_defaults(fn=cmd_log)
    p = sub.add_parser("note", help="attach a note to the latest session in a directory (text or stdin)")
    p.add_argument("text", nargs="?")
    p.add_argument("--cwd")
    p.set_defaults(fn=cmd_note)
    p = sub.add_parser("report", help="print the refinement or retro report as Markdown")
    p.add_argument("kind", choices=["refinement", "retro"])
    p.add_argument("--days", type=int)
    p.add_argument("--category", default="all", choices=["all", "work", "personal"])
    p.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
