"""Windowed browser for Undertale game files."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from .assets import AssetKind, GameAsset
from .dogcheck import (
    disable_dogcheck,
    dogcheck_exit_stubbed,
    is_dogcheck_room,
    restore_data_win_backup,
)
from .live_teleport import (
    debug_flag_enabled,
    enable_debug_mode,
    live_teleport_to_room,
    undertale_is_running,
)
from .parser import load_undertale_assets
from .teleport import (
    default_save_dir,
    find_undertale_save_dirs,
    friendly_room_label,
    read_save_info,
    teleport_to_room,
)

# Visual direction: ink-and-ember utility (not purple / cream-serif defaults)
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("dark-blue")

COLORS = {
    "bg": "#e8e2d6",
    "panel": "#f4efe6",
    "ink": "#1c1915",
    "muted": "#5c564c",
    "accent": "#c45c26",
    "accent_hover": "#a64b1c",
    "card": "#fffaf2",
    "card_hover": "#ffe8d2",
    "border": "#d2c8b6",
    "success": "#2f6b4f",
}

KIND_ORDER = [
    AssetKind.ROOM,
    AssetKind.SPRITE,
    AssetKind.TEXTURE,
    AssetKind.BACKGROUND,
    AssetKind.AUDIO,
    AssetKind.MUSIC,
    AssetKind.FONT,
    AssetKind.OTHER,
]

# Undertale has thousands of sprites — never build the whole grid at once.
PAGE_SIZE = 36


def _default_download_dir() -> Path:
    home = Path.home()
    for name in ("Downloads", "downloads"):
        candidate = home / name
        if candidate.is_dir():
            return candidate
    return home


class UndertaleExtractorApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Undertale File Extractor")
        self.geometry("1180x720")
        self.minsize(900, 560)
        self.configure(fg_color=COLORS["bg"])

        self.assets: list[GameAsset] = []
        self.filtered: list[GameAsset] = []
        self.selected: GameAsset | None = None
        self.current_kind: AssetKind | None = AssetKind.ROOM
        self.page = 0
        self.download_dir = _default_download_dir()
        self.save_dir = default_save_dir()
        self.data_win_path: Path | None = None
        self._live_room_addrs: list = []
        self._live_current_room: int | None = None
        self._thumb_cache: dict[str, ctk.CTkImage] = {}
        self._preview_image: ctk.CTkImage | None = None
        self._loading = False
        self.game_name = "Undertale"
        self._render_token = 0

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        header = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=0, height=72)
        header.grid(row=0, column=0, columnspan=3, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        brand = ctk.CTkLabel(
            header,
            text="UNDERTALE",
            font=ctk.CTkFont(family="Courier New", size=26, weight="bold"),
            text_color=COLORS["ink"],
        )
        brand.grid(row=0, column=0, padx=(20, 8), pady=(14, 0), sticky="w")

        subtitle = ctk.CTkLabel(
            header,
            text="Game File Extractor",
            font=ctk.CTkFont(family="Georgia", size=14),
            text_color=COLORS["muted"],
        )
        subtitle.grid(row=1, column=0, padx=(22, 8), pady=(0, 12), sticky="w")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=2, rowspan=2, padx=16, pady=12, sticky="e")

        self.open_btn = ctk.CTkButton(
            actions,
            text="Open Undertale Folder",
            command=self.open_game,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#fffaf2",
            font=ctk.CTkFont(size=13, weight="bold"),
            width=180,
        )
        self.open_btn.pack(side="left", padx=4)

        self.export_all_btn = ctk.CTkButton(
            actions,
            text="Export All Visible",
            command=self.export_all_visible,
            fg_color=COLORS["ink"],
            hover_color="#33302b",
            text_color="#fffaf2",
            width=140,
            state="disabled",
        )
        self.export_all_btn.pack(side="left", padx=4)

        self.dl_dir_btn = ctk.CTkButton(
            actions,
            text="Download Folder…",
            command=self.choose_download_dir,
            fg_color=COLORS["border"],
            hover_color="#c4baa8",
            text_color=COLORS["ink"],
            width=130,
        )
        self.dl_dir_btn.pack(side="left", padx=4)

        self.save_dir_btn = ctk.CTkButton(
            actions,
            text="Save Folder…",
            command=self.choose_save_dir,
            fg_color=COLORS["border"],
            hover_color="#c4baa8",
            text_color=COLORS["ink"],
            width=110,
        )
        self.save_dir_btn.pack(side="left", padx=4)

        self.restore_btn = ctk.CTkButton(
            actions,
            text="Restore data.win",
            command=self.restore_data_win,
            fg_color=COLORS["border"],
            hover_color="#c4baa8",
            text_color=COLORS["ink"],
            width=130,
            state="disabled",
        )
        self.restore_btn.pack(side="left", padx=4)

        self.patch_btn = ctk.CTkButton(
            actions,
            text="Enable live patches",
            command=self.enable_live_patches,
            fg_color=COLORS["border"],
            hover_color="#c4baa8",
            text_color=COLORS["ink"],
            width=140,
            state="disabled",
        )
        self.patch_btn.pack(side="left", padx=4)

        # Sidebar categories
        sidebar = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=0, width=200)
        sidebar.grid(row=1, column=0, sticky="nsw")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar,
            text="Categories",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["muted"],
        ).pack(anchor="w", padx=16, pady=(16, 8))

        self.kind_buttons: dict[AssetKind | None, ctk.CTkButton] = {}
        all_btn = self._make_kind_button(sidebar, "All files", None)
        all_btn.pack(fill="x", padx=12, pady=3)
        self.kind_buttons[None] = all_btn
        for kind in KIND_ORDER:
            btn = self._make_kind_button(sidebar, kind.value, kind)
            btn.pack(fill="x", padx=12, pady=3)
            self.kind_buttons[kind] = btn

        self.count_label = ctk.CTkLabel(
            sidebar,
            text="No files loaded",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
            wraplength=170,
            justify="left",
        )
        self.count_label.pack(anchor="w", padx=16, pady=20)

        # Main browser
        main = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        main.grid(row=1, column=1, sticky="nsew", padx=0)
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        search_row = ctk.CTkFrame(main, fg_color="transparent")
        search_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        search_row.grid_columnconfigure(0, weight=1)

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.apply_filter())
        self.search_entry = ctk.CTkEntry(
            search_row,
            textvariable=self.search_var,
            placeholder_text="Search files…",
            fg_color=COLORS["card"],
            border_color=COLORS["border"],
            text_color=COLORS["ink"],
            height=36,
        )
        self.search_entry.grid(row=0, column=0, sticky="ew")

        self.status_label = ctk.CTkLabel(
            search_row,
            text="Open your Undertale install folder to begin",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
        )
        self.status_label.grid(row=1, column=0, sticky="w", pady=(6, 0))

        pager = ctk.CTkFrame(search_row, fg_color="transparent")
        pager.grid(row=0, column=1, rowspan=2, padx=(12, 0))
        self.prev_btn = ctk.CTkButton(
            pager,
            text="◀ Prev",
            width=80,
            command=self.prev_page,
            fg_color=COLORS["border"],
            hover_color="#c4baa8",
            text_color=COLORS["ink"],
            state="disabled",
        )
        self.prev_btn.pack(side="left", padx=2)
        self.page_label = ctk.CTkLabel(
            pager,
            text="Page 0/0",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
            width=90,
        )
        self.page_label.pack(side="left", padx=4)
        self.next_btn = ctk.CTkButton(
            pager,
            text="Next ▶",
            width=80,
            command=self.next_page,
            fg_color=COLORS["border"],
            hover_color="#c4baa8",
            text_color=COLORS["ink"],
            state="disabled",
        )
        self.next_btn.pack(side="left", padx=2)

        self.scroll = ctk.CTkScrollableFrame(
            main,
            fg_color=COLORS["bg"],
            corner_radius=0,
        )
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)

        # Preview / download panel
        preview = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=0, width=280)
        preview.grid(row=1, column=2, sticky="nse")
        preview.grid_propagate(False)

        ctk.CTkLabel(
            preview,
            text="Preview",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["muted"],
        ).pack(anchor="w", padx=16, pady=(16, 8))

        self.preview_canvas = ctk.CTkLabel(
            preview,
            text="Select a file",
            width=240,
            height=240,
            fg_color=COLORS["card"],
            corner_radius=8,
            text_color=COLORS["muted"],
        )
        self.preview_canvas.pack(padx=16, pady=8)

        self.preview_name = ctk.CTkLabel(
            preview,
            text="",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLORS["ink"],
            wraplength=240,
            justify="left",
        )
        self.preview_name.pack(anchor="w", padx=16, pady=(8, 2))

        self.preview_meta = ctk.CTkLabel(
            preview,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
            wraplength=240,
            justify="left",
        )
        self.preview_meta.pack(anchor="w", padx=16, pady=(0, 12))

        self.download_btn = ctk.CTkButton(
            preview,
            text="Download",
            command=self.download_selected,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="#fffaf2",
            state="disabled",
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.download_btn.pack(fill="x", padx=16, pady=4)

        self.teleport_btn = ctk.CTkButton(
            preview,
            text="Enter Room In-Game",
            command=self.teleport_selected,
            fg_color=COLORS["success"],
            hover_color="#24553f",
            text_color="#fffaf2",
            state="disabled",
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.teleport_btn.pack(fill="x", padx=16, pady=4)

        self.save_as_btn = ctk.CTkButton(
            preview,
            text="Save As…",
            command=self.save_selected_as,
            fg_color=COLORS["ink"],
            hover_color="#33302b",
            text_color="#fffaf2",
            state="disabled",
            height=36,
        )
        self.save_as_btn.pack(fill="x", padx=16, pady=4)

        save_hint = "No Undertale save found yet"
        if self.save_dir:
            save_hint = f"Game save:\n{self.save_dir}"
        self.save_path_label = ctk.CTkLabel(
            preview,
            text=save_hint,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
            wraplength=240,
            justify="left",
        )
        self.save_path_label.pack(anchor="w", padx=16, pady=(8, 0))

        self.dl_path_label = ctk.CTkLabel(
            preview,
            text=f"Downloads to:\n{self.download_dir}",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
            wraplength=240,
            justify="left",
        )
        self.dl_path_label.pack(anchor="w", padx=16, pady=16)

        self._highlight_kind(AssetKind.ROOM)
        self._show_empty_state()

    def _make_kind_button(self, parent, label: str, kind: AssetKind | None) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent,
            text=label,
            anchor="w",
            fg_color="transparent",
            hover_color=COLORS["card_hover"],
            text_color=COLORS["ink"],
            command=lambda: self.set_kind(kind),
            height=34,
        )

    def _highlight_kind(self, kind: AssetKind | None) -> None:
        for k, btn in self.kind_buttons.items():
            if k == kind:
                btn.configure(fg_color=COLORS["card_hover"], text_color=COLORS["accent"])
            else:
                btn.configure(fg_color="transparent", text_color=COLORS["ink"])

    def _show_empty_state(self) -> None:
        for child in self.scroll.winfo_children():
            child.destroy()
        tip = ctk.CTkLabel(
            self.scroll,
            text=(
                "1. Click “Open Undertale Folder”\n"
                "2. Choose the folder that contains data.win\n"
                "3. Open the Rooms category\n"
                "4. Keep Undertale running, then click a room to enter it live\n"
                "5. Or browse sprites/audio and click to download"
            ),
            font=ctk.CTkFont(family="Georgia", size=16),
            text_color=COLORS["muted"],
            justify="left",
        )
        tip.pack(anchor="w", padx=24, pady=40)

    def open_game(self) -> None:
        if self._loading:
            return
        path = filedialog.askdirectory(title="Select Undertale install folder")
        if not path:
            file_path = filedialog.askopenfilename(
                title="Or select data.win",
                filetypes=[
                    ("Undertale data.win", "*.win"),
                    ("All files", "*.*"),
                ],
            )
            if not file_path:
                return
            path = file_path
        self._load_async(path)

    def _set_status(self, message: str) -> None:
        self.status_label.configure(text=message)

    def _load_async(self, path: str) -> None:
        self._loading = True
        self.open_btn.configure(state="disabled")
        self.export_all_btn.configure(state="disabled")
        self._set_status("Starting… this can take a minute for Undertale")
        for child in self.scroll.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self.scroll,
            text="Extracting game files…\nPlease wait — do not close the window.",
            font=ctk.CTkFont(family="Georgia", size=16),
            text_color=COLORS["muted"],
            justify="center",
        ).pack(pady=60)
        self.update_idletasks()

        def report(message: str) -> None:
            # Bind message as default arg so later updates don't overwrite earlier ones.
            self.after(0, lambda m=message: self._set_status(m))

        def work() -> None:
            try:
                result = load_undertale_assets(path, progress=report)
                self.after(0, lambda r=result: self._on_loaded(r))
            except MemoryError:
                self.after(
                    0,
                    lambda: self._on_load_error(
                        MemoryError(
                            "Ran out of memory while reading data.win. "
                            "Close other programs and try again."
                        )
                    ),
                )
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_load_error(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_loaded(self, result) -> None:
        try:
            self._loading = False
            self.open_btn.configure(state="normal")
            self.assets = result.assets
            self.data_win_path = Path(result.path)
            self.game_name = result.game_name or "Undertale"
            self.title(f"Undertale File Extractor — {self.game_name}")
            self._thumb_cache.clear()
            self._live_room_addrs = []
            self.export_all_btn.configure(state="normal")
            self.restore_btn.configure(state="normal")
            self.patch_btn.configure(state="normal")
            self.page = 0
            self._update_counts()
            # Do NOT patch data.win on open — that rewrote the install file and could
            # stop Undertale launching while / after this app loaded the folder.
            # Live teleport applies debug + dogcheck patches only when you click a room.
            try:
                if self.save_dir:
                    info = read_save_info(self.save_dir)
                    self._live_current_room = info.current_room
            except Exception:
                pass
            # Default to Rooms so teleporting is one click away.
            self.set_kind(AssetKind.ROOM)
            running = (
                "Undertale is open — click a room to jump live."
                if undertale_is_running()
                else "Start Undertale anytime — browsing does not lock or patch data.win."
            )
            warn = ""
            try:
                if dogcheck_exit_stubbed(self.data_win_path):
                    warn = (
                        " WARNING: broken dogcheck patch detected — click Restore data.win "
                        "or Enable live patches (auto-heals) before using live Load (L)."
                    )
                    messagebox.showwarning(
                        "Broken dogcheck patch",
                        "Your data.win has an old dogcheck Exit stub that crashes Undertale "
                        "when pressing L (debug load):\n\n"
                        "Variable obj_mainchara.dogcheck not set…\n\n"
                        "Close Undertale, then click Restore data.win "
                        "(or Enable live patches to auto-heal from backup).",
                    )
            except Exception:
                pass
            self._set_status(
                f"Loaded {len(self.assets)} files from {result.path.name}. {running} "
                f"Use Enable live patches (game closed) for room jumps.{warn}"
            )
        except Exception as exc:
            self._on_load_error(exc)

    def restore_data_win(self) -> None:
        if not self.data_win_path:
            messagebox.showinfo("No game open", "Open your Undertale folder first.")
            return
        if undertale_is_running():
            messagebox.showwarning(
                "Close Undertale first",
                "Close Undertale completely, then click Restore data.win again.",
            )
            return
        ok = messagebox.askokcancel(
            "Restore data.win?",
            "Replace data.win with the extractor backup "
            "(data.win.dogcheckbak / data.win.debugbak).\n\n"
            "Use this if Undertale crashes on L with a dogcheck error, "
            "or will not start after patching.",
        )
        if not ok:
            return
        success, msg = restore_data_win_backup(self.data_win_path)
        if success:
            messagebox.showinfo("Restored", msg)
            self._set_status(msg)
        else:
            messagebox.showerror("Restore failed", msg)

    def enable_live_patches(self) -> None:
        """Patch data.win for live room jumps — only while Undertale is closed."""
        if not self.data_win_path:
            messagebox.showinfo("No game open", "Open your Undertale folder first.")
            return
        if undertale_is_running():
            messagebox.showwarning(
                "Close Undertale first",
                "Undertale must be fully closed before patching data.win.\n"
                "Close the game, click Enable live patches, then start Undertale again.",
            )
            return
        notes = []
        try:
            if enable_debug_mode(self.data_win_path, backup=True):
                notes.append("debug load (L) enabled")
        except OSError as exc:
            messagebox.showerror(
                "Could not patch",
                f"Windows blocked writing data.win:\n{exc}\n\n"
                "Close Undertale/Steam overlays and try again.",
            )
            return
        except Exception as exc:
            notes.append(f"debug failed: {exc}")
        try:
            ok, msg = disable_dogcheck(self.data_win_path, backup=True)
            if ok:
                notes.append("dogcheck disabled")
            else:
                notes.append(msg)
        except OSError as exc:
            messagebox.showerror("Could not patch", str(exc))
            return
        except Exception as exc:
            notes.append(f"dogcheck failed: {exc}")

        messagebox.showinfo(
            "Live patches ready",
            "Patched data.win for live room teleport:\n• "
            + "\n• ".join(notes)
            + "\n\nNow start Undertale, load your save, then click a room.\n"
            "Backup: data.win.dogcheckbak / data.win.debugbak\n\n"
            "If you get a Code Error about dogcheck when pressing L, "
            "click Restore data.win, then Enable live patches again.",
        )
        self._set_status("Live patches applied — start Undertale, then click a room.")

    def _on_load_error(self, exc: Exception) -> None:
        self._loading = False
        self.open_btn.configure(state="normal")
        self._set_status("Failed to load")
        messagebox.showerror("Could not open game", str(exc))

    def _update_counts(self) -> None:
        counts: dict[AssetKind, int] = {k: 0 for k in KIND_ORDER}
        for a in self.assets:
            counts[a.kind] = counts.get(a.kind, 0) + 1
        lines = [f"Total: {len(self.assets)}"]
        for kind in KIND_ORDER:
            if counts.get(kind):
                lines.append(f"{kind.value}: {counts[kind]}")
        self.count_label.configure(text="\n".join(lines))
        for kind, btn in self.kind_buttons.items():
            if kind is None:
                btn.configure(text=f"All files ({len(self.assets)})")
            else:
                btn.configure(text=f"{kind.value} ({counts.get(kind, 0)})")

    def set_kind(self, kind: AssetKind | None) -> None:
        self.current_kind = kind
        self.page = 0
        self._highlight_kind(kind)
        self.apply_filter()

    def apply_filter(self) -> None:
        if self._loading and not self.assets:
            return
        query = self.search_var.get().strip().lower()
        items = self.assets
        if self.current_kind is not None:
            items = [a for a in items if a.kind == self.current_kind]
        if query:
            items = [
                a
                for a in items
                if query in a.display_name.lower() or query in a.id.lower()
            ]
        self.filtered = items
        pages = max(1, (len(self.filtered) + PAGE_SIZE - 1) // PAGE_SIZE) if self.filtered else 0
        if self.page >= pages and pages > 0:
            self.page = pages - 1
        self._render_list()

    def prev_page(self) -> None:
        if self.page > 0:
            self.page -= 1
            self._render_list()

    def next_page(self) -> None:
        pages = max(1, (len(self.filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
        if self.page + 1 < pages:
            self.page += 1
            self._render_list()

    def _render_list(self) -> None:
        self._render_token += 1
        token = self._render_token
        for child in self.scroll.winfo_children():
            child.destroy()

        total = len(self.filtered)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else 0
        if total == 0:
            self.page_label.configure(text="Page 0/0")
            self.prev_btn.configure(state="disabled")
            self.next_btn.configure(state="disabled")
            empty = ctk.CTkLabel(
                self.scroll,
                text="No files match this filter",
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=14),
            )
            empty.pack(pady=40)
            return

        start = self.page * PAGE_SIZE
        end = min(start + PAGE_SIZE, total)
        page_items = self.filtered[start:end]
        self.page_label.configure(text=f"Page {self.page + 1}/{pages}")
        self.prev_btn.configure(state="normal" if self.page > 0 else "disabled")
        self.next_btn.configure(state="normal" if self.page + 1 < pages else "disabled")

        hint = ctk.CTkLabel(
            self.scroll,
            text=f"Showing {start + 1}–{end} of {total}  ·  click a file to download",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["muted"],
        )
        hint.grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 8))

        cols = 3
        # Build tiles in small batches so the window stays responsive.
        self._build_tiles_batch(page_items, cols, 0, token)

    def _build_tiles_batch(
        self,
        page_items: list[GameAsset],
        cols: int,
        index: int,
        token: int,
        batch: int = 6,
    ) -> None:
        if token != self._render_token:
            return
        end = min(index + batch, len(page_items))
        for i in range(index, end):
            asset = page_items[i]
            row, col = divmod(i, cols)
            tile = self._make_tile(self.scroll, asset)
            tile.grid(row=row + 1, column=col, padx=8, pady=8, sticky="nsew")
        for c in range(cols):
            self.scroll.grid_columnconfigure(c, weight=1)
        if end < len(page_items):
            self.after(
                1,
                lambda: self._build_tiles_batch(page_items, cols, end, token, batch),
            )

    def _make_tile(self, parent, asset: GameAsset) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(
            parent,
            fg_color=COLORS["card"],
            corner_radius=10,
            border_width=1,
            border_color=COLORS["border"],
            width=220,
            height=150,
        )
        frame.grid_propagate(False)

        # Lightweight placeholder first — real thumbnail filled later.
        thumb_label = ctk.CTkLabel(
            frame,
            text=asset.extension.upper().lstrip(".") or "FILE",
            width=88,
            height=72,
            fg_color=COLORS["panel"],
            corner_radius=6,
            text_color=COLORS["accent"],
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        thumb_label.pack(pady=(10, 4))

        if asset.is_room:
            rid = int(asset.meta.get("room_id", 0))
            display = friendly_room_label(asset.name, rid)
            if is_dogcheck_room(rid):
                display = f"{display} ⚠"
        else:
            display = asset.display_name
        name_label = ctk.CTkLabel(
            frame,
            text=display[:28] + ("…" if len(display) > 28 else ""),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["ink"],
        )
        name_label.pack()

        meta = ctk.CTkLabel(
            frame,
            text=(
                f"Room ID {asset.meta.get('room_id')} · click to enter"
                if asset.is_room
                else f"{asset.kind.value} · {_fmt_size(asset.size)}"
            ),
            font=ctk.CTkFont(size=11),
            text_color=COLORS["muted"],
        )
        meta.pack(pady=(0, 8))

        def on_click(_event=None, a: GameAsset = asset) -> None:
            self.select_asset(a)
            if a.is_room:
                self.teleport_selected()
            else:
                self.download_selected()

        def on_select(_event=None, a: GameAsset = asset) -> None:
            self.select_asset(a)

        for widget in (frame, thumb_label, name_label, meta):
            widget.bind("<Button-1>", on_click)
            widget.bind("<Button-3>", on_select)
            widget.bind("<Double-Button-1>", on_click)

        if asset.is_image:
            self.after(10, lambda: self._fill_thumb(thumb_label, asset))
        else:
            badge = {
                AssetKind.AUDIO: "♪ AUDIO",
                AssetKind.MUSIC: "♫ MUSIC",
                AssetKind.FONT: "Aa FONT",
                AssetKind.ROOM: "DOOR",
                AssetKind.OTHER: "FILE",
            }.get(asset.kind, asset.extension.upper() or "FILE")
            thumb_label.configure(text=badge)
        return frame

    def _fill_thumb(self, label: ctk.CTkLabel, asset: GameAsset) -> None:
        if not label.winfo_exists():
            return
        try:
            if asset.id in self._thumb_cache:
                label.configure(image=self._thumb_cache[asset.id], text="")
                return
            thumb = asset.thumbnail(72)
            if thumb is None:
                return
            img = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=thumb.size)
            self._thumb_cache[asset.id] = img
            if label.winfo_exists():
                label.configure(image=img, text="")
        except Exception:
            if label.winfo_exists():
                label.configure(text="?", text_color=COLORS["muted"])

    def select_asset(self, asset: GameAsset) -> None:
        self.selected = asset
        if asset.is_room:
            room_id = int(asset.meta.get("room_id", -1))
            title = friendly_room_label(asset.name, room_id)
            if is_dogcheck_room(room_id):
                title = f"{title}  ⚠ dog"
            self.preview_name.configure(text=title)
            self.preview_meta.configure(
                text=(
                    f"Room ID {room_id}\n"
                    f"Size {asset.meta.get('width', '?')}×{asset.meta.get('height', '?')}\n"
                    + (
                        "Dogcheck room — Annoying Dog until patches disable it\n"
                        if is_dogcheck_room(room_id)
                        else ""
                    )
                    + "Click to enter while Undertale is open"
                )
            )
            self.download_btn.configure(state="disabled")
            self.save_as_btn.configure(state="disabled")
            self.teleport_btn.configure(state="normal")
        else:
            self.preview_name.configure(text=asset.display_name)
            self.preview_meta.configure(
                text=f"{asset.kind.value}\n{_fmt_size(asset.size)}\nClick Download to save"
            )
            self.download_btn.configure(state="normal")
            self.save_as_btn.configure(state="normal")
            self.teleport_btn.configure(state="disabled")

        try:
            if asset.is_image:
                img = asset.get_image()
                if img:
                    preview = img.copy()
                    preview.thumbnail((220, 220), Image.Resampling.NEAREST)
                    ctk_img = ctk.CTkImage(
                        light_image=preview,
                        dark_image=preview,
                        size=preview.size,
                    )
                    self._preview_image = ctk_img
                    self.preview_canvas.configure(image=ctk_img, text="")
                    return
            if asset.is_audio:
                self._preview_image = None
                self.preview_canvas.configure(image=None, text=f"Audio\n{asset.extension}")
                return
            self._preview_image = None
            self.preview_canvas.configure(image=None, text=asset.extension or "File")
        except Exception as exc:
            self.preview_canvas.configure(image=None, text=f"Preview failed\n{exc}")

    def teleport_selected(self) -> None:
        if not self.selected or not self.selected.is_room:
            return
        room_id = int(self.selected.meta.get("room_id", -1))
        if room_id < 0:
            messagebox.showerror("Teleport failed", "This room has no valid id.")
            return

        label = friendly_room_label(self.selected.name, room_id)
        if is_dogcheck_room(room_id):
            label = f"{label}  (dogcheck room)"

        # Prefer live teleport while Undertale is running.
        if undertale_is_running():
            if is_dogcheck_room(room_id):
                cont = messagebox.askokcancel(
                    "Dogcheck room",
                    f"{label}\n\n"
                    "Vanilla Undertale blocks this room with the Annoying Dog "
                    "unless dogcheck is disabled.\n\n"
                    "If you still see the dog: close Undertale → Enable live patches "
                    "→ restart the game.\n\n"
                    "Jump anyway?",
                )
                if not cont:
                    return
            self._set_status(f"Jumping to {label} in the open game…")
            self.update_idletasks()
            try:
                result, self._live_room_addrs = live_teleport_to_room(
                    room_id,
                    save_folder=self.save_dir,
                    data_win=self.data_win_path,
                )
            except Exception as exc:
                messagebox.showerror("Live teleport failed", str(exc))
                return

            if result.ok:
                self._live_current_room = room_id
                self._set_status(result.detail)
                self.preview_meta.configure(
                    text=f"Entered room {room_id}\n{self.selected.name}\n(live load)"
                )
                return

            if result.method in {"restart_required", "patches_required", "broken_dogcheck"}:
                title = (
                    "Broken dogcheck patch"
                    if result.method == "broken_dogcheck"
                    else "Enable live patches first"
                )
                messagebox.showinfo(title, result.detail)
                self._set_status(result.detail)
                return

            # Live failed — ask about save fallback
            fallback = messagebox.askyesno(
                "Could not jump live",
                f"{result.detail}\n\n"
                "Update your save file instead?\n"
                "(Then use Undertale title screen → Continue)\n\n"
                f"Target: {label}",
            )
            if not fallback:
                self._set_status(result.detail)
                return
        else:
            ok = messagebox.askokcancel(
                "Enter room?",
                (
                    f"Undertale is not running.\n\n"
                    f"Set save to:\n{label}\n\n"
                    "Then open Undertale → Continue.\n\n"
                    "Tip: leave Undertale open next time to jump live."
                ),
            )
            if not ok:
                return

        if self.save_dir is None:
            picked = filedialog.askdirectory(
                title="Select Undertale save folder (contains file0)"
            )
            if not picked:
                return
            self.save_dir = Path(picked)
            self.save_path_label.configure(text=f"Game save:\n{self.save_dir}")

        try:
            info = teleport_to_room(room_id, self.save_dir)
            self._live_current_room = room_id
            self._set_status(
                f"Save updated → room {room_id}. Open Undertale and press Continue."
            )
            self.preview_meta.configure(
                text=(
                    f"Save set to room {room_id}\n"
                    f"{info.folder}\n"
                    "Title screen → Continue"
                )
            )
            messagebox.showinfo(
                "Room set in save",
                f"Save now points to room {room_id}.\n\n"
                "Open Undertale → Continue.\n\n"
                "For live jumps, start Undertale first, then click rooms here.",
            )
        except Exception as exc:
            messagebox.showerror("Teleport failed", str(exc))

    def download_selected(self) -> None:
        if not self.selected:
            return
        if self.selected.is_room:
            self.teleport_selected()
            return
        try:
            path = self.selected.export_to(self.download_dir, overwrite=False)
            self._set_status(f"Downloaded → {path}")
            self.preview_meta.configure(
                text=f"{self.selected.kind.value}\nSaved to:\n{path}"
            )
        except Exception as exc:
            messagebox.showerror("Download failed", str(exc))

    def save_selected_as(self) -> None:
        if not self.selected:
            return
        initial = self.selected.display_name
        path = filedialog.asksaveasfilename(
            title="Save file as",
            initialfile=initial,
            defaultextension=self.selected.extension or "",
            initialdir=str(self.download_dir),
        )
        if not path:
            return
        try:
            Path(path).write_bytes(self.selected.get_data())
            self._set_status(f"Saved → {path}")
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))

    def export_all_visible(self) -> None:
        if not self.filtered:
            return
        dest = filedialog.askdirectory(title="Choose folder for all visible files")
        if not dest:
            return
        dest_path = Path(dest)
        ok = 0
        errors = 0
        total = len(self.filtered)
        for i, asset in enumerate(self.filtered, start=1):
            try:
                sub = dest_path / asset.kind.value.lower()
                asset.export_to(sub, overwrite=False)
                ok += 1
            except Exception:
                errors += 1
            if i % 25 == 0:
                self._set_status(f"Exporting… {i}/{total}")
                self.update_idletasks()
        self._set_status(
            f"Exported {ok} files" + (f" ({errors} failed)" if errors else "")
        )
        messagebox.showinfo("Export complete", f"Saved {ok} files to:\n{dest_path}")

    def choose_download_dir(self) -> None:
        path = filedialog.askdirectory(
            title="Choose download folder", initialdir=str(self.download_dir)
        )
        if path:
            self.download_dir = Path(path)
            self.dl_path_label.configure(text=f"Downloads to:\n{self.download_dir}")

    def choose_save_dir(self) -> None:
        initial = str(self.save_dir) if self.save_dir else str(Path.home())
        path = filedialog.askdirectory(
            title="Select Undertale save folder (contains file0)",
            initialdir=initial,
        )
        if not path:
            # Offer known saves if any
            known = find_undertale_save_dirs()
            if known:
                self.save_dir = known[0]
                self.save_path_label.configure(text=f"Game save:\n{self.save_dir}")
                self._set_status(f"Using save folder {self.save_dir}")
            return
        folder = Path(path)
        if not (folder / "file0").is_file():
            messagebox.showwarning(
                "No file0 here",
                "That folder has no file0.\n"
                r"Typical path: %LOCALAPPDATA%\UNDERTALE",
            )
        self.save_dir = folder
        self.save_path_label.configure(text=f"Game save:\n{self.save_dir}")
        try:
            info = read_save_info(folder)
            extra = f" (currently room {info.current_room})" if info.current_room is not None else ""
            self._set_status(f"Save folder set{extra}")
        except Exception:
            self._set_status("Save folder set")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def run_app() -> None:
    app = UndertaleExtractorApp()
    app.mainloop()


if __name__ == "__main__":
    run_app()
