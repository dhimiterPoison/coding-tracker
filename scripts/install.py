"""Install Coding Diary into a private local folder (default ~/.coding-tracker).

    python scripts/install.py                       # app + launcher only
    python scripts/install.py --with-hook --with-skill   # also wire it into Claude Code

The app is installed (copied, not linked) into its own virtualenv, so this dev folder can change
without affecting your running diary. Your data lives in <home>/data and is never touched by upgrades.
Re-running the script upgrades the app in place.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import venv
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIN = sys.platform == "win32"


def run(cmd: list[str]) -> None:
    print("  $", " ".join(map(str, cmd)))
    subprocess.run(cmd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=os.environ.get("CODING_TRACKER_HOME", str(Path.home() / ".coding-tracker")))
    ap.add_argument("--with-hook", action="store_true", help="register the SessionEnd hook in ~/.claude/settings.json")
    ap.add_argument("--with-skill", action="store_true", help="install the /diary skill into ~/.claude/skills")
    args = ap.parse_args()

    home = Path(args.home).expanduser()
    env_dir = home / "venv"
    bindir = env_dir / ("Scripts" if WIN else "bin")
    py = bindir / ("python.exe" if WIN else "python")
    exe = bindir / ("tracker.exe" if WIN else "tracker")

    print(f"Installing into {home}")
    home.mkdir(parents=True, exist_ok=True)
    if not py.exists():
        venv.EnvBuilder(with_pip=True).create(env_dir)
    run([str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run([str(py), "-m", "pip", "install", "--quiet", "--upgrade", "--force-reinstall", "--no-deps", str(REPO)])
    run([str(py), "-m", "pip", "install", "--quiet", str(REPO)])  # dependencies

    env = dict(os.environ, CODING_TRACKER_HOME=str(home))
    print("Importing existing Claude Code sessions…")
    subprocess.run([str(exe), "sync"], check=True, env=env)

    launcher = home / ("start-diary.cmd" if WIN else "start-diary.sh")
    if WIN:
        launcher.write_text(
            f'@echo off\r\nset CODING_TRACKER_HOME={home}\r\nstart "" http://127.0.0.1:8765\r\n"{exe}" serve\r\n',
            encoding="utf-8")
    else:
        launcher.write_text(
            f'#!/bin/sh\nexport CODING_TRACKER_HOME="{home}"\n(sleep 1; xdg-open http://127.0.0.1:8765 '
            f'|| open http://127.0.0.1:8765) >/dev/null 2>&1 &\nexec "{exe}" serve\n', encoding="utf-8")
        launcher.chmod(0o755)
    print(f"Launcher: {launcher}")

    if args.with_hook:
        subprocess.run([str(exe), "install-hook"], check=True, env=env)
    if args.with_skill:
        skill_dir = Path.home() / ".claude" / "skills" / "diary"
        skill_dir.mkdir(parents=True, exist_ok=True)
        text = (REPO / "skill" / "diary" / "SKILL.md").read_text(encoding="utf-8")
        (skill_dir / "SKILL.md").write_text(text.replace("{{TRACKER}}", str(exe)), encoding="utf-8")
        print(f"Skill installed: {skill_dir / 'SKILL.md'}")

    print("\nDone. Start the dashboard with:", launcher)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
