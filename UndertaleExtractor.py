"""
Undertale File Extractor
========================
Browse files and click Rooms to enter them while Undertale is open.

Open the folder to browse (does not lock or patch data.win).
Use Enable live patches (Undertale closed) for live room jumps.
Use Restore data.win if the game will not start after patching.

Windows: pip install Pillow customtkinter
         python UndertaleExtractor.py
"""

from __future__ import annotations

import argparse
import ctypes
import io
import os
import re
import shutil
import struct
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

__version__ = "1.5.0"


# --- assets.py ---

from typing import Callable



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

# Classic HxD patches (Marxvee) — only applied when the offset falls inside
# scr_dogcheck bytecode (so we know we are not patching random data).
MARXVEE_PATCHES: tuple[tuple[int, bytes], ...] = (
    (0x7213E4, bytes.fromhex("000100B7")),  # Undertale 1.00
    (0x7216D4, bytes.fromhex("000100B7")),  # Undertale 1.001
)

# Steam / newer builds (UndertaleModTool maintainers).
STEAM_BYTE_PATCHES: tuple[tuple[int, int, int], ...] = (
    (0x76DF44, 0x01, 0x00),
    (0x76E058, 0x00, 0x01),
    (0x77473C, 0x01, 0x00),
)

# Bytecode opcodes (high byte of each instruction word).
# v15 / v14 (see UndertaleModTool bytecode wiki).
OP_PUSHI_V15 = 0x84
OP_PUSH = 0xC0
OP_POP_V15 = 0x45
OP_POP_V14 = 0x41
OP_EXIT_V15 = 0x9D
OP_EXIT_V14 = 0x9E

EXIT_WORD_V15 = struct.pack("<I", 0x9D000000)
EXIT_WORD_V14 = struct.pack("<I", 0x9E000000)
# Back-compat alias used by tests / heal detection (v15 Exit).
EXIT_WORD = EXIT_WORD_V15

DOGCHECK_NAMES = frozenset(
    {
        "gml_Script_scr_dogcheck",
        "scr_dogcheck",
        "gml_Script_dogcheck",
    }
)

# Rooms that vanilla dogcheck rejects (Undertale wiki / community lists).
# Teleporting here shows the Annoying Dog unless dogcheck is disabled.
DOGCHECK_ROOM_RANGES: tuple[tuple[int, int], ...] = (
    (0, 3),
    (78, 80),
    (239, 241),
    (266, 335),
)

BACKUP_SUFFIXES = (".dogcheckbak", ".debugbak", ".bak")


def is_dogcheck_room(room_id: int) -> bool:
    """True if vanilla Undertale sends this room id to the Annoying Dog."""
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
    """Restore data.win from the newest extractor backup. Close Undertale first."""
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


def _find_code_entries(reader: BinaryReader) -> list[tuple[str, int, int]]:
    """Return list of (name, bytecode_abs_offset, length) for CODE entries."""
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
                entries.append((name, bytecode_abs, length))
                continue

            bc14_start = off + 8
            if bc14_start + length <= reader.size:
                entries.append((name, bc14_start, length))
        except Exception:
            continue
    return entries


def _dogcheck_entries(data: bytes) -> list[tuple[str, int, int]]:
    reader = BinaryReader(data)
    return [
        e
        for e in _find_code_entries(reader)
        if e[0] in DOGCHECK_NAMES or e[0].lower().endswith("dogcheck")
    ]


def _keep_bytes_for_dogcheck_assign(data: bytes, bc_off: int, length: int) -> tuple[int, bytes] | None:
    """
    Return (keep_len, exit_word) for a safe stub:
      dogcheck = 1;  // keep these instructions
      exit;          // then never room_goto(room_of_dog)
    """
    if length < 16:
        return None
    w0 = struct.unpack_from("<I", data, bc_off)[0]
    op0 = _opcode(w0)

    # Bytecode 15: pushi.e 1 ; pop.v.v self.dogcheck
    if op0 == OP_PUSHI_V15:
        # Int16 value sits in the low 16 bits for PushI
        if (w0 & 0xFFFF) != 1 and ((w0 >> 8) & 0xFFFF) != 1:
            # Still accept PushI as the start of dogcheck=1 on odd encodings
            pass
        w1 = struct.unpack_from("<I", data, bc_off + 4)[0]
        if _opcode(w1) != OP_POP_V15:
            return None
        return 4 + 8, EXIT_WORD_V15  # PushI (1 word) + Pop (2 words)

    # Bytecode 14: push.e 1 ; pop.v.v self.dogcheck
    if op0 == OP_PUSH:
        # Int16 push is often 1 word; variable push is 2 — dogcheck=1 uses int16.
        w1 = struct.unpack_from("<I", data, bc_off + 4)[0]
        if _opcode(w1) == OP_POP_V14:
            return 4 + 8, EXIT_WORD_V14
        # Rare: push takes 2 words then pop
        if length >= 20:
            w2 = struct.unpack_from("<I", data, bc_off + 8)[0]
            if _opcode(w2) == OP_POP_V14:
                return 8 + 8, EXIT_WORD_V14
        return None

    return None


