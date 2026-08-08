"""Live Undertale room teleport while the game is running (Windows)."""

from __future__ import annotations

import ctypes
import struct
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_ACCESS = (
    PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION
)

MEM_COMMIT = 0x1000
PAGE_READABLE = {
    0x02,  # PAGE_READONLY
    0x04,  # PAGE_READWRITE
    0x08,  # PAGE_WRITECOPY
    0x20,  # PAGE_EXECUTE_READ
    0x40,  # PAGE_EXECUTE_READWRITE
    0x80,  # PAGE_EXECUTE_WRITECOPY
}

# Known data.win offsets where debug flag is a single byte (0 → 1).
DEBUG_OFFSETS = (
    0x725B24,  # 1.00
    0x725D8C,  # 1.001
    0x7748C4,  # 1.08
    0x725DDC,  # 1.001 linux-ish / variants
)

VK_INSERT = 0x2D
VK_DELETE = 0x2E
KEYEVENTF_KEYUP = 0x0002


@dataclass
class LiveTeleportResult:
    ok: bool
    method: str
    detail: str
    addresses_written: int = 0
    debug_enabled: bool = False


# Cache entries: (address, "i32"|"f64")
RoomAddr = tuple[int, str]


def is_windows() -> bool:
    return sys.platform.startswith("win")


def find_undertale_hwnd() -> int:
    """Return HWND for the Undertale window, or 0."""
    if not is_windows():
        return 0
    user32 = ctypes.windll.user32
    for title in ("UNDERTALE", "Undertale", "undertale"):
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return int(hwnd)
    # Partial title match fallback
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if "undertale" in buf.value.lower() and user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    user32.EnumWindows(enum_proc, 0)
    return found[0] if found else 0


def find_undertale_pid() -> int | None:
    if not is_windows():
        return None
    hwnd = find_undertale_hwnd()
    if not hwnd:
        # Fallback: snapshot processes by name
        return _pid_by_name(("UNDERTALE.exe", "undertale.exe", "Undertale.exe"))
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) or None


def _pid_by_name(names: tuple[str, ...]) -> int | None:
    # Minimal Toolhelp snapshot
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
    Flip Undertale's debug flag in data.win so Insert/Del room-warp works.
    Returns True if debug is (now) enabled.
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
        # Heuristic: search for rare pattern near known debug init (best-effort)
        return False
    if changed:
        if backup:
            bak = path.with_suffix(path.suffix + ".debugbak")
            if not bak.exists():
                bak.write_bytes(path.read_bytes())
        path.write_bytes(data)
    return True


def _send_key_to_undertale(vk_code: int) -> bool:
    hwnd = find_undertale_hwnd()
    user32 = ctypes.windll.user32
    if hwnd:
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.05)
    # keybd_event works even if focus is a bit flaky
    user32.keybd_event(vk_code, 0, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)
    return True


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def _scan_for_pattern(handle, needle: bytes, *, align: int, max_hits: int = 64) -> list[int]:
    kernel32 = ctypes.windll.kernel32
    hits: list[int] = []
    address = 0
    mbi = _MEMORY_BASIC_INFORMATION()
    limit = 0x7FFFFFFF0000 if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x7FFF0000
    while address < limit:
        res = kernel32.VirtualQueryEx(
            handle,
            ctypes.c_void_p(address),
            ctypes.byref(mbi),
            ctypes.sizeof(mbi),
        )
        if res == 0:
            break
        base = mbi.BaseAddress or 0
        size = int(mbi.RegionSize)
        prot = int(mbi.Protect)
        state = int(mbi.State)
        next_addr = base + size
        if next_addr <= address:
            break
        address = next_addr

        if state != MEM_COMMIT or (prot & 0xFF) not in PAGE_READABLE:
            continue
        if size > 64 * 1024 * 1024:
            continue

        buf = (ctypes.c_char * size)()
        read = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(
            handle, ctypes.c_void_p(base), buf, size, ctypes.byref(read)
        )
        if not ok or read.value == 0:
            continue
        data = bytes(buf[: read.value])
        start = 0
        while True:
            idx = data.find(needle, start)
            if idx < 0:
                break
            if align <= 1 or idx % align == 0:
                hits.append(base + idx)
                if len(hits) >= max_hits:
                    return hits
            start = idx + align
    return hits


def _scan_for_int32(handle, value: int, *, max_hits: int = 64) -> list[int]:
    return _scan_for_pattern(handle, struct.pack("<i", int(value)), align=4, max_hits=max_hits)


def _scan_for_f64(handle, value: float, *, max_hits: int = 64) -> list[int]:
    return _scan_for_pattern(handle, struct.pack("<d", float(value)), align=4, max_hits=max_hits)


