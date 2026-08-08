"""Disable Undertale dogcheck (Annoying Dog room blocker) in data.win."""

from __future__ import annotations

import struct
from pathlib import Path

from .binary import BinaryReader

# Classic HxD patches (Marxvee) — only applied when the offset falls inside
# scr_dogcheck bytecode (so we know we are not patching random data).
MARXVEE_PATCHES: tuple[tuple[int, bytes], ...] = (
    (0x7213E4, bytes.fromhex("000100B7")),  # Undertale 1.00
    (0x7216D4, bytes.fromhex("000100B7")),  # Undertale 1.001
)

# Steam / newer builds (UndertaleModTool maintainers).
STEAM_BYTE_PATCHES: tuple[tuple[int, int, int], ...] = (
    (0x76DF44, 0x01, 0x00),
    (0x76E058, 0x00, 0x01),
    (0x77473C, 0x01, 0x00),
)

# Bytecode opcodes (high byte of each instruction word).
# v15 / v14 (see UndertaleModTool bytecode wiki).
OP_PUSHI_V15 = 0x84
OP_PUSH = 0xC0
OP_POP_V15 = 0x45
OP_POP_V14 = 0x41
OP_EXIT_V15 = 0x9D
OP_EXIT_V14 = 0x9E

EXIT_WORD_V15 = struct.pack("<I", 0x9D000000)
EXIT_WORD_V14 = struct.pack("<I", 0x9E000000)
# Back-compat alias used by tests / heal detection (v15 Exit).
EXIT_WORD = EXIT_WORD_V15

DOGCHECK_NAMES = frozenset(
    {
        "gml_Script_scr_dogcheck",
        "scr_dogcheck",
        "gml_Script_dogcheck",
    }
)

# Rooms that vanilla dogcheck rejects (Undertale wiki / community lists).
# Teleporting here shows the Annoying Dog unless dogcheck is disabled.
DOGCHECK_ROOM_RANGES: tuple[tuple[int, int], ...] = (
    (0, 3),
    (78, 80),
    (239, 241),
    (266, 335),
)

BACKUP_SUFFIXES = (".dogcheckbak", ".debugbak", ".bak")


def is_dogcheck_room(room_id: int) -> bool:
    """True if vanilla Undertale sends this room id to the Annoying Dog."""
    for lo, hi in DOGCHECK_ROOM_RANGES:
        if lo <= room_id <= hi:
            return True
    return False


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


def _opcode(word: int) -> int:
    return (word >> 24) & 0xFF


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


def _dogcheck_entries(data: bytes) -> list[tuple[str, int, int]]:
    reader = BinaryReader(data)
    return [
        e
        for e in _find_code_entries(reader)
        if e[0] in DOGCHECK_NAMES or e[0].lower().endswith("dogcheck")
    ]


def _keep_bytes_for_dogcheck_assign(data: bytes, bc_off: int, length: int) -> tuple[int, bytes] | None:
    """
    Return (keep_len, exit_word) for a safe stub:
      dogcheck = 1;  // keep these instructions
      exit;          // then never room_goto(room_of_dog)
    """
    if length < 16:
        return None
    w0 = struct.unpack_from("<I", data, bc_off)[0]
    op0 = _opcode(w0)

    # Bytecode 15: pushi.e 1 ; pop.v.v self.dogcheck
    if op0 == OP_PUSHI_V15:
        # Int16 value sits in the low 16 bits for PushI
        if (w0 & 0xFFFF) != 1 and ((w0 >> 8) & 0xFFFF) != 1:
            # Still accept PushI as the start of dogcheck=1 on odd encodings
            pass
        w1 = struct.unpack_from("<I", data, bc_off + 4)[0]
        if _opcode(w1) != OP_POP_V15:
            return None
        return 4 + 8, EXIT_WORD_V15  # PushI (1 word) + Pop (2 words)

    # Bytecode 14: push.e 1 ; pop.v.v self.dogcheck
    if op0 == OP_PUSH:
        # Int16 push is often 1 word; variable push is 2 — dogcheck=1 uses int16.
        w1 = struct.unpack_from("<I", data, bc_off + 4)[0]
        if _opcode(w1) == OP_POP_V14:
            return 4 + 8, EXIT_WORD_V14
        # Rare: push takes 2 words then pop
        if length >= 20:
            w2 = struct.unpack_from("<I", data, bc_off + 8)[0]
            if _opcode(w2) == OP_POP_V14:
                return 8 + 8, EXIT_WORD_V14
        return None

    return None


