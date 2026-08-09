"""Undertale battlegroup / fight launcher (debug Home key).

Patches ONLY the Home-key battlegroup assignment (PushI near vk_home in
obj_time / matching bytecode patterns). Never sprays raw ints across RAM —
that corrupted battles into glitched Mettaton fights.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from pathlib import Path

from .binary import BinaryReader
from .dogcheck import _find_code_entries
from .live_teleport import (
    _send_key_to_undertale,
    debug_flag_enabled,
    enable_debug_mode,
    find_undertale_hwnd,
    undertale_is_running,
)
from .memory_patch import (
    patch_int32_in_data_win_image,
    _open_process,
    _write,
    kernel32,
)
from .live_teleport import find_undertale_pid, is_windows, VK_ESCAPE

# Seed offsets from TCRF (only used when the dword is a known factory default).
HOME_BATTLEGROUP_OFFSETS = (
    0x9F553C,  # 1.00 — So Sorry 140
    0x9EB414,  # 1.001 — Mettaton 80
    0x9EB918,  # 1.001 Linux
    0xBD8200,  # 1.06 — Mettaton 57
)

HOME_DEFAULTS = frozenset({57, 80, 140, 81})

OP_PUSHI = 0x84  # bytecode 15+
OP_PUSH = 0xC0  # bytecode 14 (Int16 push uses this)
DT_INT16 = 0x0F  # UndertaleInstruction.DataType.Int16 — lives in bits 16–19
VK_HOME = 0x24
VK_HOME_KEY = 0x24  # Win32 / GML


@dataclass(frozen=True)
class Battlegroup:
    id: int
    name: str
    rare: bool = False


@dataclass(frozen=True)
class HomeBattlegroupSite:
    offset: int
    value: int
    kind: str  # "raw" | "pushi"
    source: str = ""
    # Full original dword so we preserve opcode + type nibble when rewriting
    template: int = 0

    def encode(self, battlegroup_id: int) -> int:
        bg = int(battlegroup_id) & 0xFFFF
        if self.kind == "pushi":
            tmpl = self.template or pushi_word(self.value)
            return (tmpl & 0xFFFF0000) | bg
        return bg


def pushi_word(value: int, *, opcode: int = OP_PUSHI, type_nibble: int = DT_INT16) -> int:
    """GameMaker PushI / Push.e encoding: opcode | type | int16 value."""
    return ((opcode & 0xFF) << 24) | ((type_nibble & 0xF) << 16) | (int(value) & 0xFFFF)


def _pushi_bytes(value: int, *, opcode: int = OP_PUSHI) -> bytes:
    return struct.pack("<I", pushi_word(value, opcode=opcode))


def is_int16_push(word: int) -> bool:
    """True for PushI (0x84) or bytecode-14 Push with Int16 type (0xC0 / 0x0F)."""
    op = (word >> 24) & 0xFF
    typ = (word >> 16) & 0xF
    if op == OP_PUSHI:
        return True  # type should be 0x0F; still accept odd type=0 from older buggy patches
    if op == OP_PUSH and typ == DT_INT16:
        return True
    return False


def push_imm(word: int) -> int:
    return word & 0xFFFF


BATTLEGROUPS: tuple[Battlegroup, ...] = (
    Battlegroup(2, "Dummy"),
    Battlegroup(3, "Fake Froggit"),
    Battlegroup(4, "Froggit"),
    Battlegroup(5, "Whimsun"),
    Battlegroup(6, "Froggit + Whimsun"),
    Battlegroup(7, "Moldsmal"),
    Battlegroup(9, "Froggit + Froggit"),
    Battlegroup(13, "Loox"),
    Battlegroup(18, "Vegetoid"),
    Battlegroup(20, "Napstablook"),
    Battlegroup(22, "Toriel"),
    Battlegroup(23, "Doggo"),
    Battlegroup(24, "Lesser Dog"),
    Battlegroup(25, "Dogamy + Dogaressa"),
    Battlegroup(26, "Greater Dog"),
    Battlegroup(27, "Papyrus"),
    Battlegroup(28, "Gyftrot"),
    Battlegroup(40, "Aaron"),
    Battlegroup(41, "Temmie"),
    Battlegroup(44, "Shyren"),
    Battlegroup(45, "Mad Dummy"),
    Battlegroup(47, "Undyne"),
    Battlegroup(48, "Mettaton (quiz)"),
    Battlegroup(49, "Royal Guards"),
    Battlegroup(50, "Tsunderplane"),
    Battlegroup(51, "Vulkin"),
    Battlegroup(52, "Pyrope"),
    Battlegroup(56, "Muffet"),
    Battlegroup(57, "Mettaton (second)"),
    Battlegroup(58, "Undyne (date fight)"),
    Battlegroup(59, "Madjick"),
    Battlegroup(60, "Knight Knight"),
    Battlegroup(61, "Final Froggit"),
    Battlegroup(76, "Royal Guards (alt)"),
    Battlegroup(80, "Mettaton (third)"),
    Battlegroup(81, "Mettaton EX"),
    Battlegroup(82, "Lemon Bread", rare=True),
    Battlegroup(83, "Reaper Bird", rare=True),
    Battlegroup(84, "Snowdrake's Mother", rare=True),
    Battlegroup(85, "Memoryheads", rare=True),
    Battlegroup(86, "Endogeny", rare=True),
    Battlegroup(91, "Monster Kid"),
    Battlegroup(92, "Undyne the Undying", rare=True),
    Battlegroup(93, "Glad Dummy"),
    Battlegroup(94, "Mettaton NEO", rare=True),
    Battlegroup(95, "Sans", rare=True),
    Battlegroup(100, "Asgore (intro)"),
    Battlegroup(101, "Asgore"),
    Battlegroup(135, "Glyde", rare=True),
    Battlegroup(140, "So Sorry", rare=True),
    Battlegroup(255, "Asriel", rare=True),
    Battlegroup(256, "Asriel (final)", rare=True),
)

RARE_BATTLEGROUPS = tuple(b for b in BATTLEGROUPS if b.rare)


def find_home_pushi_sites_in_bytes(data: bytes) -> list[HomeBattlegroupSite]:
    """
    Find int16-push battlegroup immediates that sit shortly after vk_home (36).

    Real data.win encodes PushI as 0x840F00xx (type Int16=0x0F), not 0x840000xx.
    """
    sites: list[HomeBattlegroupSite] = []
    seen: set[int] = set()
    # Match both bytecode 15 PushI and bytecode 14 Push.e for vk_home
    needles = (_pushi_bytes(VK_HOME, opcode=OP_PUSHI), _pushi_bytes(VK_HOME, opcode=OP_PUSH))
    for needle in needles:
        start = 0
        while True:
            idx = data.find(needle, start)
            if idx < 0:
                break
            window = data[idx : idx + 128]
            pos = 4
            while pos + 4 <= len(window):
                word = struct.unpack_from("<I", window, pos)[0]
                if is_int16_push(word):
                    imm = push_imm(word)
                    if imm in HOME_DEFAULTS or 1 <= imm <= 256:
                        abs_off = idx + pos
                        if abs_off not in seen:
                            seen.add(abs_off)
                            sites.append(
                                HomeBattlegroupSite(
                                    abs_off, imm, "pushi", "vk_home_pattern", template=word
                                )
                            )
                        break
                pos += 4
            start = idx + 4
    return sites


def _obj_time_default_pushis(raw: bytes) -> list[HomeBattlegroupSite]:
    """Fallback: factory-default PushIs inside obj_time scripts only."""
    out: list[HomeBattlegroupSite] = []
    try:
        reader = BinaryReader(raw)
        for name, bc_off, length in _find_code_entries(reader):
            if "obj_time" not in (name or "").lower():
                continue
            pos = 0
            while pos + 4 <= length:
                word = struct.unpack_from("<I", raw, bc_off + pos)[0]
                if is_int16_push(word) and push_imm(word) in HOME_DEFAULTS:
                    out.append(
                        HomeBattlegroupSite(
                            bc_off + pos,
                            push_imm(word),
                            "pushi",
                            f"obj_time_default:{name}",
                            template=word,
                        )
                    )
                op = (word >> 24) & 0xFF
                if op in (0x45, 0x41, 0xD9, 0xDA):
                    pos += 8
                else:
                    pos += 4
    except Exception:
        pass
    return out


def discover_home_battlegroup_sites(data: bytes | bytearray) -> list[HomeBattlegroupSite]:
    """Find Home battlegroup slots — correct PushI encoding + safe fallbacks."""
    raw = bytes(data)
    found: dict[int, HomeBattlegroupSite] = {}

    for site in find_home_pushi_sites_in_bytes(raw):
        found[site.offset] = site

    try:
        reader = BinaryReader(raw)
        for name, bc_off, length in _find_code_entries(reader):
            if "obj_time" not in (name or "").lower():
                continue
            chunk = raw[bc_off : bc_off + length]
            for site in find_home_pushi_sites_in_bytes(chunk):
                abs_off = bc_off + site.offset
                found[abs_off] = HomeBattlegroupSite(
                    abs_off,
                    site.value,
                    "pushi",
                    f"obj_time:{name}",
                    template=site.template,
                )
    except Exception:
        pass

    # TCRF offsets — factory default raw int OR correctly-typed PushI
    for offset in HOME_BATTLEGROUP_OFFSETS:
        if offset + 4 > len(raw):
            continue
        word = struct.unpack_from("<I", raw, offset)[0]
        if is_int16_push(word) and push_imm(word) in HOME_DEFAULTS:
            found[offset] = HomeBattlegroupSite(
                offset, push_imm(word), "pushi", "tcrf", template=word
            )
        elif word in HOME_DEFAULTS:
            found[offset] = HomeBattlegroupSite(offset, word, "raw", "tcrf")

    # If vk_home pattern missed (odd build), use obj_time factory defaults only
    if not found:
        for site in _obj_time_default_pushis(raw):
            found[site.offset] = site

    return sorted(found.values(), key=lambda s: s.offset)


def set_home_battlegroup(data_win: str | Path, battlegroup_id: int, *, backup: bool = True) -> tuple[bool, str]:
    if battlegroup_id < 0 or battlegroup_id > 1000:
        return False, "Battlegroup id must be between 0 and 1000."
    path = Path(data_win)
    data = bytearray(path.read_bytes())
    sites = discover_home_battlegroup_sites(data)
    if not sites:
        debug = "on" if debug_flag_enabled(path) else "off"
        return (
            False,
            f"Could not find the Home-key battlegroup in this data.win (debug flag is {debug}). "
            "Click Restore data.win, then Enable live patches, Launch Undertale once, "
            "close it, and try Start Fight again. Or use UndertaleModTool → ChangeHomeBattlegroup.",
        )

    if backup:
        bak = path.with_suffix(path.suffix + ".battlebak")
        if not bak.exists():
            bak.write_bytes(bytes(data))

    wrote = []
    for site in sites:
        struct.pack_into("<I", data, site.offset, site.encode(battlegroup_id))
        wrote.append(f"0x{site.offset:X}/{site.kind}/{site.source or '?'}")
    path.write_bytes(data)
    return True, f"Home battlegroup set to {battlegroup_id} on disk ({', '.join(wrote)})."


def _patch_live_form_sites(data_win: Path, sites: list[HomeBattlegroupSite], battlegroup_id: int) -> tuple[bool, list[str]]:
    notes = []
    any_ok = False
    for site in sites:
        new_word = site.encode(battlegroup_id)
        ok, msg = patch_int32_in_data_win_image(data_win, site.offset, new_word)
        notes.append(f"FORM+0x{site.offset:X}:{msg}")
        if ok:
            any_ok = True
    return any_ok, notes


def _patch_live_vk_home_patterns(battlegroup_id: int) -> tuple[int, str]:
    """
    In the live process, find PushI vk_home … PushI <id> and rewrite only that
    battlegroup PushI. Never spray raw integers across RAM.
    """
    if not is_windows():
        return 0, "Windows only"
    pid = find_undertale_pid()
    if not pid:
        return 0, "not running"
    n = replace_home_battlegroup_near_vk_home(pid, None, battlegroup_id, max_hits=8)
    return n, f"rewrote {n} Home PushI site(s) → {battlegroup_id}"


def replace_home_battlegroup_near_vk_home(
    pid: int,
    old_id: int | None,
    new_id: int,
    *,
    max_hits: int = 6,
) -> int:
    """Replace int16-push battlegroup after vk_home push in process memory."""
    from .memory_patch import iter_process_memory

    needles = (_pushi_bytes(VK_HOME, opcode=OP_PUSHI), _pushi_bytes(VK_HOME, opcode=OP_PUSH))
    handle = _open_process(pid)
    hits = 0
    try:
        for base, data in iter_process_memory(handle):
            for home in needles:
                start = 0
                while hits < max_hits:
                    idx = data.find(home, start)
                    if idx < 0:
                        break
                    end = min(len(data), idx + 128)
                    pos = idx + 4
                    while pos + 4 <= end:
                        word = struct.unpack_from("<I", data, pos)[0]
                        if is_int16_push(word):
                            imm = push_imm(word)
                            match = (old_id is None and 1 <= imm <= 256 and imm != new_id) or (
                                old_id is not None and imm == old_id
                            )
                            if match:
                                new_word = (word & 0xFFFF0000) | (new_id & 0xFFFF)
                                try:
                                    _write(handle, base + pos, struct.pack("<I", new_word))
                                    hits += 1
                                except RuntimeError:
                                    pass
                                break
                        pos += 4
                    start = idx + 4
            if hits >= max_hits:
                break
    finally:
        if kernel32:
            kernel32.CloseHandle(handle)
    return hits


def set_home_battlegroup_live(data_win: str | Path, battlegroup_id: int) -> tuple[bool, str]:
    """Surgical live patch: FORM sites + vk_home pattern only (no raw int sprays)."""
    path = Path(data_win)
    raw = path.read_bytes()
    # Re-discover from the *current* on-disk bytes (caller should write disk first)
    sites = discover_home_battlegroup_sites(raw)
    notes: list[str] = []
    any_ok = False

    form_ok, form_notes = _patch_live_form_sites(path, sites, battlegroup_id)
    notes.extend(form_notes)
    if form_ok:
        any_ok = True

    n, detail = _patch_live_vk_home_patterns(battlegroup_id)
    notes.append(f"vk_home patterns: {detail}")
    if n:
        any_ok = True

    if any_ok:
        return True, "Live patch OK (" + "; ".join(notes) + ")."
    return (
        False,
        "Live patch missed the Home handler. Close Undertale → Launch → Start Fight. "
        "(" + "; ".join(notes) + ")",
    )


def trigger_home_fight() -> tuple[bool, str]:
    if not undertale_is_running():
        return False, "Undertale is not running. Launch it first, load a save, then start the fight."
    if not find_undertale_hwnd():
        return False, "Could not find the UNDERTALE window."
    # Escape menus first so Home is handled by obj_time
    _send_key_to_undertale(VK_ESCAPE, presses=1)
    time.sleep(0.08)
    if not _send_key_to_undertale(VK_HOME_KEY, presses=2):
        return False, "Could not send the Home key. Click the Undertale window and press Home."
    time.sleep(0.2)
    return True, "Sent Home — fight should start if debug mode is on."


def start_fight(
    battlegroup_id: int,
    *,
    data_win: str | Path | None = None,
    ensure_debug: bool = True,
    save_folder: str | Path | None = None,
    prefer_rare_if_enabled: bool = False,
) -> tuple[bool, str]:
    """
    Set Home battlegroup surgically and trigger the fight.
    """
    notes: list[str] = []
    if not data_win or not Path(data_win).is_file():
        return False, "Open your Undertale folder (data.win) first."

    if prefer_rare_if_enabled:
        from .chaos import rare_mode_enabled

        if rare_mode_enabled(save_folder):
            rare_ids = {b.id for b in RARE_BATTLEGROUPS}
            if battlegroup_id not in rare_ids and RARE_BATTLEGROUPS:
                battlegroup_id = RARE_BATTLEGROUPS[0].id
                notes.append(f"rare mode → battlegroup {battlegroup_id}")

    if ensure_debug and not debug_flag_enabled(data_win):
        try:
            if not undertale_is_running():
                enable_debug_mode(data_win, backup=True)
                notes.append("enabled debug")
            else:
                notes.append("debug flag off on disk — relaunch after Enable live patches")
        except Exception as exc:
            notes.append(f"debug failed: {exc}")

    ok, msg = set_home_battlegroup(data_win, battlegroup_id, backup=True)
    notes.append(msg)
    if not ok:
        return False, " | ".join(notes)

    if undertale_is_running():
        live_ok, live_msg = set_home_battlegroup_live(data_win, battlegroup_id)
        notes.append(live_msg)
        if not live_ok:
            return False, " | ".join(notes)
        time.sleep(0.15)
        ok2, msg2 = trigger_home_fight()
        notes.append(msg2)
        return ok2, " | ".join(notes)

    return (
        False,
        "Battlegroup saved to data.win. Launch Undertale, load your save, then Start Fight "
        "(or press Home).\n" + " | ".join(notes),
    )


def start_random_rare_fight(
    *,
    data_win: str | Path | None = None,
    save_folder: str | Path | None = None,
) -> tuple[bool, str]:
    import random

    if not RARE_BATTLEGROUPS:
        return False, "No rare battlegroups configured."
    bg = random.choice(RARE_BATTLEGROUPS)
    ok, msg = start_fight(bg.id, data_win=data_win, save_folder=save_folder)
    return ok, f"{bg.name} ({bg.id}): {msg}"
