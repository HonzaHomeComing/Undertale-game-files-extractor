"""Disable Undertale dogcheck (Annoying Dog room blocker) in data.win."""

from __future__ import annotations

import struct
from pathlib import Path

from .binary import BinaryReader

# Classic HxD patches (Marxvee). Only applied when originals are known.
MARXVEE_PATCHES: tuple[tuple[int, bytes, tuple[bytes, ...]], ...] = (
    (0x7213E4, bytes.fromhex("000100B7"), (bytes.fromhex("000100B7"),)),
    (0x7216D4, bytes.fromhex("000100B7"), (bytes.fromhex("000100B7"),)),
)

_MARXVEE_ORIGINALS: dict[int, tuple[bytes, ...]] = {
    # Without known pre-patch bytes we refuse to write (avoids bricking).
}

# Steam / newer builds (from UndertaleModTool maintainers):
# These invert the dog-room branch but still let scr_dogcheck SET the
# `dogcheck` variable — required by scr_load (debug L).
STEAM_BYTE_PATCHES: tuple[tuple[int, int, int], ...] = (
    (0x76DF44, 0x01, 0x00),
    (0x76E058, 0x00, 0x01),
    (0x77473C, 0x01, 0x00),
)

# Old broken approach wrote this Exit opcode at the start of scr_dogcheck.
# That skips `dogcheck = 1` and makes debug Load crash:
#   Variable obj_mainchara.dogcheck not set before reading it (scr_load).
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
            continue
        data[offset : offset + len(patch)] = patch
        applied.append(f"marxvee@0x{offset:X}")
    return applied


def _apply_steam_bytes(data: bytearray) -> list[str]:
    """Apply Steam dogcheck byte flips only if the whole set matches."""
    applied = []
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
    """Return list of (name, bytecode_abs_offset, length) for CODE entries."""
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
            if length == 0 or length > 200_000:
                continue

            locals_count = reader.read_u16()
            args = reader.read_u16()
            rel = reader.read_i32()
            bytecode_abs = reader.position - 4 + rel
            args_n = args & 0x7FFF
            if (
                locals_count < 512
                and args_n < 64
                and abs(rel) < reader.size
                and 0 < bytecode_abs < reader.size
                and bytecode_abs + length <= reader.size
            ):
                entries.append((name, bytecode_abs, length))
                continue

            bc14_start = off + 8
            if bc14_start + length <= reader.size:
                entries.append((name, bc14_start, length))
        except Exception:
            continue
    return entries


def dogcheck_exit_stubbed(data_win: str | Path | bytes | bytearray) -> bool:
    """True if scr_dogcheck starts with Exit (broken patch that crashes debug L)."""
    if isinstance(data_win, (bytes, bytearray)):
        data = bytes(data_win)
    else:
        data = Path(data_win).read_bytes()
    try:
        reader = BinaryReader(data)
        for name, bc_off, length in _find_code_entries(reader):
            if name in DOGCHECK_NAMES or name.lower().endswith("dogcheck"):
                if length >= 4 and data[bc_off : bc_off + 4] == EXIT_WORD:
                    return True
    except Exception:
        return False
    return False


def disable_dogcheck(data_win: str | Path, *, backup: bool = True) -> tuple[bool, str]:
    """
    Patch data.win so dogcheck no longer sends you to the Annoying Dog room.

    Must NOT replace scr_dogcheck with a bare Exit — that skips `dogcheck = 1`
    and crashes scr_load / debug L with:
      Variable obj_mainchara.dogcheck not set before reading it
    """
    path = Path(data_win)

    # Heal previous bad CODE Exit stubs by restoring backup first.
    if dogcheck_exit_stubbed(path):
        bak = find_data_win_backup(path)
        if bak is not None:
            path.write_bytes(bak.read_bytes())
        else:
            return (
                False,
                "data.win has a broken dogcheck Exit stub (causes the L-key crash). "
                "No backup found — use Steam → Properties → Verify integrity, "
                "then click Enable live patches again.",
            )

    raw = bytearray(path.read_bytes())
    before = bytes(raw)

    notes: list[str] = []
    notes.extend(_apply_marxvee(raw))
    notes.extend(_apply_steam_bytes(raw))
    # Intentionally no CODE Exit stub — see module docstring / crash above.

    if bytes(raw) == before:
        if any("already" in n for n in notes):
            return True, "Dogcheck already disabled (safe patches)."
        return (
            False,
            "Could not auto-disable dogcheck for this data.win version. "
            "Debug Load (L) still works for normal rooms; for secret rooms use "
            "UndertaleModTool → Scripts → DisableDogcheck.",
        )

    if backup:
        _backup(path)
    path.write_bytes(raw)
    return True, "Dogcheck disabled (" + ", ".join(notes) + "). Restart Undertale once."


def dogcheck_likely_disabled(data_win: str | Path) -> bool:
    """True only for safe disable methods — never for the broken Exit stub."""
    path = Path(data_win)
    data = path.read_bytes()
    if dogcheck_exit_stubbed(data):
        return False
    for offset, patch, _origs in MARXVEE_PATCHES:
        if offset + len(patch) <= len(data) and data[offset : offset + len(patch)] == patch:
            # Marxvee bytes alone aren't proof if we never wrote known originals;
            # only count when steam set also matches or we explicitly applied.
            pass
    steam_ok = 0
    for offset, _old, new in STEAM_BYTE_PATCHES:
        if offset < len(data) and data[offset] == new:
            steam_ok += 1
    return steam_ok >= 2
