---
name: diary
description: Work with the user's private coding diary (Coding Diary). Use when the user says /diary, asks to review or wrap up their day ("how did today go", "end of day", "where did I lose focus", "what did I do today/yesterday"), wants a wrap-up note saved for the session, wants to log a manual session, or wants meeting prep (weekly refinement or sprint retro). All data is local.
---

# Coding Diary

Sessions are imported automatically from Claude Code transcripts by a SessionEnd hook. This skill covers
the parts that need you: the end-of-day review, adding context, manual entries and meeting prep. The CLI is:

`"{{TRACKER}}"` — referred to below as `tracker`. Always quote the path.

`/diary` with no argument means `/diary day`.

## End-of-day review (`/diary day`)

Goal: let the user see what happened today, where their flow held, where they lost it, and what to
change tomorrow. Default is today; for another day use `--date YYYY-MM-DD` ("yesterday" → work out the
date). Add `--category work` if the user wants work projects only.

1. Run `tracker day` (add `--date …` if asked). It syncs first, so a session still open is included.
   If it says no sessions were recorded, say so and offer `/diary log` for work Claude did not see.
2. Write the review from that output only, in this shape and no longer than needed:
   - **What happened**: 3–5 bullets, per project, outcome first (use *Shipped*, the session titles in the
     *Timeline* and *Where you left off*). Lead with what moved, not with minutes.
   - **Where flow held**: the focus stretches, with times and project. Say what made them work if the data
     shows it (few prompts per interruption, one project, a long uninterrupted run).
   - **Where flow broke**: each fragmentation episode and interruption burst from *Where flow broke*, with
     its time window, the projects involved and the likely cause (partner ping, unclear task, parallel
     sessions). If there are none, say flow was not seriously broken; do not invent a problem.
   - **Try tomorrow**: one to three concrete changes, each tied to something in today's numbers (for
     example "park campaign-base in a note until 11:00", "write acceptance criteria before the next
     prompt on X"). Compare with the *usual* numbers when the output gives them.
   - **First step tomorrow**: from *Where you left off*, one line per project that has unfinished work.
3. Be honest and specific. Cite times and numbers; do not flatter, and do not pad a good day with
   criticism or a bad day with comfort.
4. Ask one short question at the end only if the data cannot tell you something that matters, for
   example a long gap with no activity ("what happened between 12:00 and 14:30, a meeting?"). Offer
   `/diary log` for anything Claude did not see.
5. If the user wants it for their notes or standup, give 3–5 plain lines they can paste. Do not edit any
   file for them unless they ask.

How to read the numbers (say so when it matters for a conclusion):
- **On the clock** is the time at least one session was active, counted once. **Session time** adds
  sessions up, so parallel sessions exceed the clock. The clock includes stretches where Claude worked
  unattended, so it is an upper bound on hands-on time.
- A **switch** is a prompt that goes to a different project than your previous prompt, across all sessions.
- A **focus stretch** is consecutive prompts on one project that held 25+ minutes (the threshold is in the config).
- The diary only sees Claude Code sessions plus what the user logged by hand.

## Wrap-up note (`/diary wrap`)

When the user wants to close out a session:

1. Write 2–4 sentences: what was accomplished, any decision and why, what is left / the next step.
   Base it on this conversation only; do not invent progress.
2. Save it on this session: `tracker note "<the note>" --cwd "<current working directory>"`
   (for multi-line text, pipe it via stdin: `tracker note --cwd "<cwd>"`).
3. Reply with the note you saved and nothing else.

Notes show up in the day review under *Your notes*, so a good wrap-up makes tomorrow's review better.

## Log a manual session (`/diary log`)

For work Claude did not see (meetings, pairing, reading, debugging in an IDE). Ask only for what is missing
(project, minutes, title); infer the type from: feature, bugfix, refactor, testing, docs, ui-design,
devops-config, exploration, review, planning, other.

`tracker log --project "<name>" --minutes <n> --title "<what>" --type <type> [--day YYYY-MM-DD] [--start HH:MM] [--rating 1-5] [--notes "<text>"]`

## Quick look and meeting prep

- `tracker today` is a one-line summary plus today's sessions.
- `tracker report refinement` (last 7 days) and `tracker report retro` (last 21 days) print Markdown for
  meeting prep. Add `--category work` to restrict to work projects, `--days N` to change the window.
  Summarise the output for the user; offer paste-ready bullet points for refinement or retro.

## Dashboard

`tracker serve` starts the dashboard at http://127.0.0.1:8765 (local only). Do not start it unless asked.

## Rules

- Never send diary content anywhere outside this machine.
- Give honest, specific feedback when asked about habits (flow, interruptions, context switching),
  grounded in the numbers the CLI prints; do not flatter.
