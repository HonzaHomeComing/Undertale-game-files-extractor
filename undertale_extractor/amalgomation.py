"""Amalgomation — secret Debug Toolkit fight (id 666), entirely in-game.

Not listed on the monster slider. Enter 666 in the custom id box.

Starts a host battle inside Undertale, then a silent director (no extra window)
rewrites the host so it:
  • morphs appearance between random sprites from data.win every second
  • picks random bullet-generator attacks each round
  • every 2 rounds stacks another random attack pattern
  • randomizes monster HP / DF every second
  • glitches damage numbers
  • cannot be spared, killed, or fled from — chaos escalates until you die
"""

from __future__ import annotations

import random
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .battles import push_imm, start_fight
from .binary import BinaryReader
from .dogcheck import _find_code_entries
from .live_teleport import (
    _send_key_to_undertale,
    find_undertale_hwnd,
    find_undertale_pid,
    is_windows,
    undertale_is_running,
)
from .memory_patch import (
    _open_process,
    _read,
    _write,
    iter_process_memory,
    kernel32,
    patch_int32_in_data_win_image,
    write_int32_in_running_game,
)

AMALGOMATION_ID = 666
# Vessel fight: Endogeny battlegroup — rewritten in-place into Amalgomation.
HOST_BATTLEGROUP = 86

VK_F6 = 0x75  # debug: mercy 0, ATK 999

OP_PUSHI = 0x84
OP_BF = 0xB8
OP_B = 0xB6
OP_BT = 0xB7

_DRAW_NAMES = (
    "gml_Object_obj_endogeny_body_Draw_0",
    "gml_Object_obj_endogeny_body_Draw",
)
_STEP_NAMES = (
    "gml_Object_obj_endogeny_Step_0",
    "gml_Object_obj_endogeny_Step",
)

# Object-name substrings that look like bullet / attack generators
_GEN_NEEDLES = (
    "bulletgen",
    "bulgen",
    "blt_",
    "gen",
    "blaster",
    "gaster",
    "spear",
    "bonebox",
    "bone",
    "rocketdog",
    "laserdog",
    "amalgam",
    "spiderbullet",
    "lavafire",
    "butterfly",
    "carrot",
    "blackbox",
    "gigavine",
    "sidegen",
    "vertbullet",
    "randomgen",
    "stormstar",
    "asgore",
    "sans",
    "mettaton",
)

_HOST_GEN_NAMES = (
    "obj_amalgam_rocketdog",
    "obj_amalgam_laserdog",
)

_HOST_SPRITE_NAMES = (
    "spr_endogeny",
    "spr_endogeny_head",
    "spr_endogeny_2",
)


@dataclass
class ResourceIndex:
    sprites: dict[str, int] = field(default_factory=dict)
    objects: dict[str, int] = field(default_factory=dict)
    sprite_ids: list[int] = field(default_factory=list)
    gen_object_ids: list[int] = field(default_factory=list)


@dataclass
class PatchSite:
    offset: int  # file offset of PushI dword
    original: int
    kind: str


@dataclass
class AmalgomationPlan:
    sprite_sites: list[PatchSite] = field(default_factory=list)
    attack_sites: list[PatchSite] = field(default_factory=list)
    firingrate_sites: list[PatchSite] = field(default_factory=list)
    branch_sites: list[PatchSite] = field(default_factory=list)
    mercymod_sites: list[PatchSite] = field(default_factory=list)
    resources: ResourceIndex = field(default_factory=ResourceIndex)


@dataclass
class ChaosState:
    layer: int = 1
    rounds: int = 0
    stack: list[str] = field(default_factory=list)
    running: bool = False
    fake_hp: int = 666
    fake_df: int = 66
    fake_damage: int = 9999


_DIRECTOR_LOCK = threading.Lock()
_ACTIVE_DIRECTOR: "AmalgomationDirector | None" = None


