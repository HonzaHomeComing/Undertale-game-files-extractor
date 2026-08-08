"""Launch Undertale from its install folder (Windows-first)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

EXE_NAMES = ("UNDERTALE.exe", "Undertale.exe", "undertale.exe")


def find_undertale_exe(game_dir: str | Path | None = None, data_win: str | Path | None = None) -> Path | None:
    """Locate UNDERTALE.exe next to data.win or in game_dir."""
    candidates: list[Path] = []
    if data_win:
        p = Path(data_win)
        candidates.append(p.parent if p.is_file() else p)
    if game_dir:
        candidates.append(Path(game_dir))
    seen: set[Path] = set()
    for folder in candidates:
        try:
            key = folder.resolve()
        except OSError:
            key = folder
        if key in seen:
            continue
        seen.add(key)
        for name in EXE_NAMES:
            exe = folder / name
            if exe.is_file():
                return exe
    return None


def launch_undertale(
    *,
    data_win: str | Path | None = None,
    game_dir: str | Path | None = None,
) -> tuple[bool, str]:
    """
    Force-start Undertale using the patched data.win in the install folder.
    Returns (ok, message).
    """
    exe = find_undertale_exe(game_dir=game_dir, data_win=data_win)
    if exe is None:
        return (
            False,
            "Could not find UNDERTALE.exe.\n"
            "Open your Undertale folder in this app first "
            "(the folder that contains data.win and UNDERTALE.exe).",
        )
    cwd = exe.parent
    try:
        if sys.platform.startswith("win"):
            # Detach so closing the extractor does not kill the game.
            flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            subprocess.Popen(
                [str(exe)],
                cwd=str(cwd),
                close_fds=True,
                creationflags=flags,
                env=os.environ.copy(),
            )
        else:
            subprocess.Popen([str(exe)], cwd=str(cwd), start_new_session=True)
    except OSError as exc:
        return False, f"Failed to start Undertale:\n{exc}"
    return True, f"Started {exe.name} from:\n{cwd}"
