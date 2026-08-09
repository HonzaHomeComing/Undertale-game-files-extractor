"""Amalgomation — secret Debug Toolkit fight (id 666).

Not listed on the monster slider. Enter 666 in the custom id box.

This is a toolkit-driven encounter: it starts an in-game amalgamate host
battle, then runs a Chaos Director that stacks random attack “layers”,
scrambles HP/armor/damage readouts in memory, shows a creature collage
built from random sprites in data.win, and escalates until the player dies.

Full native GML amalgamate objects would need UndertaleModTool; this delivers
the experience from the extractor while the game is live.
"""

from __future__ import annotations

import random
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .battles import start_fight
from .live_teleport import (
    _send_key_to_undertale,
    find_undertale_hwnd,
    find_undertale_pid,
    is_windows,
    undertale_is_running,
)
from .memory_patch import _open_process, _read, _write, iter_process_memory, kernel32

AMALGOMATION_ID = 666
# In-game host: Endogeny — "It's the Amalgamate."
HOST_BATTLEGROUP = 86

# Attack themes stacked every 2 director "rounds"
ATTACK_POOL: tuple[str, ...] = (
    "Froggit flies",
    "Whimsun tears",
    "Migosp swarm",
    "Vegetoid carrots",
    "Loox rings",
    "Snowdrake puns",
    "Icecap hats",
    "Doggo spears",
    "Lesser Dog neck",
    "Papyrus bones",
    "Gyftrot gifts",
    "Aaron flexes",
    "Woshua water",
    "Shyren notes",
    "Mad Dummy missiles",
    "Undyne spears",
    "Mettaton discs",
    "Muffet webs",
    "Royal Guard guns",
    "Tsunderplane jets",
    "Vulkin lava",
    "Pyrope fire",
    "Madjick orbs",
    "Knight Knight stars",
    "Final Froggit",
    "Astigmatism glare",
    "Whimsalot butterflies",
    "Sans bones",
    "Sans gasterblasters",
    "Asgore fire",
    "Asriel chaos buster",
    "Flowey pellets",
    "Memoryhead faces",
    "Reaper Bird",
    "Lemon Bread",
    "Glyde circles",
)

DIALOGS: tuple[str, ...] = (
    "* It's raining somewhere else.",
    "* You feel your sins crawling on your back.",
    "* But nobody came.",
    "* Get Dunked On!!!!!",
    "* You're gonna have a bad time.",
    "* ABSOLUTELY SCREAMING!!!",
    "* ahuhuhuhu...",
    "* Hoi! I'm temmie!",
    "* I'M NOT GONNA TELL YOU TO STOP HAVING A GOOD TIME.",
    "* The music grows more distorted.",
    "* Files that should not touch are touching.",
    "* SPRITE_SHEET_ERROR: too many faces.",
    "* It smells like sweet lemons and ozone.",
    "* Determination, but wrong.",
    "* The save file is watching you.",
    "* loading mus_zzz_c... failed... retrying...",
    "* (The amalgam hums in a voice made of UI fonts.)",
    "* Your inventory contains 1x [UNDEFINED].",
    "* * * Mettaton attacks! Undyne attacks! Froggit attacks!",
    "* It's so cold. It's so hot. It's room_void.",
)

VK_F6 = 0x75  # battle debug: mercy 0, ATK 999


@dataclass
class ChaosState:
    layer: int = 1
    rounds: int = 0
    stack: list[str] = field(default_factory=list)
    last_dialog: str = ""
    fake_hp: int = 666
    fake_df: int = 66
    fake_damage: int = 9999
    running: bool = False


def is_amalgomation_id(battlegroup_id: int) -> bool:
    return int(battlegroup_id) == AMALGOMATION_ID


def _pick_attack(rng: random.Random, used: set[str]) -> str:
    choices = [a for a in ATTACK_POOL if a not in used] or list(ATTACK_POOL)
    return rng.choice(choices)


