"""Edit Undertale file0 saves: stats, inventory, equipment."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .teleport import ROOM_LINE_INDEX, default_save_dir, read_save_info

# 0-based line indices in file0 (community / Flowey's Time Machine layout).
LINE_NAME = 0
LINE_LOVE = 1
LINE_HP = 2
LINE_MAXHP = 3
LINE_AT = 4
LINE_WEAPON_AT = 5
LINE_DF = 6
LINE_ARMOR_DF = 7
LINE_EXP = 9
LINE_GOLD = 10
LINE_KILLS = 11
# Inventory slots at 12,14,16,18,20,22,24,26 (0-based)
INV_SLOTS = (12, 14, 16, 18, 20, 22, 24, 26)
LINE_WEAPON = 28
LINE_ARMOR = 29

# Flowey's Time Machine item list (index == item id).
ITEMS: tuple[str, ...] = (
    "Empty",
    "Monster Candy",
    "Croquet Roll",
    "Stick",
    "Bandage",
    "Rock Candy",
    "Pumpkin Rings",
    "Spider Donut",
    "Stoic Onion",
    "Ghost Fruit",
    "Spider Cider",
    "Butterscotch Pie",
    "Faded Ribbon",
    "Toy Knife",
    "Tough Glove",
    "Manly Bandana",
    "Snowman Piece",
    "Nice Cream",
    "Puppydough Icecream",
    "Bisicle",
    "Unisicle",
    "Cinnamon Bun",
    "Temmie Flakes",
    "Abandoned Quiche",
    "Old Tutu",
    "Ballet Shoes",
    "Punch Card",
    "Annoying Dog",
    "Dog Salad",
    "Dog Residue (1)",
    "Dog Residue (2)",
    "Dog Residue (3)",
    "Dog Residue (4)",
    "Dog Residue (5)",
    "Dog Residue (6)",
    "Astronaut Food",
    "Instant Noodles",
    "Crab Apple",
    "Hot Dog...?",
    "Hot Cat",
    "Glamburger",
    "Sea Tea",
    "Starfait",
    "Legendary Hero",
    "Cloudy Glasses",
    "Torn Notebook",
    "Stained Apron",
    "Burnt Pan",
    "Cowboy Hat",
    "Empty Gun",
    "Heart Locket",
    "Worn Dagger",
    "Real Knife",
    "The Locket",
    "Bad Memory",
    "Dream",
    "Undyne's Letter",
    "Undyne Letter EX",
    "Potato Chisps",
    "Junk Food",
    "Mystery Key",
    "Face Steak",
    "Hush Puppy",
    "Snail Pie",
    "temy armor",
)

WEAPONS: dict[int, str] = {
    3: "Stick",
    13: "Toy Knife",
    14: "Tough Glove",
    25: "Ballet Shoes",
    45: "Torn Notebook",
    47: "Burnt Pan",
    49: "Empty Gun",
    51: "Worn Dagger",
    52: "Real Knife",
}

ARMORS: dict[int, str] = {
    4: "Bandage",
    12: "Faded Ribbon",
    15: "Manly Bandana",
    24: "Old Tutu",
    44: "Cloudy Glasses",
    46: "Stained Apron",
    48: "Cowboy Hat",
    50: "Heart Locket",
    53: "The Locket",
    64: "temy armor",
}


def item_name(item_id: int) -> str:
    if 0 <= item_id < len(ITEMS):
        return ITEMS[item_id]
    return f"Item {item_id}"


@dataclass
class PlayerStats:
    name: str = "CHARA"
    love: int = 1
    hp: int = 20
    max_hp: int = 20
    at: int = 10
    weapon_at: int = 0
    df: int = 10
    armor_df: int = 0
    exp: int = 0
    gold: int = 0
    kills: int = 0
    inventory: list[int] | None = None
    weapon: int = 3
    armor: int = 4
    room: int | None = None

    def __post_init__(self) -> None:
        if self.inventory is None:
            self.inventory = [0] * 8


def _fmt(existing: str, value: int | float | str) -> str:
    if isinstance(value, str):
        return value
    existing = existing.strip()
    if "." in existing:
        return f"{float(value):.6f}"
    return str(int(value))


def _read_int(lines: list[str], idx: int, default: int = 0) -> int:
    if idx >= len(lines):
        return default
    try:
        return int(float(lines[idx].strip()))
    except ValueError:
        return default


def read_player_stats(save_folder: str | Path | None = None) -> PlayerStats:
    info = read_save_info(save_folder)
    lines = info.file0.read_text(encoding="utf-8", errors="replace").splitlines()
    inv = [_read_int(lines, i) for i in INV_SLOTS]
    return PlayerStats(
        name=lines[LINE_NAME].strip() if lines else "CHARA",
        love=_read_int(lines, LINE_LOVE, 1),
        hp=_read_int(lines, LINE_HP, 20),
        max_hp=_read_int(lines, LINE_MAXHP, 20),
        at=_read_int(lines, LINE_AT, 10),
        weapon_at=_read_int(lines, LINE_WEAPON_AT, 0),
        df=_read_int(lines, LINE_DF, 10),
        armor_df=_read_int(lines, LINE_ARMOR_DF, 0),
        exp=_read_int(lines, LINE_EXP, 0),
        gold=_read_int(lines, LINE_GOLD, 0),
        kills=_read_int(lines, LINE_KILLS, 0),
        inventory=inv,
        weapon=_read_int(lines, LINE_WEAPON, 3),
        armor=_read_int(lines, LINE_ARMOR, 4),
        room=info.current_room,
    )


def write_player_stats(
    stats: PlayerStats,
    save_folder: str | Path | None = None,
    *,
    backup: bool = True,
    also_file9: bool = True,
) -> Path:
    """Write stats/inventory into file0 (and file9). Returns file0 path."""
    info = read_save_info(save_folder)
    lines = info.file0.read_text(encoding="utf-8", errors="replace").splitlines()
    # Ensure enough lines for room index
    while len(lines) <= max(INV_SLOTS[-1], LINE_ARMOR, ROOM_LINE_INDEX):
        lines.append("0")

    if backup:
        shutil.copy2(info.file0, info.file0.with_suffix(info.file0.suffix + ".bak"))

    def set_line(idx: int, value: int | str) -> None:
        lines[idx] = _fmt(lines[idx], value)

    set_line(LINE_NAME, stats.name)
    set_line(LINE_LOVE, stats.love)
    set_line(LINE_HP, stats.hp)
    set_line(LINE_MAXHP, stats.max_hp)
    set_line(LINE_AT, stats.at)
    set_line(LINE_WEAPON_AT, stats.weapon_at)
    set_line(LINE_DF, stats.df)
    set_line(LINE_ARMOR_DF, stats.armor_df)
    set_line(LINE_EXP, stats.exp)
    set_line(LINE_GOLD, stats.gold)
    set_line(LINE_KILLS, stats.kills)
    inv = list(stats.inventory or [0] * 8)
    while len(inv) < 8:
        inv.append(0)
    for slot, idx in enumerate(INV_SLOTS):
        set_line(idx, int(inv[slot]))
    set_line(LINE_WEAPON, stats.weapon)
    set_line(LINE_ARMOR, stats.armor)

    payload = "\n".join(lines)
    if info.file0.read_bytes().endswith(b"\n"):
        payload += "\n"
    info.file0.write_text(payload, encoding="utf-8")

    if also_file9:
        file9 = info.folder / "file9"
        if file9.is_file():
            if backup:
                shutil.copy2(file9, file9.with_suffix(file9.suffix + ".bak"))
            file9.write_text(payload if payload.endswith("\n") else payload + "\n", encoding="utf-8")
    return info.file0


def default_save_folder() -> Path | None:
    return default_save_dir()
