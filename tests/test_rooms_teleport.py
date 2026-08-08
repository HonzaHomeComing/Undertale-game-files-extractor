"""Tests for room listing and save-file teleport."""

from __future__ import annotations

from pathlib import Path

from undertale_extractor.assets import AssetKind
from undertale_extractor.parser import load_undertale_assets
from undertale_extractor.teleport import (
    ROOM_LINE_INDEX,
    friendly_room_label,
    read_save_info,
    teleport_to_room,
)
from tests.fixture_builder import build_minimal_data_win


def _fake_save(folder: Path, room: int = 10) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["CHARA"] + ["0"] * 548
    lines[ROOM_LINE_INDEX] = str(room)
    lines.append("1234")  # time line
    (folder / "file0").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / "undertale.ini").write_text(
        '[General]\nName="CHARA"\nRoom="10"\nLove="1"\n',
        encoding="utf-8",
    )
    return folder


def test_parse_rooms(tmp_path: Path):
    build_minimal_data_win(tmp_path / "data.win")
    result = load_undertale_assets(tmp_path, include_loose=False)
    rooms = [a for a in result.assets if a.kind == AssetKind.ROOM]
    assert len(rooms) == 2
    assert rooms[0].meta["room_id"] == 0
    assert rooms[0].name == "room_ruins1"
    assert rooms[1].name == "room_torielhouse"
    assert rooms[0].is_room


def test_teleport_updates_file0_and_ini(tmp_path: Path):
    save = _fake_save(tmp_path / "UNDERTALE", room=10)
    info = teleport_to_room(42, save)
    assert info.current_room == 42
    lines = (save / "file0").read_text(encoding="utf-8").splitlines()
    assert int(float(lines[ROOM_LINE_INDEX])) == 42
    assert (save / "file0.bak").exists()
    ini = (save / "undertale.ini").read_text(encoding="utf-8")
    assert 'Room="42"' in ini or "Room=42" in ini


def test_friendly_room_label():
    assert "ruins1" in friendly_room_label("room_ruins1", 7).lower()
    assert "007" in friendly_room_label("room_ruins1", 7)


def test_read_save_info(tmp_path: Path):
    save = _fake_save(tmp_path / "UNDERTALE", room=99)
    info = read_save_info(save)
    assert info.current_room == 99
    assert info.player_name == "CHARA"
