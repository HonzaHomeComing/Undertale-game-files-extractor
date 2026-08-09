"""Tests for live teleport helpers (no Undertale process required)."""

from __future__ import annotations

from pathlib import Path

from undertale_extractor import live_teleport as lt
from undertale_extractor.teleport import ROOM_LINE_INDEX


def test_enable_debug_mode_known_offset(tmp_path: Path):
    data = bytearray(0x780000)
    offset = 0x725B24
    data[offset] = 0
    path = tmp_path / "data.win"
    path.write_bytes(data)
    assert lt.enable_debug_mode(path, backup=True) is True
    assert path.read_bytes()[offset] == 1
    assert path.with_suffix(".win.debugbak").exists()


def test_enable_debug_already_on(tmp_path: Path):
    data = bytearray(0x780000)
    for offset in lt.DEBUG_OFFSETS:
        if offset < len(data):
            data[offset] = 1
    path = tmp_path / "data.win"
    path.write_bytes(data)
    assert lt.enable_debug_mode(path, backup=True) is True
    assert not path.with_suffix(".win.debugbak").exists()
    assert path.read_bytes() == bytes(data)


def test_debug_flag_enabled(tmp_path: Path):
    data = bytearray(0x780000)
    path = tmp_path / "data.win"
    path.write_bytes(data)
    assert lt.debug_flag_enabled(path) is False
    data[0x725B24] = 1
    path.write_bytes(data)
    assert lt.debug_flag_enabled(path) is True


def test_undertale_is_running_safe():
    assert lt.undertale_is_running() in (True, False)


def test_title_looks_like_game_excludes_extractor():
    assert lt._title_looks_like_game("UNDERTALE") is True
    assert lt._title_looks_like_game("Undertale") is True
    assert lt._title_looks_like_game("Undertale File Extractor — UNDERTALE") is False
    assert lt._title_looks_like_game("UNDERTALE Game File Extractor") is False
    assert lt._title_looks_like_game("Undertale Data Wiper") is False
    assert lt._title_looks_like_game("") is False


def test_live_teleport_not_running_message(tmp_path: Path):
    # Minimal save so save-update path isn't the failure mode
    save = tmp_path / "UNDERTALE"
    save.mkdir()
    lines = ["CHARA"] + ["0"] * 548
    lines[ROOM_LINE_INDEX] = "10"
    (save / "file0").write_text("\n".join(lines) + "\n", encoding="utf-8")

    result, cache = lt.live_teleport_to_room(101, save_folder=save)
    assert result.ok is False
    assert cache == []
    assert result.method in {"not_running", "unsupported"}


def test_clear_ini_battle_traps(tmp_path: Path):
    save = tmp_path / "UNDERTALE"
    save.mkdir()
    lines = ["CHARA"] + ["0"] * 548
    lines[ROOM_LINE_INDEX] = "10"
    (save / "file0").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (save / "undertale.ini").write_text(
        "[General]\nName=\"CHARA\"\nRoom=\"10\"\n[FFFFF]\nF=\"1\"\nP=\"2\"\n",
        encoding="utf-8",
    )
    lt._clear_ini_battle_traps(save)
    text = (save / "undertale.ini").read_text(encoding="utf-8")
    assert 'F="0"' in text or "F=0" in text
