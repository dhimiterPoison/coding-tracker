# Coding Diary

A private, local diary of your coding sessions. It reads the transcripts Claude Code already writes on your
machine, works out what you did and how focused it was, and gives you a dashboard plus meeting prep for
weekly refinement and sprint retros. **No cloud, no telemetry, no external requests.** Data lives in
`~/.coding-tracker/data/diary.db`; the server only listens on `127.0.0.1`.

## What it records per session

| Question | How it is derived |
|---|---|
| What tasks were worked on? | Your prompts (timeline), commit messages, files edited, Claude's last message ("where it ended") |
| Interruptions? | You pressing Esc, or rejecting a tool call, per active hour |
| Context switches? | Project switches between active blocks in a day, jumps between code areas while editing, prompts unrelated to the previous one |
| Flow or distracted? | Active time is split into blocks (a gap over 10 min ends one). Blocks of 25+ min count as deep flow. Score 0-100 = 40% flow share, 25% longest block, 20% few interruptions, 15% few topic jumps. Labels: deep flow (70+), steady (40-69), scattered |
| Type of work | Keyword + file + tool heuristic: feature, bugfix, refactor, testing, docs, ui-design, devops-config, exploration, review, planning. Override it in the session drawer; your correction survives re-imports |

You can also log manual sessions (meetings, pairing, anything Claude did not see), self-rate how a session
felt, and keep private notes.

## Views

- **Overview**: KPIs vs the previous period, the *Coach* (rule-based wins / watch-outs / things to try), activity per day by work type, when you focus, time by project.
- **Sessions**: searchable log; click one for prompts, commits, files and notes.
- **Projects**: every repo with where you left off, 30-day activity and a Work/Personal switch. Good for skimming before standup.
- **Meeting prep**: a weekly *refinement* draft (per project: shipped, worked on, hot files, left off, things to raise) and a *sprint retro* draft (numbers vs previous sprint, went well / improve / try). Copy as Markdown.

## Install (private local copy)

```bash
python scripts/install.py                         # installs into ~/.coding-tracker and imports history
python scripts/install.py --with-hook --with-skill   # also: auto-capture + /diary skill
~/.coding-tracker/start-diary.cmd                 # opens http://127.0.0.1:8765
```

The app is *copied* into its own virtualenv (`~/.coding-tracker/venv`), so editing this folder never affects
your running diary. Re-run the installer to upgrade; your data is untouched.

- `--with-hook` adds a `SessionEnd` hook to `~/.claude/settings.json` (a `.bak` copy is written first). Remove it with `tracker install-hook --remove`.
- `--with-skill` installs `/diary` (wrap-up note, manual log, today/report) into `~/.claude/skills/diary`.
- While the dashboard runs it also re-syncs every 5 minutes, and **Sync** forces it, so nothing is lost if a session was closed abruptly.

## CLI

```
tracker sync [--force]       import new/changed transcripts
tracker serve [--port N]     dashboard on 127.0.0.1
tracker today                today's sessions
tracker log --project X --minutes 45 --title "Pairing on auth" --type feature
tracker note "short progress summary" --cwd .
tracker report refinement|retro [--category work] [--days N]
tracker install-hook [--remove]
```

## Config

`~/.coding-tracker/data/config.json`: `idle_gap_minutes`, `flow_block_minutes`, `ignore_cwd_contains`,
`work_path_hints` (path fragments that mark a project as *work* automatically, e.g. `["C:\\Work"]`),
`default_category`, `sprint_length_days` (21), `refinement_length_days` (7). Set the category of an existing
project from the Projects page.

## Privacy and security

- Prompt snippets (280 chars each) and Claude's last message are stored locally so the diary has substance. Delete `~/.coding-tracker/data` to erase everything; "Hide from stats" removes a session from all numbers.
- The server rejects non-local `Host` headers and requires an `X-Tracker` header on writes, so a random website cannot read or change your diary through your browser.

## Develop

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```

Layout: `tracker/parser.py` (transcript → session), `insights.py` (aggregation, scoring, coach),
`reports.py` (meeting prep), `server.py` + `static/` (dashboard), `cli.py`, `scripts/install.py`.