def _write_bytes(handle, address: int, raw: bytes) -> bool:
    kernel32 = ctypes.windll.kernel32
    written = ctypes.c_size_t(0)
    ok = kernel32.WriteProcessMemory(
        handle,
        ctypes.c_void_p(address),
        raw,
        len(raw),
        ctypes.byref(written),
    )
    return bool(ok and written.value == len(raw))


def _write_int32(handle, address: int, value: int) -> bool:
    return _write_bytes(handle, address, struct.pack("<i", int(value)))


def _write_f64(handle, address: int, value: float) -> bool:
    return _write_bytes(handle, address, struct.pack("<d", float(value)))


def live_teleport_to_room(
    room_id: int,
    *,
    current_room: int | None = None,
    data_win: str | Path | None = None,
    cached_addresses: list[RoomAddr] | None = None,
    max_room_id: int = 400,
) -> tuple[LiveTeleportResult, list[RoomAddr]]:
    """
    Teleport while Undertale is running.

    Strategy (with debug mode):
      1. Write (target-1) into the live `room` value in memory
      2. Send Insert → game does room_goto(room+1) == target

    Returns (result, address_cache_for_next_time).
    """
    if not is_windows():
        return (
            LiveTeleportResult(False, "unsupported", "Live teleport requires Windows."),
            cached_addresses or [],
        )

    pid = find_undertale_pid()
    if not pid:
        return (
            LiveTeleportResult(
                False,
                "not_running",
                "Undertale is not running. Start the game, load a save, then click a room.",
            ),
            cached_addresses or [],
        )

    debug_on = False
    if data_win and Path(data_win).is_file():
        debug_on = enable_debug_mode(data_win, backup=True)

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_ACCESS, False, pid)
    if not handle:
        return (
            LiveTeleportResult(
                False,
                "access_denied",
                "Could not open Undertale process. Try running this app as Administrator.",
            ),
            cached_addresses or [],
        )

    try:
        search_room = current_room
        addrs: list[RoomAddr] = list(cached_addresses or [])

        if addrs:
            buf_i = ctypes.c_int32()
            buf_d = ctypes.c_double()
            read = ctypes.c_size_t(0)
            addr0, kind0 = addrs[0]
            if kind0 == "f64":
                if kernel32.ReadProcessMemory(
                    handle, ctypes.c_void_p(addr0), ctypes.byref(buf_d), 8, ctypes.byref(read)
                ):
                    search_room = int(buf_d.value)
            else:
                if kernel32.ReadProcessMemory(
                    handle, ctypes.c_void_p(addr0), ctypes.byref(buf_i), 4, ctypes.byref(read)
                ):
                    search_room = int(buf_i.value)

        if search_room is None:
            return (
                LiveTeleportResult(
                    False,
                    "need_current_room",
                    "Save once in Undertale (at a save point), then try again "
                    "so we can find your current room in memory.",
                ),
                addrs,
            )

        if not addrs:
            int_hits = _scan_for_int32(handle, int(search_room), max_hits=16)
            f64_hits = _scan_for_f64(handle, float(search_room), max_hits=16)
            addrs = [(a, "i32") for a in int_hits] + [(a, "f64") for a in f64_hits]
            if not addrs:
                return (
                    LiveTeleportResult(
                        False,
                        "not_found",
                        f"Could not find room {search_room} in memory. "
                        "Save your game once, stay on the overworld (not a menu/battle), "
                        "then try again.",
                    ),
                    [],
                )
            if len(addrs) > 16:
                addrs = addrs[:16]

        target = int(room_id)
        if target < 0 or target > max_room_id:
            return (
                LiveTeleportResult(False, "bad_room", f"Room id {target} looks invalid."),
                addrs,
            )

        if target == 0:
            write_value = 1
            vk = VK_DELETE
        else:
            write_value = target - 1
            vk = VK_INSERT

        written = 0
        for addr, kind in addrs:
            if kind == "f64":
                if _write_f64(handle, addr, float(write_value)):
                    written += 1
            else:
                if _write_int32(handle, addr, write_value):
                    written += 1

        if written == 0:
            return (
                LiveTeleportResult(
                    False,
                    "write_failed",
                    "Found room memory but could not write. Try as Administrator.",
                ),
                addrs,
            )

        _send_key_to_undertale(vk)
        time.sleep(0.15)
        for addr, kind in addrs:
            if kind == "f64":
                _write_f64(handle, addr, float(target))
            else:
                _write_int32(handle, addr, target)

        hint = ""
        if data_win:
            hint = (
                " If nothing happened, restart Undertale once "
                "(debug warp keys need a restart after first setup)."
            )

        return (
            LiveTeleportResult(
                True,
                "live",
                f"Teleported to room {target} while Undertale is open "
                f"(updated {written} memory slot(s)).{hint}",
                addresses_written=written,
                debug_enabled=debug_on,
            ),
            addrs,
        )
    finally:
        kernel32.CloseHandle(handle)
