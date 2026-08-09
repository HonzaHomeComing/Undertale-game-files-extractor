"""Undertale battlegroup / fight launcher (debug Home key + live memory patch)."""

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
from .memory_patch import patch_int32_in_data_win_image, patch_u32_everywhere_in_game

# Seed offsets from TCRF / community (version-specific; discovery finds more).
HOME_BATTLEGROUP_OFFSETS = (
    0x9F553C,  # 1.00 (default So Sorry 140)
    0x9EB414,  # 1.001 (Mettaton 80)
    0x9EB918,  # 1.001 Linux
    0xBD8200,  # 1.06 (Mettaton 57)
)

# Common factory defaults for the Home-key battlegroup.
HOME_DEFAULTS = frozenset({57, 80, 140, 81})

OP_PUSHI = 0x84
VK_HOME = 0x24  # also the GML vk_home constant pushed before keyboard_check


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

    def encode(self, battlegroup_id: int) -> int:
        bg = int(battlegroup_id) & 0xFFFF
        if self.kind == "pushi":
            return (OP_PUSHI << 24) | bg
        return bg


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


def _classify_word(word: int) -> tuple[str, int] | None:
    """Return (kind, battlegroup_id) if word looks like a Home battlegroup slot."""
    if (word >> 24) == OP_PUSHI:
        imm = word & 0xFFFF
        if imm in HOME_DEFAULTS or imm <= 400:
            return "pushi", imm
        return None
    if word in HOME_DEFAULTS:
        return "raw", word
    if word <= 400:
        return "raw", word
    return None


def discover_home_battlegroup_sites(data: bytes | bytearray) -> list[HomeBattlegroupSite]:
    """
    Find Home-key battlegroup constants in data.win.

    Prefers PushI immediates near vk_home (36) checks inside obj_time scripts,
    then known TCRF offsets, then other default PushIs in obj_time.
    """
    raw = bytes(data)
    found: dict[int, HomeBattlegroupSite] = {}

    def add(offset: int, value: int, kind: str) -> None:
        if offset < 0 or offset + 4 > len(raw):
            return
        found[offset] = HomeBattlegroupSite(offset, value, kind)

    # 1) Known offsets
    for offset in HOME_BATTLEGROUP_OFFSETS:
        if offset + 4 > len(raw):
            continue
        word = struct.unpack_from("<I", raw, offset)[0]
        classified = _classify_word(word)
        if classified and classified[1] <= 400:
            kind, val = classified
            # Prefer pushi classification when opcode matches
            if (word >> 24) == OP_PUSHI:
                add(offset, word & 0xFFFF, "pushi")
            elif word in HOME_DEFAULTS or word <= 256:
                add(offset, word, "raw")

    # 2) obj_time scripts: PushI vk_home (36) then a later PushI battlegroup
    try:
        reader = BinaryReader(raw)
        for name, bc_off, length in _find_code_entries(reader):
            low = (name or "").lower()
            if "obj_time" not in low:
                continue
            words: list[tuple[int, int, int]] = []  # pos, op, imm_or_word
            pos = 0
            while pos + 4 <= length:
                word = struct.unpack_from("<I", raw, bc_off + pos)[0]
                op = (word >> 24) & 0xFF
                words.append((pos, op, word))
                if op in (0x45, 0x41, 0xD9, 0xDA):
                    pos += 8
                else:
                    pos += 4
            for i, (pos_i, op_i, word_i) in enumerate(words):
                if op_i != OP_PUSHI or (word_i & 0xFFFF) != VK_HOME:
                    continue
                # Look ahead for PushI with a plausible battlegroup id
                for j in range(i + 1, min(i + 24, len(words))):
                    pos_j, op_j, word_j = words[j]
                    if op_j != OP_PUSHI:
                        continue
                    imm = word_j & 0xFFFF
                    if imm in HOME_DEFAULTS or 1 <= imm <= 256:
                        add(bc_off + pos_j, imm, "pushi")
                        break
            # Also collect default PushIs in obj_time as weaker candidates
            for pos_i, op_i, word_i in words:
                if op_i != OP_PUSHI:
                    continue
                imm = word_i & 0xFFFF
                if imm in HOME_DEFAULTS:
                    add(bc_off + pos_i, imm, "pushi")
    except Exception:
        pass

    return sorted(found.values(), key=lambda s: s.offset)


