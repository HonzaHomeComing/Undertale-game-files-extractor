"""Disable Undertale dogcheck (Annoying Dog room blocker) in data.win."""

from __future__ import annotations

import struct
from pathlib import Path

from .binary import BinaryReader

# Classic HxD patches (Marxvee) — overwrite start of dogcheck logic.
MARXVEE_PATCHES: tuple[tuple[int, bytes], ...] = (
    (0x7213E4, bytes.fromhex("000100B7")),  # Undertale 1.00
    (0x7216D4, bytes.fromhex("000100B7")),  # Undertale 1.001
)

# Reported Steam / newer mac-port-adjacent offsets (byte replace old→new).
STEAM_BYTE_PATCHES: tuple[tuple[int, int, int], ...] = (
    (0x76DF44, 0x01, 0x00),
    (0x76E058, 0x00, 0x01),
    (0x77473C, 0x01, 0x00),
)

# Bytecode 15+ Exit instruction (return;), little-endian word.
EXIT_WORD = struct.pack("<I", 0x9D000000)


def _backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + ".dogcheckbak")
    if not bak.exists():
        bak.write_bytes(path.read_bytes())
    return bak


def _apply_marxvee(data: bytearray) -> list[str]:
    applied = []
    for offset, patch in MARXVEE_PATCHES:
        if offset + len(patch) > len(data):
            continue
        current = bytes(data[offset : offset + len(patch)])
        if current == patch:
            applied.append(f"marxvee@0x{offset:X}=already")
            continue
        # Only patch if it looks like real code (not empty/padding)
        if current == b"\x00" * len(patch):
            continue
        data[offset : offset + len(patch)] = patch
        applied.append(f"marxvee@0x{offset:X}")
    return applied


def _apply_steam_bytes(data: bytearray) -> list[str]:
    applied = []
    hits = 0
    for offset, old, new in STEAM_BYTE_PATCHES:
        if offset >= len(data):
            continue
        if data[offset] == new:
            hits += 1
            continue
        if data[offset] == old:
            data[offset] = new
            applied.append(f"steam@0x{offset:X}")
            hits += 1
    # Only count as success if we matched most of the set
    if len(applied) == 0 and hits >= len(STEAM_BYTE_PATCHES):
        applied.append("steam=already")
    return applied


def _find_code_entries(reader: BinaryReader) -> list[tuple[str, int, int]]:
    """
    Return list of (name, bytecode_abs_offset, length) for CODE entries.
    Supports bytecode 14 (inline) and 15+ (blob pointer).
    """
    info = None
    # Re-scan FORM for CODE
    reader.seek(0)
    if reader.read_tag() != "FORM":
        return []
    form_size = reader.read_u32()
    form_end = reader.position + form_size
    code_start = code_size = None
    while reader.position + 8 <= form_end:
        tag = reader.read_tag()
        size = reader.read_u32()
        start = reader.position
        if tag == "CODE":
            code_start, code_size = start, size
        reader.seek(start + size)
    if code_start is None:
        return []

    reader.seek(code_start)
    count = reader.read_u32()
    if count <= 0 or count > 100_000:
        return []
    offsets = [reader.read_u32() for _ in range(count)]
    entries: list[tuple[str, int, int]] = []

    for off in offsets:
        try:
            reader.seek(off)
            name_ptr = reader.read_u32()
            name = reader.read_cstring_at(name_ptr) if name_ptr else ""
            length = reader.read_u32()
            if length == 0 or length > 5_000_000:
                continue

            # Heuristic: bytecode 15 has locals/args then relative pointer
            locals_count = reader.read_u16()
            args = reader.read_u16()
            rel = reader.read_i32()
            # If locals/args look sane, treat as bytecode 15+
            if locals_count < 10_000 and (args & 0x7FFF) < 10_000:
                bytecode_abs = reader.position - 4 + rel
                if 0 < bytecode_abs < reader.size and bytecode_abs + length <= reader.size:
                    entries.append((name, bytecode_abs, length))
                    continue

            # Bytecode 14: instructions start right after length field
            # (we already read locals/args/rel incorrectly — recompute)
            bc14_start = off + 8  # name ptr + length
            if bc14_start + length <= reader.size:
                entries.append((name, bc14_start, length))
        except Exception:
            continue
    return entries


def _patch_dogcheck_code(data: bytearray, path: Path) -> list[str]:
    applied = []
    reader = BinaryReader(bytes(data))
    entries = _find_code_entries(reader)
    targets = [
        e
        for e in entries
        if "scr_dogcheck" in e[0].lower() or e[0].lower().endswith("dogcheck")
    ]
    if not targets:
        # Fallback: some builds name it gml_Script_scr_dogcheck only — already covered
        return applied

    for name, bc_off, length in targets:
        if bc_off + length > len(data):
            continue
        original = bytes(data[bc_off : bc_off + length])
        # Build stub: fill with Exit words (safe no-op return)
        stub = bytearray()
        while len(stub) + 4 <= length:
            stub.extend(EXIT_WORD)
        while len(stub) < length:
            stub.append(0)
        if bytes(stub) == original:
            applied.append(f"code:{name}=already")
            continue
        data[bc_off : bc_off + length] = stub
        applied.append(f"code:{name}")
    return applied


def disable_dogcheck(data_win: str | Path, *, backup: bool = True) -> tuple[bool, str]:
    """
    Patch data.win so dogcheck no longer sends you to the Annoying Dog room.

    Returns (changed_or_already_ok, message).
    Requires restarting Undertale after a successful patch.
    """
    path = Path(data_win)
    raw = bytearray(path.read_bytes())
    before = bytes(raw)

    notes: list[str] = []
    notes.extend(_apply_marxvee(raw))
    notes.extend(_apply_steam_bytes(raw))
    try:
        notes.extend(_patch_dogcheck_code(raw, path))
    except Exception as exc:
        notes.append(f"code-scan-error:{exc}")

    if bytes(raw) == before:
        if any("already" in n for n in notes):
            return True, "Dogcheck already disabled."
        return (
            False,
            "Could not find dogcheck to patch automatically for this data.win version. "
            "Use UndertaleModTool → Scripts → DisableDogcheck, then reopen this app.",
        )

    if backup:
        _backup(path)
    path.write_bytes(raw)
    return True, "Dogcheck disabled (" + ", ".join(notes) + "). Restart Undertale once."


def dogcheck_likely_disabled(data_win: str | Path) -> bool:
    path = Path(data_win)
    data = path.read_bytes()
    for offset, patch in MARXVEE_PATCHES:
        if offset + len(patch) <= len(data) and data[offset : offset + len(patch)] == patch:
            return True
    steam_ok = 0
    for offset, _old, new in STEAM_BYTE_PATCHES:
        if offset < len(data) and data[offset] == new:
            steam_ok += 1
    if steam_ok >= 2:
        return True
    # Code stub check: look for Exit-filled dogcheck via quick string presence only
    return b"scr_dogcheck" in data and False  # unknown without full scan
