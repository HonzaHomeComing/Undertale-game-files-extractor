"""Models for extracted Undertale / GameMaker assets."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from PIL import Image


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