def _apply_safe_code_stub(data: bytearray) -> list[str]:
    """
    Rewrite scr_dogcheck to: dogcheck = 1; exit;

    Unlike a bare Exit at offset 0, this keeps the variable assignment so
    scr_load / debug L does not crash.
    """
    applied = []
    for name, bc_off, length in _dogcheck_entries(bytes(data)):
        info = _keep_bytes_for_dogcheck_assign(bytes(data), bc_off, length)
        if info is None:
            continue
        keep, exit_word = info
        if keep >= length:
            continue
        # Already safely stubbed?
        rest = bytes(data[bc_off + keep : bc_off + length])
        if rest == exit_word * (len(rest) // 4) + rest[len(rest) // 4 * 4 :]:
            if length - keep >= 4 and data[bc_off + keep : bc_off + keep + 4] == exit_word:
                applied.append(f"code-safe:{name}=already")
                continue
        # Write Exit from keep onward
        pos = bc_off + keep
        end = bc_off + length
        while pos + 4 <= end:
            data[pos : pos + 4] = exit_word
            pos += 4
        while pos < end:
            data[pos] = 0
            pos += 1
        applied.append(f"code-safe:{name}")
    return applied


def _apply_marxvee_in_dogcheck(data: bytearray) -> list[str]:
    """Apply Marxvee bytes only when the offset lies inside scr_dogcheck."""
    applied = []
    ranges = [(off, off + length) for _n, off, length in _dogcheck_entries(bytes(data))]
    if not ranges:
        # Fall back: still try known offsets if they look like Bt/B instructions
        ranges = []
    for offset, patch in MARXVEE_PATCHES:
        if offset + len(patch) > len(data):
            continue
        in_script = any(start <= offset < end for start, end in ranges) if ranges else False
        current = bytes(data[offset : offset + len(patch)])
        if current == patch:
            applied.append(f"marxvee@0x{offset:X}=already")
            continue
        if not in_script:
            # Without a CODE match, only patch if high byte looks like a branch opcode
            op = current[3] if len(current) == 4 else 0
            if op not in (0xB6, 0xB7, 0xB8, 0xB9):  # B / Bt / Bf family
                continue
        data[offset : offset + len(patch)] = patch
        applied.append(f"marxvee@0x{offset:X}")
    return applied


def _apply_steam_bytes(data: bytearray) -> list[str]:
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


def dogcheck_exit_stubbed(data_win: str | Path | bytes | bytearray) -> bool:
    """True if scr_dogcheck starts with Exit (broken patch that crashes debug L)."""
    if isinstance(data_win, (bytes, bytearray)):
        data = bytes(data_win)
    else:
        data = Path(data_win).read_bytes()
    try:
        for _name, bc_off, length in _dogcheck_entries(data):
            if length >= 4 and data[bc_off : bc_off + 4] in (EXIT_WORD_V15, EXIT_WORD_V14):
                return True
    except Exception:
        return False
    return False


def disable_dogcheck(data_win: str | Path, *, backup: bool = True) -> tuple[bool, str]:
    """
    Patch data.win so dogcheck no longer sends you to the Annoying Dog room.

    Safe strategy (matches intent of UndertaleModTool DisableDogcheck):
      keep `dogcheck = 1`, then exit — never skip the assignment.
    """
    path = Path(data_win)

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
    try:
        notes.extend(_apply_safe_code_stub(raw))
    except Exception as exc:
        notes.append(f"code-safe-error:{exc}")
    notes.extend(_apply_marxvee_in_dogcheck(raw))
    notes.extend(_apply_steam_bytes(raw))

    if bytes(raw) == before:
        if any("already" in n for n in notes):
            return True, "Dogcheck already disabled (safe patches)."
        return (
            False,
            "Could not auto-disable dogcheck for this data.win version. "
            "Use UndertaleModTool → Scripts → DisableDogcheck, then Enable live patches "
            "(debug) here. Secret/dogcheck rooms will still show the Annoying Dog.",
        )

    if backup:
        _backup(path)
    path.write_bytes(raw)
    return True, "Dogcheck disabled (" + ", ".join(notes) + "). Restart Undertale once."


def dogcheck_likely_disabled(data_win: str | Path) -> bool:
    """True for safe disable methods — never for the broken Exit-at-start stub."""
    path = Path(data_win)
    data = path.read_bytes()
    if dogcheck_exit_stubbed(data):
        return False
    # Safe code stub: push/pop kept, then Exit
    for _name, bc_off, length in _dogcheck_entries(data):
        info = _keep_bytes_for_dogcheck_assign(data, bc_off, length)
        if info is None:
            continue
        keep, exit_word = info
        if length > keep and data[bc_off + keep : bc_off + keep + 4] == exit_word:
            return True
    for offset, patch in MARXVEE_PATCHES:
        if offset + len(patch) <= len(data) and data[offset : offset + len(patch)] == patch:
            return True
    steam_ok = sum(
        1 for offset, _old, new in STEAM_BYTE_PATCHES if offset < len(data) and data[offset] == new
    )
    return steam_ok >= 2
