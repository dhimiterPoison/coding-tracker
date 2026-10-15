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
| Context switches? | A *switch* is a prompt that goes to a different project than your previous prompt (across all sessions), plus jumps between code areas while editing and prompts sharing no keyword with your last few |
| Flow or distracted? | Active time is split into blocks (a gap over 10 min ends one). Blocks of 25+ min count as deep flow. Score 0-100 = 40% flow share, 25% longest block, 20% few interruptions, 15% few topic jumps. Labels: deep flow (70+), steady (40-69), scattered |
| Type of work | Keyword + file + tool heuristic: feature, bugfix, refactor, testing, docs, ui-design, devops-config, exploration, review, planning. Override it in the session drawer; your correction survives re-imports |

Three things keep the numbers honest:

- **Forks and resumes** replay their parent's history. Records are de-duplicated by uuid, so a prompt, commit or minute is counted in one session only.
- **Sessions that span days** are cut at midnight: each day gets only the time actually spent on it.
- **Parallel sessions**: *time on the clock* counts overlapping sessions once; *session time* adds them up. Both are shown. The clock still includes stretches where Claude ran unattended, so treat it as an upper bound on hands-on time.

You can also log manual sessions (meetings, pairing, anything Claude did not see), self-rate how a session
felt, and keep private notes.

## Views

- **Overview**: KPIs (time on the clock, deep flow, switches, interruptions) vs the previous period, the *Coach* (rule-based wins / watch-outs / things to try), activity per day by work type, when you focus, time by project.
- **Day**: one page per day with a strip of recent days. A timeline of focus stretches per project (so switching is visible), what shipped, where you left off, the coach, how your goals did, and the **Close the day** form. Closing saves your reflection (how it felt, one win, a first step per project, notes) plus a frozen copy of the review.
- **Goals**: the practices you are working on, judged per day against a weekly target (see below).
- **Sessions**: searchable log; click one for prompts, commits, files and notes.
- **Projects**: every repo with where you left off, 30-day activity and a Work/Personal switch. Good for skimming before standup.
- **Meeting prep**: a weekly *refinement* draft (per project: shipped, worked on, hot files, left off, your next step, things to raise) and a *sprint retro* draft (numbers vs previous sprint, went well / improve / try, goal progress, the wins you noted, how the days felt). Copy as Markdown.
- **Pick up where you left off** (top of Overview): the next steps you wrote when you last closed a day, or where each recent project stopped, plus this week's goals.

## Goals

A goal is a *practice*, not an outcome: write tests, protect a focus block, leave a next step, explain a decision.
Each is judged per day and counted against a weekly target, so a quiet day or a day off never counts against you.
Pick from templates or write your own.

| Tracking | How a day is judged |
|---|---|
| `focus_block` | a focus stretch of 25+ min on one project |
| `tests` | tests ran in 60%+ of the substantial sessions |
| `small_commits` | 3+ commits |
| `plan_first` | 10%+ of session time exploring, planning or reviewing |
| `low_switches` | project switches at or below your *calm* level (partly if below *switchy*) |
| `next_step` | a next step written for every project you touched (checked when you close the day) |
| `manual` | you answer met / partly / missed when you close the day |

Goals feed the coach on the Overview and Day pages, the day review, and the retro. These are heuristics about
what the diary can see, not a verdict on you as a developer; there are deliberately no scores, streaks or badges.

## Install (private local copy)

```bash
python scripts/install.py                         # installs into ~/.coding-tracker and imports history
python scripts/install.py --with-hook --with-skill   # also: auto-capture + /diary skill
~/.coding-tracker/start-diary.cmd                 # opens http://127.0.0.1:8765
```

The app is *copied* into its own virtualenv (`~/.coding-tracker/venv`), so editing this folder never affects
your running diary. Re-run the installer to upgrade; your data is untouched.

- `--with-hook` adds a `SessionEnd` hook to `~/.claude/settings.json` (a `.bak` copy is written first). Remove it with `tracker install-hook --remove`.
- `--with-skill` installs `/diary` (end-of-day review, wrap-up note, manual log, reports) into `~/.claude/skills/diary`. `/diary` alone runs the end-of-day review.
- While the dashboard runs it also re-syncs every 5 minutes, and **Sync** forces it, so nothing is lost if a session was closed abruptly.

## CLI

```
tracker sync [--force]       import new/changed transcripts
tracker serve [--port N]     dashboard on 127.0.0.1
tracker today                today's sessions, one line each
tracker day [--date D]       end-of-day review: timeline, focus stretches, where flow broke, shipped, where you left off
                             (--json for structured output, --category work to restrict)
tracker day --close --feeling 4 --win "Shipped X" --next "repo=write the tests" [--goal ID=met|partly|missed] [--narrative -]
                             close the day (saves your reflection + a frozen review; stdin with --narrative -)
tracker morning              what to pick up first, from your last closed day
tracker goals [templates | add "title" --metric tests --target 4 | archive ID]
tracker log --project X --minutes 45 --title "Pairing on auth" --type feature
tracker note "short progress summary" --cwd .
tracker report refinement|retro [--category work] [--days N]
tracker install-hook [--remove]
```

## Config

`~/.coding-tracker/data/config.json`: `idle_gap_minutes`, `flow_block_minutes`, `ignore_cwd_contains`,
`work_path_hints` (path fragments that mark a project as *work* automatically, e.g. `["C:\\Work"]`),
`default_category`, `sprint_length_days` (21), `refinement_length_days` (7), `switchy_switches_per_day` (10) and
`calm_switches_per_day` (3), which set when the coach warns about or praises project switching: tune them to how
many repos you really juggle. Set the category of an existing project from the Projects page.

## Privacy and security

- Prompt snippets (280 chars each) and Claude's last message are stored locally so the diary has substance. Delete `~/.coding-tracker/data` to erase everything; "Hide from stats" removes a session from all numbers.
- The server rejects non-local `Host` headers and requires an `X-Tracker` header on writes, so a random website cannot read or change your diary through your browser.

## Develop

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```

Layout: `tracker/parser.py` (transcript → session), `insights.py` (aggregation, scoring, coach),
`day.py` (end-of-day review), `reports.py` (meeting prep), `server.py` + `static/` (dashboard), `cli.py`, `scripts/install.py`.
