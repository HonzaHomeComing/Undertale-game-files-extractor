"""Disable Undertale dogcheck (Annoying Dog room blocker) in data.win."""

from __future__ import annotations

import struct
from pathlib import Path

from .binary import BinaryReader

# Classic HxD patches (Marxvee) — applied when offset is inside scr_dogcheck.
MARXVEE_PATCHES: tuple[tuple[int, bytes], ...] = (
    (0x7213E4, bytes.fromhex("000100B7")),  # Undertale 1.00
    (0x7216D4, bytes.fromhex("000100B7")),  # Undertale 1.001
)

# Steam / newer builds — only trusted together with a CODE stub, not alone.
STEAM_BYTE_PATCHES: tuple[tuple[int, int, int], ...] = (
    (0x76DF44, 0x01, 0x00),
    (0x76E058, 0x00, 0x01),
    (0x77473C, 0x01, 0x00),
)

OP_PUSHI_V15 = 0x84
OP_PUSH = 0xC0
OP_POP_V15 = 0x45
OP_POP_V14 = 0x41
OP_EXIT_V15 = 0x9D
OP_EXIT_V14 = 0x9E
OP_CALL_V15 = 0xD9
OP_CALL_V14 = 0xDA
OP_B_V15 = 0xB6
OP_BT_V15 = 0xB7
OP_BF_V15 = 0xB8

EXIT_WORD_V15 = struct.pack("<I", 0x9D000000)
EXIT_WORD_V14 = struct.pack("<I", 0x9E000000)
EXIT_WORD = EXIT_WORD_V15

DOGCHECK_NAMES = frozenset(
    {
        "gml_Script_scr_dogcheck",
        "scr_dogcheck",
        "gml_Script_dogcheck",
    }
)
LOAD_NAMES = frozenset(
    {
        "gml_Script_scr_load",
        "scr_load",
    }
)

DOGCHECK_ROOM_RANGES: tuple[tuple[int, int], ...] = (
    (0, 3),
    (78, 80),
    (239, 241),
    (266, 335),
)

BACKUP_SUFFIXES = (".dogcheckbak", ".debugbak", ".bak")

# Opcodes that look like real GML bytecode starts (not metadata).
_CODE_START_OPS = frozenset(
    {
        OP_PUSHI_V15,
        OP_PUSH,
        OP_POP_V15,
        OP_POP_V14,
        OP_CALL_V15,
        OP_CALL_V14,
        OP_B_V15,
        OP_BT_V15,
        OP_BF_V15,
        0xC1,
        0xC2,
        0xC3,
        0x07,
        0x15,
        0x03,
        0x41,
    }
)


def is_dogcheck_room(room_id: int) -> bool:
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


def _score_bytecode_start(data: bytes, abs_off: int) -> int:
    if abs_off < 0 or abs_off + 4 > len(data):
        return -1000
    op = _opcode(struct.unpack_from("<I", data, abs_off)[0])
    if op in _CODE_START_OPS:
        return 10
    if op in (OP_EXIT_V15, OP_EXIT_V14):
        return 0
    return -5


def _find_code_entries(reader: BinaryReader) -> list[tuple[str, int, int]]:
    """
    Return (name, bytecode_abs_offset, length).

    Picks bytecode-15 vs bytecode-14 layout per entry by scoring which start
    looks like real instructions (avoids patching the locals/args header).
    """
    data = bytes(reader._data)  # noqa: SLF001
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

            candidates: list[tuple[int, int, int]] = []  # score, abs, length

            # Bytecode 15+: locals, args, relative pointer to bytecode blob
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
                score = _score_bytecode_start(data, bytecode_abs) + 2
                candidates.append((score, bytecode_abs, length))

            # Bytecode 14: instructions start right after name ptr + length
            bc14_start = off + 8
            if bc14_start + length <= reader.size:
                score = _score_bytecode_start(data, bc14_start)
                candidates.append((score, bc14_start, length))

            if not candidates:
                continue
            candidates.sort(key=lambda c: c[0], reverse=True)
            _score, abs_off, ln = candidates[0]
            entries.append((name, abs_off, ln))
        except Exception:
            continue
    return entries


def _named_entries(data: bytes, names: frozenset[str], *, suffix: str | None = None) -> list[tuple[str, int, int]]:
    reader = BinaryReader(data)
    out = []
    for e in _find_code_entries(reader):
        n = e[0]
        if n in names or (suffix and n.lower().endswith(suffix)):
            out.append(e)
    return out


def _dogcheck_entries(data: bytes) -> list[tuple[str, int, int]]:
    return _named_entries(data, DOGCHECK_NAMES, suffix="dogcheck")


def _find_first_pop(data: bytes, bc_off: int, length: int) -> tuple[int, int, bytes] | None:
    """Return (rel_offset, pop_opcode, pop_8_bytes) for the first Pop in the script."""
    pos = 0
    while pos + 8 <= min(length, 128):
        word = struct.unpack_from("<I", data, bc_off + pos)[0]
        op = _opcode(word)
        if op in (OP_POP_V15, OP_POP_V14):
            return pos, op, bytes(data[bc_off + pos : bc_off + pos + 8])
        pos += 4
    return None


