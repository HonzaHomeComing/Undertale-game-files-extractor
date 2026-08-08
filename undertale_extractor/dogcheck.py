"""Disable Undertale dogcheck (Annoying Dog room blocker) in data.win."""

from __future__ import annotations

import struct
from pathlib import Path

from .binary import BinaryReader

# Classic HxD patches (Marxvee). Only applied when the bytes at the offset
# still match a known pre-patch pattern — never blindly overwrite.
MARXVEE_PATCHES: tuple[tuple[int, bytes, tuple[bytes, ...]], ...] = (
    # offset, new_bytes, allowed_originals
    (0x7213E4, bytes.fromhex("000100B7"), (bytes.fromhex("000100B7"),)),  # already / unknown → skip unless we add originals
    (0x7216D4, bytes.fromhex("000100B7"), (bytes.fromhex("000100B7"),)),
)

# Known originals for Marxvee (when present, allow patch). Keep empty-safe:
# If only "already patched" is known, Marxvee won't fire on virgin files —
# CODE stub handles modern builds instead.
_MARXVEE_ORIGINALS: dict[int, tuple[bytes, ...]] = {
    # Populated with observed pre-patch sequences when known.
    # Without a match, we refuse to write (avoids bricking data.win).
}

# Reported Steam / newer offsets (byte replace old→new).
# Applied only when EVERY listed old-byte matches (set must be coherent).
STEAM_BYTE_PATCHES: tuple[tuple[int, int, int], ...] = (
    (0x76DF44, 0x01, 0x00),
    (0x76E058, 0x00, 0x01),
    (0x77473C, 0x01, 0x00),
)

# Bytecode 15+ Exit instruction (return;), little-endian word.
EXIT_WORD = struct.pack("<I", 0x9D000000)

DOGCHECK_NAMES = frozenset(
    {
        "gml_Script_scr_dogcheck",
        "scr_dogcheck",
        "gml_Script_dogcheck",
    }
)

BACKUP_SUFFIXES = (".dogcheckbak", ".debugbak", ".bak")


def _backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + ".dogcheckbak")
    if not bak.exists():
        bak.write_bytes(path.read_bytes())
    return bak


def find_data_win_backup(data_win: str | Path) -> Path | None:
    path = Path(data_win)
    for suffix in BACKUP_SUFFIXES:
        bak = path.with_suffix(path.suffix + suffix)
        if bak.is_file():
            return bak
    return None


def restore_data_win_backup(data_win: str | Path) -> tuple[bool, str]:
    """Restore data.win from the newest extractor backup. Close Undertale first."""
    path = Path(data_win)
    bak = find_data_win_backup(path)
    if bak is None:
        return False, f"No backup found next to {path.name} (looked for {', '.join(BACKUP_SUFFIXES)})."
    try:
        path.write_bytes(bak.read_bytes())
    except OSError as exc:
        return False, f"Could not restore: {exc}"
    return True, f"Restored {path.name} from {bak.name}. You can start Undertale now."


def _apply_marxvee(data: bytearray) -> list[str]:
    applied = []
    for offset, patch, _legacy in MARXVEE_PATCHES:
        if offset + len(patch) > len(data):
            continue
        current = bytes(data[offset : offset + len(patch)])
        if current == patch:
            applied.append(f"marxvee@0x{offset:X}=already")
            continue
        originals = _MARXVEE_ORIGINALS.get(offset, ())
        if current not in originals:
            # Refuse unknown bytes — wrong game version would brick launch.
            continue
        data[offset : offset + len(patch)] = patch
        applied.append(f"marxvee@0x{offset:X}")
    return applied


def _apply_steam_bytes(data: bytearray) -> list[str]:
    """Apply Steam dogcheck byte flips only if the whole set matches."""
    applied = []
    # Require every offset either already-new or still-old (no mixed junk).
    ready = True
    need_write = False
    for offset, old, new in STEAM_BYTE_PATCHES:
        if offset >= len(data):
            ready = False
            break
        val = data[offset]
        if val == new:
            continue
        if val == old:
            need_write = True
            continue
        ready = False
        break
    if not ready:
        return applied
    if not need_write:
        return ["steam=already"]
    for offset, old, new in STEAM_BYTE_PATCHES:
        if data[offset] == old:
            data[offset] = new
            applied.append(f"steam@0x{offset:X}")
    return applied


