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
    """Minimal FORM+CODE with gml_Script_scr_dogcheck (bc14 layout). Returns (buf, bc_off)."""
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


def _pushi_pop_bytecode(extra: bytes = b"\x11" * 32) -> bytes:
    """Bytecode 15: pushi.e 1; pop.v.v …; then filler that would be room checks."""
    pushi = struct.pack("<I", (OP_PUSHI_V15 << 24) | 1)
    pop = struct.pack("<I", (OP_POP_V15 << 24)) + struct.pack("<I", 0x12345678)
    return pushi + pop + extra


def test_is_dogcheck_room():
    assert is_dogcheck_room(0) is True
    assert is_dogcheck_room(87) is False
    assert is_dogcheck_room(78) is True
    assert is_dogcheck_room(300) is True
    assert is_dogcheck_room(100) is False


def test_safe_stub_keeps_assign_then_exit(tmp_path: Path):
    bytecode = _pushi_pop_bytecode()
    buf, bc_off = _build_dogcheck_form(bytecode)
    path = tmp_path / "data.win"
    path.write_bytes(buf)

    ok, msg = disable_dogcheck(path, backup=True)
    assert ok is True
    data = path.read_bytes()
    # First 12 bytes (pushi+pop) unchanged
    assert data[bc_off : bc_off + 12] == bytecode[:12]
    # Next word is Exit
    assert data[bc_off + 12 : bc_off + 16] == EXIT_WORD_V15
    assert dogcheck_exit_stubbed(path) is False
    assert dogcheck_likely_disabled(path) is True
    assert "code-safe" in msg


def test_broken_exit_at_start_detected_and_healed(tmp_path: Path):
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
    data = path.read_bytes()
    assert data[bc_off : bc_off + 4] != EXIT_WORD
    assert data[bc_off + 12 : bc_off + 16] == EXIT_WORD_V15


def test_never_leaves_exit_as_first_instruction(tmp_path: Path):
    bytecode = _pushi_pop_bytecode()
    buf, bc_off = _build_dogcheck_form(bytecode)
    path = tmp_path / "data.win"
    path.write_bytes(buf)
    disable_dogcheck(path, backup=True)
    data = path.read_bytes()
    assert data[bc_off : bc_off + 4] != EXIT_WORD


def test_restore_backup(tmp_path: Path):
    path = tmp_path / "data.win"
    path.write_bytes(b"NEWDATA")
    bak = tmp_path / "data.win.dogcheckbak"
    bak.write_bytes(b"ORIGINAL")
    ok, msg = restore_data_win_backup(path)
    assert ok is True
    assert path.read_bytes() == b"ORIGINAL"
    assert "Restored" in msg


def test_read_game_file_bytes_copies(tmp_path: Path):
    path = tmp_path / "data.win"
    path.write_bytes(b"FORM\x00\x00\x00\x00hello")
    data = read_game_file_bytes(path)
    assert data.startswith(b"FORM")
    assert path.read_bytes() == data
