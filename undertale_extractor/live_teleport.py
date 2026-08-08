"""Live Undertale room teleport while the game is running (Windows)."""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .dogcheck import disable_dogcheck, dogcheck_likely_disabled
from .teleport import teleport_to_room

# Known data.win offsets where debug flag is a single byte (0 → 1).
DEBUG_OFFSETS = (
    0x725B24,  # 1.00
    0x725D8C,  # 1.001
    0x7748C4,  # 1.08
    0x725DDC,  # variants
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


def find_undertale_hwnd() -> int:
    """Return HWND for the Undertale window, or 0."""
    if not is_windows():
        return 0
    user32 = ctypes.windll.user32
    for title in ("UNDERTALE", "Undertale", "undertale"):
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return int(hwnd)
    found: list[int] = []

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
    if hwnd:
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            return int(pid.value)
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
        debug_on = debug_flag_enabled(data_win)
        dog_ok = dogcheck_likely_disabled(data_win)
        # Never rewrite data.win while Undertale is running — that can block
        # launch / cause sharing errors. User must use "Enable live patches" first.
        if not debug_on or not dog_ok:
            return (
                LiveTeleportResult(
                    False,
                    "patches_required",
                    "Live teleport needs a one-time data.win patch.\n\n"
                    "1. Close Undertale completely\n"
                    "2. Click Enable live patches in this app\n"
                    "3. Start Undertale, load your save\n"
                    "4. Click the room again\n\n"
                    "If Undertale will not start, click Restore data.win.",
                ),
                [],
            )

    if data_win and Path(data_win).is_file() and not debug_flag_enabled(data_win):
        return (
            LiveTeleportResult(
                False,
                "no_debug",
                "Could not enable Undertale debug mode automatically. "
                "Live teleport needs debug Load (L).",
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