def is_amalgomation_id(battlegroup_id: int) -> bool:
    return int(battlegroup_id) == AMALGOMATION_ID


def _list_chunk_names(data: bytes, tag: str) -> dict[str, int]:
    """Return {name: index} for a GameMaker pointer-list chunk (SPRT/OBJT/…)."""
    reader = BinaryReader(data)
    reader.seek(0)
    if reader.read_tag() != "FORM":
        return {}
    form_size = reader.read_u32()
    form_end = reader.position + form_size
    chunk_start = None
    while reader.position + 8 <= form_end:
        t = reader.read_tag()
        size = reader.read_u32()
        start = reader.position
        if t == tag:
            chunk_start = start
        try:
            reader.seek(start + size)
        except ValueError:
            break
    if chunk_start is None:
        return {}
    reader.seek(chunk_start)
    count = reader.read_u32()
    if count <= 0 or count > 200_000:
        return {}
    offsets = [reader.read_u32() for _ in range(count)]
    out: dict[str, int] = {}
    for idx, off in enumerate(offsets):
        try:
            if off <= 0 or off >= len(data):
                continue
            reader.seek(off)
            name = reader.read_offset_string() or ""
            if name:
                out[name] = idx
        except Exception:
            continue
    return out


def discover_resources(data: bytes) -> ResourceIndex:
    sprites = _list_chunk_names(data, "SPRT")
    objects = _list_chunk_names(data, "OBJT")
    sprite_ids = sorted({i for i in sprites.values() if 1 <= i <= 20000})
    gen_ids: list[int] = []
    seen: set[int] = set()
    for name, idx in objects.items():
        low = name.lower()
        if not any(n in low for n in _GEN_NEEDLES):
            continue
        # Skip pure monster controllers that are not gens when possible
        if low.startswith("obj_") and "monster" in low and "gen" not in low:
            continue
        if idx in seen:
            continue
        seen.add(idx)
        gen_ids.append(idx)
    # Prefer known host gens first if present
    preferred = []
    for hn in _HOST_GEN_NAMES:
        if hn in objects and objects[hn] not in preferred:
            preferred.append(objects[hn])
    rest = [g for g in gen_ids if g not in preferred]
    random.Random(666).shuffle(rest)
    return ResourceIndex(
        sprites=sprites,
        objects=objects,
        sprite_ids=sprite_ids,
        gen_object_ids=preferred + rest,
    )


def _find_named_code(data: bytes, names: tuple[str, ...]) -> list[tuple[str, int, int]]:
    want = {n.lower() for n in names}
    out = []
    for name, off, length in _find_code_entries(BinaryReader(data)):
        if name.lower() in want or any(name.lower().endswith(n.lower()) for n in names):
            out.append((name, off, length))
    return out


def _scan_pushi(data: bytes, bc_off: int, length: int) -> list[tuple[int, int]]:
    """Return list of (abs_offset, imm) for PushI-like words in a code blob."""
    hits = []
    pos = 0
    end = min(length, len(data) - bc_off)
    while pos + 4 <= end:
        word = struct.unpack_from("<I", data, bc_off + pos)[0]
        op = (word >> 24) & 0xFF
        if op in (OP_PUSHI, 0xC0):
            hits.append((bc_off + pos, push_imm(word)))
        pos += 4
    return hits


def _scan_branches(data: bytes, bc_off: int, length: int) -> list[tuple[int, int, int]]:
    """Return (abs_offset, opcode, word) for B/BT/BF in blob."""
    hits = []
    pos = 0
    end = min(length, len(data) - bc_off)
    while pos + 4 <= end:
        word = struct.unpack_from("<I", data, bc_off + pos)[0]
        op = (word >> 24) & 0xFF
        if op in (OP_B, OP_BT, OP_BF):
            hits.append((bc_off + pos, op, word))
        pos += 4
    return hits


