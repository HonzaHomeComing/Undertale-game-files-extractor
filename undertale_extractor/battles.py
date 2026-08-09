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
    VK_ESCAPE,
)
from .memory_patch import patch_int32_in_data_win_image

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

# Home key is GameMaker KeyPress event 36 on obj_mainchara:
#   global.battlegroup = 57 + nnn;   (later PC builds; older use 80 / 140)
_HOME_CODE_MARKERS = (
    "keypress_36",
    "keyboard_36",
    "keypress_vk_home",
)


def _iter_code_pushis(raw: bytes, bc_off: int, length: int):
    pos = 0
    while pos + 4 <= length:
        word = struct.unpack_from("<I", raw, bc_off + pos)[0]
        if is_int16_push(word):
            yield pos, word, push_imm(word)
        op = (word >> 24) & 0xFF
        if op in (0x45, 0x41, 0xD9, 0xDA):
            pos += 8
        else:
            pos += 4


def _sites_in_home_keypress(raw: bytes) -> list[HomeBattlegroupSite]:
    """Primary: PushI factory defaults inside obj_mainchara KeyPress_36."""
    out: list[HomeBattlegroupSite] = []
    try:
        reader = BinaryReader(raw)
        for name, bc_off, length in _find_code_entries(reader):
            low = (name or "").lower()
            if not any(m in low for m in _HOME_CODE_MARKERS):
                continue
            # Prefer mainchara; still accept other objects with KeyPress_36
            for pos, word, imm in _iter_code_pushis(raw, bc_off, length):
                # 57+nnn base, or older hard-coded defaults; also allow already-patched ids
                if imm in HOME_DEFAULTS or 1 <= imm <= 256:
                    # In KeyPress_36 the battlegroup PushI is the interesting constant.
                    # Skip tiny literals like 0/1 used for debug flags.
                    if imm == 0 or imm == 1:
                        continue
                    out.append(
                        HomeBattlegroupSite(
                            bc_off + pos,
                            imm,
                            "pushi",
                            f"keypress36:{name}",
                            template=word,
                        )
                    )
    except Exception:
        pass
    # If multiple PushIs (e.g. 57 and later 82 for plot 998), keep factory defaults first
    defaults = [s for s in out if s.value in HOME_DEFAULTS]
    if defaults:
        return defaults
    # Otherwise keep a single best candidate (smallest offset)
    return out[:1]


def discover_home_battlegroup_sites(data: bytes | bytearray) -> list[HomeBattlegroupSite]:
    """
    Find the Home-key battlegroup constant.

    Real Undertale uses obj_mainchara KeyPress_36:
        global.battlegroup = 57 + nnn;
    Not a file-wide search for PushI(36) (that matches hundreds of unrelated 36s).
    """
    raw = bytes(data)
    found: dict[int, HomeBattlegroupSite] = {}

    for site in _sites_in_home_keypress(raw):
        found[site.offset] = site

    # TCRF hex offsets (raw int32 or PushI) when they still hold a factory default
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
            f"Could not find obj_mainchara KeyPress_36 battlegroup in this data.win "
            f"(debug flag is {debug}). Restore data.win → Enable live patches → try again.",
        )

    if backup:
        bak = path.with_suffix(path.suffix + ".battlebak")
        if not bak.exists():
            bak.write_bytes(bytes(data))

    # Only patch a small set — never dozens of false positives
    sites = sites[:4]
    wrote = []
    for site in sites:
        struct.pack_into("<I", data, site.offset, site.encode(battlegroup_id))
        wrote.append(f"0x{site.offset:X}/{site.kind}/{site.source or '?'}")
    path.write_bytes(data)
    return True, f"Home battlegroup set to {battlegroup_id} on disk ({', '.join(wrote)})."


def _patch_live_form_sites(data_win: Path, sites: list[HomeBattlegroupSite], battlegroup_id: int) -> tuple[bool, list[str]]:
    notes = []
    any_ok = False
    for site in sites[:4]:
        new_word = site.encode(battlegroup_id)
        ok, msg = patch_int32_in_data_win_image(data_win, site.offset, new_word)
        notes.append(f"FORM+0x{site.offset:X}:{msg}")
        if ok:
            any_ok = True
        # Surgical RAM replace of this site's exact old dword only (max a few hits)
        old_word = site.template or site.encode(site.value)
        if old_word != new_word:
            from .memory_patch import patch_u32_everywhere_in_game

            n, detail = patch_u32_everywhere_in_game(old_word, new_word)
            # patch_u32_everywhere uses max 8 — OK for unique PushI templates
            if n:
                notes.append(f"ram {detail}")
                any_ok = True
    return any_ok, notes


def set_home_battlegroup_live(data_win: str | Path, battlegroup_id: int) -> tuple[bool, str]:
    """Live patch only the discovered KeyPress_36 / TCRF sites — no file-wide sprays."""
    path = Path(data_win)
    raw = path.read_bytes()
    sites = discover_home_battlegroup_sites(raw)[:4]
    if not sites:
        return False, "No Home KeyPress_36 site to patch live."
    ok, notes = _patch_live_form_sites(path, sites, battlegroup_id)
    if ok:
        return True, "Live patch OK (" + "; ".join(notes) + ")."
    return (
        False,
        "Live patch missed KeyPress_36. Close Undertale → Launch → Start Fight. "
        "(" + "; ".join(notes) + ")",
    )


def trigger_home_fight() -> tuple[bool, str]:
    if not undertale_is_running():
        return False, "Undertale is not running. Launch it first, load a save, then start the fight."
    if not find_undertale_hwnd():
        return False, "Could not find the UNDERTALE window."
    # Must be in the overworld with Frisk (KeyPress_36 is on obj_mainchara).
    _send_key_to_undertale(VK_ESCAPE, presses=1)
    time.sleep(0.08)
    if not _send_key_to_undertale(VK_HOME_KEY, presses=2):
        return False, "Could not send the Home key. Click the Undertale window and press Home."
    time.sleep(0.2)
    return True, "Sent Home — be in the overworld (not a menu). Fight starts if debug is on."


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
