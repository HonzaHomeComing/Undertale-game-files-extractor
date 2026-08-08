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


def test_live_teleport_restart_required(tmp_path: Path):
    if not lt.is_windows():
        return
    # Can't easily fake a running process on CI; just ensure API accepts data_win
    data = bytearray(0x780000)
    path = tmp_path / "data.win"
    path.write_bytes(data)
    # Without a running game this returns not_running before restart check
    result, _ = lt.live_teleport_to_room(5, data_win=path)
    assert result.method in {"not_running", "unsupported", "restart_required"}
