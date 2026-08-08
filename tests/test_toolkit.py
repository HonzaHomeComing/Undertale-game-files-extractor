"""Tests for save editor, launcher helpers, battles, safer dogcheck stub."""

from __future__ import annotations

import struct
from pathlib import Path

from undertale_extractor import battles, launcher, save_editor
from undertale_extractor.dogcheck import (
    EXIT_WORD_V15,
    OP_POP_V15,
    OP_PUSHI_V15,
    disable_dogcheck,
    dogcheck_likely_disabled,
)
from undertale_extractor.teleport import ROOM_LINE_INDEX


def _minimal_save(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["CHARA"] + ["0"] * 549
    lines[1] = "1"
    lines[2] = "20"
    lines[3] = "20"
    lines[4] = "10"
    lines[6] = "10"
    lines[9] = "0"
    lines[10] = "0"
    lines[12] = "1"  # monster candy
    lines[28] = "3"
    lines[29] = "4"
    lines[ROOM_LINE_INDEX] = "6"
    path = folder / "file0"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_read_write_stats(tmp_path: Path):
    save = tmp_path / "UNDERTALE"
    _minimal_save(save)
    stats = save_editor.read_player_stats(save)
    assert stats.name == "CHARA"
    assert stats.love == 1
    assert stats.inventory[0] == 1
    stats.love = 5
    stats.gold = 999
    stats.inventory = [11, 11, 11, 11, 0, 0, 0, 0]
    stats.weapon = 52
    stats.armor = 53
    save_editor.write_player_stats(stats, save, backup=True)
    again = save_editor.read_player_stats(save)
    assert again.love == 5
    assert again.gold == 999
    assert again.inventory[:4] == [11, 11, 11, 11]
    assert again.weapon == 52
    assert (save / "file0.bak").exists()


def test_item_name():
    assert save_editor.item_name(0) == "Empty"
    assert save_editor.item_name(11) == "Butterscotch Pie"
    assert "Item" in save_editor.item_name(999)


def test_find_exe(tmp_path: Path):
    game = tmp_path / "Undertale"
    game.mkdir()
    (game / "data.win").write_bytes(b"FORM")
    exe = game / "UNDERTALE.exe"
    exe.write_bytes(b"MZ")
    found = launcher.find_undertale_exe(data_win=game / "data.win")
    assert found == exe


def test_set_home_battlegroup(tmp_path: Path):
    offset = battles.HOME_BATTLEGROUP_OFFSETS[0]
    data = bytearray(offset + 16)
    data[0:4] = b"FORM"
    struct.pack_into("<I", data, offset, 80)  # existing mettaton-ish id
    path = tmp_path / "data.win"
    path.write_bytes(data)
    ok, msg = battles.set_home_battlegroup(path, 95, backup=True)
    assert ok is True
    assert struct.unpack_from("<I", path.read_bytes(), offset)[0] == 95
    assert path.with_suffix(".win.battlebak").exists()


def test_dogcheck_stub_does_not_wipe_trailing_bytes(tmp_path: Path):
    """Safer stub only overwrites push+pop+exit, not the whole CODE length."""
    pushi = struct.pack("<I", (OP_PUSHI_V15 << 24) | 1)
    pop = struct.pack("<I", (OP_POP_V15 << 24)) + struct.pack("<I", 0x11111111)
    marker = b"KEEPME!!"
    bytecode = pushi + pop + (b"\x22" * 8) + marker + (b"\x33" * 16)

    # minimal FORM+CODE bc14 layout (same as dogcheck tests)
    name = b"gml_Script_scr_dogcheck"
    code = bytearray()
    code += struct.pack("<I", 1)
    entry_ptr_pos = len(code)
    code += struct.pack("<I", 0)
    entry_body_pos = len(code)
    name_ptr_pos = len(code)
    code += struct.pack("<I", 0)
    code += struct.pack("<I", len(bytecode))
    code += bytecode
    str_blob = struct.pack("<I", len(name)) + name + b"\x00"
    buf = bytearray(b"FORM" + struct.pack("<I", 0) + b"CODE" + struct.pack("<I", len(code)))
    code_at = len(buf)
    buf += code
    str_at = len(buf)
    buf += str_blob
    buf[4:8] = struct.pack("<I", len(buf) - 8)
    entry_abs = code_at + entry_body_pos
    buf[code_at + entry_ptr_pos : code_at + entry_ptr_pos + 4] = struct.pack("<I", entry_abs)
    buf[code_at + name_ptr_pos : code_at + name_ptr_pos + 4] = struct.pack("<I", str_at + 4)
    bc_off = entry_abs + 8

    path = tmp_path / "data.win"
    path.write_bytes(buf)
    ok, msg = disable_dogcheck(path, backup=True)
    assert ok is True, msg
    data = path.read_bytes()
    assert data[bc_off + 12 : bc_off + 16] == EXIT_WORD_V15
    # Trailing marker after the original push+pop+8bytes should still exist
    assert marker in data[bc_off:]
    assert dogcheck_likely_disabled(path) is True
