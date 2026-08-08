"""Tests for dogcheck disable helper."""

from __future__ import annotations

import struct
from pathlib import Path

from undertale_extractor.binary import read_game_file_bytes
from undertale_extractor.dogcheck import (
    EXIT_WORD,
    EXIT_WORD_V15,
    OP_POP_V15,
    OP_PUSHI_V15,
    disable_dogcheck,
    dogcheck_exit_stubbed,
    dogcheck_likely_disabled,
    is_dogcheck_room,
    restore_data_win_backup,
)


def _build_dogcheck_form(bytecode: bytes) -> tuple[bytearray, int]:
    name = b"gml_Script_scr_dogcheck"
    name_len = len(name)
    bc_len = len(bytecode)

    code = bytearray()
    code += struct.pack("<I", 1)
    entry_ptr_pos = len(code)
    code += struct.pack("<I", 0)
    entry_body_pos = len(code)
    name_ptr_pos = len(code)
    code += struct.pack("<I", 0)
    code += struct.pack("<I", bc_len)
    code += bytecode
    str_blob = struct.pack("<I", name_len) + name + b"\x00"

    buf = bytearray()
    buf += b"FORM"
    size_pos = len(buf)
    buf += struct.pack("<I", 0)
    buf += b"CODE"
    buf += struct.pack("<I", len(code))
    code_payload_at = len(buf)
    buf += code
    str_at = len(buf)
    buf += str_blob
    buf[size_pos : size_pos + 4] = struct.pack("<I", len(buf) - 8)

    entry_abs = code_payload_at + entry_body_pos
    name_chars = str_at + 4
    buf[code_payload_at + entry_ptr_pos : code_payload_at + entry_ptr_pos + 4] = struct.pack(
        "<I", entry_abs
    )
    buf[code_payload_at + name_ptr_pos : code_payload_at + name_ptr_pos + 4] = struct.pack(
        "<I", name_chars
    )
    bc_off = entry_abs + 8
    return buf, bc_off


def _build_dogcheck_form_bc15(bytecode: bytes) -> tuple[bytearray, int]:
    """CODE entry with bytecode-15 header (locals/args/rel) pointing at a blob."""
    name = b"gml_Script_scr_dogcheck"
    name_len = len(name)
    bc_len = len(bytecode)

    # Layout: FORM / CODE / [count][entry_ptr][entry...][string][bytecode blob]
    code = bytearray()
    code += struct.pack("<I", 1)  # count
    entry_ptr_pos = len(code)
    code += struct.pack("<I", 0)  # entry abs — patch
    entry_body_pos = len(code)
    name_ptr_pos = len(code)
    code += struct.pack("<I", 0)  # name — patch
    code += struct.pack("<I", bc_len)  # length
    locals_args_rel_pos = len(code)
    code += struct.pack("<H", 0)  # locals
    code += struct.pack("<H", 0)  # args
    rel_pos = len(code)
    code += struct.pack("<i", 0)  # rel — patch to bytecode blob

    str_blob = struct.pack("<I", name_len) + name + b"\x00"

    buf = bytearray()
    buf += b"FORM"
    size_pos = len(buf)
    buf += struct.pack("<I", 0)
    buf += b"CODE"
    buf += struct.pack("<I", 0)  # size placeholder
    code_payload_at = len(buf)
    code_size_pos = code_payload_at - 4
    buf += code
    str_at = len(buf)
    buf += str_blob
    bc_at = len(buf)
    buf += bytecode

    # Patch CODE size and FORM size
    buf[code_size_pos : code_size_pos + 4] = struct.pack("<I", len(buf) - code_payload_at)
    buf[size_pos : size_pos + 4] = struct.pack("<I", len(buf) - 8)

    entry_abs = code_payload_at + entry_body_pos
    name_chars = str_at + 4
    buf[code_payload_at + entry_ptr_pos : code_payload_at + entry_ptr_pos + 4] = struct.pack(
        "<I", entry_abs
    )
    buf[code_payload_at + name_ptr_pos : code_payload_at + name_ptr_pos + 4] = struct.pack(
        "<I", name_chars
    )
    # rel is relative to the position of the rel field itself (reader.position - 4 + rel after reading rel)
    # In _find_code_entries: bytecode_abs = reader.position - 4 + rel after read_i32
    # So after reading rel at rel_abs, position is rel_abs+4, then bytecode_abs = rel_abs + rel
    # Wait: `bytecode_abs = reader.position - 4 + rel` after read_i32, so = start_of_rel_field + rel
    rel_abs = code_payload_at + rel_pos
    rel = bc_at - rel_abs
    buf[rel_abs : rel_abs + 4] = struct.pack("<i", rel)

    return buf, bc_at