def _apply_safe_code_stub(data: bytearray) -> list[str]:
    """
    Rewrite scr_dogcheck to: dogcheck = 1; exit;

    Unlike a bare Exit at offset 0, this keeps the variable assignment so
    scr_load / debug L does not crash.
    """
    applied = []
    for name, bc_off, length in _dogcheck_entries(bytes(data)):
        info = _keep_bytes_for_dogcheck_assign(bytes(data), bc_off, length)
        if info is None:
            continue
        keep, exit_word = info
        if keep >= length:
            continue
        # Already safely stubbed?
        rest = bytes(data[bc_off + keep : bc_off + length])
        if rest == exit_word * (len(rest) // 4) + rest[len(rest) // 4 * 4 :]:
            if length - keep >= 4 and data[bc_off + keep : bc_off + keep + 4] == exit_word:
                applied.append(f"code-safe:{name}=already")
                continue
        # Write Exit from keep onward
        pos = bc_off + keep
        end = bc_off + length
        while pos + 4 <= end:
            data[pos : pos + 4] = exit_word
            pos += 4
        while pos < end:
            data[pos] = 0
            pos += 1
        applied.append(f"code-safe:{name}")
    return applied


def _apply_marxvee_in_dogcheck(data: bytearray) -> list[str]:
    """Apply Marxvee bytes only when the offset lies inside scr_dogcheck."""
    applied = []
    ranges = [(off, off + length) for _n, off, length in _dogcheck_entries(bytes(data))]
    if not ranges:
        # Fall back: still try known offsets if they look like Bt/B instructions
        ranges = []
    for offset, patch in MARXVEE_PATCHES:
        if offset + len(patch) > len(data):
            continue
        in_script = any(start <= offset < end for start, end in ranges) if ranges else False
        current = bytes(data[offset : offset + len(patch)])
        if current == patch:
            applied.append(f"marxvee@0x{offset:X}=already")
            continue
        if not in_script:
            # Without a CODE match, only patch if high byte looks like a branch opcode
            op = current[3] if len(current) == 4 else 0
            if op not in (0xB6, 0xB7, 0xB8, 0xB9):  # B / Bt / Bf family
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


def disable_dogcheck(data_win: str | Path, *, backup: bool = True) -> tuple[bool, str]:
    """
    Patch data.win so dogcheck no longer sends you to the Annoying Dog room.

    Safe strategy (matches intent of UndertaleModTool DisableDogcheck):
      keep `dogcheck = 1`, then exit — never skip the assignment.
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
                "No backup found — use Steam → Properties → Verify integrity, "
                "then click Enable live patches again.",
            )

    raw = bytearray(path.read_bytes())
    before = bytes(raw)

    notes: list[str] = []
    try:
        notes.extend(_apply_safe_code_stub(raw))
    except Exception as exc:
        notes.append(f"code-safe-error:{exc}")
    notes.extend(_apply_marxvee_in_dogcheck(raw))
    notes.extend(_apply_steam_bytes(raw))

    if bytes(raw) == before:
        if any("already" in n for n in notes):
            return True, "Dogcheck already disabled (safe patches)."
        return (
            False,
            "Could not auto-disable dogcheck for this data.win version. "
            "Use UndertaleModTool → Scripts → DisableDogcheck, then Enable live patches "
            "(debug) here. Secret/dogcheck rooms will still show the Annoying Dog.",
        )

    if backup:
        _backup(path)
    path.write_bytes(raw)
    return True, "Dogcheck disabled (" + ", ".join(notes) + "). Restart Undertale once."


def dogcheck_likely_disabled(data_win: str | Path) -> bool:
    """True for safe disable methods — never for the broken Exit-at-start stub."""
    path = Path(data_win)
    data = path.read_bytes()
    if dogcheck_exit_stubbed(data):
        return False
    # Safe code stub: push/pop kept, then Exit
    for _name, bc_off, length in _dogcheck_entries(data):
        info = _keep_bytes_for_dogcheck_assign(data, bc_off, length)
        if info is None:
            continue
        keep, exit_word = info
        if length > keep and data[bc_off + keep : bc_off + keep + 4] == exit_word:
            return True
    for offset, patch in MARXVEE_PATCHES:
        if offset + len(patch) <= len(data) and data[offset : offset + len(patch)] == patch:
            return True
    steam_ok = sum(
        1 for offset, _old, new in STEAM_BYTE_PATCHES if offset < len(data) and data[offset] == new
    )
    return steam_ok >= 2


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
KEYEVENTF_KEYUP = 0x0002


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


def debug_flag_enabled(data_win: str | Path) -> bool:
    path = Path(data_win)
    data = path.read_bytes()
    return any(offset < len(data) and data[offset] == 1 for offset in DEBUG_OFFSETS)


def _send_key_to_undertale(vk_code: int, *, presses: int = 1) -> bool:
    hwnd = find_undertale_hwnd()
    user32 = ctypes.windll.user32
    if not hwnd:
        return False
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.08)
    for _ in range(presses):
        user32.keybd_event(vk_code, 0, 0, 0)
        time.sleep(0.04)
        user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.06)
    return True


def live_teleport_to_room(
    room_id: int,
    *,
    save_folder: str | Path | None = None,
    data_win: str | Path | None = None,
    current_room: int | None = None,  # kept for API compatibility; unused
    cached_addresses: list | None = None,  # kept for API compatibility; unused
    max_room_id: int = 400,
) -> tuple[LiveTeleportResult, list]:
    """
    Teleport to an exact room while Undertale is running.

    Method (reliable with debug mode):
      1. Write the target room into file0 / undertale.ini
      2. Focus Undertale and press L (debug Load)
      → game reloads the save in that room immediately
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
        # Only debug mode is required for live Load (L).
        # Dogcheck disable is optional but recommended for secret rooms.
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
    except Exception as exc:
        return (
            LiveTeleportResult(False, "save_failed", f"Could not update save: {exc}"),
            [],
        )

    # Give the OS a moment to finish writing the save before the game reads it.
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

    return (
        LiveTeleportResult(
            True,
            "live_load",
            f"Loaded room {room_id} live (save updated + debug Load). "
            "If nothing changed, click Undertale once and press L, "
            "or restart Undertale once so debug mode is active.",
            debug_enabled=debug_on,
        ),
        [],
    )


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
                "Undertale is open — click a room to jump live."
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
            "(data.win.dogcheckbak / data.win.debugbak).\n\n"
            "Use this if Undertale crashes on L with a dogcheck error, "
            "or will not start after patching.",
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
        notes = []
        try:
            if enable_debug_mode(self.data_win_path, backup=True):
                notes.append("debug load (L) enabled")
        except OSError as exc:
            messagebox.showerror(
                "Could not patch",
                f"Windows blocked writing data.win:\n{exc}\n\n"
                "Close Undertale/Steam overlays and try again.",
            )
            return
        except Exception as exc:
            notes.append(f"debug failed: {exc}")
        try:
            ok, msg = disable_dogcheck(self.data_win_path, backup=True)
            if ok:
                notes.append("dogcheck disabled")
            else:
                notes.append(msg)
        except OSError as exc:
            messagebox.showerror("Could not patch", str(exc))
            return
        except Exception as exc:
            notes.append(f"dogcheck failed: {exc}")

        messagebox.showinfo(
            "Live patches ready",
            "Patched data.win for live room teleport:\n• "
            + "\n• ".join(notes)
            + "\n\nNow start Undertale, load your save, then click a room.\n"
            "Backup: data.win.dogcheckbak / data.win.debugbak\n\n"
            "If you get a Code Error about dogcheck when pressing L, "
            "click Restore data.win, then Enable live patches again.",
        )
        self._set_status("Live patches applied — start Undertale, then click a room.")

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

        display = (
            friendly_room_label(asset.name, int(asset.meta.get("room_id", 0)))
            if asset.is_room
            else asset.display_name
        )
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
