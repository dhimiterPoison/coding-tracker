import json


def test_config_with_bom_is_still_read(home):
    """Notepad and PowerShell write a UTF-8 BOM; it must not silently reset the config to defaults."""
    from tracker import config
    config.ensure_dirs()
    config.CONFIG_PATH.write_bytes(b"\xef\xbb\xbf" + json.dumps({"work_path_hints": ["\\MFF\\"]}).encode())
    assert config.load()["work_path_hints"] == ["\\MFF\\"]


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
