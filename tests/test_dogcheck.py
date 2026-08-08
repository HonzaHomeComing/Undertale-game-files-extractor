"""Tests for dogcheck disable helper."""

from __future__ import annotations

import struct
from pathlib import Path

from undertale_extractor.binary import BinaryReader, read_game_file_bytes
from undertale_extractor.dogcheck import (
    EXIT_WORD,
    MARXVEE_PATCHES,
    disable_dogcheck,
    dogcheck_likely_disabled,
    restore_data_win_backup,
)


def test_refuses_blind_marxvee_on_unknown_bytes(tmp_path: Path):
    """Unknown bytes at Marxvee offsets must not be overwritten (avoids bricking)."""
    offset, patch, _ = MARXVEE_PATCHES[1]
    buf = bytearray(offset + 32)
    buf[0:4] = b"FORM"
    buf[4:8] = (len(buf) - 8).to_bytes(4, "little")
    buf[offset : offset + 4] = b"\x11\x22\x33\x44"
    path = tmp_path / "data.win"
    path.write_bytes(buf)

    ok, msg = disable_dogcheck(path, backup=True)
    # No CODE dogcheck entry and no known marxvee original → should not change file
    assert path.read_bytes()[offset : offset + 4] == b"\x11\x22\x33\x44"
    assert ok is False or "already" in msg.lower() or "could not" in msg.lower()


def test_already_patched_marxvee(tmp_path: Path):
    offset, patch, _ = MARXVEE_PATCHES[0]
    buf = bytearray(offset + 32)
    buf[0:4] = b"FORM"
    buf[4:8] = (len(buf) - 8).to_bytes(4, "little")
    buf[offset : offset + 4] = patch
    path = tmp_path / "data.win"
    path.write_bytes(buf)
    assert dogcheck_likely_disabled(path) is True


def test_code_stub_only_writes_exit_head(tmp_path: Path):
    """Build a tiny FORM with CODE entry named gml_Script_scr_dogcheck."""
    name = b"gml_Script_scr_dogcheck"
    name_len = len(name)
    bytecode = b"\xAA\xBB\xCC\xDD" + b"\x11" * 12
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

    path = tmp_path / "data.win"
    path.write_bytes(buf)

    from undertale_extractor import dogcheck as dc

    entries = dc._find_code_entries(BinaryReader(bytes(buf)))
    assert any("dogcheck" in n.lower() for n, _, _ in entries)

    ok, msg = disable_dogcheck(path, backup=True)
    assert ok is True
    data = path.read_bytes()
    bc_off = entry_abs + 8  # bc14: after name ptr + length
    assert data[bc_off : bc_off + 4] == EXIT_WORD
    assert data[bc_off + 4 : bc_off + bc_len] == bytecode[4:]
    assert path.with_suffix(".win.dogcheckbak").exists()
    assert dogcheck_likely_disabled(path) is True


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
    # Original still intact / readable
    assert path.read_bytes() == data
