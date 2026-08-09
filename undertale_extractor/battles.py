"""Undertale battlegroup / fight launcher (debug Home key + live memory patch)."""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from pathlib import Path

from .live_teleport import (
    _send_key_to_undertale,
    debug_flag_enabled,
    enable_debug_mode,
    find_undertale_hwnd,
    undertale_is_running,
)
from .memory_patch import patch_int32_in_data_win_image

# data.win offsets for the Home-key battlegroup id (little-endian int32).
HOME_BATTLEGROUP_OFFSETS = (
    0x9F553C,  # 1.00
    0x9EB414,  # 1.001
    0x9EB918,  # 1.001 Linux
    0xBD8200,  # 1.06 / later
)

VK_HOME = 0x24


@dataclass(frozen=True)
class Battlegroup:
    id: int
    name: str
    rare: bool = False


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


def set_home_battlegroup(data_win: str | Path, battlegroup_id: int, *, backup: bool = True) -> tuple[bool, str]:
    if battlegroup_id < 0 or battlegroup_id > 1000:
        return False, "Battlegroup id must be between 0 and 1000."
    path = Path(data_win)
    data = bytearray(path.read_bytes())
    wrote = []
    for offset in HOME_BATTLEGROUP_OFFSETS:
        if offset + 4 > len(data):
            continue
        current = struct.unpack_from("<I", data, offset)[0]
        # Accept common defaults (incl. Mettaton 80 / 57 / So Sorry 140).
        if current > 400 and current != battlegroup_id:
            continue
        if current == battlegroup_id:
            wrote.append(f"0x{offset:X}=already")
            continue
        if backup:
            bak = path.with_suffix(path.suffix + ".battlebak")
            if not bak.exists():
                bak.write_bytes(bytes(data))
        struct.pack_into("<I", data, offset, int(battlegroup_id))
        wrote.append(f"0x{offset:X}")
    if not wrote:
        return (
            False,
            "Could not find a Home battlegroup slot in this data.win.",
        )
    path.write_bytes(data)
    return True, f"Home battlegroup set to {battlegroup_id} on disk ({', '.join(wrote)})."


def set_home_battlegroup_live(data_win: str | Path, battlegroup_id: int) -> tuple[bool, str]:
    """Patch the Home battlegroup inside the running game's loaded data.win image."""
    path = Path(data_win)
    raw = path.read_bytes()
    notes = []
    any_ok = False
    for offset in HOME_BATTLEGROUP_OFFSETS:
        if offset + 4 > len(raw):
            continue
        current = struct.unpack_from("<I", raw, offset)[0]
        if current > 400:
            continue
        ok, msg = patch_int32_in_data_win_image(path, offset, battlegroup_id, expected_old=current)
        notes.append(f"0x{offset:X}:{msg}")
        if ok:
            any_ok = True
    if any_ok:
        return True, "Live memory patched (" + "; ".join(notes) + ")."
    return False, "Live memory patch failed (" + "; ".join(notes) + ")."


def trigger_home_fight() -> tuple[bool, str]:
    if not undertale_is_running():
        return False, "Undertale is not running. Launch it first, load a save, then start the fight."
    if not find_undertale_hwnd():
        return False, "Could not find the UNDERTALE window."
    if not _send_key_to_undertale(VK_HOME, presses=1):
        return False, "Could not send the Home key. Click the Undertale window and press Home."
    time.sleep(0.15)
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
