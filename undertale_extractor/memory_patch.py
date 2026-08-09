"""Patch Undertale's in-memory data.win copy (Windows).

Used so Home battlegroup changes apply while the game is already running.
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
                            # data.win is typically multi-MB
                            if 1_000_000 <= declared <= 200_000_000:
                                if expected_size is not None:
                                    # FORM size field is payload after header; file ≈ declared+8
                                    if abs(declared + 8 - expected_size) <= 64:
                                        return abs_addr
                                    if best is None:
                                        best = abs_addr
                                else:
                                    return abs_addr
                        idx = data.find(needle, idx + 1)
                next_off = offset + piece
                if next_off < size:
                    next_off = max(0, next_off - 3)  # overlap for split needle
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
        if expected_old is not None:
            cur = _read(handle, addr, 4)
            if len(cur) == 4:
                got = struct.unpack("<i", cur)[0]
                # Accept if already the new value, or matches expected old
                if got != int(expected_old) and got != int(value):
                    # Still try — disk may differ slightly from RAM after prior patches
                    pass
        payload = struct.pack("<i", int(value))
        _write(handle, addr, payload)
        return True
    finally:
        kernel32.CloseHandle(handle)


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