def scramble_u32_candidates(
    pid: int,
    candidates: list[int],
    low: int,
    high: int,
    *,
    limit: int = 12,
) -> int:
    """Write random int32 values to previously found addresses."""
    if not candidates:
        return 0
    handle = _open_process(pid)
    wrote = 0
    try:
        for addr in candidates[:limit]:
            val = random.randint(low, high)
            try:
                _write(handle, addr, struct.pack("<i", val))
                wrote += 1
            except RuntimeError:
                continue
    finally:
        if kernel32:
            kernel32.CloseHandle(handle)
    return wrote


def find_int32_addresses(pid: int, value: int, *, max_hits: int = 40) -> list[int]:
    """Scan process memory for a little-endian int32 (battle HP hunting)."""
    needle = struct.pack("<i", int(value))
    hits: list[int] = []
    handle = _open_process(pid)
    try:
        for base, data in iter_process_memory(handle):
            start = 0
            while len(hits) < max_hits:
                idx = data.find(needle, start)
                if idx < 0:
                    break
                # Prefer aligned hits
                if idx % 4 == 0:
                    hits.append(base + idx)
                start = idx + 4
            if len(hits) >= max_hits:
                break
    finally:
        if kernel32:
            kernel32.CloseHandle(handle)
    return hits


def start_amalgomation_fight(
    *,
    data_win: str | Path | None,
    save_folder: str | Path | None = None,
) -> tuple[bool, str]:
    """Begin the host amalgamate battle (Endogeny). Director is started by the UI."""
    if not data_win or not Path(data_win).is_file():
        return False, "Open your Undertale folder (data.win) first."
    if not undertale_is_running():
        return (
            False,
            "Launch Undertale, load a save, stand in the overworld, then enter 666 again.",
        )
    ok, msg = start_fight(
        HOST_BATTLEGROUP,
        data_win=data_win,
        ensure_debug=True,
        save_folder=save_folder,
    )
    if not ok:
        return False, f"Amalgomation host fight failed: {msg}"
    return (
        True,
        "AMALGOMATION stirring (host: Endogeny). Chaos Director will escalate attacks. "
        "It cannot be reasoned with. " + msg,
    )


