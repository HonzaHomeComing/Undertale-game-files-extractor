"""Patch Undertale's in-memory data.win / bytecode (Windows).

Used so Home battlegroup and related live edits apply while the game runs.
"""

from __future__ import annotations

import ctypes
import struct
from ctypes import wintypes
from pathlib import Path
from typing import Optional

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
    max_replacements: int = 32,
    max_addr: int = 0x7FFFFFFF,
) -> int:
    """
    Replace little-endian uint32 words in committed memory.
    Used to patch PushI immediates / raw battlegroup constants that GameMaker
    may have copied out of the data.win mapping into a bytecode buffer.
    """
    if old_word == new_word:
        return 0
    needle = struct.pack("<I", old_word & 0xFFFFFFFF)
    replacement = struct.pack("<I", new_word & 0xFFFFFFFF)
    handle = _open_process(pid)
    replaced = 0
    try:
        mbi = MEMORY_BASIC_INFORMATION()
        address = 0
        while address < max_addr and replaced < max_replacements:
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
                offset = 0
                while offset < size and replaced < max_replacements:
                    piece = min(1 * 1024 * 1024, size - offset)
                    data = _read(handle, base + offset, piece)
                    if data:
                        idx = 0
                        while True:
                            found = data.find(needle, idx)
                            if found < 0:
                                break
                            abs_addr = base + offset + found
                            try:
                                _write(handle, abs_addr, replacement)
                                replaced += 1
                                if replaced >= max_replacements:
                                    break
                            except RuntimeError:
                                pass
                            idx = found + 1
                    next_off = offset + piece
                    if next_off < size:
                        next_off = max(0, next_off - 3)
                    offset = next_off if next_off > offset else offset + piece
            next_addr = base + size
            if next_addr <= address:
                break
            address = next_addr
    finally:
        kernel32.CloseHandle(handle)
    return replaced


from .live_teleport import find_undertale_pid, is_windows


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