def _rebuild_dogcheck_always_pass(data: bytearray, bc_off: int, length: int, name: str) -> str | None:
    """
    Replace the start of scr_dogcheck with:
        dogcheck = 1;
        exit;
    Leave the rest of the blob untouched (unreachable) so we never overwrite
    neighboring scripts if the reported length is wrong — that used to stop
    Undertale from launching.
    """
    found = _find_first_pop(bytes(data), bc_off, length)
    if found is None:
        return None
    _rel, pop_op, pop_bytes = found
    use_v15 = pop_op == OP_POP_V15
    exit_word = EXIT_WORD_V15 if use_v15 else EXIT_WORD_V14

    if use_v15:
        push = struct.pack("<I", (OP_PUSHI_V15 << 24) | 1)
    else:
        push = struct.pack("<I", (OP_PUSH << 24) | 1)

    stub = push + pop_bytes + exit_word
    if length < len(stub):
        return None

    already = bytes(data[bc_off : bc_off + len(stub)]) == stub
    if already:
        return f"rebuild:{name}=already"

    data[bc_off : bc_off + len(stub)] = stub
    return f"rebuild:{name}"


def _apply_rebuild_stubs(data: bytearray) -> list[str]:
    applied = []
    entries = _dogcheck_entries(bytes(data))
    if not entries:
        applied.append("rebuild:scr_dogcheck-not-found")
        return applied
    for name, bc_off, length in entries:
        note = _rebuild_dogcheck_always_pass(data, bc_off, length, name)
        if note:
            applied.append(note)
        else:
            applied.append(f"rebuild:{name}-no-pop")
    return applied


def _apply_marxvee_in_dogcheck(data: bytearray) -> list[str]:
    applied = []
    ranges = [(off, off + length) for _n, off, length in _dogcheck_entries(bytes(data))]
    for offset, patch in MARXVEE_PATCHES:
        if offset + len(patch) > len(data):
            continue
        in_script = any(start <= offset < end for start, end in ranges)
        current = bytes(data[offset : offset + len(patch)])
        if current == patch:
            applied.append(f"marxvee@0x{offset:X}=already")
            continue
        if not in_script:
            op = current[3] if len(current) == 4 else 0
            if op not in (0xB6, 0xB7, 0xB8, 0xB9):
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


def _has_rebuild_stub(data: bytes) -> bool:
    for name, bc_off, length in _dogcheck_entries(data):
        found = _find_first_pop(data, bc_off, length)
        if found is None:
            continue
        rel, pop_op, pop_bytes = found
        # After rebuild, pop should be at offset 4 (right after push)
        if rel != 4:
            # Could still be valid if we kept original push size 4
            pass
        use_v15 = pop_op == OP_POP_V15
        exit_word = EXIT_WORD_V15 if use_v15 else EXIT_WORD_V14
        push_len = 4
        if (
            length > push_len + 8
            and data[bc_off + push_len : bc_off + push_len + 8] == pop_bytes
            and data[bc_off + push_len + 8 : bc_off + push_len + 12] == exit_word
        ):
            # Verify push opcode
            op0 = _opcode(struct.unpack_from("<I", data, bc_off)[0])
            if op0 in (OP_PUSHI_V15, OP_PUSH):
                return True
    return False


def dogcheck_likely_disabled(data_win: str | Path) -> bool:
    """True only when a real disable method is present — not steam-bytes alone."""
    path = Path(data_win)
    data = path.read_bytes()
    if dogcheck_exit_stubbed(data):
        return False
    if _has_rebuild_stub(data):
        return True
    for offset, patch in MARXVEE_PATCHES:
        if offset + len(patch) <= len(data) and data[offset : offset + len(patch)] == patch:
            # Only count Marxvee if it sits inside scr_dogcheck
            if any(off <= offset < off + ln for _n, off, ln in _dogcheck_entries(data)):
                return True
    return False


def disable_dogcheck(data_win: str | Path, *, backup: bool = True) -> tuple[bool, str]:
    """
    Patch data.win so dogcheck no longer sends you to the Annoying Dog room.

    Rewrites scr_dogcheck to `dogcheck = 1; exit;` (same idea as UMT DisableDogcheck
    for load purposes: never goto room_of_dog, always leave dogcheck set).
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
                "No backup found — use Steam → Verify integrity of game files, "
                "then click Enable live patches again.",
            )

    raw = bytearray(path.read_bytes())
    before = bytes(raw)

    notes: list[str] = []
    try:
        notes.extend(_apply_rebuild_stubs(raw))
    except Exception as exc:
        notes.append(f"rebuild-error:{exc}")
    notes.extend(_apply_marxvee_in_dogcheck(raw))
    notes.extend(_apply_steam_bytes(raw))

    changed = bytes(raw) != before
    if changed:
        if backup:
            _backup(path)
        path.write_bytes(raw)

    if dogcheck_likely_disabled(path):
        return True, "Dogcheck disabled (" + ", ".join(notes) + "). Restart Undertale once."

    return (
        False,
        "Could not disable dogcheck on this data.win.\n"
        f"Details: {', '.join(notes) if notes else 'no strategies matched'}\n\n"
        "Teleport (debug L) may still work for normal rooms, but the Annoying Dog "
        "will appear on secret/blocked rooms.\n\n"
        "Fix: UndertaleModTool → Scripts → DisableDogcheck, save data.win, "
        "then use this app for room jumps.",
    )
