"""Parse GameMaker Studio data.win / game.unx archives (Undertale-compatible)."""

from __future__ import annotations

import io
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .assets import AssetKind, GameAsset, safe_filename
from .binary import BinaryReader

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
                    from PIL import ImageDraw

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