def build_amalgomation_plan(data: bytes) -> AmalgomationPlan:
    res = discover_resources(data)
    plan = AmalgomationPlan(resources=res)

    host_sprite_ids = {
        res.sprites[n] for n in _HOST_SPRITE_NAMES if n in res.sprites
    }
    # Also accept any spr_*endogeny*
    for name, idx in res.sprites.items():
        if "endogeny" in name.lower():
            host_sprite_ids.add(idx)

    host_gen_ids = {
        res.objects[n] for n in _HOST_GEN_NAMES if n in res.objects
    }
    for name, idx in res.objects.items():
        low = name.lower()
        if "amalgam_rocketdog" in low or "amalgam_laserdog" in low:
            host_gen_ids.add(idx)

    for _name, off, length in _find_named_code(data, _DRAW_NAMES):
        for abs_off, imm in _scan_pushi(data, off, length):
            if imm in host_sprite_ids or (host_sprite_ids and imm in host_sprite_ids):
                word = struct.unpack_from("<I", data, abs_off)[0]
                plan.sprite_sites.append(PatchSite(abs_off, word, "sprite"))
            elif not host_sprite_ids and 1 <= imm <= 8000:
                # Fallback: first few PushIs in Draw are usually sprite ids
                word = struct.unpack_from("<I", data, abs_off)[0]
                if len([s for s in plan.sprite_sites if s.kind == "sprite"]) < 4:
                    plan.sprite_sites.append(PatchSite(abs_off, word, "sprite"))

    gen_set = set(res.gen_object_ids[:120])
    for _name, off, length in _find_named_code(data, _STEP_NAMES):
        pushis = _scan_pushi(data, off, length)
        for abs_off, imm in pushis:
            word = struct.unpack_from("<I", data, abs_off)[0]
            if imm in host_gen_ids or imm in gen_set:
                plan.attack_sites.append(PatchSite(abs_off, word, "attack"))
            elif imm == 10:
                # global.firingrate = 10 in stock Endogeny
                plan.firingrate_sites.append(PatchSite(abs_off, word, "firingrate"))
            elif imm in (999999, 222):
                plan.mercymod_sites.append(PatchSite(abs_off, word, "mercy"))
        # Soften BF between the first two host-gen creates so both patterns can fire
        host_hits = [s for s in plan.attack_sites if push_imm(s.original) in host_gen_ids]
        if len(host_hits) >= 2:
            lo = min(s.offset for s in host_hits[:2])
            hi = max(s.offset for s in host_hits[:2])
            for abs_off, op, word in _scan_branches(data, off, length):
                if lo <= abs_off <= hi and op == OP_BF:
                    plan.branch_sites.append(PatchSite(abs_off, word, "branch_bf"))

    # Nested gens: Alarm/Create on rocketdog/laserdog — more stack slots as layers grow
    nested_names = tuple(
        f"gml_Object_{n}_{suffix}"
        for n in ("obj_amalgam_rocketdog", "obj_amalgam_laserdog")
        for suffix in ("Alarm_0", "Alarm_1", "Alarm_2", "Alarm_3", "Alarm_4", "Step_0", "Create_0")
    )
    for _name, off, length in _find_named_code(data, nested_names):
        for abs_off, imm in _scan_pushi(data, off, length):
            if imm in gen_set or imm in host_gen_ids:
                word = struct.unpack_from("<I", data, abs_off)[0]
                plan.attack_sites.append(PatchSite(abs_off, word, "attack_nested"))

    # De-dupe sites by offset
    seen_off: set[int] = set()
    uniq: list[PatchSite] = []
    for s in plan.attack_sites:
        if s.offset in seen_off:
            continue
        seen_off.add(s.offset)
        uniq.append(s)
    plan.attack_sites = uniq[:16]

    return plan