class AmalgomationDirector:
    """
    Tk-friendly chaos loop. Call tick() on a timer from the UI thread,
    or run_background() from a daemon thread with a stop event.
    """

    def __init__(self, *, on_update=None):
        self.state = ChaosState()
        self.rng = random.Random()
        self.on_update = on_update
        self._stop = threading.Event()
        self._player_hp_addrs: list[int] = []
        self._monster_hp_addrs: list[int] = []
        self._tick_count = 0

    def start(self) -> None:
        self.state = ChaosState(running=True, layer=1, rounds=0, stack=[])
        first = _pick_attack(self.rng, set())
        self.state.stack = [first]
        self.state.last_dialog = self.rng.choice(DIALOGS)
        self._stop.clear()
        self._tick_count = 0
        self._prime_memory_targets()
        self._push_update()

    def stop(self) -> None:
        self.state.running = False
        self._stop.set()

    def _prime_memory_targets(self) -> None:
        if not is_windows():
            return
        pid = find_undertale_pid()
        if not pid:
            return
        try:
            # Player often starts fights at 20 HP; monster amalgamate HP is larger
            self._player_hp_addrs = find_int32_addresses(pid, 20, max_hits=24)
            self._monster_hp_addrs = []
            for seed in (100, 200, 300, 500, 1000, 1500):
                self._monster_hp_addrs.extend(find_int32_addresses(pid, seed, max_hits=8))
            # de-dupe
            self._monster_hp_addrs = list(dict.fromkeys(self._monster_hp_addrs))[:40]
        except Exception:
            self._player_hp_addrs = []
            self._monster_hp_addrs = []

    def tick(self) -> ChaosState:
        """One director pulse (~1 second). Safe to call from UI timer."""
        if not self.state.running:
            return self.state
        self._tick_count += 1
        st = self.state

        # Fake readouts (damage text / armor / hp display chaos)
        st.fake_hp = self.rng.randint(1, 99999)
        st.fake_df = self.rng.randint(0, 999)
        st.fake_damage = self.rng.choice(
            [0, 1, 9, 99, 999, 9999, 99999, -1, 32767, self.rng.randint(0, 999999)]
        )

        if self._tick_count % 3 == 0:
            st.last_dialog = self.rng.choice(DIALOGS)

        # Every ~2 rounds (≈ 8 ticks): add another stacked attack pattern
        if self._tick_count % 8 == 0:
            st.rounds += 1
            if st.rounds % 2 == 0 or len(st.stack) == 1:
                nxt = _pick_attack(self.rng, set(st.stack))
                st.stack.append(nxt)
                st.layer = len(st.stack)
            # Crank in-battle ATK / disable mercy as layers grow
            if st.layer >= 2 and find_undertale_hwnd():
                _send_key_to_undertale(VK_F6, presses=1)

        # Memory scramble: monster HP/DF-like values + chip player HP at high layers
        pid = find_undertale_pid()
        if pid and is_windows():
            try:
                scramble_u32_candidates(pid, self._monster_hp_addrs, 1, 99999, limit=10)
                if st.layer >= 3:
                    # Chip the player — amalgomation cannot be escaped forever
                    scramble_u32_candidates(pid, self._player_hp_addrs, 1, max(2, 12 - st.layer), limit=6)
                if st.layer >= 6:
                    scramble_u32_candidates(pid, self._player_hp_addrs, 0, 1, limit=8)
            except Exception:
                pass

        self._push_update()
        return st

    def _push_update(self) -> None:
        if self.on_update:
            try:
                self.on_update(self.state)
            except Exception:
                pass

    def run_background(self, interval: float = 1.0) -> None:
        self.start()

        def loop() -> None:
            while not self._stop.is_set() and self.state.running:
                try:
                    self.tick()
                except Exception:
                    pass
                self._stop.wait(interval)

        threading.Thread(target=loop, daemon=True).start()


