"""Little-endian binary helpers for GameMaker data.win files."""

from __future__ import annotations

import os
import shutil
import struct
import tempfile
from pathlib import Path


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
