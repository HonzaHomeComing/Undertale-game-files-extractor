"""Undertale battlegroup / fight launcher (debug Home key)."""

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


# Curated list — major encounters + notable groups (from scr_battlegroup).
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
    Battlegroup(82, "Lemon Bread"),
    Battlegroup(83, "Reaper Bird"),
    Battlegroup(84, "Snowdrake's Mother"),
    Battlegroup(85, "Memoryheads"),
    Battlegroup(86, "Endogeny"),
    Battlegroup(91, "Monster Kid"),
    Battlegroup(92, "Undyne the Undying"),
    Battlegroup(93, "Glad Dummy"),
    Battlegroup(94, "Mettaton NEO"),
    Battlegroup(95, "Sans"),
    Battlegroup(100, "Asgore (intro)"),
    Battlegroup(101, "Asgore"),
    Battlegroup(135, "Glyde"),
    Battlegroup(140, "So Sorry"),
    Battlegroup(255, "Asriel"),
    Battlegroup(256, "Asriel (final)"),
)


def set_home_battlegroup(data_win: str | Path, battlegroup_id: int, *, backup: bool = True) -> tuple[bool, str]:
    """
    Write the debug Home-key battlegroup id into data.win at known offsets.
    Close Undertale before calling for a reliable write; if the game is already
    running with debug on, the in-memory value may not update until restart —
    but sending Home still uses whatever is loaded. Prefer set-then-restart, or
    set while closed then Launch.
    """
    if battlegroup_id < 0 or battlegroup_id > 1000:
        return False, "Battlegroup id must be between 0 and 1000."
    path = Path(data_win)
    data = bytearray(path.read_bytes())
    wrote = []
    for offset in HOME_BATTLEGROUP_OFFSETS:
        if offset + 4 > len(data):
            continue
        current = struct.unpack_from("<I", data, offset)[0]
        # Only patch if it already looks like a battlegroup slot (0..400).
        if current > 400:
            continue
        if current == battlegroup_id:
            wrote.append(f"0x{offset:X}=already")
            continue
        if backup:
            bak = path.with_suffix(path.suffix + ".battlebak")
            if not bak.exists():
                bak.write_bytes(path.read_bytes())
        struct.pack_into("<I", data, offset, int(battlegroup_id))
        wrote.append(f"0x{offset:X}")
    if not wrote:
        return (
            False,
            "Could not find a Home battlegroup slot in this data.win.\n"
            "Enable live patches (debug), or set the fight in UndertaleModTool.",
        )
    path.write_bytes(data)
    return True, f"Home battlegroup set to {battlegroup_id} ({', '.join(wrote)})."


def trigger_home_fight() -> tuple[bool, str]:
    """Focus Undertale and press Home to start the current battlegroup fight."""
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
) -> tuple[bool, str]:
    """
    Set Home battlegroup (if data_win given) and trigger the fight.
    If Undertale is running, data.win writes may not apply until restart —
    we still try Home with the currently loaded debug battlegroup, and tell
    the user to relaunch if needed.
    """
    notes = []
    if data_win and Path(data_win).is_file():
        if ensure_debug and not debug_flag_enabled(data_win):
            try:
                if not undertale_is_running():
                    enable_debug_mode(data_win, backup=True)
                    notes.append("enabled debug")
            except Exception as exc:
                notes.append(f"debug failed: {exc}")
        if undertale_is_running():
            notes.append(
                "Undertale is open — Home battlegroup patch needs a restart to take effect. "
                "Close the game, click Launch Patched Undertale, load a save, then Start Fight again."
            )
            # Still try Home in case the value was already set from a previous launch.
        else:
            ok, msg = set_home_battlegroup(data_win, battlegroup_id, backup=True)
            notes.append(msg)
            if not ok:
                return False, " | ".join(notes)

    if not undertale_is_running():
        return (
            False,
            "Battlegroup saved. Launch Undertale, load your save, then click Start Fight "
            "(or press Home in-game).\n" + " | ".join(notes),
        )

    ok, msg = trigger_home_fight()
    notes.append(msg)
    return ok, " | ".join(notes)
