"""Tests for live teleport helpers (no Undertale process required)."""

from __future__ import annotations

from pathlib import Path

from undertale_extractor import live_teleport as lt


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


def test_undertale_is_running_safe():
    # Should not crash on Linux CI
    assert lt.undertale_is_running() in (True, False)


def test_live_teleport_not_running_message():
    result, cache = lt.live_teleport_to_room(10, current_room=5)
    assert result.ok is False
    assert cache == []
    assert result.method in {"not_running", "unsupported"}