def open_amalgomation_ui(parent, *, data_win: Path | None, save_folder: Path | None, on_status=None):
    """Modal-ish director window with shifting sprite collage + chaos readout."""
    import customtkinter as ctk
    from tkinter import messagebox
    from PIL import Image, ImageTk

    COLORS = {
        "bg": "#1a1210",
        "panel": "#2a1c18",
        "ink": "#f2e6d8",
        "muted": "#a89080",
        "accent": "#c43c2e",
    }

    warn = (
        "AMALGOMATION is exclusive to this toolkit (id 666).\n\n"
        "It starts an amalgamate host fight, then stacks random attack patterns "
        "every two rounds until you cannot dodge. It cannot be spared, killed, or fled from.\n\n"
        "Stand in the overworld. Continue?"
    )
    if not messagebox.askyesno("AMALGOMATION", warn, parent=parent):
        return

    ok, msg = start_amalgomation_fight(data_win=data_win, save_folder=save_folder)
    if on_status:
        on_status(msg)
    if not ok:
        messagebox.showwarning("AMALGOMATION", msg, parent=parent)
        return

    win = ctk.CTkToplevel(parent)
    win.title("AMALGOMATION")
    win.geometry("520x640")
    win.configure(fg_color=COLORS["bg"])
    win.attributes("-topmost", True)

    ctk.CTkLabel(
        win,
        text="AMALGOMATION",
        font=ctk.CTkFont(family="Courier New", size=26, weight="bold"),
        text_color=COLORS["accent"],
    ).pack(anchor="w", padx=16, pady=(14, 2))
    ctk.CTkLabel(
        win,
        text="a creature made of files that should not combine",
        text_color=COLORS["muted"],
    ).pack(anchor="w", padx=16)

    canvas = ctk.CTkLabel(win, text="", width=480, height=220, fg_color=COLORS["panel"])
    canvas.pack(padx=16, pady=12)

    dialog_var = ctk.StringVar(value="* …")
    stats_var = ctk.StringVar(value="HP ????   DF ????   DMG ????")
    stack_var = ctk.StringVar(value="Layer 1")
    ctk.CTkLabel(win, textvariable=dialog_var, text_color=COLORS["ink"], wraplength=480, justify="left").pack(
        anchor="w", padx=16, pady=6
    )
    ctk.CTkLabel(win, textvariable=stats_var, text_color=COLORS["accent"], font=ctk.CTkFont(family="Courier New", size=14)).pack(
        anchor="w", padx=16
    )
    ctk.CTkLabel(win, textvariable=stack_var, text_color=COLORS["muted"], wraplength=480, justify="left").pack(
        anchor="w", padx=16, pady=8
    )

    sprite_images: list = []
    photo_holder = {"img": None}

    def load_sprite_pool() -> None:
        nonlocal sprite_images
        if not data_win or not Path(data_win).is_file():
            return
        try:
            from .parser import load_undertale_assets
            from .assets import AssetKind

            result = load_undertale_assets(str(data_win), progress=lambda _m: None)
            imgs = []
            for asset in result.assets:
                if asset.kind != AssetKind.SPRITE:
                    continue
                try:
                    im = asset.get_image()
                    if im is None:
                        continue
                    im = im.convert("RGBA")
                    im.thumbnail((160, 160))
                    imgs.append(im)
                    if len(imgs) >= 80:
                        break
                except Exception:
                    continue
            sprite_images = imgs
        except Exception:
            sprite_images = []

    win.after(100, load_sprite_pool)

    def paint_creature() -> None:
        if not sprite_images:
            canvas.configure(text="loading file-meat…")
            return
        base = Image.new("RGBA", (480, 220), (20, 12, 10, 255))
        for _ in range(random.randint(3, 8)):
            piece = random.choice(sprite_images).copy()
            piece = piece.rotate(random.randint(0, 359), expand=True)
            piece.thumbnail((random.randint(40, 140), random.randint(40, 140)))
            x = random.randint(-20, 400)
            y = random.randint(-20, 160)
            base.alpha_composite(piece, (x, y))
        # jitter / glitch bars
        for _ in range(5):
            y = random.randint(0, 210)
            for x in range(0, 480, 3):
                if random.random() < 0.15:
                    base.putpixel((x, y), (255, random.randint(0, 80), random.randint(0, 40), 200))
        photo = ImageTk.PhotoImage(base)
        photo_holder["img"] = photo
        canvas.configure(image=photo, text="")

    director = AmalgomationDirector()

    def on_update(st: ChaosState) -> None:
        dialog_var.set(st.last_dialog)
        stats_var.set(
            f"HP {st.fake_hp}   DF {st.fake_df}   DMG {st.fake_damage}   [UNSTABLE]"
        )
        stack_var.set(
            f"Chaos layer {st.layer} — stacked patterns:\n"
            + " + ".join(st.stack[-8:])
            + (" …" if len(st.stack) > 8 else "")
        )

    director.on_update = on_update
    director.start()

    def pulse() -> None:
        if not director.state.running:
            return
        director.tick()
        paint_creature()
        win.after(1000, pulse)

    def halt() -> None:
        director.stop()
        win.destroy()

    ctk.CTkButton(
        win,
        text="Sever link (stop director)",
        command=halt,
        fg_color=COLORS["accent"],
        hover_color="#a03020",
    ).pack(pady=12)

    win.protocol("WM_DELETE_WINDOW", halt)
    win.after(400, pulse)
    messagebox.showinfo(
        "AMALGOMATION",
        "The host fight has started. Keep Undertale focused in the overworld/battle.\n"
        "Patterns will stack every two rounds. Do not expect mercy.",
        parent=win,
    )
