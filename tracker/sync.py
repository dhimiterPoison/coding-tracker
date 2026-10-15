"""Import Claude Code transcripts into the diary database (idempotent, incremental)."""
from __future__ import annotations

import logging
from pathlib import Path

from . import config, db
from .parser import parse_transcript

log = logging.getLogger("tracker")


def _category_for(cfg: dict, project: str, root: str) -> str:
    for hint in cfg.get("work_path_hints", []):
        if hint and hint.lower() in (root or "").lower():
            return "work"
    return cfg.get("default_category", "personal")


def ingest_file(conn, path: Path, cfg: dict | None = None, force: bool = False) -> str:
    """Import one transcript. Returns 'imported' | 'unchanged' | 'skipped'."""
    cfg = cfg or config.load()
    try:
        st = path.stat()
    except OSError:
        return "skipped"
    if not force:
        row = conn.execute(
            "SELECT src_size, src_mtime FROM sessions WHERE src_path=?", (str(path),)).fetchone()
        if row and row["src_size"] == st.st_size and row["src_mtime"] == st.st_mtime:
            return "unchanged"
    parsed = parse_transcript(path, cfg)
    if not parsed:
        return "skipped"
    sid = parsed.pop("id")
    root = parsed.pop("project_path")
    db.ensure_project(conn, parsed["project"], root, _category_for(cfg, parsed["project"], root))
    db.upsert_parsed(conn, sid, parsed)
    return "imported"


def sync_all(force: bool = False, root: Path | None = None) -> dict:
    """Scan every project folder under ~/.claude/projects for top-level session transcripts."""
    root = root or config.CLAUDE_PROJECTS_DIR
    cfg = config.load()
    stats = {"imported": 0, "unchanged": 0, "skipped": 0}
    if not root.exists():
        return stats
    with db.session() as conn:
        for f in sorted(root.glob("*/*.jsonl")):
            try:
                stats[ingest_file(conn, f, cfg, force)] += 1
            except Exception:  # one corrupt file must not stop the sync
                log.exception("failed to ingest %s", f)
                stats["skipped"] += 1
    return stats