def restore_amalgomation_backup_if_any(data_win: str | Path) -> tuple[bool, str]:
    """
    Undo prior Amalgomation disk corruption (string overflows / branch hacks).
    Returns (restored, message).
    """
    path = Path(data_win)
    bak = path.with_suffix(path.suffix + ".amalgobak")
    if not bak.is_file():
        return False, ""
    try:
        current = path.read_bytes()
        clean = bak.read_bytes()
    except OSError as exc:
        return False, f"Could not read amalgomation backup: {exc}"
    if current == clean:
        return False, ""
    try:
        path.write_bytes(clean)
    except OSError as exc:
        return False, f"Could not restore amalgomation backup: {exc}"
    return True, (
        "Restored data.win from .amalgobak (previous Amalgomation install corrupted it). "
        "Close Undertale completely, click Launch Undertale, load your save, then enter 666 again."
    )


def prepare_amalgomation_plan(data_win: str | Path) -> tuple[bool, str, AmalgomationPlan]:
    """
    Index sprites/attack sites only. Does NOT rewrite data.win structure
    (earlier installs corrupted strings and broke fight start).
    """
    path = Path(data_win)
    if not path.is_file():
        return False, "data.win missing", AmalgomationPlan()
    restored, restore_msg = restore_amalgomation_backup_if_any(path)
    if restored:
        # Caller must relaunch — in-memory FORM is still dirty.
        return False, restore_msg, AmalgomationPlan()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return False, str(exc), AmalgomationPlan()
    plan = build_amalgomation_plan(raw)
    ok = bool(plan.resources.sprite_ids) or bool(plan.sprite_sites) or bool(plan.attack_sites)
    msg = (
        f"Amalgomation ready (sprite sites={len(plan.sprite_sites)}, "
        f"attack sites={len(plan.attack_sites)}, "
        f"gens={len(plan.resources.gen_object_ids)}, "
        f"spritepool={len(plan.resources.sprite_ids)})"
    )
    return ok, msg, plan


def install_amalgomation_into_data_win(data_win: str | Path) -> tuple[bool, str, AmalgomationPlan]:
    """Backward-compatible name — now non-destructive (plan only). """
    return prepare_amalgomation_plan(data_win)


def scramble_u32_candidates(
    pid: int,
    candidates: list[int],
    low: int,
    high: int,
    *,
    limit: int = 12,
) -> int:
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
                if idx % 4 == 0:
                    hits.append(base + idx)
                start = idx + 4
            if len(hits) >= max_hits:
                break
    finally:
        if kernel32:
            kernel32.CloseHandle(handle)
    return hits