def _pushi_pop_bytecode(extra: bytes = b"\x11" * 32) -> bytes:
    pushi = struct.pack("<I", (OP_PUSHI_V15 << 24) | 1)
    pop = struct.pack("<I", (OP_POP_V15 << 24)) + struct.pack("<I", 0x12345678)
    return pushi + pop + extra


def test_is_dogcheck_room():
    assert is_dogcheck_room(0) is True
    assert is_dogcheck_room(87) is False
    assert is_dogcheck_room(300) is True


def test_rebuild_stub_bc14_layout(tmp_path: Path):
    bytecode = _pushi_pop_bytecode()
    buf, bc_off = _build_dogcheck_form(bytecode)
    path = tmp_path / "data.win"
    path.write_bytes(buf)

    ok, msg = disable_dogcheck(path, backup=True)
    assert ok is True, msg
    data = path.read_bytes()
    assert data[bc_off : bc_off + 4] == struct.pack("<I", (OP_PUSHI_V15 << 24) | 1)
    assert data[bc_off + 12 : bc_off + 16] == EXIT_WORD_V15
    assert dogcheck_exit_stubbed(path) is False
    assert dogcheck_likely_disabled(path) is True


def test_rebuild_stub_bc15_layout(tmp_path: Path):
    """Steam-like entry: bytecode lives at relative pointer, not at entry+8."""
    bytecode = _pushi_pop_bytecode()
    buf, bc_off = _build_dogcheck_form_bc15(bytecode)
    path = tmp_path / "data.win"
    path.write_bytes(buf)

    ok, msg = disable_dogcheck(path, backup=True)
    assert ok is True, msg
    data = path.read_bytes()
    assert data[bc_off + 12 : bc_off + 16] == EXIT_WORD_V15
    assert dogcheck_likely_disabled(path) is True


def test_broken_exit_at_start_healed(tmp_path: Path):
    good = _pushi_pop_bytecode()
    good_buf, bc_off = _build_dogcheck_form(good)
    broken = bytearray(good_buf)
    broken[bc_off : bc_off + 4] = EXIT_WORD
    path = tmp_path / "data.win"
    path.write_bytes(broken)
    (tmp_path / "data.win.dogcheckbak").write_bytes(good_buf)

    assert dogcheck_exit_stubbed(path) is True
    ok, _msg = disable_dogcheck(path, backup=True)
    assert ok is True
    assert dogcheck_exit_stubbed(path) is False
    assert dogcheck_likely_disabled(path) is True


def test_steam_bytes_alone_do_not_count_as_disabled(tmp_path: Path):
    from undertale_extractor.dogcheck import STEAM_BYTE_PATCHES

    size = max(o for o, _, _ in STEAM_BYTE_PATCHES) + 8
    buf = bytearray(size)
    buf[0:4] = b"FORM"
    buf[4:8] = (size - 8).to_bytes(4, "little")
    for offset, _old, new in STEAM_BYTE_PATCHES:
        buf[offset] = new
    path = tmp_path / "data.win"
    path.write_bytes(buf)
    assert dogcheck_likely_disabled(path) is False


def test_restore_backup(tmp_path: Path):
    path = tmp_path / "data.win"
    path.write_bytes(b"NEWDATA")
    (tmp_path / "data.win.dogcheckbak").write_bytes(b"ORIGINAL")
    ok, msg = restore_data_win_backup(path)
    assert ok is True
    assert path.read_bytes() == b"ORIGINAL"


def test_read_game_file_bytes_copies(tmp_path: Path):
    path = tmp_path / "data.win"
    path.write_bytes(b"FORM\x00\x00\x00\x00hello")
    assert read_game_file_bytes(path).startswith(b"FORM")
