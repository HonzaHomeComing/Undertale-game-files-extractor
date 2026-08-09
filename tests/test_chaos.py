"""Tests for chaos helpers, rare mode, and memory-patch API shape."""

from __future__ import annotations

import struct
from pathlib import Path

from undertale_extractor import battles, chaos, save_editor
from undertale_extractor.teleport import ROOM_LINE_INDEX


def _minimal_save(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["CHARA"] + ["0"] * 549
    lines[1] = "5"
    lines[2] = "99"
    lines[3] = "99"
    lines[4] = "50"
    lines[6] = "50"
    lines[9] = "999"
    lines[10] = "500"
    lines[11] = "10"
    lines[28] = "3"
    lines[29] = "4"
    lines[ROOM_LINE_INDEX] = "80"
    lines[chaos.LINE_FUN] = "0"
    path = folder / "file0"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_fresh_ruins_stats():
    s = chaos.fresh_ruins_stats("FRISK")
    assert s.name == "FRISK"
    assert s.love == 1
    assert s.hp == 20 and s.max_hp == 20
    assert s.exp == 0 and s.gold == 0 and s.kills == 0
    assert s.room == chaos.RUINS_FIRST_SAVE_ROOM
    assert s.weapon == 3 and s.armor == 4


def test_live_ruins_reset_writes_save(tmp_path: Path, monkeypatch):
    save = tmp_path / "UNDERTALE"
    _minimal_save(save)
    monkeypatch.setattr(chaos, "undertale_is_running", lambda: False)
    ok, msg = chaos.live_ruins_reset(save_folder=save, data_win=None)
    assert ok is True
    stats = save_editor.read_player_stats(save)
    assert stats.love == 1
    assert stats.hp == 20
    assert stats.exp == 0
    assert stats.gold == 0
    assert stats.kills == 0
    assert stats.room == chaos.RUINS_FIRST_SAVE_ROOM
    assert "room" in msg.lower() or "Ruins" in msg


def test_rare_mode_toggle(tmp_path: Path, monkeypatch):
    save = tmp_path / "UNDERTALE"
    _minimal_save(save)
    monkeypatch.setattr(chaos, "undertale_is_running", lambda: False)
    assert chaos.rare_mode_enabled(save) is False
    ok, _msg = chaos.set_rare_encounters(True, save_folder=save, live_reload=False)
    assert ok is True
    assert chaos.rare_mode_enabled(save) is True
    lines = (save / "file0").read_text(encoding="utf-8").splitlines()
    assert lines[chaos.LINE_FUN] == "90"
    assert (save / "extractor_rare_mode.json").is_file()
    ok2, _ = chaos.set_rare_encounters(False, save_folder=save, live_reload=False)
    assert ok2 is True
    assert chaos.rare_mode_enabled(save) is False
    lines2 = (save / "file0").read_text(encoding="utf-8").splitlines()
    assert lines2[chaos.LINE_FUN] == "0"


def test_is_text_room():
    assert chaos._is_text_or_special_room("room_intro_chara") is True
    assert chaos._is_text_or_special_room("room_battle_froggit") is True
    assert chaos._is_text_or_special_room("room_ruins1") is False
    assert chaos._is_text_or_special_room("room_tundra1") is False


def test_room_transition_script_filter():
    assert chaos._is_room_transition_script("gml_Object_obj_doorA_Collision_xxx")
    assert chaos._is_room_transition_script("gml_Object_obj_doorway_Create_0")
    assert not chaos._is_room_transition_script("gml_Script_ossafe_file_text_eof")
    assert not chaos._is_room_transition_script("gml_Object_obj_time_Create_0")
    assert not chaos._is_room_transition_script("gml_Script_scr_load")


def test_restore_room_chaos(tmp_path: Path):
    path = tmp_path / "data.win"
    path.write_bytes(b"FORM_CORRUPT")
    bak = path.with_suffix(".win.roomchaosbak")
    bak.write_bytes(b"FORM_CLEAN!!")
    ok, msg = chaos.restore_room_chaos(path)
    assert ok is True
    assert path.read_bytes() == b"FORM_CLEAN!!"
    assert "roomchaosbak" in msg


def test_randomize_room_gotos_rewrites_pushi(tmp_path: Path):
    """Build a tiny FORM+CODE with PushI room;Call and shuffle destinations."""
    OP_PUSHI = 0x84
    OP_CALL = 0xD9
    bytecode = b""
    for rid in (0, 1, 2):
        bytecode += struct.pack("<I", (OP_PUSHI << 24) | rid)
        bytecode += struct.pack("<I", (OP_CALL << 24)) + struct.pack("<I", 0)

    name = b"gml_Object_obj_doorA_Collision_0"
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

    buf = bytearray(b"FORM" + struct.pack("<I", 0))
    buf += b"CODE" + struct.pack("<I", len(code))
    code_at = len(buf)
    buf += code
    str_at = len(buf)
    buf += str_blob
    buf[4:8] = struct.pack("<I", len(buf) - 8)
    entry_abs = code_at + entry_body_pos
    buf[code_at + entry_ptr_pos : code_at + entry_ptr_pos + 4] = struct.pack("<I", entry_abs)
    buf[code_at + name_ptr_pos : code_at + name_ptr_pos + 4] = struct.pack("<I", str_at + 4)

    path = tmp_path / "data.win"
    path.write_bytes(buf)

    orig = chaos.playable_room_ids
    chaos.playable_room_ids = lambda _p: list(range(12))  # type: ignore
    try:
        ok, msg, mapping = chaos.randomize_room_gotos(path, seed=1, backup=True)
    finally:
        chaos.playable_room_ids = orig
    assert ok is True, msg
    assert mapping
    assert path.with_suffix(".win.roomchaosbak").exists()
    data = path.read_bytes()
    found_vals = []
    bc_off = entry_abs + 8
    for i in range(3):
        word = struct.unpack_from("<I", data, bc_off + i * 12)[0]
        found_vals.append(word & 0xFFFF)
    assert all(v in mapping.values() for v in found_vals)


def test_randomize_skips_ossafe_scripts(tmp_path: Path):
    """PushI+Call in ossafe scripts must not be rewritten."""
    OP_PUSHI = 0x84
    OP_CALL = 0xD9
    bytecode = struct.pack("<I", (OP_PUSHI << 24) | 6)
    bytecode += struct.pack("<I", (OP_CALL << 24)) + struct.pack("<I", 0)
    name = b"gml_Script_ossafe_file_text_eof"
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
    buf = bytearray(b"FORM" + struct.pack("<I", 0))
    buf += b"CODE" + struct.pack("<I", len(code))
    code_at = len(buf)
    buf += code
    str_at = len(buf)
    buf += str_blob
    buf[4:8] = struct.pack("<I", len(buf) - 8)
    entry_abs = code_at + entry_body_pos
    buf[code_at + entry_ptr_pos : code_at + entry_ptr_pos + 4] = struct.pack("<I", entry_abs)
    buf[code_at + name_ptr_pos : code_at + name_ptr_pos + 4] = struct.pack("<I", str_at + 4)
    path = tmp_path / "data.win"
    path.write_bytes(buf)
    orig = chaos.playable_room_ids
    chaos.playable_room_ids = lambda _p: list(range(12))  # type: ignore
    try:
        ok, msg, _ = chaos.randomize_room_gotos(path, seed=1, backup=True)
    finally:
        chaos.playable_room_ids = orig
    assert ok is False
    word = struct.unpack_from("<I", path.read_bytes(), entry_abs + 8)[0]
    assert (word & 0xFFFF) == 6



def test_rare_battlegroups_marked():
    assert battles.RARE_BATTLEGROUPS
    assert all(b.rare for b in battles.RARE_BATTLEGROUPS)
    assert any(b.name == "Sans" for b in battles.RARE_BATTLEGROUPS)


def test_memory_patch_api_imports():
    from undertale_extractor import memory_patch

    assert callable(memory_patch.patch_int32_in_data_win_image)
    assert callable(memory_patch.write_int32_in_running_game)
