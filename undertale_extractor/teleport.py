"""Teleport into Undertale rooms by editing the local save file."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path


# file0 uses 1-based line numbers in community docs; room is line 548.
ROOM_LINE_INDEX = 547  # 0-based


@dataclass
class SaveInfo:
    folder: Path
    file0: Path
    ini_path: Path | None
    current_room: int | None = None
    player_name: str | None = None


def find_undertale_save_dirs() -> list[Path]:
    """Locate Undertale save folders on this machine (Windows-first)."""
    candidates: list[Path] = []
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    home = Path.home()

    guesses = []
    if local:
        guesses.append(Path(local) / "UNDERTALE")
    guesses.extend(
        [
            home / "AppData" / "Local" / "UNDERTALE",
            home / ".config" / "UNDERTALE",
            home / "Library" / "Application Support" / "com.tobyfox.undertale",
        ]
    )
    seen: set[Path] = set()
    for path in guesses:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if path.is_dir() and (path / "file0").is_file():
            candidates.append(path)
    return candidates


def default_save_dir() -> Path | None:
    dirs = find_undertale_save_dirs()
    return dirs[0] if dirs else None


def read_save_info(folder: str | Path | None = None) -> SaveInfo:
    folder_path = Path(folder) if folder else default_save_dir()
    if folder_path is None:
        raise FileNotFoundError(
            "Could not find Undertale saves. Expected something like:\n"
            r"%LOCALAPPDATA%\UNDERTALE\file0"
        )
    file0 = folder_path / "file0"
    if not file0.is_file():
        raise FileNotFoundError(f"No file0 save in {folder_path}")

    lines = file0.read_text(encoding="utf-8", errors="replace").splitlines()
    current = None
    if len(lines) > ROOM_LINE_INDEX:
        try:
            current = int(float(lines[ROOM_LINE_INDEX].strip()))
        except ValueError:
            current = None
    name = lines[0].strip() if lines else None
    ini = folder_path / "undertale.ini"
    return SaveInfo(
        folder=folder_path,
        file0=file0,
        ini_path=ini if ini.is_file() else None,
        current_room=current,
        player_name=name,
    )


def _format_room_value(existing: str, room_id: int) -> str:
    """Keep float style if the save already used floats (e.g. 220.000000)."""
    existing = existing.strip()
    if "." in existing:
        return f"{float(room_id):.6f}"
    return str(int(room_id))


def _update_ini_room(ini_path: Path, room_id: int) -> None:
    text = ini_path.read_text(encoding="utf-8", errors="replace")
    # Undertale ini often looks like: Room="220"  or Room=220
    pattern = re.compile(r'(?im)^(\s*Room\s*=\s*)("?)(\d+(?:\.\d+)?)("?)(\s*)$')
    if pattern.search(text):
        def repl(match: re.Match[str]) -> str:
            quote = match.group(2) or match.group(4) or ""
            # Prefer keeping quotes if either side had them
            left_q = match.group(2)
            right_q = match.group(4)
            if left_q or right_q:
                return f'{match.group(1)}"{int(room_id)}"'
            return f"{match.group(1)}{int(room_id)}"

        new_text = pattern.sub(repl, text, count=1)
    else:
        # Insert under [General] if possible
        general = re.search(r"(?im)^\[General\]\s*$", text)
        if general:
            insert_at = general.end()
            new_text = text[:insert_at] + f"\nRoom=\"{int(room_id)}\"" + text[insert_at:]
        else:
            new_text = text.rstrip() + f"\n[General]\nRoom=\"{int(room_id)}\"\n"
    ini_path.write_text(new_text, encoding="utf-8")


def teleport_to_room(
    room_id: int,
    save_folder: str | Path | None = None,
    *,
    also_file9: bool = True,
    backup: bool = True,
) -> SaveInfo:
    """
    Set the current room in Undertale's save so Continue loads that room.

    Close Undertale before calling this. Then open the game and press Continue.
    """
    if room_id < 0:
        raise ValueError("Room id must be >= 0")

    info = read_save_info(save_folder)
    lines = info.file0.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= ROOM_LINE_INDEX:
        raise ValueError(
            f"Save file looks incomplete ({len(lines)} lines). "
            "Load Undertale once and save in-game, then try again."
        )

    if backup:
        bak = info.file0.with_suffix(info.file0.suffix + ".bak")
        shutil.copy2(info.file0, bak)

    lines[ROOM_LINE_INDEX] = _format_room_value(lines[ROOM_LINE_INDEX], room_id)
    # Preserve final newline style
    payload = "\n".join(lines)
    if info.file0.read_bytes().endswith(b"\n"):
        payload += "\n"
    info.file0.write_text(payload, encoding="utf-8")

    if also_file9:
        file9 = info.folder / "file9"
        if file9.is_file():
            if backup:
                shutil.copy2(file9, file9.with_suffix(file9.suffix + ".bak"))
            lines9 = file9.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines9) > ROOM_LINE_INDEX:
                lines9[ROOM_LINE_INDEX] = _format_room_value(lines9[ROOM_LINE_INDEX], room_id)
                file9.write_text("\n".join(lines9) + "\n", encoding="utf-8")

    if info.ini_path and info.ini_path.is_file():
        if backup:
            shutil.copy2(info.ini_path, info.ini_path.with_suffix(info.ini_path.suffix + ".bak"))
        _update_ini_room(info.ini_path, room_id)

    return read_save_info(info.folder)


def friendly_room_label(name: str, room_id: int) -> str:
    pretty = name
    if pretty.lower().startswith("room_"):
        pretty = pretty[5:]
    pretty = pretty.replace("_", " ")
    return f"{room_id:03d}  {pretty}"
