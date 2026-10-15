---
name: diary
description: Work with the user's private coding diary (Coding Diary). Use when the user says /diary, asks what they did today or this week, wants to log a manual session, wants a wrap-up note saved for the session, or wants meeting prep (weekly refinement or sprint retro). All data is local.
---

# Coding Diary

Sessions are imported automatically from Claude Code transcripts by a SessionEnd hook. This skill covers
the parts that need you: adding context, manual entries and meeting prep. The CLI is:

`"{{TRACKER}}"` — referred to below as `tracker`. Always quote the path.

## Wrap-up note (`/diary wrap`)

When the user wants to close out a session:

1. Write 2–4 sentences: what was accomplished, any decision and why, what is left / the next step.
   Base it on this conversation only; do not invent progress.
2. Save it on this session: `tracker note "<the note>" --cwd "<current working directory>"`
   (for multi-line text, pipe it via stdin: `tracker note --cwd "<cwd>"`).
3. Reply with the note you saved and nothing else.

## Log a manual session (`/diary log`)

For work Claude did not see (meetings, pairing, reading, debugging in an IDE). Ask only for what is missing
(project, minutes, title); infer the type from: feature, bugfix, refactor, testing, docs, ui-design,
devops-config, exploration, review, planning, other.

`tracker log --project "<name>" --minutes <n> --title "<what>" --type <type> [--day YYYY-MM-DD] [--start HH:MM] [--rating 1-5] [--notes "<text>"]`

## Today / this week

- `tracker today` lists today's sessions with type, duration and focus label.
- `tracker report refinement` (last 7 days) and `tracker report retro` (last 21 days) print Markdown for
  meeting prep. Add `--category work` to restrict to work projects, `--days N` to change the window.
  Summarise the output for the user; offer to paste-ready bullet points for refinement or retro.

## Dashboard

`tracker serve` starts the dashboard at http://127.0.0.1:8765 (local only). Do not start it unless asked.

## Rules

- Never send diary content anywhere outside this machine.
- Give honest, specific feedback when asked about habits (flow, interruptions, context switching),
  grounded in the numbers the CLI prints; do not flatter.
