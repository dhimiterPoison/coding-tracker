"""Import Claude Code transcripts into the diary database (idempotent, incremental)."""
from __future__ import annotations

import logging
from pathlib import Path

from . import config, db
from .parser import PARSER_VERSION, family_of, parse_transcript, uuids_of

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
            "SELECT src_size, src_mtime, parser_version FROM sessions WHERE src_path=?", (str(path),)).fetchone()
        if (row and row["src_size"] == st.st_size and row["src_mtime"] == st.st_mtime
                and row["parser_version"] == PARSER_VERSION):
            return "unchanged"
    return _import(conn, path, cfg, cascade=True)


def _family_members(conn, family: str | None, path: Path) -> list[Path]:
    if not family:
        return []
    rows = conn.execute("SELECT src_path FROM sessions WHERE family=? AND src_path IS NOT NULL AND src_path!=?",
                        (family, str(path))).fetchall()
    return [Path(r["src_path"]) for r in rows if Path(r["src_path"]).exists()]


def _import(conn, path: Path, cfg: dict, cascade: bool) -> str:
    """Parse and store one transcript. Forks copy their parent's history; the member of a family with
    the smallest file name keeps it, later members skip every record an earlier one already holds."""
    members = _family_members(conn, family_of(path), path)
    skip: set[str] = set()
    for other in members:
        if other.stem < path.stem:
            skip |= uuids_of(other)
    parsed = parse_transcript(path, cfg, skip)
    if not parsed:
        # A fork with nothing of its own left: drop its stale row, unless it carries something you wrote.
        conn.execute("DELETE FROM sessions WHERE src_path=? AND COALESCE(notes,'')='' AND rating IS NULL "
                     "AND type_override IS NULL AND hidden=0", (str(path),))
        return "skipped"
    sid = parsed.pop("id")
    root = parsed.pop("project_path")
    db.ensure_project(conn, parsed["project"], root, _category_for(cfg, parsed["project"], root))
    db.upsert_parsed(conn, sid, parsed)
    if cascade:   # later members were imported without knowing about this one: redo them
        for other in members:
            if other.stem > path.stem:
                _import(conn, other, cfg, cascade=False)
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