class AmalgomationDirector:
    """Silent in-process chaos loop — no UI window."""

    def __init__(self, data_win: Path, plan: AmalgomationPlan):
        self.data_win = Path(data_win)
        self.plan = plan
        self.state = ChaosState()
        self.rng = random.Random()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._player_hp_addrs: list[int] = []
        self._monster_hp_addrs: list[int] = []
        self._monster_df_addrs: list[int] = []
        self._tick_count = 0
        self._file_size = self.data_win.stat().st_size if self.data_win.is_file() else None
        self._active_attack_slots: list[int] = []  # object ids currently stacked

    def start(self) -> None:
        self.state = ChaosState(running=True, layer=1, rounds=0, stack=[])
        self._stop.clear()
        self._tick_count = 0
        gens = self.plan.resources.gen_object_ids or [1]
        first = self.rng.choice(gens)
        self._active_attack_slots = [first]
        self.state.stack = [self._label_for_gen(first)]
        self._prime_memory_targets()
        self._apply_attack_slots()
        self._morph_sprites()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.state.running = False
        self._stop.set()

    def _label_for_gen(self, oid: int) -> str:
        for name, idx in self.plan.resources.objects.items():
            if idx == oid:
                return name.replace("obj_", "")
        return f"gen#{oid}"

    def _prime_memory_targets(self) -> None:
        if not is_windows():
            return
        pid = find_undertale_pid()
        if not pid:
            return
        try:
            self._player_hp_addrs = find_int32_addresses(pid, 20, max_hits=24)
            self._monster_hp_addrs = []
            for seed in (50, 100, 150, 200, 300, 500, 1000, 1500):
                self._monster_hp_addrs.extend(find_int32_addresses(pid, seed, max_hits=6))
            self._monster_hp_addrs = list(dict.fromkeys(self._monster_hp_addrs))[:48]
            self._monster_df_addrs = []
            for seed in (0, 1, 2, 3, 4, 5, 10, 20, 25):
                self._monster_df_addrs.extend(find_int32_addresses(pid, seed, max_hits=4))
            self._monster_df_addrs = list(dict.fromkeys(self._monster_df_addrs))[:24]
        except Exception:
            self._player_hp_addrs = []
            self._monster_hp_addrs = []
            self._monster_df_addrs = []

    def _write_site_value(self, site: PatchSite, value: int) -> None:
        word = (site.original & 0xFFFF0000) | (int(value) & 0xFFFF)
        if not is_windows():
            return
        pid = find_undertale_pid()
        if not pid or not self._file_size:
            return
        try:
            write_int32_in_running_game(
                pid, site.offset, word, expected_size=self._file_size
            )
        except Exception:
            try:
                patch_int32_in_data_win_image(self.data_win, site.offset, word)
            except Exception:
                pass

    def _morph_sprites(self) -> None:
        pool = self.plan.resources.sprite_ids
        if not pool or not self.plan.sprite_sites:
            return
        # Each site gets a different random sprite → mismatched file-meat body
        used: set[int] = set()
        for site in self.plan.sprite_sites:
            choices = [s for s in pool if s not in used] or pool
            pick = self.rng.choice(choices)
            used.add(pick)
            self._write_site_value(site, pick)

    def _apply_attack_slots(self) -> None:
        if not self.plan.attack_sites:
            return
        slots = list(self._active_attack_slots) or [self.rng.choice(self.plan.resources.gen_object_ids or [1])]
        # Assign stacked gens across all known attack PushI sites (cycle)
        for i, site in enumerate(self.plan.attack_sites):
            oid = slots[i % len(slots)]
            self._write_site_value(site, oid)
        # As layers grow, crank firing rate down (more bullets)
        rate = max(1, 10 - self.state.layer * 2)
        for site in self.plan.firingrate_sites[:4]:
            self._write_site_value(site, rate)

    def _add_stacked_attack(self) -> None:
        gens = self.plan.resources.gen_object_ids
        if not gens:
            return
        used = set(self._active_attack_slots)
        choices = [g for g in gens if g not in used] or gens
        nxt = self.rng.choice(choices)
        self._active_attack_slots.append(nxt)
        self.state.stack.append(self._label_for_gen(nxt))
        self.state.layer = len(self._active_attack_slots)
        self._apply_attack_slots()

    def tick(self) -> ChaosState:
        if not self.state.running:
            return self.state
        self._tick_count += 1
        st = self.state

        st.fake_hp = self.rng.randint(1, 99999)
        st.fake_df = self.rng.randint(0, 999)
        st.fake_damage = self.rng.choice(
            [0, 1, 9, 99, 999, 9999, 99999, -1, 32767, self.rng.randint(0, 999999)]
        )

        # Appearance: constantly change between random game sprites
        self._morph_sprites()

        # Every round (~4s): swap the newest attack pattern for another random one
        if self._tick_count % 4 == 0:
            st.rounds += 1
            gens = self.plan.resources.gen_object_ids
            if gens and self._active_attack_slots:
                self._active_attack_slots[-1] = self.rng.choice(gens)
                st.stack[-1] = self._label_for_gen(self._active_attack_slots[-1])
            self._apply_attack_slots()
            # Every 2 rounds: stack another attack
            if st.rounds > 0 and st.rounds % 2 == 0:
                self._add_stacked_attack()
                if find_undertale_hwnd():
                    _send_key_to_undertale(VK_F6, presses=1)

        # HP / armor scramble every second
        pid = find_undertale_pid()
        if pid and is_windows():
            try:
                scramble_u32_candidates(pid, self._monster_hp_addrs, 1, 99999, limit=12)
                scramble_u32_candidates(pid, self._monster_df_addrs, 0, 999, limit=8)
                # Glitch damage readouts — poke common damage ints
                dmg_addrs = find_int32_addresses(pid, 0, max_hits=8) if self._tick_count % 5 == 0 else []
                if dmg_addrs:
                    scramble_u32_candidates(
                        pid, dmg_addrs, -9, 99999, limit=6
                    )
                # Escalate: chip then kill the player
                if st.layer >= 3:
                    scramble_u32_candidates(
                        pid, self._player_hp_addrs, 1, max(2, 14 - st.layer), limit=6
                    )
                if st.layer >= 6:
                    scramble_u32_candidates(pid, self._player_hp_addrs, 0, 1, limit=10)
                if st.layer >= 8:
                    scramble_u32_candidates(pid, self._player_hp_addrs, 0, 0, limit=12)
            except Exception:
                pass

        return st

    def _loop(self) -> None:
        while not self._stop.is_set() and self.state.running:
            try:
                self.tick()
            except Exception:
                pass
            self._stop.wait(1.0)


