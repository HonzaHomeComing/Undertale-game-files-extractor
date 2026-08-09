"""
Undertale File Extractor
========================
Browse files, live-teleport rooms, launch a patched game, and edit saves.

Buttons:
  Enable live patches — debug Load (L) + safe dogcheck disable
  Launch Undertale — force-start UNDERTALE.exe with current data.win
  Debug Toolkit — stats, inventory, fights, Ruins reset, room chaos, rare mode
  Restore data.win — undo patches if the game will not start

Windows: pip install Pillow customtkinter
         python UndertaleExtractor.py
"""

from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

try:
    import customtkinter as ctk
    from PIL import Image, ImageDraw
except ImportError:
    print("Missing packages. Run: pip install Pillow customtkinter")
    input("Press Enter to exit...")
    raise SystemExit(1)

__version__ = "1.7.0"


# --- assets.py ---

class AssetKind(str, Enum):
    SPRITE = "Sprites"
    TEXTURE = "Textures"
    BACKGROUND = "Backgrounds"
    AUDIO = "Audio"
    MUSIC = "Music"
    FONT = "Fonts"
    ROOM = "Rooms"
    OTHER = "Other"


SAFE_NAME = re.compile(r"[^\w.\-]+", re.UNICODE)


def safe_filename(name: str, fallback: str = "file") -> str:
    cleaned = SAFE_NAME.sub("_", name.strip()).strip("._")
    return cleaned or fallback


@dataclass
class GameAsset:
    """A single browsable / downloadable game file."""

    id: str
    name: str
    kind: AssetKind
    extension: str
    size: int
    # Lazy payload providers keep memory reasonable for large archives.
    _data_fn: Callable[[], bytes] | None = field(default=None, repr=False)
    _image_fn: Callable[[], Image.Image] | None = field(default=None, repr=False)
    _cached_data: bytes | None = field(default=None, repr=False)
    _cached_image: Image.Image | None = field(default=None, repr=False)
    source_path: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        if self.name.lower().endswith(self.extension.lower()):
            return self.name
        return f"{self.name}{self.extension}"

    @property
    def is_image(self) -> bool:
        if self._image_fn is not None:
            return True
        return self.extension.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

    @property
    def is_room(self) -> bool:
        return self.kind == AssetKind.ROOM or bool(self.meta.get("teleport"))

    @property
    def is_audio(self) -> bool:
        return self.extension.lower() in {".wav", ".ogg", ".mp3"}

    def get_data(self) -> bytes:
        if self._cached_data is not None:
            return self._cached_data
        if self._data_fn is not None:
            self._cached_data = self._data_fn()
            return self._cached_data
        if self._image_fn is not None:
            img = self.get_image()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            self._cached_data = buf.getvalue()
            return self._cached_data
        raise RuntimeError(f"No data available for {self.id}")

    def get_image(self) -> Image.Image | None:
        if not self.is_image and self._image_fn is None:
            return None
        if self._cached_image is not None:
            return self._cached_image
        if self._image_fn is not None:
            self._cached_image = self._image_fn()
            return self._cached_image
        data = self.get_data()
        self._cached_image = Image.open(io.BytesIO(data)).convert("RGBA")
        return self._cached_image

    def export_to(self, directory: str | Path, overwrite: bool = True) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / safe_filename(self.display_name)
        if target.exists() and not overwrite:
            stem, ext = target.stem, target.suffix
            n = 1
            while True:
                candidate = directory / f"{stem}_{n}{ext}"
                if not candidate.exists():
                    target = candidate
                    break
                n += 1
        target.write_bytes(self.get_data())
        return target

    def thumbnail(self, max_size: int = 96) -> Image.Image | None:
        img = self.get_image()
        if img is None:
            return None
        thumb = img.copy()
        thumb.thumbnail((max_size, max_size), Image.Resampling.NEAREST)
        return thumb


# --- binary.py ---

def read_game_file_bytes(path: str | Path) -> bytes:
    """
    Read a game file without holding a long-lived lock on the install copy.

    Copies to a temp file first so Steam / Undertale can open data.win while
    this app is browsing (Windows exclusive opens used to block launch).
    """
    path = Path(path)
    tmp_dir = Path(tempfile.gettempdir()) / "undertale_extractor_read"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"{os.getpid()}_{path.name}"
    try:
        shutil.copy2(path, tmp_path)
        return tmp_path.read_bytes()
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


class BinaryReader:
    """Random-access little-endian reader over a bytes buffer."""

    def __init__(self, data: bytes | bytearray | memoryview):
        self._data = memoryview(data)
        self._pos = 0

    @classmethod
    def from_path(cls, path: str | Path) -> "BinaryReader":
        return cls(read_game_file_bytes(path))

    @property
    def position(self) -> int:
        return self._pos

    @property
    def size(self) -> int:
        return len(self._data)

    def seek(self, offset: int) -> None:
        if offset < 0 or offset > len(self._data):
            raise ValueError(f"Seek out of range: {offset}")
        self._pos = offset

    def remaining(self) -> int:
        return len(self._data) - self._pos

    def read(self, n: int) -> bytes:
        if self._pos + n > len(self._data):
            raise EOFError(f"Need {n} bytes at {self._pos}, only {self.remaining()} left")
        out = self._data[self._pos : self._pos + n].tobytes()
        self._pos += n
        return out

    def read_u8(self) -> int:
        return self.read(1)[0]

    def read_u16(self) -> int:
        self._ensure(2)
        value = struct.unpack_from("<H", self._data, self._pos)[0]
        self._pos += 2
        return value

    def read_i16(self) -> int:
        self._ensure(2)
        value = struct.unpack_from("<h", self._data, self._pos)[0]
        self._pos += 2
        return value

    def read_u32(self) -> int:
        self._ensure(4)
        value = struct.unpack_from("<I", self._data, self._pos)[0]
        self._pos += 4
        return value

    def read_i32(self) -> int:
        self._ensure(4)
        value = struct.unpack_from("<i", self._data, self._pos)[0]
        self._pos += 4
        return value

    def read_f32(self) -> float:
        self._ensure(4)
        value = struct.unpack_from("<f", self._data, self._pos)[0]
        self._pos += 4
        return value

    def read_tag(self) -> str:
        return self.read(4).decode("ascii", errors="replace")

    def peek_tag(self) -> str:
        if self.remaining() < 4:
            return "EOF"
        return bytes(self._data[self._pos : self._pos + 4]).decode("ascii", errors="replace")

    def skip(self, n: int) -> None:
        self.seek(self._pos + n)

    def read_cstring_at(self, offset: int) -> str:
        """Read a GameMaker string. Absolute pointers point at the character data."""
        if offset < 4:
            return ""
        length = struct.unpack_from("<I", self._data, offset - 4)[0]
        end = offset + length
        if length > 1_000_000 or end > len(self._data):
            end = offset
            while end < len(self._data) and self._data[end] != 0:
                end += 1
            return bytes(self._data[offset:end]).decode("utf-8", errors="replace")
        return bytes(self._data[offset:end]).decode("utf-8", errors="replace")

    def read_offset_string(self) -> str:
        ptr = self.read_u32()
        if ptr == 0:
            return ""
        return self.read_cstring_at(ptr)

    def slice(self, offset: int, length: int) -> bytes:
        return self._data[offset : offset + length].tobytes()

    def find(self, needle: bytes, start: int | None = None, end: int | None = None) -> int:
        begin = start or 0
        stop = end if end is not None else len(self._data)
        idx = bytes(self._data[begin:stop]).find(needle)
        if idx < 0:
            return -1
        return begin + idx

    def _ensure(self, n: int) -> None:
        if self._pos + n > len(self._data):
            raise EOFError(f"Need {n} bytes at {self._pos}")


def write_u32(buf: bytearray, value: int) -> None:
    buf.extend(struct.pack("<I", value & 0xFFFFFFFF))


def write_u16(buf: bytearray, value: int) -> None:
    buf.extend(struct.pack("<H", value & 0xFFFF))


def write_i32(buf: bytearray, value: int) -> None:
    buf.extend(struct.pack("<i", value))


# --- teleport.py ---

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


# --- dogcheck.py ---

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

# Prefer chaos/rare backups first — those patches can brick boot if they
# rewrote unrelated bytecode; dogcheck/debug backups are older fallbacks.
BACKUP_SUFFIXES = (
    ".roomchaosbak",
    ".rarebak",
    ".dogcheckbak",
    ".debugbak",
    ".battlebak",
    ".bak",
)

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


# --- live_teleport.py ---

# Known data.win offsets where debug flag is a single byte (0 → 1).
DEBUG_OFFSETS = (
    0x725B24,  # 1.00
    0x725D8C,  # 1.001
    0x725DDC,  # variants
    0x7748C4,  # 1.08-ish
    0x7748F0,  # Steam (UndertaleModTool maintainers)
)

VK_L = 0x4C
VK_S = 0x53
VK_ESCAPE = 0x1B
VK_INSERT = 0x2D
VK_X = 0x58
VK_C = 0x43
VK_Z = 0x5A
KEYEVENTF_KEYUP = 0x0002
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102


@dataclass
class LiveTeleportResult:
    ok: bool
    method: str
    detail: str
    addresses_written: int = 0
    debug_enabled: bool = False


def is_windows() -> bool:
    return sys.platform.startswith("win")


# Window titles that mention Undertale but are NOT the game (this app, tools, …).
_NOT_GAME_TITLE_SNIPPETS = (
    "extractor",
    "wiper",
    "mod tool",
    "modtool",
    "undertale file",
    "data wiper",
    "png_to_blender",
)


def _title_looks_like_game(title: str) -> bool:
    """True only for the real game window, not this extractor."""
    t = title.strip()
    if not t:
        return False
    low = t.lower()
    if any(s in low for s in _NOT_GAME_TITLE_SNIPPETS):
        return False
    # Steam / GameMaker default title is exactly "UNDERTALE"
    return low == "undertale"


def find_undertale_hwnd() -> int:
    """Return HWND for the Undertale *game* window, or 0."""
    if not is_windows():
        return 0
    user32 = ctypes.windll.user32

    # Prefer a window owned by UNDERTALE.exe (authoritative).
    pid = _pid_by_name(("UNDERTALE.exe", "undertale.exe", "Undertale.exe"))
    if pid:
        hwnd = _hwnd_for_pid(pid)
        if hwnd:
            return hwnd

    # Exact title match only — never substring "undertale" (matches this app's title).
    for title in ("UNDERTALE", "Undertale", "undertale"):
        hwnd = int(user32.FindWindowW(None, title) or 0)
        if not hwnd:
            continue
        if _hwnd_pid(hwnd) == os.getpid():
            continue
        return hwnd
    return 0


def _hwnd_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _hwnd_for_pid(pid: int) -> int:
    """Find a visible top-level window belonging to pid."""
    if not is_windows() or not pid:
        return 0
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        if _hwnd_pid(int(hwnd)) != pid:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        found.append(int(hwnd))
        return False  # stop

    user32.EnumWindows(enum_proc, 0)
    return found[0] if found else 0


def find_undertale_pid() -> int | None:
    if not is_windows():
        return None
    # Process name first — do not trust window titles (extractor title contains "Undertale").
    return _pid_by_name(("UNDERTALE.exe", "undertale.exe", "Undertale.exe"))


def _pid_by_name(names: tuple[str, ...]) -> int | None:
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID = ctypes.c_void_p(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID:
        return None
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not kernel32.Process32First(snap, ctypes.byref(entry)):
            return None
        want = {n.lower() for n in names}
        while True:
            name = entry.szExeFile.decode("utf-8", errors="ignore").lower()
            if name in want:
                return int(entry.th32ProcessID)
            if not kernel32.Process32Next(snap, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return None


def undertale_is_running() -> bool:
    return find_undertale_pid() is not None


def enable_debug_mode(data_win: str | Path, *, backup: bool = True) -> bool:
    """
    Flip Undertale's debug flag in data.win so S/L save-load warps work.
    Returns True if debug is (now) enabled at a known offset.
    """
    path = Path(data_win)
    data = bytearray(path.read_bytes())
    changed = False
    already = False
    for offset in DEBUG_OFFSETS:
        if offset < len(data):
            if data[offset] == 1:
                already = True
            elif data[offset] == 0:
                data[offset] = 1
                changed = True
    if not changed and not already:
        return False
    if changed:
        if backup:
            bak = path.with_suffix(path.suffix + ".debugbak")
            if not bak.exists():
                bak.write_bytes(path.read_bytes())
        path.write_bytes(data)
    return True


def enable_debug_mode_live(data_win: str | Path) -> tuple[bool, str]:
    """
    Enable debug on disk and in the running process FORM image.
    Home-key fights need the in-memory flag, not only the file on disk.
    """
    path = Path(data_win)
    if not path.is_file():
        return False, "data.win missing"
    disk_ok = enable_debug_mode(path, backup=True)
    if not is_windows() or not undertale_is_running():
        return disk_ok, "debug on disk" if disk_ok else "debug offsets not found"

    pid = find_undertale_pid()
    if not pid:
        return disk_ok, "debug on disk (process not found)"
    size = path.stat().st_size
    data = path.read_bytes()
    wrote = 0
    handle = None
    try:
        handle = _open_process(pid)
        form = find_form_base(handle, expected_size=size)
        if form is None:
            return disk_ok, "debug on disk (live FORM not found — relaunch after Enable live patches)"
        for offset in DEBUG_OFFSETS:
            if offset >= len(data):
                continue
            try:
                _write(handle, form + offset, b"\x01")
                wrote += 1
            except RuntimeError:
                continue
    except RuntimeError as exc:
        return disk_ok, f"debug on disk; live failed: {exc}"
    finally:
        if handle and kernel32:
            kernel32.CloseHandle(handle)
    if wrote:
        return True, f"debug live ({wrote} flag byte(s))"
    return disk_ok, "debug on disk (live write missed)"


def debug_flag_enabled(data_win: str | Path) -> bool:
    path = Path(data_win)
    data = path.read_bytes()
    return any(offset < len(data) and data[offset] == 1 for offset in DEBUG_OFFSETS)


def _send_key_to_undertale(vk_code: int, *, presses: int = 1) -> bool:
    """Focus Undertale and send a virtual-key via keybd_event + PostMessage."""
    hwnd = find_undertale_hwnd()
    user32 = ctypes.windll.user32
    if not hwnd:
        return False
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.05)
    for _ in range(presses):
        # PostMessage reaches the game even when some overlays steal focus mid-frame.
        user32.PostMessageW(hwnd, WM_KEYDOWN, vk_code, 0)
        user32.keybd_event(vk_code, 0, 0, 0)
        time.sleep(0.035)
        user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)
        user32.PostMessageW(hwnd, WM_KEYUP, vk_code, 0)
        time.sleep(0.05)
    return True


def _clear_ini_battle_traps(save_folder: str | Path | None) -> None:
    """Clear undertale.ini flags that trap you in Flowey/special battles."""

    try:
        info = read_save_info(save_folder)
    except Exception:
        return
    if not info.ini_path or not info.ini_path.is_file():
        return
    text = info.ini_path.read_text(encoding="utf-8", errors="replace")
    original = text
    # [FFFFF] F="1" means trapped in Flowey battle — force clear.
    text = re.sub(r'(?im)^(\s*F\s*=\s*"?)1("?\s*)$', r"\g<1>0\2", text)
    if text != original:
        info.ini_path.write_text(text, encoding="utf-8")


def _skip_cutscene_keys() -> None:
    """Mash skip/cancel so dialogue and menus release input."""
    for vk in (VK_ESCAPE, VK_X, VK_C, VK_Z):
        _send_key_to_undertale(vk, presses=2)


