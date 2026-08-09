"""Ruins live reset, room chaos randomizer, rare-encounter helpers."""

from __future__ import annotations

import json
import random
import struct
from pathlib import Path

from .binary import BinaryReader
from .dogcheck import _find_code_entries
from .live_teleport import live_teleport_to_room, undertale_is_running
from .save_editor import (
    LINE_ARMOR,
    LINE_ARMOR_DF,
    LINE_AT,
    LINE_DF,
    LINE_EXP,
    LINE_GOLD,
    LINE_HP,
    LINE_KILLS,
    LINE_LOVE,
    LINE_MAXHP,
    LINE_NAME,
    LINE_WEAPON,
    LINE_WEAPON_AT,
    INV_SLOTS,
    PlayerStats,
    read_player_stats,
    write_player_stats,
)
from .teleport import ROOM_LINE_INDEX, read_save_info, teleport_to_room

# First SAVE point in the Ruins (Entrance).
RUINS_FIRST_SAVE_ROOM = 6  # room_ruins1 — "Ruins - Entrance"
# Leaf Pile (first encounter SAVE) as alternate.
RUINS_LEAF_PILE_ROOM = 12

# file0 line 36 (1-based) = fun value
LINE_FUN = 35

OP_PUSHI = 0x84
OP_PUSH = 0xC0
OP_CALL_V15 = 0xD9
OP_CALL_V14 = 0xDA

# Room name substrings that should NOT be chaos destinations / door targets.
_TEXT_ROOM_MARKERS = (
    "intro",
    "story",
    "credit",
    "ending",
    "end_",
    "gameover",
    "battle",
    "battlegroup",
    "menu",
    "name",
    "gaster",
    "dogcheck",
    "of_dog",
    "room_of_dog",
    "shop",  # keep shops stable
    "phone",
    "writer",
    "dialog",
    "text",
    "savepoint",  # not real rooms
    "area0",
    "nothing",
    "blank",
    "test",
    "flowey_defeat",
    "sansemail",
)


def _is_text_or_special_room(name: str) -> bool:
    low = name.lower()
    return any(m in low for m in _TEXT_ROOM_MARKERS)


def list_rooms_from_data_win(data_win: str | Path) -> list[tuple[int, str]]:
    """Return (room_id, name) for all ROOM entries."""
    path = Path(data_win)
    reader = BinaryReader.from_path(path)
    reader.seek(0)
    if reader.read_tag() != "FORM":
        return []
    form_size = reader.read_u32()
    form_end = reader.position + form_size
    room_start = None
    while reader.position + 8 <= form_end:
        tag = reader.read_tag()
        size = reader.read_u32()
        start = reader.position
        if tag == "ROOM":
            room_start = start
        reader.seek(start + size)
    if room_start is None:
        return []
    reader.seek(room_start)
    count = reader.read_u32()
    if count <= 0 or count > 50_000:
        return []
    offsets = [reader.read_u32() for _ in range(count)]
    rooms: list[tuple[int, str]] = []
    for index, off in enumerate(offsets):
        try:
            reader.seek(off)
            name = reader.read_offset_string() or f"room_{index}"
        except Exception:
            name = f"room_{index}"
        rooms.append((index, name))
    return rooms


def playable_room_ids(data_win: str | Path) -> list[int]:
    return [rid for rid, name in list_rooms_from_data_win(data_win) if not _is_text_or_special_room(name)]


def fresh_ruins_stats(name: str = "CHARA") -> PlayerStats:
    """Default / zeroed new-game-ish stats at Ruins start."""
    return PlayerStats(
        name=name or "CHARA",
        love=1,
        hp=20,
        max_hp=20,
        at=10,
        weapon_at=0,
        df=10,
        armor_df=0,
        exp=0,
        gold=0,
        kills=0,
        inventory=[0] * 8,
        weapon=3,  # Stick
        armor=4,  # Bandage
        room=RUINS_FIRST_SAVE_ROOM,
    )


def live_ruins_reset(
    *,
    save_folder: str | Path | None = None,
    data_win: str | Path | None = None,
    room_id: int = RUINS_FIRST_SAVE_ROOM,
) -> tuple[bool, str]:
    """
    Reset stats to defaults, move to first Ruins SAVE, and live-reload (L) if running.
    """
    try:
        current = read_player_stats(save_folder)
        name = current.name or "CHARA"
    except Exception:
        name = "CHARA"
    stats = fresh_ruins_stats(name)
    stats.room = room_id
    path = write_player_stats(stats, save_folder, backup=True)
    # Also set room line / ini via teleport helper
    teleport_to_room(room_id, save_folder, backup=False)

    if undertale_is_running() and data_win:
        result, _ = live_teleport_to_room(room_id, save_folder=save_folder, data_win=data_win)
        if result.ok:
            return True, f"Live reset → Ruins SAVE (room {room_id}), stats cleared. ({path})"
        return True, (
            f"Save reset → Ruins room {room_id}, stats cleared ({path}). "
            f"Live reload: {result.detail}"
        )
    return True, (
        f"Save reset → Ruins room {room_id}, stats cleared ({path}). "
        "Start Undertale / press Continue (or L with debug)."
    )


