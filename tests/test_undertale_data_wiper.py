"""Tests for UndertaleDataWiper.py discovery and wipe helpers."""

from __future__ import annotations

from pathlib import Path

import UndertaleDataWiper as wiper


def test_discover_local_saves(tmp_path: Path, monkeypatch):
    local = tmp_path / "Local"
    saves = local / "UNDERTALE"
    saves.mkdir(parents=True)
    (saves / "file0").write_text("save", encoding="utf-8")
    (saves / "undertale.ini").write_text("[General]\nName=Frisk\n", encoding="utf-8")

    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(wiper, "_steam_roots", lambda: [])

    scan = wiper.discover_undertale_data(include_game_install=False)
    assert len(scan.targets) == 1
    assert scan.targets[0].category == "Saves"
    assert scan.targets[0].path == saves
    assert scan.targets[0].size_bytes() > 0


def test_discover_steam_cloud_and_game(tmp_path: Path, monkeypatch):
    steam = tmp_path / "Steam"
    user = steam / "userdata" / "12345" / wiper.STEAM_APP_ID
    user.mkdir(parents=True)
    (user / "remote").mkdir()
    (user / "remote" / "file9").write_bytes(b"x")

    game = steam / "steamapps" / "common" / "Undertale"
    game.mkdir(parents=True)
    (game / "data.win").write_bytes(b"FORM")
    (game / "data.win.dogcheckbak").write_bytes(b"bak")

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty_local"))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(wiper, "_steam_roots", lambda: [steam])
    monkeypatch.setattr(wiper, "_steam_library_folders", lambda _root: [steam])

    scan = wiper.discover_undertale_data(include_game_install=True)
    cats = {t.category for t in scan.targets}
    assert "Steam cloud" in cats
    assert "Game install" in cats
    assert "Backups" in cats
    paths = {t.path for t in scan.targets}
    assert user in paths
    assert game in paths
    assert (game / "data.win.dogcheckbak") in paths


def test_wipe_targets_deletes_files_and_dirs(tmp_path: Path):
    folder = tmp_path / "UNDERTALE"
    folder.mkdir()
    (folder / "file0").write_text("gone", encoding="utf-8")
    bak = tmp_path / "data.win.bak"
    bak.write_bytes(b"bak")

    targets = [
        wiper.WipeTarget(folder, "Saves", "test"),
        wiper.WipeTarget(bak, "Backups", "test"),
    ]
    ok, errors = wiper.wipe_targets(targets)
    assert ok == 2
    assert errors == []
    assert not folder.exists()
    assert not bak.exists()


def test_fmt_size():
    assert wiper._fmt_size(500) == "500 B"
    assert "KB" in wiper._fmt_size(2048)
    assert "MB" in wiper._fmt_size(2 * 1024 * 1024)


def test_scan_only_cli(tmp_path: Path, monkeypatch, capsys):
    local = tmp_path / "Local"
    (local / "UNDERTALE").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(wiper, "_steam_roots", lambda: [])
    code = wiper.main(["--scan-only"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Saves" in out
    assert "UNDERTALE" in out