def set_home_battlegroup(data_win: str | Path, battlegroup_id: int, *, backup: bool = True) -> tuple[bool, str]:
    if battlegroup_id < 0 or battlegroup_id > 1000:
        return False, "Battlegroup id must be between 0 and 1000."
    path = Path(data_win)
    data = bytearray(path.read_bytes())
    sites = discover_home_battlegroup_sites(data)
    if not sites:
        # Fallback: try known offsets as raw even if discovery failed filters
        for offset in HOME_BATTLEGROUP_OFFSETS:
            if offset + 4 <= len(data):
                word = struct.unpack_from("<I", data, offset)[0]
                if word <= 400 or (word >> 24) == OP_PUSHI:
                    kind = "pushi" if (word >> 24) == OP_PUSHI else "raw"
                    val = (word & 0xFFFF) if kind == "pushi" else word
                    sites.append(HomeBattlegroupSite(offset, val, kind))
    if not sites:
        return False, "Could not find a Home battlegroup slot in this data.win."

    if backup:
        bak = path.with_suffix(path.suffix + ".battlebak")
        if not bak.exists():
            bak.write_bytes(bytes(data))

    wrote = []
    for site in sites:
        new_word = site.encode(battlegroup_id)
        struct.pack_into("<I", data, site.offset, new_word)
        wrote.append(f"0x{site.offset:X}/{site.kind}")
    path.write_bytes(data)
    return True, f"Home battlegroup set to {battlegroup_id} on disk ({', '.join(wrote)})."


def set_home_battlegroup_live(data_win: str | Path, battlegroup_id: int) -> tuple[bool, str]:
    """
    Patch Home battlegroup in the running game.

    Writes FORM+file_offset AND scans process memory for old PushI/raw words so
    copied bytecode buffers (not just the data.win mapping) update too.
    """
    path = Path(data_win)
    raw = path.read_bytes()
    sites = discover_home_battlegroup_sites(raw)
    notes: list[str] = []
    any_ok = False
    new_id = int(battlegroup_id) & 0xFFFF
    new_pushi = (OP_PUSHI << 24) | new_id

    for site in sites:
        new_word = site.encode(battlegroup_id)
        ok, msg = patch_int32_in_data_win_image(
            path, site.offset, new_word, expected_old=site.encode(site.value)
        )
        notes.append(f"0x{site.offset:X}:{msg}")
        if ok:
            any_ok = True
        # Also search/replace this site's old encoded word anywhere in RAM
        old_word = site.encode(site.value)
        if old_word != new_word:
            n, detail = patch_u32_everywhere_in_game(old_word, new_word)
            if n:
                notes.append(f"site-scan {detail}")
                any_ok = True

    # Sweep common factory defaults still sitting in bytecode copies
    for default in sorted(HOME_DEFAULTS):
        if default == new_id:
            continue
        old_pushi = (OP_PUSHI << 24) | default
        n, detail = patch_u32_everywhere_in_game(old_pushi, new_pushi)
        if n:
            notes.append(f"pushi {default}→{new_id}: {detail}")
            any_ok = True
        n2, detail2 = patch_u32_everywhere_in_game(default, new_id)
        if n2:
            notes.append(f"raw {default}→{new_id}: {detail2}")
            any_ok = True

    if any_ok:
        return True, "Live memory patched (" + "; ".join(notes) + ")."
    return False, "Live memory patch failed (" + "; ".join(notes) + ")."


def trigger_home_fight() -> tuple[bool, str]:
    if not undertale_is_running():
        return False, "Undertale is not running. Launch it first, load a save, then start the fight."
    if not find_undertale_hwnd():
        return False, "Could not find the UNDERTALE window."
    if not _send_key_to_undertale(VK_HOME, presses=2):
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
    Set Home battlegroup and trigger the fight.
    Always writes data.win; if the game is running, also patches memory so the
    selection applies immediately (fixes always-Mettaton bug).
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
            notes.append(
                "Could not patch the live game — close Undertale, Launch again, "
                "then Start Fight (disk patch is ready)."
            )
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
    """Start a fight from the rare battlegroup list."""
    import random

    if not RARE_BATTLEGROUPS:
        return False, "No rare battlegroups configured."
    bg = random.choice(RARE_BATTLEGROUPS)
    ok, msg = start_fight(bg.id, data_win=data_win, save_folder=save_folder)
    return ok, f"{bg.name} ({bg.id}): {msg}"