def stop_amalgomation_director() -> None:
    global _ACTIVE_DIRECTOR
    with _DIRECTOR_LOCK:
        if _ACTIVE_DIRECTOR is not None:
            _ACTIVE_DIRECTOR.stop()
            _ACTIVE_DIRECTOR = None


def start_amalgomation_fight(
    *,
    data_win: str | Path | None,
    save_folder: str | Path | None = None,
) -> tuple[bool, str]:
    """Start host fight first, then morph it live — no extra windows, no disk corruption."""
    if not data_win or not Path(data_win).is_file():
        return False, "Open your Undertale folder (data.win) first."
    if not undertale_is_running():
        return (
            False,
            "Launch Undertale, load a save, stand in the overworld, then enter 666 again.",
        )

    stop_amalgomation_director()
    ok_inst, inst_msg, plan = prepare_amalgomation_plan(data_win)
    if not ok_inst:
        # Includes "close and relaunch" after restoring a corrupted backup.
        return False, inst_msg

    # Fight first — director only starts after the battle has time to appear.
    ok, msg = start_fight(
        HOST_BATTLEGROUP,
        data_win=data_win,
        ensure_debug=True,
        save_folder=save_folder,
    )
    if not ok:
        return False, f"Amalgomation fight failed to start: {msg}"

    director = AmalgomationDirector(Path(data_win), plan)
    with _DIRECTOR_LOCK:
        global _ACTIVE_DIRECTOR
        _ACTIVE_DIRECTOR = director

    def _boot() -> None:
        # One more Home burst after focus settles, then morph once battle exists.
        time.sleep(0.9)
        if not undertale_is_running():
            return
        from .battles import VK_HOME_KEY

        if find_undertale_hwnd():
            _send_key_to_undertale(0x1B, presses=1)  # Esc — leave menus
            time.sleep(0.08)
            _send_key_to_undertale(VK_HOME_KEY, presses=3)
        time.sleep(1.6)
        if undertale_is_running():
            director.start()

    threading.Thread(target=_boot, daemon=True).start()

    return (
        True,
        "AMALGOMATION — focus the Undertale window; the fight should start now. "
        "If not, press Home once in the overworld. "
        + inst_msg
        + " | "
        + msg,
    )


def open_amalgomation_ui(parent, *, data_win: Path | None, save_folder: Path | None, on_status=None):
    """Launch with no popup dialogs — status line only."""
    ok, msg = start_amalgomation_fight(data_win=data_win, save_folder=save_folder)
    if on_status:
        on_status(msg if ok else f"AMALGOMATION: {msg}")
    # Intentionally no messagebox — user wants only the game window.
