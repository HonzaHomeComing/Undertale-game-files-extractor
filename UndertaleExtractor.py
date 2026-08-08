"""
Undertale File Extractor
========================
Windows app: extract Undertale game files, scroll through them, click to download.

How to run on Windows
---------------------
1. Install Python 3.10+ from https://www.python.org/downloads/
   (check "Add Python to PATH" during setup)
2. Open Command Prompt and run:
      pip install Pillow customtkinter
3. Double-click UndertaleExtractor.py
   OR run:  python UndertaleExtractor.py
4. Click "Open Undertale Folder" and select:
      C:\\Program Files (x86)\\Steam\\steamapps\\common\\Undertale
   (or wherever your data.win is)
5. Click any file to download it to your Downloads folder.

Tip: the browser shows 36 files per page so Undertale does not freeze.
"""

from __future__ import annotations

import argparse
import io
import re
import struct
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from tkinter import filedialog, messagebox

try:
    import customtkinter as ctk
    from PIL import Image
except ImportError:
    print("Missing packages. Open Command Prompt and run:")
    print("  pip install Pillow customtkinter")
    input("Press Enter to exit...")
    raise SystemExit(1)

__version__ = "1.1.0"


# ----- binary helpers -----


class BinaryReader:
    """Random-access little-endian reader over a bytes buffer."""

    def __init__(self, data: bytes | bytearray | memoryview):
        self._data = memoryview(data)
        self._pos = 0

    @classmethod
    def from_path(cls, path: str | Path) -> "BinaryReader":
        return cls(Path(path).read_bytes())

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


# ----- assets -----


class AssetKind(str, Enum):
    SPRITE = "Sprites"
    TEXTURE = "Textures"
    BACKGROUND = "Backgrounds"
    AUDIO = "Audio"
    MUSIC = "Music"
    FONT = "Fonts"
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
        return self.extension.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

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


# ----- data.win parser -----

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


# ----- windowed app -----

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
        self.current_kind: AssetKind | None = AssetKind.SPRITE
        self.page = 0
        self.download_dir = _default_download_dir()
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

        self.dl_path_label = ctk.CTkLabel(
            preview,
            text=f"Saves to:\n{self.download_dir}",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
            wraplength=240,
            justify="left",
        )
        self.dl_path_label.pack(anchor="w", padx=16, pady=16)

        self._highlight_kind(AssetKind.SPRITE)
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
                "3. Scroll the extracted files\n"
                "4. Click any image or file to download it"
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
            self.game_name = result.game_name or "Undertale"
            self.title(f"Undertale File Extractor — {self.game_name}")
            self._thumb_cache.clear()
            self.export_all_btn.configure(state="normal")
            self.page = 0
            self._update_counts()
            # Default to Sprites (paginated) instead of dumping every file into the UI.
            self.set_kind(AssetKind.SPRITE)
            self._set_status(
                f"Loaded {len(self.assets)} files from {result.path.name} "
                f"(showing {PAGE_SIZE} per page)"
            )
        except Exception as exc:
            self._on_load_error(exc)

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

        name_label = ctk.CTkLabel(
            frame,
            text=asset.display_name[:28] + ("…" if len(asset.display_name) > 28 else ""),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["ink"],
        )
        name_label.pack()

        meta = ctk.CTkLabel(
            frame,
            text=f"{asset.kind.value} · {_fmt_size(asset.size)}",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        )
        meta.pack(pady=(0, 8))

        def on_click(_event=None, a: GameAsset = asset) -> None:
            self.select_asset(a)
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
        self.preview_name.configure(text=asset.display_name)
        self.preview_meta.configure(
            text=f"{asset.kind.value}\n{_fmt_size(asset.size)}\nClick Download to save"
        )
        self.download_btn.configure(state="normal")
        self.save_as_btn.configure(state="normal")

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

    def download_selected(self) -> None:
        if not self.selected:
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
            self.dl_path_label.configure(text=f"Saves to:\n{self.download_dir}")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def run_app() -> None:
    app = UndertaleExtractorApp()
    app.mainloop()




# ----- entry point -----


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Undertale File Extractor — browse and download game assets (Windows)"
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Optional Undertale folder or data.win to open immediately",
    )
    parser.add_argument(
        "--extract-all",
        metavar="OUT_DIR",
        help="Extract all assets to OUT_DIR without opening the window",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.extract_all:
        if not args.path:
            print("error: path to Undertale / data.win is required with --extract-all", file=sys.stderr)
            return 2
        result = load_undertale_assets(args.path)
        out = Path(args.extract_all)
        for asset in result.assets:
            dest = out / asset.kind.value.lower()
            path = asset.export_to(dest, overwrite=False)
            print(path)
        print(f"Extracted {len(result.assets)} files to {out}")
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
            print("Undertale Extractor crashed:", exc)
        raise