def live_teleport_to_room(
    room_id: int,
    *,
    save_folder: str | Path | None = None,
    data_win: str | Path | None = None,
    current_room: int | None = None,  # kept for API compatibility; unused
    cached_addresses: list | None = None,  # kept for API compatibility; unused
    max_room_id: int = 400,
    force: bool = True,
) -> tuple[LiveTeleportResult, list]:
    """
    Teleport to an exact room while Undertale is running.

    Method (reliable with debug mode):
      1. Write the target room into file0 / undertale.ini
      2. Clear ini battle-trap flags
      3. Skip dialogue (Esc/X), leave battle room via Insert if needed
      4. Focus Undertale and press L (debug Load) several times

    During battles, L normally loads a battle save-state — force mode first
    tries Insert (debug next-room) to leave room_battle, then L loads file0.
    """
    _ = (current_room, cached_addresses, max_room_id)

    if not is_windows():
        return (
            LiveTeleportResult(False, "unsupported", "Live teleport requires Windows."),
            [],
        )

    if not undertale_is_running():
        return (
            LiveTeleportResult(
                False,
                "not_running",
                "Undertale is not running. Start the game, load a save, then click a room.",
            ),
            [],
        )

    debug_on = False
    if data_win and Path(data_win).is_file():
        if dogcheck_exit_stubbed(data_win):
            return (
                LiveTeleportResult(
                    False,
                    "broken_dogcheck",
                    "Your data.win has a broken dogcheck patch that crashes when pressing L.\n\n"
                    "1. Close Undertale\n"
                    "2. Click Restore data.win\n"
                    "3. Click Enable live patches\n"
                    "4. Start Undertale and try again",
                ),
                [],
            )
        debug_on = debug_flag_enabled(data_win)
        if not debug_on:
            return (
                LiveTeleportResult(
                    False,
                    "patches_required",
                    "Live teleport needs debug Load (L) enabled once.\n\n"
                    "1. Close Undertale completely\n"
                    "2. Click Enable live patches in this app\n"
                    "3. Start Undertale, load your save\n"
                    "4. Click the room again\n\n"
                    "If you see the Annoying Dog, run Enable live patches again "
                    "(it now uses a safer dogcheck disable).\n"
                    "If you see a Code Error about dogcheck, click Restore data.win first.",
                ),
                [],
            )

    try:
        teleport_to_room(room_id, save_folder, backup=True)
        if force:
            _clear_ini_battle_traps(save_folder)
    except Exception as exc:
        return (
            LiveTeleportResult(False, "save_failed", f"Could not update save: {exc}"),
            [],
        )

    # Give the OS a moment to finish writing the save before the game reads it.
    time.sleep(0.15)

    if force:
        _skip_cutscene_keys()
        time.sleep(0.08)
        # Insert = debug "next room" — escapes room_battle so L is not battle-load.
        _send_key_to_undertale(VK_INSERT, presses=1)
        time.sleep(0.12)

    if not _send_key_to_undertale(VK_L, presses=1):
        return (
            LiveTeleportResult(
                False,
                "no_window",
                "Updated your save, but could not focus the UNDERTALE window. "
                "Click the Undertale window and press L (debug load), "
                "or restart Undertale once if debug was just enabled.",
            ),
            [],
        )

    if force:
        # Second load after leaving battle / skipping dialogue
        time.sleep(0.2)
        _send_key_to_undertale(VK_L, presses=2)
        time.sleep(0.1)

    return (
        LiveTeleportResult(
            True,
            "live_load",
            f"Forced load → room {room_id} (save updated, skip keys, Insert+L). "
            "Works in cutscenes/battles when debug is on. "
            "If still stuck, click Undertale and press L once more.",
            debug_enabled=debug_on,
        ),
        [],
    )


# --- memory_patch.py ---

PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400

MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04
PAGE_WRITECOPY = 0x08
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40
PAGE_EXECUTE_WRITECOPY = 0x80

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if hasattr(ctypes, "WinDLL") else None


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def _open_process(pid: int):
    if kernel32 is None:
        raise RuntimeError("Memory patching requires Windows.")
    access = (
        PROCESS_VM_READ
        | PROCESS_VM_WRITE
        | PROCESS_VM_OPERATION
        | PROCESS_QUERY_INFORMATION
    )
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        raise RuntimeError(
            "Could not open Undertale process (try running as Administrator)."
        )
    return handle


def _read(handle, address: int, size: int) -> bytes:
    buf = (ctypes.c_char * size)()
    read = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read)
    )
    if not ok:
        return b""
    return bytes(buf[: read.value])


def _write(handle, address: int, data: bytes) -> None:
    written = ctypes.c_size_t(0)
    ok = kernel32.WriteProcessMemory(
        handle,
        ctypes.c_void_p(address),
        data,
        len(data),
        ctypes.byref(written),
    )
    if not ok or written.value != len(data):
        # Retry after VirtualProtectEx to writable
        old = wintypes.DWORD(0)
        kernel32.VirtualProtectEx(
            handle,
            ctypes.c_void_p(address),
            len(data),
            PAGE_EXECUTE_READWRITE,
            ctypes.byref(old),
        )
        ok = kernel32.WriteProcessMemory(
            handle,
            ctypes.c_void_p(address),
            data,
            len(data),
            ctypes.byref(written),
        )
        if old.value:
            kernel32.VirtualProtectEx(
                handle,
                ctypes.c_void_p(address),
                len(data),
                old,
                ctypes.byref(old),
            )
        if not ok or written.value != len(data):
            raise RuntimeError("WriteProcessMemory failed (try Administrator).")


def find_form_base(
    handle,
    *,
    expected_size: int | None = None,
    needle: bytes = b"FORM",
    max_addr: int = 0x7FFFFFFF,
) -> Optional[int]:
    """Scan committed readable regions for a GameMaker FORM header."""
    mbi = MEMORY_BASIC_INFORMATION()
    address = 0
    best: Optional[int] = None
    while address < max_addr:
        result = kernel32.VirtualQueryEx(
            handle,
            ctypes.c_void_p(address),
            ctypes.byref(mbi),
            ctypes.sizeof(mbi),
        )
        if not result:
            break
        base = int(mbi.BaseAddress or 0)
        size = int(mbi.RegionSize or 0)
        if size <= 0:
            break
        protect = int(mbi.Protect)
        readable = (
            int(mbi.State) == MEM_COMMIT
            and not (protect & PAGE_NOACCESS)
            and not (protect & PAGE_GUARD)
            and size >= 16
        )
        if readable:
            offset = 0
            while offset < size:
                piece = min(2 * 1024 * 1024, size - offset)
                data = _read(handle, base + offset, piece)
                if data:
                    idx = data.find(needle)
                    while idx != -1:
                        abs_addr = base + offset + idx
                        header = _read(handle, abs_addr, 8)
                        if len(header) == 8 and header[:4] == b"FORM":
                            declared = struct.unpack_from("<I", header, 4)[0]
                            if 1_000_000 <= declared <= 200_000_000:
                                if expected_size is not None:
                                    if abs(declared + 8 - expected_size) <= 64:
                                        return abs_addr
                                    if best is None:
                                        best = abs_addr
                                else:
                                    return abs_addr
                        idx = data.find(needle, idx + 1)
                next_off = offset + piece
                if next_off < size:
                    next_off = max(0, next_off - 3)
                offset = next_off if next_off > offset else offset + piece
        next_addr = base + size
        if next_addr <= address:
            break
        address = next_addr
    return best


def write_int32_in_running_game(
    pid: int,
    file_offset: int,
    value: int,
    *,
    expected_size: int | None = None,
    expected_old: int | None = None,
) -> bool:
    """Write a little-endian int32 at data.win file_offset inside the live process."""
    handle = _open_process(pid)
    try:
        form = find_form_base(handle, expected_size=expected_size)
        if form is None:
            return False
        addr = form + int(file_offset)
        payload = struct.pack("<I", int(value) & 0xFFFFFFFF)
        _write(handle, addr, payload)
        return True
    finally:
        kernel32.CloseHandle(handle)


def replace_u32_pattern_in_process(
    pid: int,
    old_word: int,
    new_word: int,
    *,
    max_replacements: int = 8,
    max_addr: int = 0x7FFFFFFF,
) -> int:
    """Replace little-endian uint32 words in committed memory (use sparingly)."""
    if old_word == new_word:
        return 0
    needle = struct.pack("<I", old_word & 0xFFFFFFFF)
    replacement = struct.pack("<I", new_word & 0xFFFFFFFF)
    handle = _open_process(pid)
    replaced = 0
    try:
        for base, data in iter_process_memory(handle, max_addr=max_addr):
            idx = 0
            while replaced < max_replacements:
                found = data.find(needle, idx)
                if found < 0:
                    break
                try:
                    _write(handle, base + found, replacement)
                    replaced += 1
                except RuntimeError:
                    pass
                idx = found + 1
            if replaced >= max_replacements:
                break
    finally:
        kernel32.CloseHandle(handle)
    return replaced


def iter_process_memory(handle, *, max_addr: int = 0x7FFFFFFF):
    """Yield (base_address, bytes) for committed readable regions."""
    mbi = MEMORY_BASIC_INFORMATION()
    address = 0
    while address < max_addr:
        result = kernel32.VirtualQueryEx(
            handle,
            ctypes.c_void_p(address),
            ctypes.byref(mbi),
            ctypes.sizeof(mbi),
        )
        if not result:
            break
        base = int(mbi.BaseAddress or 0)
        size = int(mbi.RegionSize or 0)
        if size <= 0:
            break
        protect = int(mbi.Protect)
        readable = (
            int(mbi.State) == MEM_COMMIT
            and not (protect & PAGE_NOACCESS)
            and not (protect & PAGE_GUARD)
            and size >= 4
        )
        if readable:
            # Cap huge regions into chunks
            offset = 0
            while offset < size:
                piece = min(2 * 1024 * 1024, size - offset)
                data = _read(handle, base + offset, piece)
                if data:
                    yield base + offset, data
                offset += piece
        next_addr = base + size
        if next_addr <= address:
            break
        address = next_addr


def replace_pattern_in_process(*_a, **_k):
    """Deprecated alias placeholder."""
    return 0




def patch_int32_in_data_win_image(
    data_win: str | Path,
    file_offset: int,
    value: int,
    *,
    expected_old: int | None = None,
) -> tuple[bool, str]:
    """
    Locate Undertale's loaded data.win FORM in memory and write an int32 at file_offset.
    Returns (ok, detail).
    """
    if not is_windows():
        return False, "Windows only"
    pid = find_undertale_pid()
    if not pid:
        return False, "Undertale not running"
    path = Path(data_win)
    if not path.is_file():
        return False, "data.win missing"
    size = path.stat().st_size
    try:
        ok = write_int32_in_running_game(
            pid,
            file_offset,
            value,
            expected_size=size,
            expected_old=expected_old,
        )
    except RuntimeError as exc:
        return False, str(exc)
    if ok:
        return True, f"wrote {value} @ 0x{file_offset:X}"
    return False, "FORM image not found in process memory"


def patch_u32_everywhere_in_game(old_word: int, new_word: int) -> tuple[int, str]:
    """Replace a u32 word across the live Undertale process (bytecode copies)."""
    if not is_windows():
        return 0, "Windows only"
    pid = find_undertale_pid()
    if not pid:
        return 0, "Undertale not running"
    try:
        n = replace_u32_pattern_in_process(pid, old_word, new_word)
    except RuntimeError as exc:
        return 0, str(exc)
    return n, f"replaced {n} occurrence(s)"


# --- launcher.py ---

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


# --- save_editor.py ---

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


# --- battles.py ---

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
    # Clear menus/dialog so Home is received by the player object.
    for _ in range(3):
        _send_key_to_undertale(VK_ESCAPE, presses=1)
        time.sleep(0.06)
    time.sleep(0.1)
    if not _send_key_to_undertale(VK_HOME_KEY, presses=3):
        return False, "Could not send the Home key. Click the Undertale window and press Home."
    time.sleep(0.35)
    # Second burst — first Home is sometimes eaten while focus settles.
    _send_key_to_undertale(VK_HOME_KEY, presses=2)
    time.sleep(0.2)
    return True, "Sent Home — fight should start in the overworld (debug must be on)."


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

        if rare_mode_enabled(save_folder):
            rare_ids = {b.id for b in RARE_BATTLEGROUPS}
            if battlegroup_id not in rare_ids and RARE_BATTLEGROUPS:
                battlegroup_id = RARE_BATTLEGROUPS[0].id
                notes.append(f"rare mode → battlegroup {battlegroup_id}")

    if ensure_debug:
        try:
            if undertale_is_running():
                ok_dbg, dbg_msg = enable_debug_mode_live(data_win)
                notes.append(dbg_msg)
                if not ok_dbg and not debug_flag_enabled(data_win):
                    notes.append("debug off — click Enable live patches, relaunch, retry")
            elif not debug_flag_enabled(data_win):
                if enable_debug_mode(data_win, backup=True):
                    notes.append("enabled debug")
                else:
                    notes.append("debug offsets not found")
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
        time.sleep(0.2)
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

    if not RARE_BATTLEGROUPS:
        return False, "No rare battlegroups configured."
    bg = random.choice(RARE_BATTLEGROUPS)
    ok, msg = start_fight(bg.id, data_win=data_win, save_folder=save_folder)
    return ok, f"{bg.name} ({bg.id}): {msg}"


# --- chaos.py ---

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


# Only rewrite room ids inside door/warp-style scripts. Blindly patching every
# PushI-before-Call bricks file I/O (ossafe_file_text_eof) and boot.
_ROOM_GOTO_ALLOW = (
    "door",
    "doorway",
    "warp",
    "portal",
    "stair",
    "elevat",
    "gateway",
    "ladder",
    "hole",
    "bridge",
    "dock",
    "transit",
    "teleport",
    "roomgoto",
    "room_goto",
)
_ROOM_GOTO_DENY = (
    "ossafe",
    "file_text",
    "file_bin",
    "file_open",
    "ini_",
    "obj_time",
    "scr_load",
    "scr_save",
    "scr_dogcheck",
    "gamepad",
    "draw_",
    "battle",
    "bullet",
    "attack",
    "writer",
    "dialog",
    "shop",
)


def _is_room_transition_script(name: str) -> bool:
    low = (name or "").lower()
    if any(bad in low for bad in _ROOM_GOTO_DENY):
        return False
    return any(ok in low for ok in _ROOM_GOTO_ALLOW)


_RARE_SCRIPT_ALLOW = (
    "encounter",
    "battlegroup",
    "scr_steps",
    "population",
    "monster",
    "rare",
    "glyde",
    "sorry",
)
_RARE_SCRIPT_DENY = _ROOM_GOTO_DENY


def _is_rare_encounter_script(name: str) -> bool:
    low = (name or "").lower()
    if any(bad in low for bad in _RARE_SCRIPT_DENY):
        return False
    return any(ok in low for ok in _RARE_SCRIPT_ALLOW)


def restore_room_chaos(data_win: str | Path) -> tuple[bool, str]:
    """Restore data.win from data.win.roomchaosbak if present."""
    path = Path(data_win)
    bak = path.with_suffix(path.suffix + ".roomchaosbak")
    if not bak.is_file():
        return False, f"No {bak.name} next to data.win. Use Restore data.win on the main window."
    try:
        path.write_bytes(bak.read_bytes())
    except OSError as exc:
        return False, f"Could not restore: {exc}"
    return True, f"Restored {path.name} from {bak.name}. Start Undertale again."


