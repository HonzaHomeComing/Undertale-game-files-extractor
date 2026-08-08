"""Tests for dogcheck disable helper."""

from __future__ import annotations

import struct
from pathlib import Path

from undertale_extractor.binary import BinaryReader, read_game_file_bytes
from undertale_extractor.dogcheck import (
    EXIT_WORD,
    MARXVEE_PATCHES,
    disable_dogcheck,
    dogcheck_exit_stubbed,
    dogcheck_likely_disabled,
    restore_data_win_backup,
)


def _build_dogcheck_form(bytecode: bytes) -> tuple[bytearray, int]:
    """Minimal FORM+CODE with gml_Script_scr_dogcheck (bc14). Returns (buf, bc_off)."""
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


def test_refuses_blind_marxvee_on_unknown_bytes(tmp_path: Path):
    offset, patch, _ = MARXVEE_PATCHES[1]
    buf = bytearray(offset + 32)
    buf[0:4] = b"FORM"
    buf[4:8] = (len(buf) - 8).to_bytes(4, "little")
    buf[offset : offset + 4] = b"\x11\x22\x33\x44"
    path = tmp_path / "data.win"
    path.write_bytes(buf)

    ok, msg = disable_dogcheck(path, backup=True)
    assert path.read_bytes()[offset : offset + 4] == b"\x11\x22\x33\x44"
    assert ok is False or "already" in msg.lower() or "could not" in msg.lower()


def test_never_writes_exit_stub_on_scr_dogcheck(tmp_path: Path):
    """Exit-at-start breaks scr_load (dogcheck variable never set)."""
    bytecode = b"\xAA\xBB\xCC\xDD" + b"\x11" * 12
    buf, bc_off = _build_dogcheck_form(bytecode)
    path = tmp_path / "data.win"
    path.write_bytes(buf)

    ok, _msg = disable_dogcheck(path, backup=True)
    data = path.read_bytes()
    assert data[bc_off : bc_off + 4] != EXIT_WORD
    assert data[bc_off : bc_off + len(bytecode)] == bytecode
    assert dogcheck_exit_stubbed(path) is False


def test_heals_exit_stub_from_backup(tmp_path: Path):
    good = b"\xAA\xBB\xCC\xDD" + b"\x11" * 12
    good_buf, bc_off = _build_dogcheck_form(good)
    broken = bytearray(good_buf)
    broken[bc_off : bc_off + 4] = EXIT_WORD

    path = tmp_path / "data.win"
    path.write_bytes(broken)
    bak = tmp_path / "data.win.dogcheckbak"
    bak.write_bytes(good_buf)

    assert dogcheck_exit_stubbed(path) is True
    ok, msg = disable_dogcheck(path, backup=True)
    assert dogcheck_exit_stubbed(path) is False
    assert path.read_bytes()[bc_off : bc_off + 4] == good[:4]
    assert "backup" in msg.lower() or ok is True or "could not" in msg.lower() or "disabled" in msg.lower()


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


def test_steam_already_counts_as_disabled(tmp_path: Path):
    from undertale_extractor.dogcheck import STEAM_BYTE_PATCHES

    size = max(o for o, _, _ in STEAM_BYTE_PATCHES) + 8
    buf = bytearray(size)
    buf[0:4] = b"FORM"
    buf[4:8] = (size - 8).to_bytes(4, "little")
    for offset, _old, new in STEAM_BYTE_PATCHES:
        buf[offset] = new
    path = tmp_path / "data.win"
    path.write_bytes(buf)
    assert dogcheck_likely_disabled(path) is True
