import json


def test_config_with_bom_is_still_read(home):
    """Notepad and PowerShell write a UTF-8 BOM; it must not silently reset the config to defaults."""
    from tracker import config
    config.ensure_dirs()
    config.CONFIG_PATH.write_bytes(b"\xef\xbb\xbf" + json.dumps({"work_path_hints": ["\\Work\\"]}).encode())
    assert config.load()["work_path_hints"] == ["\\Work\\"]


def test_cli_output_survives_a_legacy_windows_codepage(home):
    """Piped stdout on Windows defaults to cp1252, which cannot print the arrows and stars in our output."""
    import os
    import subprocess
    import sys
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    r = subprocess.run([sys.executable, "-m", "tracker", "stats"], env=env, capture_output=True)
    assert r.returncode == 0, r.stderr.decode(errors="replace")
    assert "→".encode("utf-8") in r.stdout


def test_install_hook_keeps_unicode_in_settings(tmp_path, monkeypatch):
    from pathlib import Path
    from tracker import cli
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"note": "keep — this"}, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["install-hook"]) == 0
    text = settings.read_text(encoding="utf-8")
    assert "keep — this" in text and "SessionEnd" in text
    assert cli.main(["install-hook", "--remove"]) == 0
    assert "SessionEnd" not in settings.read_text(encoding="utf-8")