def randomize_room_gotos(
    data_win: str | Path,
    *,
    seed: int | None = None,
    backup: bool = True,
) -> tuple[bool, str, dict[int, int]]:
    """
    Shuffle room_goto destinations among playable (non-text) rooms by rewriting
    PushI immediates that look like room ids and sit just before a Call —
    only inside door/warp-named scripts (safe allowlist).
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

    # Backup BEFORE mutating — never overwrite an existing first backup.
    if backup:
        bak = path.with_suffix(path.suffix + ".roomchaosbak")
        if not bak.exists():
            bak.write_bytes(path.read_bytes())

    raw = bytearray(path.read_bytes())
    reader = BinaryReader(bytes(raw))
    changed = 0
    scripts_touched = 0
    for name, bc_off, length in _find_code_entries(reader):
        if not _is_room_transition_script(name):
            continue
        scripts_touched += 1
        pos = 0
        while pos + 8 <= length:
            word = struct.unpack_from("<I", raw, bc_off + pos)[0]
            op = _opcode(word)
            if op == OP_PUSHI:
                value = word & 0xFFFF
                if value in mapping:
                    # Require Call as the very next instruction (or after one 1-word op)
                    ahead = pos + 4
                    found_call = False
                    for step in range(2):
                        if ahead + 4 > length:
                            break
                        w2 = struct.unpack_from("<I", raw, bc_off + ahead)[0]
                        op2 = _opcode(w2)
                        if op2 in (OP_CALL_V15, OP_CALL_V14):
                            found_call = True
                            break
                        if step == 0 and op2 not in (0x45, 0x41):
                            ahead += 4
                            continue
                        break
                    if found_call:
                        new_val = mapping[value]
                        new_word = (word & 0xFFFF0000) | (new_val & 0xFFFF)
                        struct.pack_into("<I", raw, bc_off + pos, new_word)
                        changed += 1
                pos += 4
                continue
            if op in (0x45, 0x41):
                pos += 8
                continue
            if op in (OP_CALL_V15, OP_CALL_V14):
                pos += 8
                continue
            pos += 4

    if changed == 0:
        return (
            False,
            "No door/warp room_goto sites found to rewrite "
            f"(scanned {scripts_touched} transition scripts).",
            mapping,
        )

    path.write_bytes(raw)

    meta = path.with_suffix(path.suffix + ".roomchaos.json")
    meta.write_text(json.dumps({str(k): v for k, v in mapping.items()}, indent=2), encoding="utf-8")
    return (
        True,
        f"Randomized {changed} door/warp transitions among {len(playable)} playable rooms "
        f"({scripts_touched} scripts). Restart Undertale. Undo: Restore data.win "
        f"or Chaos → Undo room chaos (data.win.roomchaosbak).",
        mapping,
    )


def _force_rare_chance_pushes(data_win: str | Path, *, backup: bool = True) -> tuple[int, str]:
    """
    Bump small chance immediates that sit near rare battlegroup PushIs to 100,
    so rare fights win RNG checks more reliably.
    """

    rare_ids = {b.id for b in RARE_BATTLEGROUPS}
    path = Path(data_win)
    if not path.is_file():
        return 0, "no data.win"
    raw = bytearray(path.read_bytes())
    reader = BinaryReader(bytes(raw))

    changed = 0
    for name, bc_off, length in _find_code_entries(reader):
        if not _is_rare_encounter_script(name):
            continue
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


# --- amalgomation.py ---

AMALGOMATION_ID = 666
# Vessel fight: Endogeny battlegroup — rewritten in-place into Amalgomation.
HOST_BATTLEGROUP = 86

VK_F6 = 0x75  # debug: mercy 0, ATK 999

OP_PUSHI = 0x84
OP_BF = 0xB8
OP_B = 0xB6
OP_BT = 0xB7

_DRAW_NAMES = (
    "gml_Object_obj_endogeny_body_Draw_0",
    "gml_Object_obj_endogeny_body_Draw",
)
_STEP_NAMES = (
    "gml_Object_obj_endogeny_Step_0",
    "gml_Object_obj_endogeny_Step",
)

# Object-name substrings that look like bullet / attack generators
_GEN_NEEDLES = (
    "bulletgen",
    "bulgen",
    "blt_",
    "gen",
    "blaster",
    "gaster",
    "spear",
    "bonebox",
    "bone",
    "rocketdog",
    "laserdog",
    "amalgam",
    "spiderbullet",
    "lavafire",
    "butterfly",
    "carrot",
    "blackbox",
    "gigavine",
    "sidegen",
    "vertbullet",
    "randomgen",
    "stormstar",
    "asgore",
    "sans",
    "mettaton",
)

_HOST_GEN_NAMES = (
    "obj_amalgam_rocketdog",
    "obj_amalgam_laserdog",
)

_HOST_SPRITE_NAMES = (
    "spr_endogeny",
    "spr_endogeny_head",
    "spr_endogeny_2",
)


@dataclass
class ResourceIndex:
    sprites: dict[str, int] = field(default_factory=dict)
    objects: dict[str, int] = field(default_factory=dict)
    sprite_ids: list[int] = field(default_factory=list)
    gen_object_ids: list[int] = field(default_factory=list)


@dataclass
class PatchSite:
    offset: int  # file offset of PushI dword
    original: int
    kind: str


@dataclass
class AmalgomationPlan:
    sprite_sites: list[PatchSite] = field(default_factory=list)
    attack_sites: list[PatchSite] = field(default_factory=list)
    firingrate_sites: list[PatchSite] = field(default_factory=list)
    branch_sites: list[PatchSite] = field(default_factory=list)
    mercymod_sites: list[PatchSite] = field(default_factory=list)
    resources: ResourceIndex = field(default_factory=ResourceIndex)


@dataclass
class ChaosState:
    layer: int = 1
    rounds: int = 0
    stack: list[str] = field(default_factory=list)
    running: bool = False
    fake_hp: int = 666
    fake_df: int = 66
    fake_damage: int = 9999


_DIRECTOR_LOCK = threading.Lock()
_ACTIVE_DIRECTOR: "AmalgomationDirector | None" = None


def is_amalgomation_id(battlegroup_id: int) -> bool:
    return int(battlegroup_id) == AMALGOMATION_ID


def _list_chunk_names(data: bytes, tag: str) -> dict[str, int]:
    """Return {name: index} for a GameMaker pointer-list chunk (SPRT/OBJT/…)."""
    reader = BinaryReader(data)
    reader.seek(0)
    if reader.read_tag() != "FORM":
        return {}
    form_size = reader.read_u32()
    form_end = reader.position + form_size
    chunk_start = None
    while reader.position + 8 <= form_end:
        t = reader.read_tag()
        size = reader.read_u32()
        start = reader.position
        if t == tag:
            chunk_start = start
        try:
            reader.seek(start + size)
        except ValueError:
            break
    if chunk_start is None:
        return {}
    reader.seek(chunk_start)
    count = reader.read_u32()
    if count <= 0 or count > 200_000:
        return {}
    offsets = [reader.read_u32() for _ in range(count)]
    out: dict[str, int] = {}
    for idx, off in enumerate(offsets):
        try:
            if off <= 0 or off >= len(data):
                continue
            reader.seek(off)
            name = reader.read_offset_string() or ""
            if name:
                out[name] = idx
        except Exception:
            continue
    return out


def discover_resources(data: bytes) -> ResourceIndex:
    sprites = _list_chunk_names(data, "SPRT")
    objects = _list_chunk_names(data, "OBJT")
    sprite_ids = sorted({i for i in sprites.values() if 1 <= i <= 20000})
    gen_ids: list[int] = []
    seen: set[int] = set()
    for name, idx in objects.items():
        low = name.lower()
        if not any(n in low for n in _GEN_NEEDLES):
            continue
        # Skip pure monster controllers that are not gens when possible
        if low.startswith("obj_") and "monster" in low and "gen" not in low:
            continue
        if idx in seen:
            continue
        seen.add(idx)
        gen_ids.append(idx)
    # Prefer known host gens first if present
    preferred = []
    for hn in _HOST_GEN_NAMES:
        if hn in objects and objects[hn] not in preferred:
            preferred.append(objects[hn])
    rest = [g for g in gen_ids if g not in preferred]
    random.Random(666).shuffle(rest)
    return ResourceIndex(
        sprites=sprites,
        objects=objects,
        sprite_ids=sprite_ids,
        gen_object_ids=preferred + rest,
    )


def _find_named_code(data: bytes, names: tuple[str, ...]) -> list[tuple[str, int, int]]:
    want = {n.lower() for n in names}
    out = []
    for name, off, length in _find_code_entries(BinaryReader(data)):
        if name.lower() in want or any(name.lower().endswith(n.lower()) for n in names):
            out.append((name, off, length))
    return out


def _scan_pushi(data: bytes, bc_off: int, length: int) -> list[tuple[int, int]]:
    """Return list of (abs_offset, imm) for PushI-like words in a code blob."""
    hits = []
    pos = 0
    end = min(length, len(data) - bc_off)
    while pos + 4 <= end:
        word = struct.unpack_from("<I", data, bc_off + pos)[0]
        op = (word >> 24) & 0xFF
        if op in (OP_PUSHI, 0xC0):
            hits.append((bc_off + pos, push_imm(word)))
        pos += 4
    return hits


def _scan_branches(data: bytes, bc_off: int, length: int) -> list[tuple[int, int, int]]:
    """Return (abs_offset, opcode, word) for B/BT/BF in blob."""
    hits = []
    pos = 0
    end = min(length, len(data) - bc_off)
    while pos + 4 <= end:
        word = struct.unpack_from("<I", data, bc_off + pos)[0]
        op = (word >> 24) & 0xFF
        if op in (OP_B, OP_BT, OP_BF):
            hits.append((bc_off + pos, op, word))
        pos += 4
    return hits


def build_amalgomation_plan(data: bytes) -> AmalgomationPlan:
    res = discover_resources(data)
    plan = AmalgomationPlan(resources=res)

    host_sprite_ids = {
        res.sprites[n] for n in _HOST_SPRITE_NAMES if n in res.sprites
    }
    # Also accept any spr_*endogeny*
    for name, idx in res.sprites.items():
        if "endogeny" in name.lower():
            host_sprite_ids.add(idx)

    host_gen_ids = {
        res.objects[n] for n in _HOST_GEN_NAMES if n in res.objects
    }
    for name, idx in res.objects.items():
        low = name.lower()
        if "amalgam_rocketdog" in low or "amalgam_laserdog" in low:
            host_gen_ids.add(idx)

    for _name, off, length in _find_named_code(data, _DRAW_NAMES):
        for abs_off, imm in _scan_pushi(data, off, length):
            if imm in host_sprite_ids or (host_sprite_ids and imm in host_sprite_ids):
                word = struct.unpack_from("<I", data, abs_off)[0]
                plan.sprite_sites.append(PatchSite(abs_off, word, "sprite"))
            elif not host_sprite_ids and 1 <= imm <= 8000:
                # Fallback: first few PushIs in Draw are usually sprite ids
                word = struct.unpack_from("<I", data, abs_off)[0]
                if len([s for s in plan.sprite_sites if s.kind == "sprite"]) < 4:
                    plan.sprite_sites.append(PatchSite(abs_off, word, "sprite"))

    gen_set = set(res.gen_object_ids[:120])
    for _name, off, length in _find_named_code(data, _STEP_NAMES):
        pushis = _scan_pushi(data, off, length)
        for abs_off, imm in pushis:
            word = struct.unpack_from("<I", data, abs_off)[0]
            if imm in host_gen_ids or imm in gen_set:
                plan.attack_sites.append(PatchSite(abs_off, word, "attack"))
            elif imm == 10:
                # global.firingrate = 10 in stock Endogeny
                plan.firingrate_sites.append(PatchSite(abs_off, word, "firingrate"))
            elif imm in (999999, 222):
                plan.mercymod_sites.append(PatchSite(abs_off, word, "mercy"))
        # Soften BF between the first two host-gen creates so both patterns can fire
        host_hits = [s for s in plan.attack_sites if push_imm(s.original) in host_gen_ids]
        if len(host_hits) >= 2:
            lo = min(s.offset for s in host_hits[:2])
            hi = max(s.offset for s in host_hits[:2])
            for abs_off, op, word in _scan_branches(data, off, length):
                if lo <= abs_off <= hi and op == OP_BF:
                    plan.branch_sites.append(PatchSite(abs_off, word, "branch_bf"))

    # Nested gens: Alarm/Create on rocketdog/laserdog — more stack slots as layers grow
    nested_names = tuple(
        f"gml_Object_{n}_{suffix}"
        for n in ("obj_amalgam_rocketdog", "obj_amalgam_laserdog")
        for suffix in ("Alarm_0", "Alarm_1", "Alarm_2", "Alarm_3", "Alarm_4", "Step_0", "Create_0")
    )
    for _name, off, length in _find_named_code(data, nested_names):
        for abs_off, imm in _scan_pushi(data, off, length):
            if imm in gen_set or imm in host_gen_ids:
                word = struct.unpack_from("<I", data, abs_off)[0]
                plan.attack_sites.append(PatchSite(abs_off, word, "attack_nested"))

    # De-dupe sites by offset
    seen_off: set[int] = set()
    uniq: list[PatchSite] = []
    for s in plan.attack_sites:
        if s.offset in seen_off:
            continue
        seen_off.add(s.offset)
        uniq.append(s)
    plan.attack_sites = uniq[:16]

    return plan


def restore_amalgomation_backup_if_any(data_win: str | Path) -> tuple[bool, str]:
    """
    Undo prior Amalgomation disk corruption (string overflows / branch hacks).
    Returns (restored, message).
    """
    path = Path(data_win)
    bak = path.with_suffix(path.suffix + ".amalgobak")
    if not bak.is_file():
        return False, ""
    try:
        current = path.read_bytes()
        clean = bak.read_bytes()
    except OSError as exc:
        return False, f"Could not read amalgomation backup: {exc}"
    if current == clean:
        return False, ""
    try:
        path.write_bytes(clean)
    except OSError as exc:
        return False, f"Could not restore amalgomation backup: {exc}"
    return True, (
        "Restored data.win from .amalgobak (previous Amalgomation install corrupted it). "
        "Close Undertale completely, click Launch Undertale, load your save, then enter 666 again."
    )


def prepare_amalgomation_plan(data_win: str | Path) -> tuple[bool, str, AmalgomationPlan]:
    """
    Index sprites/attack sites only. Does NOT rewrite data.win structure
    (earlier installs corrupted strings and broke fight start).
    """
    path = Path(data_win)
    if not path.is_file():
        return False, "data.win missing", AmalgomationPlan()
    restored, restore_msg = restore_amalgomation_backup_if_any(path)
    if restored:
        # Caller must relaunch — in-memory FORM is still dirty.
        return False, restore_msg, AmalgomationPlan()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return False, str(exc), AmalgomationPlan()
    plan = build_amalgomation_plan(raw)
    ok = bool(plan.resources.sprite_ids) or bool(plan.sprite_sites) or bool(plan.attack_sites)
    msg = (
        f"Amalgomation ready (sprite sites={len(plan.sprite_sites)}, "
        f"attack sites={len(plan.attack_sites)}, "
        f"gens={len(plan.resources.gen_object_ids)}, "
        f"spritepool={len(plan.resources.sprite_ids)})"
    )
    return ok, msg, plan


def install_amalgomation_into_data_win(data_win: str | Path) -> tuple[bool, str, AmalgomationPlan]:
    """Backward-compatible name — now non-destructive (plan only). """
    return prepare_amalgomation_plan(data_win)


def scramble_u32_candidates(
    pid: int,
    candidates: list[int],
    low: int,
    high: int,
    *,
    limit: int = 12,
) -> int:
    if not candidates:
        return 0
    handle = _open_process(pid)
    wrote = 0
    try:
        for addr in candidates[:limit]:
            val = random.randint(low, high)
            try:
                _write(handle, addr, struct.pack("<i", val))
                wrote += 1
            except RuntimeError:
                continue
    finally:
        if kernel32:
            kernel32.CloseHandle(handle)
    return wrote


def find_int32_addresses(pid: int, value: int, *, max_hits: int = 40) -> list[int]:
    needle = struct.pack("<i", int(value))
    hits: list[int] = []
    handle = _open_process(pid)
    try:
        for base, data in iter_process_memory(handle):
            start = 0
            while len(hits) < max_hits:
                idx = data.find(needle, start)
                if idx < 0:
                    break
                if idx % 4 == 0:
                    hits.append(base + idx)
                start = idx + 4
            if len(hits) >= max_hits:
                break
    finally:
        if kernel32:
            kernel32.CloseHandle(handle)
    return hits


class AmalgomationDirector:
    """Silent in-process chaos loop — no UI window."""

    def __init__(self, data_win: Path, plan: AmalgomationPlan):
        self.data_win = Path(data_win)
        self.plan = plan
        self.state = ChaosState()
        self.rng = random.Random()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._player_hp_addrs: list[int] = []
        self._monster_hp_addrs: list[int] = []
        self._monster_df_addrs: list[int] = []
        self._tick_count = 0
        self._file_size = self.data_win.stat().st_size if self.data_win.is_file() else None
        self._active_attack_slots: list[int] = []  # object ids currently stacked

    def start(self) -> None:
        self.state = ChaosState(running=True, layer=1, rounds=0, stack=[])
        self._stop.clear()
        self._tick_count = 0
        gens = self.plan.resources.gen_object_ids or [1]
        first = self.rng.choice(gens)
        self._active_attack_slots = [first]
        self.state.stack = [self._label_for_gen(first)]
        self._prime_memory_targets()
        self._apply_attack_slots()
        self._morph_sprites()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.state.running = False
        self._stop.set()

    def _label_for_gen(self, oid: int) -> str:
        for name, idx in self.plan.resources.objects.items():
            if idx == oid:
                return name.replace("obj_", "")
        return f"gen#{oid}"

    def _prime_memory_targets(self) -> None:
        if not is_windows():
            return
        pid = find_undertale_pid()
        if not pid:
            return
        try:
            self._player_hp_addrs = find_int32_addresses(pid, 20, max_hits=24)
            self._monster_hp_addrs = []
            for seed in (50, 100, 150, 200, 300, 500, 1000, 1500):
                self._monster_hp_addrs.extend(find_int32_addresses(pid, seed, max_hits=6))
            self._monster_hp_addrs = list(dict.fromkeys(self._monster_hp_addrs))[:48]
            self._monster_df_addrs = []
            for seed in (0, 1, 2, 3, 4, 5, 10, 20, 25):
                self._monster_df_addrs.extend(find_int32_addresses(pid, seed, max_hits=4))
            self._monster_df_addrs = list(dict.fromkeys(self._monster_df_addrs))[:24]
        except Exception:
            self._player_hp_addrs = []
            self._monster_hp_addrs = []
            self._monster_df_addrs = []

    def _write_site_value(self, site: PatchSite, value: int) -> None:
        word = (site.original & 0xFFFF0000) | (int(value) & 0xFFFF)
        if not is_windows():
            return
        pid = find_undertale_pid()
        if not pid or not self._file_size:
            return
        try:
            write_int32_in_running_game(
                pid, site.offset, word, expected_size=self._file_size
            )
        except Exception:
            try:
                patch_int32_in_data_win_image(self.data_win, site.offset, word)
            except Exception:
                pass

    def _morph_sprites(self) -> None:
        pool = self.plan.resources.sprite_ids
        if not pool or not self.plan.sprite_sites:
            return
        # Each site gets a different random sprite → mismatched file-meat body
        used: set[int] = set()
        for site in self.plan.sprite_sites:
            choices = [s for s in pool if s not in used] or pool
            pick = self.rng.choice(choices)
            used.add(pick)
            self._write_site_value(site, pick)

    def _apply_attack_slots(self) -> None:
        if not self.plan.attack_sites:
            return
        slots = list(self._active_attack_slots) or [self.rng.choice(self.plan.resources.gen_object_ids or [1])]
        # Assign stacked gens across all known attack PushI sites (cycle)
        for i, site in enumerate(self.plan.attack_sites):
            oid = slots[i % len(slots)]
            self._write_site_value(site, oid)
        # As layers grow, crank firing rate down (more bullets)
        rate = max(1, 10 - self.state.layer * 2)
        for site in self.plan.firingrate_sites[:4]:
            self._write_site_value(site, rate)

    def _add_stacked_attack(self) -> None:
        gens = self.plan.resources.gen_object_ids
        if not gens:
            return
        used = set(self._active_attack_slots)
        choices = [g for g in gens if g not in used] or gens
        nxt = self.rng.choice(choices)
        self._active_attack_slots.append(nxt)
        self.state.stack.append(self._label_for_gen(nxt))
        self.state.layer = len(self._active_attack_slots)
        self._apply_attack_slots()

    def tick(self) -> ChaosState:
        if not self.state.running:
            return self.state
        self._tick_count += 1
        st = self.state

        st.fake_hp = self.rng.randint(1, 99999)
        st.fake_df = self.rng.randint(0, 999)
        st.fake_damage = self.rng.choice(
            [0, 1, 9, 99, 999, 9999, 99999, -1, 32767, self.rng.randint(0, 999999)]
        )

        # Appearance: constantly change between random game sprites
        self._morph_sprites()

        # Every round (~4s): swap the newest attack pattern for another random one
        if self._tick_count % 4 == 0:
            st.rounds += 1
            gens = self.plan.resources.gen_object_ids
            if gens and self._active_attack_slots:
                self._active_attack_slots[-1] = self.rng.choice(gens)
                st.stack[-1] = self._label_for_gen(self._active_attack_slots[-1])
            self._apply_attack_slots()
            # Every 2 rounds: stack another attack
            if st.rounds > 0 and st.rounds % 2 == 0:
                self._add_stacked_attack()
                if find_undertale_hwnd():
                    _send_key_to_undertale(VK_F6, presses=1)

        # HP / armor scramble every second
        pid = find_undertale_pid()
        if pid and is_windows():
            try:
                scramble_u32_candidates(pid, self._monster_hp_addrs, 1, 99999, limit=12)
                scramble_u32_candidates(pid, self._monster_df_addrs, 0, 999, limit=8)
                # Glitch damage readouts — poke common damage ints
                dmg_addrs = find_int32_addresses(pid, 0, max_hits=8) if self._tick_count % 5 == 0 else []
                if dmg_addrs:
                    scramble_u32_candidates(
                        pid, dmg_addrs, -9, 99999, limit=6
                    )
                # Escalate: chip then kill the player
                if st.layer >= 3:
                    scramble_u32_candidates(
                        pid, self._player_hp_addrs, 1, max(2, 14 - st.layer), limit=6
                    )
                if st.layer >= 6:
                    scramble_u32_candidates(pid, self._player_hp_addrs, 0, 1, limit=10)
                if st.layer >= 8:
                    scramble_u32_candidates(pid, self._player_hp_addrs, 0, 0, limit=12)
            except Exception:
                pass

        return st

    def _loop(self) -> None:
        while not self._stop.is_set() and self.state.running:
            try:
                self.tick()
            except Exception:
                pass
            self._stop.wait(1.0)


def stop_amalgomation_director() -> None:
    global _ACTIVE_DIRECTOR
    with _DIRECTOR_LOCK:
        if _ACTIVE_DIRECTOR is not None:
            _ACTIVE_DIRECTOR.stop()
            _ACTIVE_DIRECTOR = None


def start_amalgomation_fight(
    *,
    data_win: str | Path | None,
    save_folder: str | Path | None = None,
) -> tuple[bool, str]:
    """Start host fight first, then morph it live — no extra windows, no disk corruption."""
    if not data_win or not Path(data_win).is_file():
        return False, "Open your Undertale folder (data.win) first."
    if not undertale_is_running():
        return (
            False,
            "Launch Undertale, load a save, stand in the overworld, then enter 666 again.",
        )

    stop_amalgomation_director()
    ok_inst, inst_msg, plan = prepare_amalgomation_plan(data_win)
    if not ok_inst:
        # Includes "close and relaunch" after restoring a corrupted backup.
        return False, inst_msg

    # Fight first — director only starts after the battle has time to appear.
    ok, msg = start_fight(
        HOST_BATTLEGROUP,
        data_win=data_win,
        ensure_debug=True,
        save_folder=save_folder,
    )
    if not ok:
        return False, f"Amalgomation fight failed to start: {msg}"

    director = AmalgomationDirector(Path(data_win), plan)
    with _DIRECTOR_LOCK:
        global _ACTIVE_DIRECTOR
        _ACTIVE_DIRECTOR = director

    def _boot() -> None:
        # One more Home burst after focus settles, then morph once battle exists.
        time.sleep(0.9)
        if not undertale_is_running():
            return

        if find_undertale_hwnd():
            _send_key_to_undertale(0x1B, presses=1)  # Esc — leave menus
            time.sleep(0.08)
            _send_key_to_undertale(VK_HOME_KEY, presses=3)
        time.sleep(1.6)
        if undertale_is_running():
            director.start()

    threading.Thread(target=_boot, daemon=True).start()

    return (
        True,
        "AMALGOMATION — focus the Undertale window; the fight should start now. "
        "If not, press Home once in the overworld. "
        + inst_msg
        + " | "
        + msg,
    )


def open_amalgomation_ui(parent, *, data_win: Path | None, save_folder: Path | None, on_status=None):
    """Launch with no popup dialogs — status line only."""
    ok, msg = start_amalgomation_fight(data_win=data_win, save_folder=save_folder)
    if on_status:
        on_status(msg if ok else f"AMALGOMATION: {msg}")
    # Intentionally no messagebox — user wants only the game window.


# --- toolkit.py ---

COLORS = {
    "bg": "#e8e2d6",
    "panel": "#f4efe6",
    "ink": "#1c1915",
    "muted": "#5c564c",
    "accent": "#c45c26",
    "accent_hover": "#a64b1c",
    "border": "#d2c8b6",
    "success": "#2f6b4f",
}


class DebugToolkit(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        *,
        data_win: Path | None,
        save_dir: Path | None,
        on_status=None,
    ):
        super().__init__(master)
        self.title("Undertale Debug Toolkit")
        self.geometry("680x620")
        self.minsize(560, 520)
        self.configure(fg_color=COLORS["bg"])
        self.data_win = Path(data_win) if data_win else None
        self.save_dir = Path(save_dir) if save_dir else None
        self.on_status = on_status
        self._stats = PlayerStats()
        self._inv_vars: list[ctk.StringVar] = []
        self.var_rare = ctk.BooleanVar(value=False)

        ctk.CTkLabel(
            self,
            text="Debug Toolkit",
            font=ctk.CTkFont(family="Courier New", size=22, weight="bold"),
            text_color=COLORS["ink"],
        ).pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(
            self,
            text="Launch, edit stats/items, fights, Ruins reset, room chaos, rare encounters.",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=16, pady=(0, 8))

        launch_row = ctk.CTkFrame(self, fg_color="transparent")
        launch_row.pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(
            launch_row,
            text="Launch Patched Undertale",
            command=self.launch_patched,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#fffaf2",
            width=200,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            launch_row,
            text="Prepare patches",
            command=self.prepare_patches,
            fg_color=COLORS["ink"],
            hover_color="#33302b",
            text_color="#fffaf2",
            width=140,
        ).pack(side="left")

        self.tabs = ctk.CTkTabview(self, fg_color=COLORS["panel"])
        self.tabs.pack(fill="both", expand=True, padx=16, pady=8)
        self.tabs.add("Stats")
        self.tabs.add("Items")
        self.tabs.add("Fights")
        self.tabs.add("Chaos")
        self._build_stats_tab(self.tabs.tab("Stats"))
        self._build_items_tab(self.tabs.tab("Items"))
        self._build_fights_tab(self.tabs.tab("Fights"))
        self._build_chaos_tab(self.tabs.tab("Chaos"))

        self.status = ctk.CTkLabel(self, text="", text_color=COLORS["muted"], wraplength=640)
        self.status.pack(anchor="w", padx=16, pady=(0, 12))

        self.after(100, self.reload_from_save)

    def _say(self, msg: str) -> None:
        self.status.configure(text=msg)
        if self.on_status:
            self.on_status(msg)

    def prepare_patches(self) -> None:
        if not self.data_win or not self.data_win.is_file():
            messagebox.showinfo("No game", "Open your Undertale folder in the main window first.", parent=self)
            return
        if undertale_is_running():
            messagebox.showwarning(
                "Close Undertale first",
                "Close Undertale completely before preparing patches.",
                parent=self,
            )
            return
        notes = []
        try:
            if enable_debug_mode(self.data_win, backup=True):
                notes.append("debug ON")
        except Exception as exc:
            notes.append(f"debug failed: {exc}")
        try:
            ok, msg = disable_dogcheck(self.data_win, backup=True)
            ok = ok and dogcheck_likely_disabled(self.data_win)
            notes.append("dogcheck OFF" if ok else f"dogcheck still ON — {msg}")
        except Exception as exc:
            notes.append(f"dogcheck failed: {exc}")
        messagebox.showinfo("Patches", "\n".join(notes) + "\n\nThen click Launch Patched Undertale.", parent=self)
        self._say("Patches: " + "; ".join(notes))

    def launch_patched(self) -> None:
        if not self.data_win or not self.data_win.is_file():
            messagebox.showinfo("No game", "Open your Undertale folder in the main window first.", parent=self)
            return
        if undertale_is_running():
            messagebox.showinfo("Already running", "Undertale is already open.", parent=self)
            return
        # Ensure debug at least; dogcheck best-effort (safe stub won't brick launch).
        try:
            enable_debug_mode(self.data_win, backup=True)
        except Exception:
            pass
        try:
            disable_dogcheck(self.data_win, backup=True)
        except Exception:
            pass
        ok, msg = launch_undertale(data_win=self.data_win)
        if ok:
            messagebox.showinfo(
                "Launched",
                msg
                + "\n\nLoad your save (Continue). Use Stats/Items/Fights tabs anytime.\n"
                "For fights: pick a battle → Start Fight (or press Home in-game).",
                parent=self,
            )
            self._say(msg)
        else:
            messagebox.showerror("Launch failed", msg, parent=self)

    def reload_from_save(self) -> None:
        try:
            self._stats = read_player_stats(self.save_dir)
        except Exception as exc:
            self._say(f"Could not read save: {exc}")
            return
        s = self._stats
        self.var_name.set(s.name)
        self.var_love.set(str(s.love))
        self.var_hp.set(str(s.hp))
        self.var_maxhp.set(str(s.max_hp))
        self.var_at.set(str(s.at))
        self.var_df.set(str(s.df))
        self.var_exp.set(str(s.exp))
        self.var_gold.set(str(s.gold))
        self.var_kills.set(str(s.kills))
        for i, var in enumerate(self._inv_vars):
            iid = s.inventory[i] if s.inventory and i < len(s.inventory) else 0
            var.set(f"{iid}: {item_name(iid)}")
        self.var_weapon.set(f"{s.weapon}: {WEAPONS.get(s.weapon, item_name(s.weapon))}")
        self.var_armor.set(f"{s.armor}: {ARMORS.get(s.armor, item_name(s.armor))}")
        try:
            self.var_rare.set(rare_mode_enabled(self.save_dir))
        except Exception:
            pass
        self._say(f"Loaded save ({s.name}, LV {s.love}, room {s.room}).")

    def _build_stats_tab(self, tab) -> None:
        grid = ctk.CTkFrame(tab, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=8, pady=8)
        self.var_name = ctk.StringVar()
        self.var_love = ctk.StringVar()
        self.var_hp = ctk.StringVar()
        self.var_maxhp = ctk.StringVar()
        self.var_at = ctk.StringVar()
        self.var_df = ctk.StringVar()
        self.var_exp = ctk.StringVar()
        self.var_gold = ctk.StringVar()
        self.var_kills = ctk.StringVar()
        fields = [
            ("Name", self.var_name),
            ("LOVE", self.var_love),
            ("HP", self.var_hp),
            ("Max HP", self.var_maxhp),
            ("AT", self.var_at),
            ("DF", self.var_df),
            ("EXP", self.var_exp),
            ("Gold", self.var_gold),
            ("Kills", self.var_kills),
        ]
        for row, (label, var) in enumerate(fields):
            ctk.CTkLabel(grid, text=label, width=80, anchor="w").grid(row=row, column=0, sticky="w", pady=3)
            ctk.CTkEntry(grid, textvariable=var, width=220).grid(row=row, column=1, sticky="w", pady=3)
        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.pack(fill="x", padx=8, pady=8)
        ctk.CTkButton(btns, text="Reload", command=self.reload_from_save, width=100).pack(side="left", padx=4)
        ctk.CTkButton(
            btns,
            text="Save stats",
            command=self.save_stats,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            width=120,
        ).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Max out", command=self.max_stats, width=100).pack(side="left", padx=4)

    def _parse_stats(self) -> PlayerStats:
        s = self._stats

        def num(var, default=0):
            try:
                return int(float(var.get().strip()))
            except ValueError:
                return default

        return PlayerStats(
            name=self.var_name.get().strip() or s.name,
            love=num(self.var_love, s.love),
            hp=num(self.var_hp, s.hp),
            max_hp=num(self.var_maxhp, s.max_hp),
            at=num(self.var_at, s.at),
            weapon_at=s.weapon_at,
            df=num(self.var_df, s.df),
            armor_df=s.armor_df,
            exp=num(self.var_exp, s.exp),
            gold=num(self.var_gold, s.gold),
            kills=num(self.var_kills, s.kills),
            inventory=list(s.inventory or [0] * 8),
            weapon=s.weapon,
            armor=s.armor,
            room=s.room,
        )

    def max_stats(self) -> None:
        self.var_love.set("20")
        self.var_hp.set("99")
        self.var_maxhp.set("99")
        self.var_at.set("99")
        self.var_df.set("99")
        self.var_exp.set("99999")
        self.var_gold.set("99999")

    def save_stats(self) -> None:
        try:
            stats = self._parse_stats()
            # Keep inventory/equip from current _stats / item tab vars
            stats.inventory = self._inventory_from_vars()
            stats.weapon = self._id_from_combo(self.var_weapon.get(), stats.weapon)
            stats.armor = self._id_from_combo(self.var_armor.get(), stats.armor)
            path = write_player_stats(stats, self.save_dir, backup=True)
            self._stats = stats
            self._say(f"Saved stats to {path}")
            messagebox.showinfo("Saved", f"Stats written to:\n{path}\n\nLoad/Continue in Undertale (or press L).", parent=self)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)

    def _id_from_combo(self, text: str, default: int = 0) -> int:
        text = (text or "").strip()
        if not text:
            return default
        try:
            return int(text.split(":", 1)[0].strip())
        except ValueError:
            return default

    def _inventory_from_vars(self) -> list[int]:
        return [self._id_from_combo(v.get(), 0) for v in self._inv_vars]

    def _build_items_tab(self, tab) -> None:
        frame = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        item_choices = [f"{i}: {name}" for i, name in enumerate(ITEMS)]
        self._inv_vars = []
        for slot in range(8):
            ctk.CTkLabel(frame, text=f"Slot {slot + 1}", width=70, anchor="w").grid(
                row=slot, column=0, sticky="w", pady=3
            )
            var = ctk.StringVar(value="0: Empty")
            self._inv_vars.append(var)
            ctk.CTkOptionMenu(frame, variable=var, values=item_choices, width=280).grid(
                row=slot, column=1, sticky="w", pady=3
            )
        ctk.CTkLabel(frame, text="Weapon", width=70, anchor="w").grid(row=8, column=0, sticky="w", pady=6)
        self.var_weapon = ctk.StringVar(value="3: Stick")
        ctk.CTkOptionMenu(
            frame,
            variable=self.var_weapon,
            values=[f"{i}: {n}" for i, n in sorted(WEAPONS.items())],
            width=280,
        ).grid(row=8, column=1, sticky="w", pady=6)
        ctk.CTkLabel(frame, text="Armor", width=70, anchor="w").grid(row=9, column=0, sticky="w", pady=3)
        self.var_armor = ctk.StringVar(value="4: Bandage")
        ctk.CTkOptionMenu(
            frame,
            variable=self.var_armor,
            values=[f"{i}: {n}" for i, n in sorted(ARMORS.items())],
            width=280,
        ).grid(row=9, column=1, sticky="w", pady=3)

        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.pack(fill="x", padx=8, pady=8)
        ctk.CTkButton(btns, text="Reload", command=self.reload_from_save, width=100).pack(side="left", padx=4)
        ctk.CTkButton(
            btns,
            text="Save items",
            command=self.save_items,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            width=120,
        ).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Fill pies", command=self.fill_pies, width=100).pack(side="left", padx=4)

    def fill_pies(self) -> None:
        pie = next((f"{i}: {n}" for i, n in enumerate(ITEMS) if n == "Butterscotch Pie"), "11: Butterscotch Pie")
        for var in self._inv_vars:
            var.set(pie)

    def save_items(self) -> None:
        try:
            stats = self._parse_stats()
            stats.inventory = self._inventory_from_vars()
            stats.weapon = self._id_from_combo(self.var_weapon.get(), 3)
            stats.armor = self._id_from_combo(self.var_armor.get(), 4)
            path = write_player_stats(stats, self.save_dir, backup=True)
            self._stats = stats
            self._say(f"Saved items to {path}")
            messagebox.showinfo("Saved", f"Inventory written to:\n{path}", parent=self)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)

    def _build_fights_tab(self, tab) -> None:
        ctk.CTkLabel(
            tab,
            text="Requires debug mode. Discovers the Home battlegroup in data.win "
            "(and live bytecode), patches it, then sends Home — not stuck on Mettaton.",
            text_color=COLORS["muted"],
            wraplength=600,
        ).pack(anchor="w", padx=8, pady=6)
        self.fight_var = ctk.StringVar(
            value=f"{BATTLEGROUPS[0].id}: {BATTLEGROUPS[0].name}"
        )
        values = [f"{b.id}: {b.name}" for b in BATTLEGROUPS]
        ctk.CTkOptionMenu(tab, variable=self.fight_var, values=values, width=360).pack(
            anchor="w", padx=8, pady=8
        )
        custom_row = ctk.CTkFrame(tab, fg_color="transparent")
        custom_row.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(custom_row, text="Or id:").pack(side="left")
        self.custom_fight = ctk.StringVar()
        ctk.CTkEntry(custom_row, textvariable=self.custom_fight, width=80).pack(side="left", padx=6)
        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.pack(fill="x", padx=8, pady=12)
        ctk.CTkButton(
            btn_row,
            text="Start Fight",
            command=self.do_start_fight,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            width=140,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row,
            text="Start rarest fight",
            command=self.do_start_rare_fight,
            fg_color=COLORS["ink"],
            hover_color="#33302b",
            width=160,
        ).pack(side="left")
        ctk.CTkLabel(
            tab,
            text="If the last fight was Mettaton/glitched: close Undertale → Restore data.win "
            "(or Steam Verify) → Enable live patches → Launch → overworld → Start Fight. "
            "Home fight patches obj_mainchara KeyPress_36 (battlegroup = 57+nnn). "
            "Stay in the overworld. Do not spam the 5 key (that shifts the id).\n"
            "Secret: type 666 for AMALGOMATION (in-game). If it fails once: Restore data.win → Launch → 666.",
            text_color=COLORS["muted"],
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=8, pady=4)

    def do_start_fight(self) -> None:
        try:
            if self.custom_fight.get().strip():
                bg = int(self.custom_fight.get().strip())
            else:
                bg = self._id_from_combo(self.fight_var.get(), 0)
        except ValueError:
            messagebox.showerror("Bad id", "Enter a numeric battlegroup id.", parent=self)
            return
        if is_amalgomation_id(bg):
            open_amalgomation_ui(
                self,
                data_win=self.data_win,
                save_folder=self.save_dir,
                on_status=self._say,
            )
            return
        ok, msg = start_fight(
            bg,
            data_win=self.data_win,
            ensure_debug=True,
            save_folder=self.save_dir,
        )
        if ok:
            self._say(msg)
            messagebox.showinfo("Fight", msg, parent=self)
        else:
            self._say(msg)
            messagebox.showwarning("Fight", msg, parent=self)

    def do_start_rare_fight(self) -> None:
        ok, msg = start_random_rare_fight(data_win=self.data_win, save_folder=self.save_dir)
        if ok:
            self._say(msg)
            messagebox.showinfo("Rare fight", msg, parent=self)
        else:
            self._say(msg)
            messagebox.showwarning("Rare fight", msg, parent=self)

    def _build_chaos_tab(self, tab) -> None:
        ctk.CTkLabel(
            tab,
            text="Live Ruins reset and room chaos. Rare toggle boosts FUN and prefers rare fights.",
            text_color=COLORS["muted"],
            wraplength=600,
        ).pack(anchor="w", padx=8, pady=6)

        ctk.CTkButton(
            tab,
            text="Ruins reset (live)",
            command=self.do_ruins_reset,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            width=220,
        ).pack(anchor="w", padx=8, pady=8)
        ctk.CTkLabel(
            tab,
            text="First Ruins SAVE (Entrance), LOVE 1 / HP 20 / EXP·gold·kills 0, Stick+Bandage. "
            "Works while the game is open (writes save + L reload).",
            text_color=COLORS["muted"],
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 10))

        ctk.CTkButton(
            tab,
            text="Randomize rooms",
            command=self.do_randomize_rooms,
            fg_color=COLORS["ink"],
            hover_color="#33302b",
            width=220,
        ).pack(anchor="w", padx=8, pady=8)
        ctk.CTkButton(
            tab,
            text="Undo room chaos",
            command=self.do_undo_room_chaos,
            fg_color=COLORS["muted"],
            hover_color="#4a453c",
            width=220,
        ).pack(anchor="w", padx=8, pady=(0, 8))
        ctk.CTkLabel(
            tab,
            text="Shuffles door/warp destinations only (safe allowlist — will not touch "
            "file I/O scripts). Backs up data.win.roomchaosbak. Restart Undertale after. "
            "If the game shows a Code Error on boot, click Undo room chaos or Restore data.win.",
            text_color=COLORS["muted"],
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 10))

        try:
            self.var_rare.set(rare_mode_enabled(self.save_dir))
        except Exception:
            self.var_rare.set(False)
        ctk.CTkCheckBox(
            tab,
            text="Guarantee rarest encounters",
            variable=self.var_rare,
            command=self.do_toggle_rare,
            text_color=COLORS["ink"],
        ).pack(anchor="w", padx=8, pady=8)
        rare_names = ", ".join(b.name for b in RARE_BATTLEGROUPS[:6]) + "…"
        ctk.CTkLabel(
            tab,
            text=f"Sets FUN=90, keeps a rare-mode flag, and unlocks rare fight helpers "
            f"({rare_names}). Toggle again to turn off.",
            text_color=COLORS["muted"],
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 8))

    def do_ruins_reset(self) -> None:
        if not messagebox.askyesno(
            "Ruins reset",
            "Reset stats to defaults and jump to the first Ruins SAVE while the game stays open?",
            parent=self,
        ):
            return
        ok, msg = live_ruins_reset(save_folder=self.save_dir, data_win=self.data_win)
        self._say(msg)
        if ok:
            self.reload_from_save()
            messagebox.showinfo("Ruins reset", msg, parent=self)
        else:
            messagebox.showerror("Ruins reset", msg, parent=self)

    def do_randomize_rooms(self) -> None:
        if not self.data_win or not self.data_win.is_file():
            messagebox.showinfo("No game", "Open your Undertale folder first.", parent=self)
            return
        if undertale_is_running():
            if not messagebox.askyesno(
                "Undertale is running",
                "Room chaos patches data.win on disk. Close Undertale after this and relaunch "
                "so the shuffle loads. Continue?",
                parent=self,
            ):
                return
        elif not messagebox.askyesno(
            "Randomize rooms",
            "Rewrite door/warp room transitions in data.win (backup created). Continue?",
            parent=self,
        ):
            return
        ok, msg, _mapping = randomize_room_gotos(self.data_win, backup=True)
        self._say(msg)
        if ok:
            messagebox.showinfo("Room chaos", msg, parent=self)
        else:
            messagebox.showerror("Room chaos", msg, parent=self)

    def do_undo_room_chaos(self) -> None:
        if not self.data_win or not self.data_win.is_file():
            messagebox.showinfo("No game", "Open your Undertale folder first.", parent=self)
            return
        if undertale_is_running():
            messagebox.showwarning(
                "Close Undertale first",
                "Close Undertale completely, then Undo room chaos.",
                parent=self,
            )
            return
        ok, msg = restore_room_chaos(self.data_win)
        self._say(msg)
        if ok:
            messagebox.showinfo("Restored", msg, parent=self)
        else:
            messagebox.showerror("Restore failed", msg, parent=self)

    def do_toggle_rare(self) -> None:
        enabled = bool(self.var_rare.get())
        ok, msg = set_rare_encounters(
            enabled,
            save_folder=self.save_dir,
            data_win=self.data_win,
            live_reload=True,
        )
        self._say(msg)
        if not ok:
            messagebox.showerror("Rare mode", msg, parent=self)
            self.var_rare.set(not enabled)
        else:
            messagebox.showinfo("Rare mode", msg, parent=self)


# --- parser.py ---

PNG_SIG = b"\x89PNG\r\n\x1a\n"
OGG_SIG = b"OggS"
RIFF_SIG = b"RIFF"

DATA_FILE_NAMES = ("data.win", "game.unx", "game.ios", "game.droid")


@dataclass
class TexturePageItem:
    x: int
    y: int
    width: int
    height: int
    offset_x: int
    offset_y: int
    crop_width: int
    crop_height: int
    canvas_width: int
    canvas_height: int
    texture_id: int
    absolute_offset: int = 0


@dataclass
class SoundInfo:
    name: str
    extension: str
    filename: str
    audio_id: int
    flags: int = 0


@dataclass
class ParsedArchive:
    path: Path
    game_name: str = "Unknown"
    chunks: dict[str, tuple[int, int]] = field(default_factory=dict)
    assets: list[GameAsset] = field(default_factory=list)
    textures: list[bytes] = field(default_factory=list)
    # Decoded on demand — decoding every atlas up-front freezes / OOMs on real Undertale.
    _texture_image_cache: dict[int, Image.Image] = field(default_factory=dict, repr=False)
    tpag: list[TexturePageItem] = field(default_factory=list)
    sounds: list[SoundInfo] = field(default_factory=list)

    def get_texture_image(self, index: int) -> Image.Image:
        if index in self._texture_image_cache:
            return self._texture_image_cache[index]
        if index < 0 or index >= len(self.textures):
            img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
            self._texture_image_cache[index] = img
            return img
        blob = self.textures[index]
        try:
            img = Image.open(io.BytesIO(blob)).convert("RGBA")
        except Exception:
            img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        self._texture_image_cache[index] = img
        return img


def find_data_file(folder: str | Path) -> Path | None:
    folder = Path(folder)
    if folder.is_file():
        return folder
    for name in DATA_FILE_NAMES:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    # Case-insensitive scan
    lower_map = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    for name in DATA_FILE_NAMES:
        if name in lower_map:
            return lower_map[name]
    return None


def scan_loose_files(folder: str | Path) -> list[GameAsset]:
    """Pick up standalone music / media next to data.win (Undertale mus_*.ogg)."""
    folder = Path(folder)
    if folder.is_file():
        folder = folder.parent
    assets: list[GameAsset] = []
    patterns = {
        "*.ogg": AssetKind.MUSIC,
        "mus_*.ogg": AssetKind.MUSIC,
        "*.wav": AssetKind.AUDIO,
        "*.mp3": AssetKind.MUSIC,
        "*.png": AssetKind.OTHER,
        "*.gif": AssetKind.OTHER,
    }
    seen: set[Path] = set()
    for pattern, kind in patterns.items():
        for path in sorted(folder.glob(pattern)):
            if path in seen or path.name.lower() in DATA_FILE_NAMES:
                continue
            seen.add(path)
            ext = path.suffix.lower() or ".bin"
            kind_use = kind
            if ext in {".png", ".gif", ".jpg", ".jpeg", ".webp", ".bmp"}:
                kind_use = AssetKind.OTHER
            elif path.name.lower().startswith("mus_"):
                kind_use = AssetKind.MUSIC

            def make_loader(p: Path = path) -> bytes:
                return p.read_bytes()

            assets.append(
                GameAsset(
                    id=f"loose:{path.name}",
                    name=path.stem if kind_use != AssetKind.OTHER else path.name,
                    kind=kind_use,
                    extension=ext if not path.name.endswith(ext) or kind_use != AssetKind.OTHER else ext,
                    size=path.stat().st_size,
                    _data_fn=make_loader,
                    source_path=str(path),
                    meta={"loose": True},
                )
            )
    # Normalize display: keep full filename for loose others
    for asset in assets:
        if asset.kind == AssetKind.OTHER and asset.source_path:
            asset.name = Path(asset.source_path).name
            asset.extension = ""
    return assets


def _png_length(data: bytes, offset: int) -> int:
    """Return byte length of a PNG starting at offset, or -1."""
    if data[offset : offset + 8] != PNG_SIG:
        return -1
    pos = offset + 8
    while pos + 8 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        chunk_type = data[pos + 4 : pos + 8]
        pos += 12 + length  # len + type + data + crc
        if chunk_type == b"IEND":
            return pos - offset
        if length > 50_000_000:
            break
    return -1


def _guess_audio_ext(blob: bytes) -> str:
    if blob.startswith(OGG_SIG):
        return ".ogg"
    if blob.startswith(RIFF_SIG):
        return ".wav"
    return ".bin"


class DataWinParser:
    """Extract browsable assets from a GameMaker data file."""

    def __init__(self, path: str | Path, progress: Callable[[str], None] | None = None):
        self.path = Path(path)
        self.progress = progress
        self.reader = BinaryReader.from_path(self.path)
        self.result = ParsedArchive(path=self.path)

    def _say(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def parse(self) -> ParsedArchive:
        r = self.reader
        self._say(f"Opening {self.path.name} ({self.path.stat().st_size // (1024 * 1024)} MB)…")
        if r.read_tag() != "FORM":
            raise ValueError(f"{self.path.name} is not a GameMaker FORM archive")
        form_size = r.read_u32()
        form_end = r.position + form_size

        while r.position + 8 <= form_end and r.remaining() >= 8:
            tag = r.read_tag()
            size = r.read_u32()
            start = r.position
            self.result.chunks[tag] = (start, size)
            r.seek(start + size)

        self._say("Reading game info…")
        self._parse_gen8()
        self._say("Indexing textures…")
        self._parse_textures()
        self._say("Reading sprite sheets…")
        self._parse_tpag()
        self._say("Reading sound list…")
        self._parse_sounds()
        self._say("Extracting audio…")
        self._parse_audio()
        self._say("Indexing sprites…")
        self._parse_sprites()
        self._say("Indexing backgrounds…")
        self._parse_backgrounds()
        self._say("Indexing fonts…")
        self._parse_fonts()
        self._say("Indexing rooms…")
        self._parse_rooms()
        return self.result

    def _chunk(self, tag: str) -> BinaryReader | None:
        info = self.result.chunks.get(tag)
        if not info:
            return None
        start, size = info
        chunk = BinaryReader(self.reader.slice(start, size))
        # Absolute offsets in GM files are file-absolute; keep a base.
        chunk._file_base = start  # type: ignore[attr-defined]
        chunk._abs = self.reader  # type: ignore[attr-defined]
        return chunk

    def _abs_reader(self) -> BinaryReader:
        return self.reader

    def _parse_gen8(self) -> None:
        info = self.result.chunks.get("GEN8")
        if not info:
            return
        start, _ = info
        # Display name is a string pointer around offset 100 in content — walk carefully.
        # Prefer scanning STRG for "UNDERTALE" / reading known layout.
        r = self.reader
        try:
            # GEN8 layout (GMS 1.4): after several fields, Name and DisplayName are string ptrs.
            r.seek(start)
            r.skip(4)  # disable debug / unknowns vary; try reading name pointers later
            # Fallback: use filename
            self.result.game_name = self.path.stem
            # Heuristic: search nearby string pointers for display name
            r.seek(start + 100)
            for _ in range(8):
                ptr = r.read_u32()
                if 4 < ptr < r.size - 1:
                    text = r.read_cstring_at(ptr)
                    if text and 2 <= len(text) <= 64 and text.isprintable():
                        self.result.game_name = text
                        break
        except Exception:
            self.result.game_name = self.path.stem

    def _parse_textures(self) -> None:
        info = self.result.chunks.get("TXTR")
        if not info:
            return
        start, size = info
        r = self.reader
        r.seek(start)
        count = r.read_u32()
        offsets = [r.read_u32() for _ in range(count)]
        entries: list[tuple[int, int]] = []  # (png_offset, size_hint)

        for off in offsets:
            r.seek(off)
            scaled = r.read_u32()
            # GMS1: scaled (often 0/1) then png pointer. GMS2 may differ.
            png_off = r.read_u32()
            if png_off == 0 or png_off >= r.size:
                # Maybe the second field isn't a pointer — search for PNG near entry
                png_off = r.find(PNG_SIG, off, min(off + 256, start + size))
            entries.append((png_off, -1))

        # Determine sizes
        png_offsets = [e[0] for e in entries if e[0] > 0]
        data = self.reader._data  # noqa: SLF001 — intentional raw access
        raw_textures: list[bytes] = []
        for i, png_off in enumerate(png_offsets):
            length = _png_length(bytes(data), png_off)
            if length < 0:
                # Size from next texture / chunk end
                next_off = png_offsets[i + 1] if i + 1 < len(png_offsets) else start + size
                length = max(0, next_off - png_off)
            blob = bytes(data[png_off : png_off + length])
            # Trim trailing padding after IEND if present
            plen = _png_length(blob, 0)
            if plen > 0:
                blob = blob[:plen]
            raw_textures.append(blob)

        self.result.textures = raw_textures
        for i, blob in enumerate(raw_textures):

            def make_data(b: bytes = blob) -> bytes:
                return b

            def make_image(idx: int = i) -> Image.Image:
                return self.result.get_texture_image(idx).copy()

            self.result.assets.append(
                GameAsset(
                    id=f"texture:{i}",
                    name=f"texture_{i}",
                    kind=AssetKind.TEXTURE,
                    extension=".png",
                    size=len(blob),
                    _data_fn=make_data,
                    _image_fn=make_image,
                    meta={"index": i},
                )
            )

    def _parse_tpag(self) -> None:
        info = self.result.chunks.get("TPAG")
        if not info:
            return
        start, size = info
        r = self.reader
        r.seek(start)
        count = r.read_u32()
        offsets = [r.read_u32() for _ in range(count)]
        items: list[TexturePageItem] = []
        for off in offsets:
            r.seek(off)
            item = TexturePageItem(
                x=r.read_u16(),
                y=r.read_u16(),
                width=r.read_u16(),
                height=r.read_u16(),
                offset_x=r.read_u16(),
                offset_y=r.read_u16(),
                crop_width=r.read_u16(),
                crop_height=r.read_u16(),
                canvas_width=r.read_u16(),
                canvas_height=r.read_u16(),
                texture_id=r.read_u16(),
                absolute_offset=off,
            )
            items.append(item)
        self.result.tpag = items

    def _tpag_by_offset(self) -> dict[int, TexturePageItem]:
        return {t.absolute_offset: t for t in self.result.tpag}

    def _parse_sounds(self) -> None:
        info = self.result.chunks.get("SOND")
        if not info:
            return
        start, _ = info
        r = self.reader
        r.seek(start)
        count = r.read_u32()
        offsets = [r.read_u32() for _ in range(count)]
        sounds: list[SoundInfo] = []
        for off in offsets:
            r.seek(off)
            name = r.read_offset_string()
            flags = r.read_u32()
            typ = r.read_offset_string()
            filename = r.read_offset_string()
            _effects = r.read_u32()
            _volume = r.read_f32()
            _pitch = r.read_f32()
            _group = r.read_i32()
            audio_id = r.read_i32()
            ext = Path(filename).suffix if filename else (typ if typ.startswith(".") else ".wav")
            if not ext.startswith("."):
                ext = f".{ext}" if ext else ".wav"
            sounds.append(
                SoundInfo(
                    name=name or f"sound_{len(sounds)}",
                    extension=ext.lower(),
                    filename=filename or f"sound_{len(sounds)}{ext}",
                    audio_id=audio_id,
                    flags=flags,
                )
            )
        self.result.sounds = sounds

    def _parse_audio(self) -> None:
        info = self.result.chunks.get("AUDO")
        if not info:
            return
        start, size = info
        r = self.reader
        r.seek(start)
        count = r.read_u32()
        offsets = [r.read_u32() for _ in range(count)]

        # Map audio index -> preferred name from SOND
        names: dict[int, SoundInfo] = {}
        for snd in self.result.sounds:
            if snd.audio_id >= 0 and snd.audio_id not in names:
                names[snd.audio_id] = snd

        for i, off in enumerate(offsets):
            r.seek(off)
            length = r.read_u32()
            blob = r.read(length)
            info_s = names.get(i)
            if info_s:
                name = info_s.name
                ext = info_s.extension
                if ext == ".bin":
                    ext = _guess_audio_ext(blob)
            else:
                name = f"audio_{i}"
                ext = _guess_audio_ext(blob)

            def make_data(b: bytes = blob) -> bytes:
                return b

            self.result.assets.append(
                GameAsset(
                    id=f"audio:{i}",
                    name=name,
                    kind=AssetKind.AUDIO,
                    extension=ext,
                    size=len(blob),
                    _data_fn=make_data,
                    meta={"index": i, "filename": info_s.filename if info_s else name},
                )
            )

    def _crop_sprite_frame(self, tpag: TexturePageItem) -> Image.Image:
        w, h = max(1, tpag.width), max(1, tpag.height)
        if tpag.texture_id < 0 or tpag.texture_id >= len(self.result.textures):
            return Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sheet = self.result.get_texture_image(tpag.texture_id)
        if tpag.width <= 0 or tpag.height <= 0:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        box = (tpag.x, tpag.y, tpag.x + tpag.width, tpag.y + tpag.height)
        sw, sh = sheet.size
        box = (
            max(0, min(box[0], sw)),
            max(0, min(box[1], sh)),
            max(0, min(box[2], sw)),
            max(0, min(box[3], sh)),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            return Image.new("RGBA", (w, h), (0, 0, 0, 0))
        return sheet.crop(box)

    def _parse_sprites(self) -> None:
        info = self.result.chunks.get("SPRT")
        if not info:
            return
        start, size = info
        r = self.reader
        r.seek(start)
        count = r.read_u32()
        offsets = [r.read_u32() for _ in range(count)]
        tpag_map = self._tpag_by_offset()

        for index, off in enumerate(offsets):
            r.seek(off)
            name = r.read_offset_string() or f"sprite_{index}"
            width = r.read_i32()
            height = r.read_i32()
            _ml = r.read_i32()
            _mr = r.read_i32()
            _mb = r.read_i32()
            _mt = r.read_i32()
            r.skip(5 * 4)  # unknown[3], bbox mode, sep masks
            origin_x = r.read_i32()
            origin_y = r.read_i32()
            frame_count = r.read_i32()
            if frame_count < 0 or frame_count > 10_000:
                continue
            frame_ptrs = [r.read_u32() for _ in range(frame_count)]

            # Skip collision masks (best-effort) — not needed for export
            # Remaining data until next sprite is mask data.

            frames: list[TexturePageItem] = []
            for ptr in frame_ptrs:
                item = tpag_map.get(ptr)
                if item:
                    frames.append(item)

            if not frames:
                continue

            if index % 200 == 0:
                self._say(f"Indexing sprites… {index}/{count}")

            for fi, frame in enumerate(frames):
                frame_name = name if len(frames) == 1 else f"{name}_{fi}"

                def make_image(fr: TexturePageItem = frame) -> Image.Image:
                    return self._crop_sprite_frame(fr)

                # Estimate size roughly
                est = max(1, frame.width) * max(1, frame.height) * 4
                self.result.assets.append(
                    GameAsset(
                        id=f"sprite:{index}:{fi}",
                        name=frame_name,
                        kind=AssetKind.SPRITE,
                        extension=".png",
                        size=est,
                        _image_fn=make_image,
                        meta={
                            "sprite": name,
                            "frame": fi,
                            "width": width,
                            "height": height,
                            "origin": (origin_x, origin_y),
                        },
                    )
                )

    def _parse_backgrounds(self) -> None:
        info = self.result.chunks.get("BGND")
        if not info:
            return
        start, _ = info
        r = self.reader
        r.seek(start)
        count = r.read_u32()
        offsets = [r.read_u32() for _ in range(count)]
        tpag_map = self._tpag_by_offset()

        for index, off in enumerate(offsets):
            r.seek(off)
            name = r.read_offset_string() or f"background_{index}"
            r.skip(3 * 4)  # unknowns
            tpag_ptr = r.read_u32()
            item = tpag_map.get(tpag_ptr)
            if not item:
                continue

            def make_image(fr: TexturePageItem = item) -> Image.Image:
                return self._crop_sprite_frame(fr)

            self.result.assets.append(
                GameAsset(
                    id=f"background:{index}",
                    name=name,
                    kind=AssetKind.BACKGROUND,
                    extension=".png",
                    size=max(1, item.width) * max(1, item.height) * 4,
                    _image_fn=make_image,
                    meta={"index": index},
                )
            )

    def _parse_fonts(self) -> None:
        info = self.result.chunks.get("FONT")
        if not info:
            return
        start, _ = info
        r = self.reader
        r.seek(start)
        count = r.read_u32()
        offsets = [r.read_u32() for _ in range(count)]
        tpag_map = self._tpag_by_offset()

        for index, off in enumerate(offsets):
            try:
                r.seek(off)
                name = r.read_offset_string() or f"font_{index}"
                display = r.read_offset_string()
                _size = r.read_u32()
                _bold = r.read_u32()
                _italic = r.read_u32()
                # charset / antialias fields vary — try to find a TPAG pointer nearby
                # GMS1 font: after italic comes rangeStart (u16), rangeEnd (u16), charset?, then tpag ptr
                _range_start = r.read_u16()
                _range_end = r.read_u16()
                _charset = r.read_u32()
                tpag_ptr = r.read_u32()
                item = tpag_map.get(tpag_ptr)
                label = display or name
                if item:

                    def make_image(fr: TexturePageItem = item) -> Image.Image:
                        return self._crop_sprite_frame(fr)

                    self.result.assets.append(
                        GameAsset(
                            id=f"font:{index}",
                            name=safe_filename(label, f"font_{index}"),
                            kind=AssetKind.FONT,
                            extension=".png",
                            size=max(1, item.width) * max(1, item.height) * 4,
                            _image_fn=make_image,
                            meta={"font": name, "display": display},
                        )
                    )
            except Exception:
                continue

    def _parse_rooms(self) -> None:
        """Index ROOM chunk entries (name + id) for in-game teleport."""
        info = self.result.chunks.get("ROOM")
        if not info:
            return
        start, _ = info
        r = self.reader
        r.seek(start)
        count = r.read_u32()
        if count < 0 or count > 50_000:
            return
        offsets = [r.read_u32() for _ in range(count)]

        for index, off in enumerate(offsets):
            try:
                r.seek(off)
                name = r.read_offset_string() or f"room_{index}"
                width = r.read_i32()
                height = r.read_i32()
            except Exception:
                name = f"room_{index}"
                width = 0
                height = 0

            # Small label card used as a thumbnail in the browser.
            def make_image(
                label: str = name,
                rid: int = index,
            ) -> Image.Image:
                img = Image.new("RGBA", (160, 96), (28, 24, 20, 255))
                try:

                    draw = ImageDraw.Draw(img)
                    draw.rectangle((4, 4, 156, 92), outline=(196, 92, 38, 255), width=2)
                    pretty = label[5:] if label.lower().startswith("room_") else label
                    pretty = pretty.replace("_", " ")
                    draw.text((10, 14), f"ROOM {rid}", fill=(255, 250, 242, 255))
                    draw.text((10, 40), pretty[:22], fill=(232, 226, 214, 255))
                    draw.text((10, 68), "click to enter", fill=(196, 92, 38, 255))
                except Exception:
                    pass
                return img

            self.result.assets.append(
                GameAsset(
                    id=f"room:{index}",
                    name=name,
                    kind=AssetKind.ROOM,
                    extension="",
                    size=max(0, width) * max(0, height),
                    _image_fn=make_image,
                    meta={
                        "room_id": index,
                        "width": width,
                        "height": height,
                        "teleport": True,
                    },
                )
            )


def load_undertale_assets(
    path: str | Path,
    include_loose: bool = True,
    progress: Callable[[str], None] | None = None,
) -> ParsedArchive:
    """Load assets from a data.win path or Undertale install folder."""
    path = Path(path)
    data_file = find_data_file(path)
    if data_file is None:
        raise FileNotFoundError(
            "Could not find data.win (or game.unx). "
            "Select your Undertale install folder or the data file itself."
        )
    parser = DataWinParser(data_file, progress=progress)
    result = parser.parse()
    if include_loose:
        if progress:
            progress("Scanning loose music files…")
        loose = scan_loose_files(data_file.parent)
        # Avoid duplicates by name for music already in AUDO
        existing = {(a.name.lower(), a.kind) for a in result.assets}
        for asset in loose:
            key = (asset.name.lower(), asset.kind)
            if key not in existing:
                result.assets.append(asset)
    if progress:
        progress(f"Loaded {len(result.assets)} files — building browser…")
    return result


# --- gui.py ---

# Visual direction: ink-and-ember utility (not purple / cream-serif defaults)
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("dark-blue")

COLORS = {
    "bg": "#e8e2d6",
    "panel": "#f4efe6",
    "ink": "#1c1915",
    "muted": "#5c564c",
    "accent": "#c45c26",
    "accent_hover": "#a64b1c",
    "card": "#fffaf2",
    "card_hover": "#ffe8d2",
    "border": "#d2c8b6",
    "success": "#2f6b4f",
}

KIND_ORDER = [
    AssetKind.ROOM,
    AssetKind.SPRITE,
    AssetKind.TEXTURE,
    AssetKind.BACKGROUND,
    AssetKind.AUDIO,
    AssetKind.MUSIC,
    AssetKind.FONT,
    AssetKind.OTHER,
]

# Undertale has thousands of sprites — never build the whole grid at once.
PAGE_SIZE = 36


def _default_download_dir() -> Path:
    home = Path.home()
    for name in ("Downloads", "downloads"):
        candidate = home / name
        if candidate.is_dir():
            return candidate
    return home


class UndertaleExtractorApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Undertale File Extractor")
        self.geometry("1180x720")
        self.minsize(900, 560)
        self.configure(fg_color=COLORS["bg"])

        self.assets: list[GameAsset] = []
        self.filtered: list[GameAsset] = []
        self.selected: GameAsset | None = None
        self.current_kind: AssetKind | None = AssetKind.ROOM
        self.page = 0
        self.download_dir = _default_download_dir()
        self.save_dir = default_save_dir()
        self.data_win_path: Path | None = None
        self._live_room_addrs: list = []
        self._live_current_room: int | None = None
        self._thumb_cache: dict[str, ctk.CTkImage] = {}
        self._preview_image: ctk.CTkImage | None = None
        self._loading = False
        self.game_name = "Undertale"
        self._render_token = 0

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        header = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=0, height=72)
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        brand = ctk.CTkLabel(
            header,
            text="UNDERTALE",
            font=ctk.CTkFont(family="Courier New", size=26, weight="bold"),
            text_color=COLORS["ink"],
        )
        brand.grid(row=0, column=0, padx=(20, 8), pady=(14, 0), sticky="w")

        subtitle = ctk.CTkLabel(
            header,
            text="Game File Extractor",
            font=ctk.CTkFont(family="Georgia", size=14),
            text_color=COLORS["muted"],
        )
        subtitle.grid(row=1, column=0, padx=(22, 8), pady=(0, 12), sticky="w")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=2, rowspan=2, padx=16, pady=12, sticky="e")

        self.open_btn = ctk.CTkButton(
            actions,
            text="Open Undertale Folder",
            command=self.open_game,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#fffaf2",
            font=ctk.CTkFont(size=13, weight="bold"),
            width=180,
        )
        self.open_btn.pack(side="left", padx=4)

        self.export_all_btn = ctk.CTkButton(
            actions,
            text="Export All Visible",
            command=self.export_all_visible,
            fg_color=COLORS["ink"],
            hover_color="#33302b",
            text_color="#fffaf2",
            width=140,
            state="disabled",
        )
        self.export_all_btn.pack(side="left", padx=4)

        self.dl_dir_btn = ctk.CTkButton(
            actions,
            text="Download Folder…",
            command=self.choose_download_dir,
            fg_color=COLORS["border"],
            hover_color="#c4baa8",
            text_color=COLORS["ink"],
            width=130,
        )
        self.dl_dir_btn.pack(side="left", padx=4)

        self.save_dir_btn = ctk.CTkButton(
            actions,
            text="Save Folder…",
            command=self.choose_save_dir,
            fg_color=COLORS["border"],
            hover_color="#c4baa8",
            text_color=COLORS["ink"],
            width=110,
        )
        self.save_dir_btn.pack(side="left", padx=4)

        self.restore_btn = ctk.CTkButton(
            actions,
            text="Restore data.win",
            command=self.restore_data_win,
            fg_color=COLORS["border"],
            hover_color="#c4baa8",
            text_color=COLORS["ink"],
            width=130,
            state="disabled",
        )
        self.restore_btn.pack(side="left", padx=4)

        self.patch_btn = ctk.CTkButton(
            actions,
            text="Enable live patches",
            command=self.enable_live_patches,
            fg_color=COLORS["border"],
            hover_color="#c4baa8",
            text_color=COLORS["ink"],
            width=140,
            state="disabled",
        )
        self.patch_btn.pack(side="left", padx=4)

        self.launch_btn = ctk.CTkButton(
            actions,
            text="Launch Undertale",
            command=self.launch_patched_undertale,
            fg_color=COLORS["success"],
            hover_color="#24553e",
            text_color="#fffaf2",
            width=140,
            state="disabled",
        )
        self.launch_btn.pack(side="left", padx=4)

        self.toolkit_btn = ctk.CTkButton(
            actions,
            text="Debug Toolkit",
            command=self.open_debug_toolkit,
            fg_color=COLORS["ink"],
            hover_color="#33302b",
            text_color="#fffaf2",
            width=120,
            state="disabled",
        )
        self.toolkit_btn.pack(side="left", padx=4)
        self._toolkit: DebugToolkit | None = None

        # Sidebar categories
        sidebar = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=0, width=200)
        sidebar.grid(row=1, column=0, sticky="nsw")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar,
            text="Categories",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["muted"],
        ).pack(anchor="w", padx=16, pady=(16, 8))

        self.kind_buttons: dict[AssetKind | None, ctk.CTkButton] = {}
        all_btn = self._make_kind_button(sidebar, "All files", None)
        all_btn.pack(fill="x", padx=12, pady=3)
        self.kind_buttons[None] = all_btn
        for kind in KIND_ORDER:
            btn = self._make_kind_button(sidebar, kind.value, kind)
            btn.pack(fill="x", padx=12, pady=3)
            self.kind_buttons[kind] = btn

        self.count_label = ctk.CTkLabel(
            sidebar,
            text="No files loaded",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
            wraplength=170,
            justify="left",
        )
        self.count_label.pack(anchor="w", padx=16, pady=20)

        # Main browser
        main = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        main.grid(row=1, column=1, sticky="nsew", padx=0)
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        search_row = ctk.CTkFrame(main, fg_color="transparent")
        search_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        search_row.grid_columnconfigure(0, weight=1)

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.apply_filter())
        self.search_entry = ctk.CTkEntry(
            search_row,
            textvariable=self.search_var,
            placeholder_text="Search files…",
            fg_color=COLORS["card"],
            border_color=COLORS["border"],
            text_color=COLORS["ink"],
            height=36,
        )
        self.search_entry.grid(row=0, column=0, sticky="ew")

        self.status_label = ctk.CTkLabel(
            search_row,
            text="Open your Undertale install folder to begin",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
        )
        self.status_label.grid(row=1, column=0, sticky="w", pady=(6, 0))

        pager = ctk.CTkFrame(search_row, fg_color="transparent")
        pager.grid(row=0, column=1, rowspan=2, padx=(12, 0))
        self.prev_btn = ctk.CTkButton(
            pager,
            text="◀ Prev",
            width=80,
            command=self.prev_page,
            fg_color=COLORS["border"],
            hover_color="#c4baa8",
            text_color=COLORS["ink"],
            state="disabled",
        )
        self.prev_btn.pack(side="left", padx=2)
        self.page_label = ctk.CTkLabel(
            pager,
            text="Page 0/0",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
            width=90,
        )
        self.page_label.pack(side="left", padx=4)
        self.next_btn = ctk.CTkButton(
            pager,
            text="Next ▶",
            width=80,
            command=self.next_page,
            fg_color=COLORS["border"],
            hover_color="#c4baa8",
            text_color=COLORS["ink"],
            state="disabled",
        )
        self.next_btn.pack(side="left", padx=2)

        self.scroll = ctk.CTkScrollableFrame(
            main,
            fg_color=COLORS["bg"],
            corner_radius=0,
        )
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)

        # Preview / download panel
        preview = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=0, width=280)
        preview.grid(row=1, column=2, sticky="nse")
        preview.grid_propagate(False)

        ctk.CTkLabel(
            preview,
            text="Preview",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["muted"],
        ).pack(anchor="w", padx=16, pady=(16, 8))

        self.preview_canvas = ctk.CTkLabel(
            preview,
            text="Select a file",
            width=240,
            height=240,
            fg_color=COLORS["card"],
            corner_radius=8,
            text_color=COLORS["muted"],
        )
        self.preview_canvas.pack(padx=16, pady=8)

        self.preview_name = ctk.CTkLabel(
            preview,
            text="",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["ink"],
            wraplength=240,
            justify="left",
        )
        self.preview_name.pack(anchor="w", padx=16, pady=(8, 2))

        self.preview_meta = ctk.CTkLabel(
            preview,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
            wraplength=240,
            justify="left",
        )
        self.preview_meta.pack(anchor="w", padx=16, pady=(0, 12))

        self.download_btn = ctk.CTkButton(
            preview,
            text="Download",
            command=self.download_selected,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#fffaf2",
            state="disabled",
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.download_btn.pack(fill="x", padx=16, pady=4)

        self.teleport_btn = ctk.CTkButton(
            preview,
            text="Enter Room In-Game",
            command=self.teleport_selected,
            fg_color=COLORS["success"],
            hover_color="#24553f",
            text_color="#fffaf2",
            state="disabled",
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.teleport_btn.pack(fill="x", padx=16, pady=4)

        self.save_as_btn = ctk.CTkButton(
            preview,
            text="Save As…",
            command=self.save_selected_as,
            fg_color=COLORS["ink"],
            hover_color="#33302b",
            text_color="#fffaf2",
            state="disabled",
            height=36,
        )
        self.save_as_btn.pack(fill="x", padx=16, pady=4)

        save_hint = "No Undertale save found yet"
        if self.save_dir:
            save_hint = f"Game save:\n{self.save_dir}"
        self.save_path_label = ctk.CTkLabel(
            preview,
            text=save_hint,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
            wraplength=240,
            justify="left",
        )
        self.save_path_label.pack(anchor="w", padx=16, pady=(8, 0))

        self.dl_path_label = ctk.CTkLabel(
            preview,
            text=f"Downloads to:\n{self.download_dir}",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
            wraplength=240,
            justify="left",
        )
        self.dl_path_label.pack(anchor="w", padx=16, pady=16)

        self._highlight_kind(AssetKind.ROOM)
        self._show_empty_state()

    def _make_kind_button(self, parent, label: str, kind: AssetKind | None) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=label,
            anchor="w",
            fg_color="transparent",
            hover_color=COLORS["card_hover"],
            text_color=COLORS["ink"],
            command=lambda: self.set_kind(kind),
            height=34,
        )

    def _highlight_kind(self, kind: AssetKind | None) -> None:
        for k, btn in self.kind_buttons.items():
            if k == kind:
                btn.configure(fg_color=COLORS["card_hover"], text_color=COLORS["accent"])
            else:
                btn.configure(fg_color="transparent", text_color=COLORS["ink"])

    def _show_empty_state(self) -> None:
        for child in self.scroll.winfo_children():
            child.destroy()
        tip = ctk.CTkLabel(
            self.scroll,
            text=(
                "1. Click “Open Undertale Folder”\n"
                "2. Choose the folder that contains data.win\n"
                "3. Open the Rooms category\n"
                "4. Keep Undertale running, then click a room to enter it live\n"
                "5. Or browse sprites/audio and click to download"
            ),
            font=ctk.CTkFont(family="Georgia", size=16),
            text_color=COLORS["muted"],
            justify="left",
        )
        tip.pack(anchor="w", padx=24, pady=40)

    def open_game(self) -> None:
        if self._loading:
            return
        path = filedialog.askdirectory(title="Select Undertale install folder")
        if not path:
            file_path = filedialog.askopenfilename(
                title="Or select data.win",
                filetypes=[
                    ("Undertale data.win", "*.win"),
                    ("All files", "*.*"),
                ],
            )
            if not file_path:
                return
            path = file_path
        self._load_async(path)

    def _set_status(self, message: str) -> None:
        self.status_label.configure(text=message)

    def _load_async(self, path: str) -> None:
        self._loading = True
        self.open_btn.configure(state="disabled")
        self.export_all_btn.configure(state="disabled")
        self._set_status("Starting… this can take a minute for Undertale")
        for child in self.scroll.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self.scroll,
            text="Extracting game files…\nPlease wait — do not close the window.",
            font=ctk.CTkFont(family="Georgia", size=16),
            text_color=COLORS["muted"],
            justify="center",
        ).pack(pady=60)
        self.update_idletasks()

        def report(message: str) -> None:
            # Bind message as default arg so later updates don't overwrite earlier ones.
            self.after(0, lambda m=message: self._set_status(m))

        def work() -> None:
            try:
                result = load_undertale_assets(path, progress=report)
                self.after(0, lambda r=result: self._on_loaded(r))
            except MemoryError:
                self.after(
                    0,
                    lambda: self._on_load_error(
                        MemoryError(
                            "Ran out of memory while reading data.win. "
                            "Close other programs and try again."
                        )
                    ),
                )
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_load_error(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_loaded(self, result) -> None:
        try:
            self._loading = False
            self.open_btn.configure(state="normal")
            self.assets = result.assets
            self.data_win_path = Path(result.path)
            self.game_name = result.game_name or "Undertale"
            self.title(f"Undertale File Extractor — {self.game_name}")
            self._thumb_cache.clear()
            self._live_room_addrs = []
            self.export_all_btn.configure(state="normal")
            self.restore_btn.configure(state="normal")
            self.patch_btn.configure(state="normal")
            self.launch_btn.configure(state="normal")
            self.toolkit_btn.configure(state="normal")
            self.page = 0
            self._update_counts()
            # Do NOT patch data.win on open — that rewrote the install file and could
            # stop Undertale launching while / after this app loaded the folder.
            # Live teleport applies debug + dogcheck patches only when you click a room.
            try:
                if self.save_dir:
                    info = read_save_info(self.save_dir)
                    self._live_current_room = info.current_room
            except Exception:
                pass
            # Default to Rooms so teleporting is one click away.
            self.set_kind(AssetKind.ROOM)
            running = (
                "Undertale is open — click a room to force-load (works in cutscenes/battles)."
                if undertale_is_running()
                else "Start Undertale anytime — browsing does not lock or patch data.win."
            )
            warn = ""
            try:
                if dogcheck_exit_stubbed(self.data_win_path):
                    warn = (
                        " WARNING: broken dogcheck patch detected — click Restore data.win "
                        "or Enable live patches (auto-heals) before using live Load (L)."
                    )
                    messagebox.showwarning(
                        "Broken dogcheck patch",
                        "Your data.win has an old dogcheck Exit stub that crashes Undertale "
                        "when pressing L (debug load):\n\n"
                        "Variable obj_mainchara.dogcheck not set…\n\n"
                        "Close Undertale, then click Restore data.win "
                        "(or Enable live patches to auto-heal from backup).",
                    )
            except Exception:
                pass
            self._set_status(
                f"Loaded {len(self.assets)} files from {result.path.name}. {running} "
                f"Use Enable live patches (game closed) for room jumps.{warn}"
            )
        except Exception as exc:
            self._on_load_error(exc)

    def restore_data_win(self) -> None:
        if not self.data_win_path:
            messagebox.showinfo("No game open", "Open your Undertale folder first.")
            return
        if undertale_is_running():
            messagebox.showwarning(
                "Close Undertale first",
                "Close Undertale completely, then click Restore data.win again.",
            )
            return
        ok = messagebox.askokcancel(
            "Restore data.win?",
            "Replace data.win with the extractor backup "
            "(prefers data.win.roomchaosbak, then rarebak / dogcheckbak / debugbak).\n\n"
            "Use this if Undertale crashes on boot (Code Error / ossafe_file_text_eof), "
            "crashes on L with a dogcheck error, or will not start after patching.",
        )
        if not ok:
            return
        success, msg = restore_data_win_backup(self.data_win_path)
        if success:
            messagebox.showinfo("Restored", msg)
            self._set_status(msg)
        else:
            messagebox.showerror("Restore failed", msg)

    def enable_live_patches(self) -> None:
        """Patch data.win for live room jumps — only while Undertale is closed."""
        if not self.data_win_path:
            messagebox.showinfo("No game open", "Open your Undertale folder first.")
            return
        if undertale_is_running():
            messagebox.showwarning(
                "Close Undertale first",
                "Undertale must be fully closed before patching data.win.\n"
                "Close the game, click Enable live patches, then start Undertale again.",
            )
            return

        debug_ok = False
        dog_ok = False
        notes: list[str] = []

        try:
            debug_ok = bool(enable_debug_mode(self.data_win_path, backup=True))
            notes.append(
                "Debug Load (L): ON" if debug_ok else "Debug Load (L): could not enable"
            )
        except OSError as exc:
            messagebox.showerror(
                "Could not patch",
                f"Windows blocked writing data.win:\n{exc}\n\n"
                "Close Undertale/Steam overlays and try again.",
            )
            return
        except Exception as exc:
            notes.append(f"Debug Load (L): failed ({exc})")

        try:
            dog_ok, dog_msg = disable_dogcheck(self.data_win_path, backup=True)
            # Re-verify on disk so we never claim success incorrectly.
            dog_ok = dog_ok and dogcheck_likely_disabled(self.data_win_path)
            if dog_ok:
                notes.append("Dogcheck (Annoying Dog): OFF")
            else:
                notes.append("Dogcheck (Annoying Dog): STILL ON")
                notes.append(dog_msg)
        except OSError as exc:
            messagebox.showerror("Could not patch", str(exc))
            return
        except Exception as exc:
            notes.append(f"Dogcheck: failed ({exc})")

        body = "\n".join(f"• {n}" for n in notes)
        body += (
            "\n\nClose this dialog, start Undertale, load your save, then click a room."
            "\nBackup: data.win.dogcheckbak / data.win.debugbak"
        )

        if debug_ok and dog_ok:
            messagebox.showinfo("Live patches ready", body)
            self._set_status("Live patches OK — debug ON, dogcheck OFF. Restart Undertale.")
        elif debug_ok and not dog_ok:
            messagebox.showwarning(
                "Dogcheck still ON",
                body
                + "\n\nTeleport works, but the Annoying Dog will still appear on "
                "blocked/secret rooms.\n\n"
                "Try: Restore data.win → Enable live patches again.\n"
                "Or use UndertaleModTool → Scripts → DisableDogcheck.",
            )
            self._set_status("Debug ON, but dogcheck still ON — dog may still appear.")
        else:
            messagebox.showerror("Patch incomplete", body)
            self._set_status("Live patches incomplete — see the error dialog.")

    def launch_patched_undertale(self) -> None:
        if not self.data_win_path:
            messagebox.showinfo("No game open", "Open your Undertale folder first.")
            return
        if undertale_is_running():
            messagebox.showinfo("Already running", "Undertale is already open.")
            return
        # Best-effort patches that keep the game launchable.
        try:
            enable_debug_mode(self.data_win_path, backup=True)
        except Exception:
            pass
        try:
            disable_dogcheck(self.data_win_path, backup=True)
        except Exception:
            pass
        ok, msg = launch_undertale(data_win=self.data_win_path)
        if ok:
            messagebox.showinfo(
                "Launched",
                msg
                + "\n\nClick Continue on the title screen.\n"
                "Open Debug Toolkit for stats, items, and fights.",
            )
            self._set_status(msg)
        else:
            messagebox.showerror(
                "Launch failed",
                msg
                + "\n\nIf the game will not start after dogcheck patches, "
                "click Restore data.win, then Launch again.",
            )

    def open_debug_toolkit(self) -> None:
        if self._toolkit is not None and self._toolkit.winfo_exists():
            self._toolkit.focus()
            return
        self._toolkit = DebugToolkit(
            self,
            data_win=self.data_win_path,
            save_dir=self.save_dir,
            on_status=self._set_status,
        )

    def _on_load_error(self, exc: Exception) -> None:
        self._loading = False
        self.open_btn.configure(state="normal")
        self._set_status("Failed to load")
        messagebox.showerror("Could not open game", str(exc))

    def _update_counts(self) -> None:
        counts: dict[AssetKind, int] = {k: 0 for k in KIND_ORDER}
        for a in self.assets:
            counts[a.kind] = counts.get(a.kind, 0) + 1
        lines = [f"Total: {len(self.assets)}"]
        for kind in KIND_ORDER:
            if counts.get(kind):
                lines.append(f"{kind.value}: {counts[kind]}")
        self.count_label.configure(text="\n".join(lines))
        for kind, btn in self.kind_buttons.items():
            if kind is None:
                btn.configure(text=f"All files ({len(self.assets)})")
            else:
                btn.configure(text=f"{kind.value} ({counts.get(kind, 0)})")

    def set_kind(self, kind: AssetKind | None) -> None:
        self.current_kind = kind
        self.page = 0
        self._highlight_kind(kind)
        self.apply_filter()

    def apply_filter(self) -> None:
        if self._loading and not self.assets:
            return
        query = self.search_var.get().strip().lower()
        items = self.assets
        if self.current_kind is not None:
            items = [a for a in items if a.kind == self.current_kind]
        if query:
            items = [
                a
                for a in items
                if query in a.display_name.lower() or query in a.id.lower()
            ]
        self.filtered = items
        pages = max(1, (len(self.filtered) + PAGE_SIZE - 1) // PAGE_SIZE) if self.filtered else 0
        if self.page >= pages and pages > 0:
            self.page = pages - 1
        self._render_list()

    def prev_page(self) -> None:
        if self.page > 0:
            self.page -= 1
            self._render_list()

    def next_page(self) -> None:
        pages = max(1, (len(self.filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
        if self.page + 1 < pages:
            self.page += 1
            self._render_list()

    def _render_list(self) -> None:
        self._render_token += 1
        token = self._render_token
        for child in self.scroll.winfo_children():
            child.destroy()

        total = len(self.filtered)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else 0
        if total == 0:
            self.page_label.configure(text="Page 0/0")
            self.prev_btn.configure(state="disabled")
            self.next_btn.configure(state="disabled")
            empty = ctk.CTkLabel(
                self.scroll,
                text="No files match this filter",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=14),
            )
            empty.pack(pady=40)
            return

        start = self.page * PAGE_SIZE
        end = min(start + PAGE_SIZE, total)
        page_items = self.filtered[start:end]
        self.page_label.configure(text=f"Page {self.page + 1}/{pages}")
        self.prev_btn.configure(state="normal" if self.page > 0 else "disabled")
        self.next_btn.configure(state="normal" if self.page + 1 < pages else "disabled")

        hint = ctk.CTkLabel(
            self.scroll,
            text=f"Showing {start + 1}–{end} of {total}  ·  click a file to download",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
        )
        hint.grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 8))

        cols = 3
        # Build tiles in small batches so the window stays responsive.
        self._build_tiles_batch(page_items, cols, 0, token)

    def _build_tiles_batch(
        self,
        page_items: list[GameAsset],
        cols: int,
        index: int,
        token: int,
        batch: int = 6,
    ) -> None:
        if token != self._render_token:
            return
        end = min(index + batch, len(page_items))
        for i in range(index, end):
            asset = page_items[i]
            row, col = divmod(i, cols)
            tile = self._make_tile(self.scroll, asset)
            tile.grid(row=row + 1, column=col, padx=8, pady=8, sticky="nsew")
        for c in range(cols):
            self.scroll.grid_columnconfigure(c, weight=1)
        if end < len(page_items):
            self.after(
                1,
                lambda: self._build_tiles_batch(page_items, cols, end, token, batch),
            )

    def _make_tile(self, parent, asset: GameAsset) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(
            parent,
            fg_color=COLORS["card"],
            corner_radius=10,
            border_width=1,
            border_color=COLORS["border"],
            width=220,
            height=150,
        )
        frame.grid_propagate(False)

        # Lightweight placeholder first — real thumbnail filled later.
        thumb_label = ctk.CTkLabel(
            frame,
            text=asset.extension.upper().lstrip(".") or "FILE",
            width=88,
            height=72,
            fg_color=COLORS["panel"],
            corner_radius=6,
            text_color=COLORS["accent"],
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        thumb_label.pack(pady=(10, 4))

        if asset.is_room:
            rid = int(asset.meta.get("room_id", 0))
            display = friendly_room_label(asset.name, rid)
            if is_dogcheck_room(rid):
                display = f"{display} ⚠"
        else:
            display = asset.display_name
        name_label = ctk.CTkLabel(
            frame,
            text=display[:28] + ("…" if len(display) > 28 else ""),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["ink"],
        )
        name_label.pack()

        meta = ctk.CTkLabel(
            frame,
            text=(
                f"Room ID {asset.meta.get('room_id')} · click to enter"
                if asset.is_room
                else f"{asset.kind.value} · {_fmt_size(asset.size)}"
            ),
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        )
        meta.pack(pady=(0, 8))

        def on_click(_event=None, a: GameAsset = asset) -> None:
            self.select_asset(a)
            if a.is_room:
                self.teleport_selected()
            else:
                self.download_selected()

        def on_select(_event=None, a: GameAsset = asset) -> None:
            self.select_asset(a)

        for widget in (frame, thumb_label, name_label, meta):
            widget.bind("<Button-1>", on_click)
            widget.bind("<Button-3>", on_select)
            widget.bind("<Double-Button-1>", on_click)

        if asset.is_image:
            self.after(10, lambda: self._fill_thumb(thumb_label, asset))
        else:
            badge = {
                AssetKind.AUDIO: "♪ AUDIO",
                AssetKind.MUSIC: "♫ MUSIC",
                AssetKind.FONT: "Aa FONT",
                AssetKind.ROOM: "DOOR",
                AssetKind.OTHER: "FILE",
            }.get(asset.kind, asset.extension.upper() or "FILE")
            thumb_label.configure(text=badge)
        return frame

    def _fill_thumb(self, label: ctk.CTkLabel, asset: GameAsset) -> None:
        if not label.winfo_exists():
            return
        try:
            if asset.id in self._thumb_cache:
                label.configure(image=self._thumb_cache[asset.id], text="")
                return
            thumb = asset.thumbnail(72)
            if thumb is None:
                return
            img = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=thumb.size)
            self._thumb_cache[asset.id] = img
            if label.winfo_exists():
                label.configure(image=img, text="")
        except Exception:
            if label.winfo_exists():
                label.configure(text="?", text_color=COLORS["muted"])

    def select_asset(self, asset: GameAsset) -> None:
        self.selected = asset
        if asset.is_room:
            room_id = int(asset.meta.get("room_id", -1))
            title = friendly_room_label(asset.name, room_id)
            if is_dogcheck_room(room_id):
                title = f"{title}  ⚠ dog"
            self.preview_name.configure(text=title)
            self.preview_meta.configure(
                text=(
                    f"Room ID {room_id}\n"
                    f"Size {asset.meta.get('width', '?')}×{asset.meta.get('height', '?')}\n"
                    + (
                        "Dogcheck room — Annoying Dog until patches disable it\n"
                        if is_dogcheck_room(room_id)
                        else ""
                    )
                    + "Click to enter while Undertale is open"
                )
            )
            self.download_btn.configure(state="disabled")
            self.save_as_btn.configure(state="disabled")
            self.teleport_btn.configure(state="normal")
        else:
            self.preview_name.configure(text=asset.display_name)
            self.preview_meta.configure(
                text=f"{asset.kind.value}\n{_fmt_size(asset.size)}\nClick Download to save"
            )
            self.download_btn.configure(state="normal")
            self.save_as_btn.configure(state="normal")
            self.teleport_btn.configure(state="disabled")

        try:
            if asset.is_image:
                img = asset.get_image()
                if img:
                    preview = img.copy()
                    preview.thumbnail((220, 220), Image.Resampling.NEAREST)
                    ctk_img = ctk.CTkImage(
                        light_image=preview,
                        dark_image=preview,
                        size=preview.size,
                    )
                    self._preview_image = ctk_img
                    self.preview_canvas.configure(image=ctk_img, text="")
                    return
            if asset.is_audio:
                self._preview_image = None
                self.preview_canvas.configure(image=None, text=f"Audio\n{asset.extension}")
                return
            self._preview_image = None
            self.preview_canvas.configure(image=None, text=asset.extension or "File")
        except Exception as exc:
            self.preview_canvas.configure(image=None, text=f"Preview failed\n{exc}")

    def teleport_selected(self) -> None:
        if not self.selected or not self.selected.is_room:
            return
        room_id = int(self.selected.meta.get("room_id", -1))
        if room_id < 0:
            messagebox.showerror("Teleport failed", "This room has no valid id.")
            return

        label = friendly_room_label(self.selected.name, room_id)
        if is_dogcheck_room(room_id):
            label = f"{label}  (dogcheck room)"

        # Prefer live teleport while Undertale is running.
        if undertale_is_running():
            if is_dogcheck_room(room_id):
                cont = messagebox.askokcancel(
                    "Dogcheck room",
                    f"{label}\n\n"
                    "Vanilla Undertale blocks this room with the Annoying Dog "
                    "unless dogcheck is disabled.\n\n"
                    "If you still see the dog: close Undertale → Enable live patches "
                    "→ restart the game.\n\n"
                    "Jump anyway?",
                )
                if not cont:
                    return
            self._set_status(f"Jumping to {label} in the open game…")
            self.update_idletasks()
            try:
                result, self._live_room_addrs = live_teleport_to_room(
                    room_id,
                    save_folder=self.save_dir,
                    data_win=self.data_win_path,
                )
            except Exception as exc:
                messagebox.showerror("Live teleport failed", str(exc))
                return

            if result.ok:
                self._live_current_room = room_id
                self._set_status(result.detail)
                self.preview_meta.configure(
                    text=f"Entered room {room_id}\n{self.selected.name}\n(live load)"
                )
                return

            if result.method in {"restart_required", "patches_required", "broken_dogcheck"}:
                title = (
                    "Broken dogcheck patch"
                    if result.method == "broken_dogcheck"
                    else "Enable live patches first"
                )
                messagebox.showinfo(title, result.detail)
                self._set_status(result.detail)
                return

            # Live failed — ask about save fallback
            fallback = messagebox.askyesno(
                "Could not jump live",
                f"{result.detail}\n\n"
                "Update your save file instead?\n"
                "(Then use Undertale title screen → Continue)\n\n"
                f"Target: {label}",
            )
            if not fallback:
                self._set_status(result.detail)
                return
        else:
            ok = messagebox.askokcancel(
                "Enter room?",
                (
                    f"Undertale is not running.\n\n"
                    f"Set save to:\n{label}\n\n"
                    "Then open Undertale → Continue.\n\n"
                    "Tip: leave Undertale open next time to jump live."
                ),
            )
            if not ok:
                return

        if self.save_dir is None:
            picked = filedialog.askdirectory(
                title="Select Undertale save folder (contains file0)"
            )
            if not picked:
                return
            self.save_dir = Path(picked)
            self.save_path_label.configure(text=f"Game save:\n{self.save_dir}")

        try:
            info = teleport_to_room(room_id, self.save_dir)
            self._live_current_room = room_id
            self._set_status(
                f"Save updated → room {room_id}. Open Undertale and press Continue."
            )
            self.preview_meta.configure(
                text=(
                    f"Save set to room {room_id}\n"
                    f"{info.folder}\n"
                    "Title screen → Continue"
                )
            )
            messagebox.showinfo(
                "Room set in save",
                f"Save now points to room {room_id}.\n\n"
                "Open Undertale → Continue.\n\n"
                "For live jumps, start Undertale first, then click rooms here.",
            )
        except Exception as exc:
            messagebox.showerror("Teleport failed", str(exc))

    def download_selected(self) -> None:
        if not self.selected:
            return
        if self.selected.is_room:
            self.teleport_selected()
            return
        try:
            path = self.selected.export_to(self.download_dir, overwrite=False)
            self._set_status(f"Downloaded → {path}")
            self.preview_meta.configure(
                text=f"{self.selected.kind.value}\nSaved to:\n{path}"
            )
        except Exception as exc:
            messagebox.showerror("Download failed", str(exc))

    def save_selected_as(self) -> None:
        if not self.selected:
            return
        initial = self.selected.display_name
        path = filedialog.asksaveasfilename(
            title="Save file as",
            initialfile=initial,
            defaultextension=self.selected.extension or "",
            initialdir=str(self.download_dir),
        )
        if not path:
            return
        try:
            Path(path).write_bytes(self.selected.get_data())
            self._set_status(f"Saved → {path}")
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))

    def export_all_visible(self) -> None:
        if not self.filtered:
            return
        dest = filedialog.askdirectory(title="Choose folder for all visible files")
        if not dest:
            return
        dest_path = Path(dest)
        ok = 0
        errors = 0
        total = len(self.filtered)
        for i, asset in enumerate(self.filtered, start=1):
            try:
                sub = dest_path / asset.kind.value.lower()
                asset.export_to(sub, overwrite=False)
                ok += 1
            except Exception:
                errors += 1
            if i % 25 == 0:
                self._set_status(f"Exporting… {i}/{total}")
                self.update_idletasks()
        self._set_status(
            f"Exported {ok} files" + (f" ({errors} failed)" if errors else "")
        )
        messagebox.showinfo("Export complete", f"Saved {ok} files to:\n{dest_path}")

    def choose_download_dir(self) -> None:
        path = filedialog.askdirectory(
            title="Choose download folder", initialdir=str(self.download_dir)
        )
        if path:
            self.download_dir = Path(path)
            self.dl_path_label.configure(text=f"Downloads to:\n{self.download_dir}")

    def choose_save_dir(self) -> None:
        initial = str(self.save_dir) if self.save_dir else str(Path.home())
        path = filedialog.askdirectory(
            title="Select Undertale save folder (contains file0)",
            initialdir=initial,
        )
        if not path:
            # Offer known saves if any
            known = find_undertale_save_dirs()
            if known:
                self.save_dir = known[0]
                self.save_path_label.configure(text=f"Game save:\n{self.save_dir}")
                self._set_status(f"Using save folder {self.save_dir}")
            return
        folder = Path(path)
        if not (folder / "file0").is_file():
            messagebox.showwarning(
                "No file0 here",
                "That folder has no file0.\n"
                r"Typical path: %LOCALAPPDATA%\UNDERTALE",
            )
        self.save_dir = folder
        self.save_path_label.configure(text=f"Game save:\n{self.save_dir}")
        try:
            info = read_save_info(folder)
            extra = f" (currently room {info.current_room})" if info.current_room is not None else ""
            self._set_status(f"Save folder set{extra}")
        except Exception:
            self._set_status("Save folder set")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def run_app() -> None:
    app = UndertaleExtractorApp()
    app.mainloop()


if __name__ == "__main__":
    run_app()



def main(argv=None):
    argp = argparse.ArgumentParser(description="Undertale File Extractor")
    argp.add_argument("path", nargs="?")
    argp.add_argument("--extract-all", metavar="OUT_DIR")
    argp.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = argp.parse_args(argv)
    if args.extract_all:
        if not args.path:
            return 2
        result = load_undertale_assets(args.path)
        out = Path(args.extract_all)
        for asset in result.assets:
            if asset.is_room:
                continue
            print(asset.export_to(out / asset.kind.value.lower(), overwrite=False))
        return 0
    app = UndertaleExtractorApp()
    if args.path:
        app.after(200, lambda: app._load_async(args.path))
    app.mainloop()
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        try:
            messagebox.showerror("Undertale Extractor crashed", str(exc))
        except Exception:
            print(exc)
        raise