def _find_code_entries(reader: BinaryReader) -> list[tuple[str, int, int]]:
    """
    Return list of (name, bytecode_abs_offset, length) for CODE entries.
    Supports bytecode 14 (inline) and 15+ (blob pointer).
    """
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
        if start + size > reader.size or size < 0:
            break
        if tag == "CODE":
            code_start, code_size = start, size
        try:
            reader.seek(start + size)
        except ValueError:
            break
    if code_start is None or code_size is None:
        return []

    reader.seek(code_start)
    count = reader.read_u32()
    if count <= 0 or count > 100_000:
        return []
    offsets = [reader.read_u32() for _ in range(count)]
    entries: list[tuple[str, int, int]] = []
    code_end = code_start + code_size

    for off in offsets:
        try:
            if off < code_start or off >= code_end:
                continue
            reader.seek(off)
            name_ptr = reader.read_u32()
            name = reader.read_cstring_at(name_ptr) if name_ptr else ""
            length = reader.read_u32()
            # Dogcheck is a small script; reject absurd lengths.
            if length == 0 or length > 200_000:
                continue

            # Try bytecode 15+: locals, args, relative pointer to bytecode blob.
            locals_count = reader.read_u16()
            args = reader.read_u16()
            rel = reader.read_i32()
            bytecode_abs = reader.position - 4 + rel
            args_n = args & 0x7FFF
            # Strict: tiny locals/args and pointer must land inside the file.
            if (
                locals_count < 512
                and args_n < 64
                and abs(rel) < reader.size
                and 0 < bytecode_abs < reader.size
                and bytecode_abs + length <= reader.size
            ):
                entries.append((name, bytecode_abs, length))
                continue

            # Bytecode 14: instructions start right after name ptr + length.
            bc14_start = off + 8
            if bc14_start + length <= reader.size:
                entries.append((name, bc14_start, length))
        except Exception:
            continue
    return entries


def _patch_dogcheck_code(data: bytearray) -> list[str]:
    """
    Disable scr_dogcheck by writing a single Exit at the start of its bytecode.

    Only touches the first instruction — never fills the whole length (a wrong
    length used to be able to corrupt neighboring code and stop Undertale launching).
    """
    applied = []
    reader = BinaryReader(bytes(data))
    entries = _find_code_entries(reader)
    targets = [e for e in entries if e[0] in DOGCHECK_NAMES or e[0].lower().endswith("dogcheck")]
    if not targets:
        return applied

    for name, bc_off, length in targets:
        if length < 4 or bc_off + 4 > len(data):
            continue
        original_head = bytes(data[bc_off : bc_off + 4])
        if original_head == EXIT_WORD:
            applied.append(f"code:{name}=already")
            continue
        data[bc_off : bc_off + 4] = EXIT_WORD
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
        notes.extend(_patch_dogcheck_code(raw))
    except Exception as exc:
        notes.append(f"code-scan-error:{exc}")

    # Ignore "already" noise when deciding if anything changed.
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
    for offset, patch, _origs in MARXVEE_PATCHES:
        if offset + len(patch) <= len(data) and data[offset : offset + len(patch)] == patch:
            return True
    steam_ok = 0
    for offset, _old, new in STEAM_BYTE_PATCHES:
        if offset < len(data) and data[offset] == new:
            steam_ok += 1
    if steam_ok >= 2:
        return True
    try:
        reader = BinaryReader(data)
        for name, bc_off, length in _find_code_entries(reader):
            if name in DOGCHECK_NAMES or name.lower().endswith("dogcheck"):
                if length >= 4 and data[bc_off : bc_off + 4] == EXIT_WORD:
                    return True
    except Exception:
        pass
    return False