def _opcode(word: int) -> int:
    return (word >> 24) & 0xFF


def randomize_room_gotos(
    data_win: str | Path,
    *,
    seed: int | None = None,
    backup: bool = True,
) -> tuple[bool, str, dict[int, int]]:
    """
    Shuffle room_goto destinations among playable (non-text) rooms by rewriting
    PushI immediates that look like room ids and sit just before a Call.
    """
    path = Path(data_win)
    playable = playable_room_ids(path)
    if len(playable) < 10:
        return False, "Not enough playable rooms found to shuffle.", {}

    rng = random.Random(seed)
    shuffled = playable[:]
    rng.shuffle(shuffled)
    mapping = {old: new for old, new in zip(playable, shuffled)}
    # Avoid fixed points a bit
    for _ in range(3):
        fixed = [a for a, b in mapping.items() if a == b]
        if len(fixed) < 2:
            break
        rng.shuffle(fixed)
        for i in range(0, len(fixed) - 1, 2):
            a, b = fixed[i], fixed[i + 1]
            mapping[a], mapping[b] = mapping[b], mapping[a]

    raw = bytearray(path.read_bytes())
    reader = BinaryReader(bytes(raw))
    # Walk CODE entries' bytecode and rewrite pushi room ids before calls
    changed = 0
    for _name, bc_off, length in _find_code_entries(reader):
        pos = 0
        while pos + 8 <= length:
            word = struct.unpack_from("<I", raw, bc_off + pos)[0]
            op = _opcode(word)
            # PushI (v15) with room id in low 16 bits
            if op == OP_PUSHI:
                value = word & 0xFFFF
                if value in mapping:
                    # Look ahead for Call within next 3 instructions
                    ahead = pos + 4
                    found_call = False
                    for _ in range(3):
                        if ahead + 4 > length:
                            break
                        w2 = struct.unpack_from("<I", raw, bc_off + ahead)[0]
                        op2 = _opcode(w2)
                        if op2 in (OP_CALL_V15, OP_CALL_V14):
                            found_call = True
                            break
                        # skip typical 1-word ops; pop/call may be 2 words
                        if op2 in (0x45, 0x41, OP_CALL_V15, OP_CALL_V14):
                            ahead += 8
                        else:
                            ahead += 4
                    if found_call:
                        new_val = mapping[value]
                        new_word = (word & 0xFFFF0000) | (new_val & 0xFFFF)
                        struct.pack_into("<I", raw, bc_off + pos, new_word)
                        changed += 1
                pos += 4
                continue
            if op in (0x45, 0x41):  # Pop 2 words
                pos += 8
                continue
            if op in (OP_CALL_V15, OP_CALL_V14):
                pos += 8
                continue
            pos += 4

    if changed == 0:
        return False, "No room_goto-style PushI sites found to rewrite.", mapping

    if backup:
        bak = path.with_suffix(path.suffix + ".roomchaosbak")
        if not bak.exists():
            bak.write_bytes(path.read_bytes())
    path.write_bytes(raw)

    meta = path.with_suffix(path.suffix + ".roomchaos.json")
    meta.write_text(json.dumps({str(k): v for k, v in mapping.items()}, indent=2), encoding="utf-8")
    return (
        True,
        f"Randomized {changed} room transitions among {len(playable)} playable rooms "
        f"(excluded text/cutscene rooms). Restart Undertale to load. Backup: data.win.roomchaosbak",
        mapping,
    )


