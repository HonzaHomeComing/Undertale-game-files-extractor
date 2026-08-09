"""Debug Toolkit window: launch game, edit stats/items, start fights."""

from __future__ import annotations

from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from .amalgomation import is_amalgomation_id, open_amalgomation_ui
from .battles import BATTLEGROUPS, RARE_BATTLEGROUPS, start_fight, start_random_rare_fight
from .chaos import (
    live_ruins_reset,
    rare_mode_enabled,
    randomize_room_gotos,
    restore_room_chaos,
    set_rare_encounters,
)
from .dogcheck import disable_dogcheck, dogcheck_likely_disabled
from .launcher import launch_undertale
from .live_teleport import enable_debug_mode, undertale_is_running
from .save_editor import (
    ARMORS,
    ITEMS,
    WEAPONS,
    PlayerStats,
    item_name,
    read_player_stats,
    write_player_stats,
)

COLORS = {
    "bg": "#e8e2d6",
    "panel": "#f4efe6",
    "ink": "#1c1915",
    "muted": "#5c564c",
    "accent": "#c45c26",
    "accent_hover": "#a64b1c",
    "border": "#d2c8b6",
    "success": "#2f6b4f",
}


class DebugToolkit(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        *,
        data_win: Path | None,
        save_dir: Path | None,
        on_status=None,
    ):
        super().__init__(master)
        self.title("Undertale Debug Toolkit")
        self.geometry("680x620")
        self.minsize(560, 520)
        self.configure(fg_color=COLORS["bg"])
        self.data_win = Path(data_win) if data_win else None
        self.save_dir = Path(save_dir) if save_dir else None
        self.on_status = on_status
        self._stats = PlayerStats()
        self._inv_vars: list[ctk.StringVar] = []
        self.var_rare = ctk.BooleanVar(value=False)

        ctk.CTkLabel(
            self,
            text="Debug Toolkit",
            font=ctk.CTkFont(family="Courier New", size=22, weight="bold"),
            text_color=COLORS["ink"],
        ).pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(
            self,
            text="Launch, edit stats/items, fights, Ruins reset, room chaos, rare encounters.",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=16, pady=(0, 8))

        launch_row = ctk.CTkFrame(self, fg_color="transparent")
        launch_row.pack(fill="x", padx=16, pady=4)
        ctk.CTkButton(
            launch_row,
            text="Launch Patched Undertale",
            command=self.launch_patched,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#fffaf2",
            width=200,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            launch_row,
            text="Prepare patches",
            command=self.prepare_patches,
            fg_color=COLORS["ink"],
            hover_color="#33302b",
            text_color="#fffaf2",
            width=140,
        ).pack(side="left")

        self.tabs = ctk.CTkTabview(self, fg_color=COLORS["panel"])
        self.tabs.pack(fill="both", expand=True, padx=16, pady=8)
        self.tabs.add("Stats")
        self.tabs.add("Items")
        self.tabs.add("Fights")
        self.tabs.add("Chaos")
        self._build_stats_tab(self.tabs.tab("Stats"))
        self._build_items_tab(self.tabs.tab("Items"))
        self._build_fights_tab(self.tabs.tab("Fights"))
        self._build_chaos_tab(self.tabs.tab("Chaos"))

        self.status = ctk.CTkLabel(self, text="", text_color=COLORS["muted"], wraplength=640)
        self.status.pack(anchor="w", padx=16, pady=(0, 12))

        self.after(100, self.reload_from_save)

    def _say(self, msg: str) -> None:
        self.status.configure(text=msg)
        if self.on_status:
            self.on_status(msg)

    def prepare_patches(self) -> None:
        if not self.data_win or not self.data_win.is_file():
            messagebox.showinfo("No game", "Open your Undertale folder in the main window first.", parent=self)
            return
        if undertale_is_running():
            messagebox.showwarning(
                "Close Undertale first",
                "Close Undertale completely before preparing patches.",
                parent=self,
            )
            return
        notes = []
        try:
            if enable_debug_mode(self.data_win, backup=True):
                notes.append("debug ON")
        except Exception as exc:
            notes.append(f"debug failed: {exc}")
        try:
            ok, msg = disable_dogcheck(self.data_win, backup=True)
            ok = ok and dogcheck_likely_disabled(self.data_win)
            notes.append("dogcheck OFF" if ok else f"dogcheck still ON — {msg}")
        except Exception as exc:
            notes.append(f"dogcheck failed: {exc}")
        messagebox.showinfo("Patches", "\n".join(notes) + "\n\nThen click Launch Patched Undertale.", parent=self)
        self._say("Patches: " + "; ".join(notes))

    def launch_patched(self) -> None:
        if not self.data_win or not self.data_win.is_file():
            messagebox.showinfo("No game", "Open your Undertale folder in the main window first.", parent=self)
            return
        if undertale_is_running():
            messagebox.showinfo("Already running", "Undertale is already open.", parent=self)
            return
        # Ensure debug at least; dogcheck best-effort (safe stub won't brick launch).
        try:
            enable_debug_mode(self.data_win, backup=True)
        except Exception:
            pass
        try:
            disable_dogcheck(self.data_win, backup=True)
        except Exception:
            pass
        ok, msg = launch_undertale(data_win=self.data_win)
        if ok:
            messagebox.showinfo(
                "Launched",
                msg
                + "\n\nLoad your save (Continue). Use Stats/Items/Fights tabs anytime.\n"
                "For fights: pick a battle → Start Fight (or press Home in-game).",
                parent=self,
            )
            self._say(msg)
        else:
            messagebox.showerror("Launch failed", msg, parent=self)

    def reload_from_save(self) -> None:
        try:
            self._stats = read_player_stats(self.save_dir)
        except Exception as exc:
            self._say(f"Could not read save: {exc}")
            return
        s = self._stats
        self.var_name.set(s.name)
        self.var_love.set(str(s.love))
        self.var_hp.set(str(s.hp))
        self.var_maxhp.set(str(s.max_hp))
        self.var_at.set(str(s.at))
        self.var_df.set(str(s.df))
        self.var_exp.set(str(s.exp))
        self.var_gold.set(str(s.gold))
        self.var_kills.set(str(s.kills))
        for i, var in enumerate(self._inv_vars):
            iid = s.inventory[i] if s.inventory and i < len(s.inventory) else 0
            var.set(f"{iid}: {item_name(iid)}")
        self.var_weapon.set(f"{s.weapon}: {WEAPONS.get(s.weapon, item_name(s.weapon))}")
        self.var_armor.set(f"{s.armor}: {ARMORS.get(s.armor, item_name(s.armor))}")
        try:
            self.var_rare.set(rare_mode_enabled(self.save_dir))
        except Exception:
            pass
        self._say(f"Loaded save ({s.name}, LV {s.love}, room {s.room}).")

    def _build_stats_tab(self, tab) -> None:
        grid = ctk.CTkFrame(tab, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=8, pady=8)
        self.var_name = ctk.StringVar()
        self.var_love = ctk.StringVar()
        self.var_hp = ctk.StringVar()
        self.var_maxhp = ctk.StringVar()
        self.var_at = ctk.StringVar()
        self.var_df = ctk.StringVar()
        self.var_exp = ctk.StringVar()
        self.var_gold = ctk.StringVar()
        self.var_kills = ctk.StringVar()
        fields = [
            ("Name", self.var_name),
            ("LOVE", self.var_love),
            ("HP", self.var_hp),
            ("Max HP", self.var_maxhp),
            ("AT", self.var_at),
            ("DF", self.var_df),
            ("EXP", self.var_exp),
            ("Gold", self.var_gold),
            ("Kills", self.var_kills),
        ]
        for row, (label, var) in enumerate(fields):
            ctk.CTkLabel(grid, text=label, width=80, anchor="w").grid(row=row, column=0, sticky="w", pady=3)
            ctk.CTkEntry(grid, textvariable=var, width=220).grid(row=row, column=1, sticky="w", pady=3)
        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.pack(fill="x", padx=8, pady=8)
        ctk.CTkButton(btns, text="Reload", command=self.reload_from_save, width=100).pack(side="left", padx=4)
        ctk.CTkButton(
            btns,
            text="Save stats",
            command=self.save_stats,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            width=120,
        ).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Max out", command=self.max_stats, width=100).pack(side="left", padx=4)

    def _parse_stats(self) -> PlayerStats:
        s = self._stats

        def num(var, default=0):
            try:
                return int(float(var.get().strip()))
            except ValueError:
                return default

        return PlayerStats(
            name=self.var_name.get().strip() or s.name,
            love=num(self.var_love, s.love),
            hp=num(self.var_hp, s.hp),
            max_hp=num(self.var_maxhp, s.max_hp),
            at=num(self.var_at, s.at),
            weapon_at=s.weapon_at,
            df=num(self.var_df, s.df),
            armor_df=s.armor_df,
            exp=num(self.var_exp, s.exp),
            gold=num(self.var_gold, s.gold),
            kills=num(self.var_kills, s.kills),
            inventory=list(s.inventory or [0] * 8),
            weapon=s.weapon,
            armor=s.armor,
            room=s.room,
        )

    def max_stats(self) -> None:
        self.var_love.set("20")
        self.var_hp.set("99")
        self.var_maxhp.set("99")
        self.var_at.set("99")
        self.var_df.set("99")
        self.var_exp.set("99999")
        self.var_gold.set("99999")

    def save_stats(self) -> None:
        try:
            stats = self._parse_stats()
            # Keep inventory/equip from current _stats / item tab vars
            stats.inventory = self._inventory_from_vars()
            stats.weapon = self._id_from_combo(self.var_weapon.get(), stats.weapon)
            stats.armor = self._id_from_combo(self.var_armor.get(), stats.armor)
            path = write_player_stats(stats, self.save_dir, backup=True)
            self._stats = stats
            self._say(f"Saved stats to {path}")
            messagebox.showinfo("Saved", f"Stats written to:\n{path}\n\nLoad/Continue in Undertale (or press L).", parent=self)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)

    def _id_from_combo(self, text: str, default: int = 0) -> int:
        text = (text or "").strip()
        if not text:
            return default
        try:
            return int(text.split(":", 1)[0].strip())
        except ValueError:
            return default

    def _inventory_from_vars(self) -> list[int]:
        return [self._id_from_combo(v.get(), 0) for v in self._inv_vars]

    def _build_items_tab(self, tab) -> None:
        frame = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        item_choices = [f"{i}: {name}" for i, name in enumerate(ITEMS)]
        self._inv_vars = []
        for slot in range(8):
            ctk.CTkLabel(frame, text=f"Slot {slot + 1}", width=70, anchor="w").grid(
                row=slot, column=0, sticky="w", pady=3
            )
            var = ctk.StringVar(value="0: Empty")
            self._inv_vars.append(var)
            ctk.CTkOptionMenu(frame, variable=var, values=item_choices, width=280).grid(
                row=slot, column=1, sticky="w", pady=3
            )
        ctk.CTkLabel(frame, text="Weapon", width=70, anchor="w").grid(row=8, column=0, sticky="w", pady=6)
        self.var_weapon = ctk.StringVar(value="3: Stick")
        ctk.CTkOptionMenu(
            frame,
            variable=self.var_weapon,
            values=[f"{i}: {n}" for i, n in sorted(WEAPONS.items())],
            width=280,
        ).grid(row=8, column=1, sticky="w", pady=6)
        ctk.CTkLabel(frame, text="Armor", width=70, anchor="w").grid(row=9, column=0, sticky="w", pady=3)
        self.var_armor = ctk.StringVar(value="4: Bandage")
        ctk.CTkOptionMenu(
            frame,
            variable=self.var_armor,
            values=[f"{i}: {n}" for i, n in sorted(ARMORS.items())],
            width=280,
        ).grid(row=9, column=1, sticky="w", pady=3)

        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.pack(fill="x", padx=8, pady=8)
        ctk.CTkButton(btns, text="Reload", command=self.reload_from_save, width=100).pack(side="left", padx=4)
        ctk.CTkButton(
            btns,
            text="Save items",
            command=self.save_items,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            width=120,
        ).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Fill pies", command=self.fill_pies, width=100).pack(side="left", padx=4)

    def fill_pies(self) -> None:
        pie = next((f"{i}: {n}" for i, n in enumerate(ITEMS) if n == "Butterscotch Pie"), "11: Butterscotch Pie")
        for var in self._inv_vars:
            var.set(pie)

    def save_items(self) -> None:
        try:
            stats = self._parse_stats()
            stats.inventory = self._inventory_from_vars()
            stats.weapon = self._id_from_combo(self.var_weapon.get(), 3)
            stats.armor = self._id_from_combo(self.var_armor.get(), 4)
            path = write_player_stats(stats, self.save_dir, backup=True)
            self._stats = stats
            self._say(f"Saved items to {path}")
            messagebox.showinfo("Saved", f"Inventory written to:\n{path}", parent=self)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)

    def _build_fights_tab(self, tab) -> None:
        ctk.CTkLabel(
            tab,
            text="Requires debug mode. Discovers the Home battlegroup in data.win "
            "(and live bytecode), patches it, then sends Home — not stuck on Mettaton.",
            text_color=COLORS["muted"],
            wraplength=600,
        ).pack(anchor="w", padx=8, pady=6)
        self.fight_var = ctk.StringVar(
            value=f"{BATTLEGROUPS[0].id}: {BATTLEGROUPS[0].name}"
        )
        values = [f"{b.id}: {b.name}" for b in BATTLEGROUPS]
        ctk.CTkOptionMenu(tab, variable=self.fight_var, values=values, width=360).pack(
            anchor="w", padx=8, pady=8
        )
        custom_row = ctk.CTkFrame(tab, fg_color="transparent")
        custom_row.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(custom_row, text="Or id:").pack(side="left")
        self.custom_fight = ctk.StringVar()
        ctk.CTkEntry(custom_row, textvariable=self.custom_fight, width=80).pack(side="left", padx=6)
        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.pack(fill="x", padx=8, pady=12)
        ctk.CTkButton(
            btn_row,
            text="Start Fight",
            command=self.do_start_fight,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            width=140,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row,
            text="Start rarest fight",
            command=self.do_start_rare_fight,
            fg_color=COLORS["ink"],
            hover_color="#33302b",
            width=160,
        ).pack(side="left")
        ctk.CTkButton(
            tab,
            text="AMALGOMATION AUTO (closes game → patches → launches → skips intro → fight)",
            command=self.do_amalgomation_auto,
            fg_color="#8b1e1e",
            hover_color="#6e1515",
            text_color="#f2e6d8",
            width=560,
            height=36,
        ).pack(anchor="w", padx=8, pady=(4, 8))
        ctk.CTkLabel(
            tab,
            text="If the last fight was Mettaton/glitched: Restore data.win → Enable live patches → Launch → overworld → Start Fight.\n"
            "Or id 666 / AMALGOMATION AUTO: one click does close → restore/patch → launch → Continue → Home fight.",
            text_color=COLORS["muted"],
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=8, pady=4)

    def do_amalgomation_auto(self) -> None:
        if not self.data_win or not self.data_win.is_file():
            messagebox.showinfo("No game", "Open your Undertale folder in the main window first.", parent=self)
            return
        self.custom_fight.set("666")
        open_amalgomation_ui(
            self,
            data_win=self.data_win,
            save_folder=self.save_dir,
            on_status=self._say,
        )

    def do_start_fight(self) -> None:
        try:
            if self.custom_fight.get().strip():
                bg = int(self.custom_fight.get().strip())
            else:
                bg = self._id_from_combo(self.fight_var.get(), 0)
        except ValueError:
            messagebox.showerror("Bad id", "Enter a numeric battlegroup id.", parent=self)
            return
        if is_amalgomation_id(bg):
            open_amalgomation_ui(
                self,
                data_win=self.data_win,
                save_folder=self.save_dir,
                on_status=self._say,
            )
            return
        ok, msg = start_fight(
            bg,
            data_win=self.data_win,
            ensure_debug=True,
            save_folder=self.save_dir,
        )
        if ok:
            self._say(msg)
            messagebox.showinfo("Fight", msg, parent=self)
        else:
            self._say(msg)
            messagebox.showwarning("Fight", msg, parent=self)

    def do_start_rare_fight(self) -> None:
        ok, msg = start_random_rare_fight(data_win=self.data_win, save_folder=self.save_dir)
        if ok:
            self._say(msg)
            messagebox.showinfo("Rare fight", msg, parent=self)
        else:
            self._say(msg)
            messagebox.showwarning("Rare fight", msg, parent=self)

    def _build_chaos_tab(self, tab) -> None:
        ctk.CTkLabel(
            tab,
            text="Live Ruins reset and room chaos. Rare toggle boosts FUN and prefers rare fights.",
            text_color=COLORS["muted"],
            wraplength=600,
        ).pack(anchor="w", padx=8, pady=6)

        ctk.CTkButton(
            tab,
            text="Ruins reset (live)",
            command=self.do_ruins_reset,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            width=220,
        ).pack(anchor="w", padx=8, pady=8)
        ctk.CTkLabel(
            tab,
            text="First Ruins SAVE (Entrance), LOVE 1 / HP 20 / EXP·gold·kills 0, Stick+Bandage. "
            "Works while the game is open (writes save + L reload).",
            text_color=COLORS["muted"],
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 10))

        ctk.CTkButton(
            tab,
            text="Randomize rooms",
            command=self.do_randomize_rooms,
            fg_color=COLORS["ink"],
            hover_color="#33302b",
            width=220,
        ).pack(anchor="w", padx=8, pady=8)
        ctk.CTkButton(
            tab,
            text="Undo room chaos",
            command=self.do_undo_room_chaos,
            fg_color=COLORS["muted"],
            hover_color="#4a453c",
            width=220,
        ).pack(anchor="w", padx=8, pady=(0, 8))
        ctk.CTkLabel(
            tab,
            text="Shuffles door/warp destinations only (safe allowlist — will not touch "
            "file I/O scripts). Backs up data.win.roomchaosbak. Restart Undertale after. "
            "If the game shows a Code Error on boot, click Undo room chaos or Restore data.win.",
            text_color=COLORS["muted"],
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 10))

        try:
            self.var_rare.set(rare_mode_enabled(self.save_dir))
        except Exception:
            self.var_rare.set(False)
        ctk.CTkCheckBox(
            tab,
            text="Guarantee rarest encounters",
            variable=self.var_rare,
            command=self.do_toggle_rare,
            text_color=COLORS["ink"],
        ).pack(anchor="w", padx=8, pady=8)
        rare_names = ", ".join(b.name for b in RARE_BATTLEGROUPS[:6]) + "…"
        ctk.CTkLabel(
            tab,
            text=f"Sets FUN=90, keeps a rare-mode flag, and unlocks rare fight helpers "
            f"({rare_names}). Toggle again to turn off.",
            text_color=COLORS["muted"],
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 8))

    def do_ruins_reset(self) -> None:
        if not messagebox.askyesno(
            "Ruins reset",
            "Reset stats to defaults and jump to the first Ruins SAVE while the game stays open?",
            parent=self,
        ):
            return
        ok, msg = live_ruins_reset(save_folder=self.save_dir, data_win=self.data_win)
        self._say(msg)
        if ok:
            self.reload_from_save()
            messagebox.showinfo("Ruins reset", msg, parent=self)
        else:
            messagebox.showerror("Ruins reset", msg, parent=self)

    def do_randomize_rooms(self) -> None:
        if not self.data_win or not self.data_win.is_file():
            messagebox.showinfo("No game", "Open your Undertale folder first.", parent=self)
            return
        if undertale_is_running():
            if not messagebox.askyesno(
                "Undertale is running",
                "Room chaos patches data.win on disk. Close Undertale after this and relaunch "
                "so the shuffle loads. Continue?",
                parent=self,
            ):
                return
        elif not messagebox.askyesno(
            "Randomize rooms",
            "Rewrite door/warp room transitions in data.win (backup created). Continue?",
            parent=self,
        ):
            return
        ok, msg, _mapping = randomize_room_gotos(self.data_win, backup=True)
        self._say(msg)
        if ok:
            messagebox.showinfo("Room chaos", msg, parent=self)
        else:
            messagebox.showerror("Room chaos", msg, parent=self)

    def do_undo_room_chaos(self) -> None:
        if not self.data_win or not self.data_win.is_file():
            messagebox.showinfo("No game", "Open your Undertale folder first.", parent=self)
            return
        if undertale_is_running():
            messagebox.showwarning(
                "Close Undertale first",
                "Close Undertale completely, then Undo room chaos.",
                parent=self,
            )
            return
        ok, msg = restore_room_chaos(self.data_win)
        self._say(msg)
        if ok:
            messagebox.showinfo("Restored", msg, parent=self)
        else:
            messagebox.showerror("Restore failed", msg, parent=self)

    def do_toggle_rare(self) -> None:
        enabled = bool(self.var_rare.get())
        ok, msg = set_rare_encounters(
            enabled,
            save_folder=self.save_dir,
            data_win=self.data_win,
            live_reload=True,
        )
        self._say(msg)
        if not ok:
            messagebox.showerror("Rare mode", msg, parent=self)
            self.var_rare.set(not enabled)
        else:
            messagebox.showinfo("Rare mode", msg, parent=self)
