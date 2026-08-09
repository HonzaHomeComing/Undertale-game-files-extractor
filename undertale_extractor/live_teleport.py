"""Live Undertale room teleport while the game is running (Windows)."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .dogcheck import dogcheck_exit_stubbed
from .teleport import teleport_to_room

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


def kill_undertale(*, timeout: float = 10.0) -> tuple[bool, str]:
    """
    Force-close every Undertale game process. Returns (was_running_or_now_dead, detail).
    """
    if not is_windows():
        return False, "kill_undertale requires Windows"
    pid = find_undertale_pid()
    if not pid:
        return True, "Undertale was not running"
    k32 = ctypes.windll.kernel32
    PROCESS_TERMINATE = 0x0001
    SYNCHRONIZE = 0x00100000
    handle = k32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
    if handle:
        try:
            k32.TerminateProcess(handle, 1)
        finally:
            k32.CloseHandle(handle)
    # Fallback: taskkill (covers stubborn / multi-instance cases)
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "UNDERTALE.exe", "/T"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        subprocess.run(
            ["taskkill", "/F", "/IM", "undertale.exe", "/T"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if find_undertale_pid() is None:
            return True, f"Closed Undertale (pid {pid})"
        time.sleep(0.2)
    return find_undertale_pid() is None, (
        "Tried to close Undertale; if it is still open, end it in Task Manager."
    )


def wait_for_undertale_window(*, timeout: float = 60.0) -> bool:
    """Poll until the real UNDERTALE game window exists."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if find_undertale_hwnd():
            return True
        time.sleep(0.25)
    return False


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
    from .memory_patch import _open_process, _write, find_form_base, kernel32

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
    from .teleport import read_save_info
    import re

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