def _force_rare_chance_pushes(data_win: str | Path, *, backup: bool = True) -> tuple[int, str]:
    """
    Bump small chance immediates that sit near rare battlegroup PushIs to 100,
    so rare fights win RNG checks more reliably.
    """
    from .battles import RARE_BATTLEGROUPS

    rare_ids = {b.id for b in RARE_BATTLEGROUPS}
    path = Path(data_win)
    if not path.is_file():
        return 0, "no data.win"
    raw = bytearray(path.read_bytes())
    reader = BinaryReader(bytes(raw))

    changed = 0
    for _name, bc_off, length in _find_code_entries(reader):
        # Collect PushI sites in this script
        sites: list[tuple[int, int]] = []  # (pos, value)
        pos = 0
        while pos + 4 <= length:
            word = struct.unpack_from("<I", raw, bc_off + pos)[0]
            op = _opcode(word)
            if op == OP_PUSHI:
                sites.append((pos, word & 0xFFFF))
                pos += 4
                continue
            if op in (0x45, 0x41, OP_CALL_V15, OP_CALL_V14):
                pos += 8
                continue
            pos += 4
        for i, (pos_i, val_i) in enumerate(sites):
            if val_i not in rare_ids:
                continue
            # Look backward for a small chance PushI (1..50) within ~12 instructions
            for j in range(i - 1, max(-1, i - 12), -1):
                pos_j, val_j = sites[j]
                if 1 <= val_j <= 50:
                    new_word = (struct.unpack_from("<I", raw, bc_off + pos_j)[0] & 0xFFFF0000) | 100
                    struct.pack_into("<I", raw, bc_off + pos_j, new_word)
                    changed += 1
                    break
    if changed:
        if backup:
            bak = path.with_suffix(path.suffix + ".rarebak")
            if not bak.exists():
                bak.write_bytes(path.read_bytes())
        path.write_bytes(raw)
    return changed, f"bumped {changed} rare-chance PushI(s)"


def set_rare_encounters(
    enabled: bool,
    *,
    save_folder: str | Path | None = None,
    data_win: str | Path | None = None,
    live_reload: bool = True,
) -> tuple[bool, str]:
    """
    Toggle 'guarantee rare encounters' helpers:
    - Sets FUN high enough for rare overworld events
    - Stores a sidecar flag the toolkit uses to prefer rare fights
    - When enabling with data.win, bumps rare encounter chance immediates toward 100
    - When enabled live, reloads save with L
    """
    info = read_save_info(save_folder)
    lines = info.file0.read_text(encoding="utf-8", errors="replace").splitlines()
    while len(lines) <= max(LINE_FUN, ROOM_LINE_INDEX):
        lines.append("0")

    flag_path = info.folder / "extractor_rare_mode.json"
    extras: list[str] = []
    if enabled:
        # FUN values that unlock rare phone / fun events (community lists use 56–90+)
        lines[LINE_FUN] = "90"
        flag_path.write_text(json.dumps({"rare": True, "fun": 90}), encoding="utf-8")
        note = "Rare mode ON (FUN=90). Rare fights preferred; fun events boosted."
        if data_win and Path(data_win).is_file():
            try:
                if not undertale_is_running():
                    _n, detail = _force_rare_chance_pushes(data_win, backup=True)
                    extras.append(detail)
                    # Default Home to first rare so debug Home is rare-ready
                    from .battles import RARE_BATTLEGROUPS, set_home_battlegroup

                    if RARE_BATTLEGROUPS:
                        ok_bg, msg_bg = set_home_battlegroup(
                            data_win, RARE_BATTLEGROUPS[0].id, backup=True
                        )
                        extras.append(msg_bg if ok_bg else f"home bg: {msg_bg}")
                else:
                    extras.append(
                        "close game + toggle again to patch rare chances in data.win"
                    )
            except Exception as exc:
                extras.append(f"rare patch skipped: {exc}")
    else:
        lines[LINE_FUN] = "0"
        if flag_path.exists():
            flag_path.unlink()
        note = "Rare mode OFF (FUN=0)."
        # Restore rarebak if present
        if data_win:
            path = Path(data_win)
            bak = path.with_suffix(path.suffix + ".rarebak")
            if bak.is_file() and not undertale_is_running():
                try:
                    path.write_bytes(bak.read_bytes())
                    extras.append("restored data.win.rarebak")
                except Exception as exc:
                    extras.append(f"restore failed: {exc}")

    payload = "\n".join(lines)
    if info.file0.read_bytes().endswith(b"\n"):
        payload += "\n"
    info.file0.write_text(payload, encoding="utf-8")
    file9 = info.folder / "file9"
    if file9.is_file():
        file9.write_text(payload if payload.endswith("\n") else payload + "\n", encoding="utf-8")

    if extras:
        note = note + " " + "; ".join(extras)

    if live_reload and undertale_is_running() and data_win:
        room = None
        try:
            room = int(float(lines[ROOM_LINE_INDEX]))
        except ValueError:
            room = 6
        result, _ = live_teleport_to_room(room, save_folder=save_folder, data_win=data_win)
        if result.ok:
            return True, note + " Live reloaded."
        return True, note + f" (reload: {result.detail})"
    return True, note


def rare_mode_enabled(save_folder: str | Path | None = None) -> bool:
    try:
        info = read_save_info(save_folder)
    except Exception:
        return False
    flag_path = info.folder / "extractor_rare_mode.json"
    if not flag_path.is_file():
        return False
    try:
        return bool(json.loads(flag_path.read_text(encoding="utf-8")).get("rare"))
    except Exception:
        return False
