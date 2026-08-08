"""
Undertale Data Wiper (Windows)
==============================
Finds and deletes Undertale save/config/Steam cloud data on this PC.

What it can wipe
----------------
- %LOCALAPPDATA%\\UNDERTALE\\          (main saves: file0, undertale.ini, …)
- Steam userdata\\…\\391540\\           (Steam cloud / remote saves)
- Optional: Steam common\\Undertale   (the game install itself — off by default)

It does NOT touch unrelated Steam games, Windows system folders, or other apps.

Usage
-----
  pip install customtkinter   # optional; falls back to tkinter
  python UndertaleDataWiper.py

Always review the file list before confirming.
"""

from __future__ import annotations

import os
import shutil
import sys
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

__version__ = "1.0.0"

STEAM_APP_ID = "391540"


@dataclass
class WipeTarget:
    path: Path
    category: str
    description: str
    optional_game_install: bool = False

    @property
    def exists(self) -> bool:
        try:
            return self.path.exists()
        except OSError:
            return False

    def size_bytes(self) -> int:
        if not self.exists:
            return 0
        if self.path.is_file():
            try:
                return self.path.stat().st_size
            except OSError:
                return 0
        total = 0
        try:
            for root, _dirs, files in os.walk(self.path):
                for name in files:
                    try:
                        total += (Path(root) / name).stat().st_size
                    except OSError:
                        pass
        except OSError:
            return total
        return total


@dataclass
class ScanResult:
    targets: list[WipeTarget] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    pf = os.environ.get("ProgramFiles(x86)") or os.environ.get("ProgramFiles")
    if pf:
        roots.append(Path(pf) / "Steam")
    # Common alternate library locations / env
    for env in ("STEAM_PATH", "STEAM_DIR"):
        val = os.environ.get(env)
        if val:
            roots.append(Path(val))
    home = Path.home()
    roots.extend(
        [
            home / "Steam",
            home / ".steam" / "steam",
            home / ".local" / "share" / "Steam",
        ]
    )
    # Dedup existing
    out: list[Path] = []
    seen: set[Path] = set()
    for r in roots:
        try:
            key = r.resolve() if r.exists() else r
        except OSError:
            key = r
        if key in seen:
            continue
        seen.add(key)
        if r.exists():
            out.append(r)
    return out


def _steam_library_folders(steam_root: Path) -> list[Path]:
    """Return Steam library roots (steamapps parents)."""
    libs = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if not vdf.is_file():
        return libs
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return libs
    # Very small VDF parser for "path" "D:\\SteamLibrary"
    for line in text.splitlines():
        line = line.strip()
        if '"path"' not in line.lower():
            continue
        parts = line.split('"')
        # ["", "path", " ", "D:\\...", ...]
        paths = [p for p in parts if p and p.lower() != "path" and not p.isspace()]
        for p in paths:
            candidate = Path(p.replace("\\\\", "\\"))
            if candidate.exists() and candidate not in libs:
                libs.append(candidate)
    return libs


def discover_undertale_data(*, include_game_install: bool = False) -> ScanResult:
    result = ScanResult()
    local = os.environ.get("LOCALAPPDATA")
    roaming = os.environ.get("APPDATA")
    home = Path.home()

    # Primary Windows save folder
    candidates = []
    if local:
        candidates.append(Path(local) / "UNDERTALE")
    candidates.append(home / "AppData" / "Local" / "UNDERTALE")
    if roaming:
        candidates.append(Path(roaming) / "UNDERTALE")
    candidates.append(home / "AppData" / "Roaming" / "UNDERTALE")

    seen: set[Path] = set()
    for folder in candidates:
        try:
            key = folder.resolve()
        except OSError:
            key = folder
        if key in seen:
            continue
        seen.add(key)
        if folder.is_dir():
            result.targets.append(
                WipeTarget(
                    path=folder,
                    category="Saves",
                    description="Local Undertale save/config folder (file0, undertale.ini, …)",
                )
            )

    # Steam cloud / userdata for app 391540
    steam_roots = _steam_roots()
    if not steam_roots:
        result.notes.append("No Steam folder found (skipped Steam cloud wipe).")
    for steam in steam_roots:
        userdata = steam / "userdata"
        if userdata.is_dir():
            for user_dir in userdata.iterdir():
                if not user_dir.is_dir() or not user_dir.name.isdigit():
                    continue
                app_dir = user_dir / STEAM_APP_ID
                if app_dir.is_dir():
                    result.targets.append(
                        WipeTarget(
                            path=app_dir,
                            category="Steam cloud",
                            description=f"Steam userdata for account {user_dir.name} (app {STEAM_APP_ID})",
                        )
                    )

        # Game install(s)
        if include_game_install:
            for lib in _steam_library_folders(steam):
                game = lib / "steamapps" / "common" / "Undertale"
                if game.is_dir():
                    result.targets.append(
                        WipeTarget(
                            path=game,
                            category="Game install",
                            description="Steam Undertale install folder (removes the game files)",
                            optional_game_install=True,
                        )
                    )

    # Leftover extractor backups next to common installs (optional discovery only if game found)
    for t in list(result.targets):
        if t.optional_game_install:
            for bak_name in (
                "data.win.dogcheckbak",
                "data.win.debugbak",
                "data.win.bak",
            ):
                bak = t.path / bak_name
                if bak.is_file():
                    result.targets.append(
                        WipeTarget(
                            path=bak,
                            category="Backups",
                            description=f"Extractor/game backup file ({bak_name})",
                        )
                    )

    if not result.targets:
        result.notes.append("No Undertale data folders were found on this PC.")
    return result


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def wipe_targets(targets: list[WipeTarget]) -> tuple[int, list[str]]:
    """Delete targets. Returns (ok_count, errors)."""
    ok = 0
    errors: list[str] = []
    for t in targets:
        try:
            if not t.exists:
                continue
            if t.path.is_file():
                t.path.unlink()
            elif t.path.is_dir():
                shutil.rmtree(t.path)
            ok += 1
        except Exception as exc:
            errors.append(f"{t.path}: {exc}")
    return ok, errors


class WiperApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Undertale Data Wiper  v{__version__}")
        self.geometry("720x560")
        self.minsize(640, 480)

        self.include_game = tk.BooleanVar(value=False)
        self.targets: list[WipeTarget] = []
        self._vars: list[tk.BooleanVar] = []

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(
            frm,
            text="Undertale Data Wiper",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            frm,
            text="Deletes Undertale saves / config / Steam cloud data on this PC.\n"
            "Review every path before confirming. This cannot be undone.",
            foreground="#444",
        ).pack(anchor="w", pady=(4, 10))

        opts = ttk.Frame(frm)
        opts.pack(fill="x", pady=4)
        ttk.Checkbutton(
            opts,
            text="Also wipe Steam game install folder (Undertale itself)",
            variable=self.include_game,
            command=self.rescan,
        ).pack(side="left")
        ttk.Button(opts, text="Rescan", command=self.rescan).pack(side="right", padx=4)
        ttk.Button(opts, text="Add folder…", command=self.add_folder).pack(side="right")

        self.list_frame = ttk.Frame(frm)
        self.list_frame.pack(fill="both", expand=True, pady=8)

        canvas = tk.Canvas(self.list_frame, highlightthickness=0)
        scroll = ttk.Scrollbar(self.list_frame, orient="vertical", command=canvas.yview)
        self.inner = ttk.Frame(canvas)
        self.inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.canvas = canvas

        self.status = tk.StringVar(value="Scanning…")
        ttk.Label(frm, textvariable=self.status, wraplength=680).pack(anchor="w", pady=4)

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="Select all", command=lambda: self._set_all(True)).pack(
            side="left", padx=2
        )
        ttk.Button(btns, text="Select none", command=lambda: self._set_all(False)).pack(
            side="left", padx=2
        )
        ttk.Button(btns, text="Wipe selected…", command=self.wipe_selected).pack(
            side="right", padx=2
        )

        warn = ttk.Label(
            frm,
            text="Tip: turn off Steam Cloud for Undertale first, or Steam may re-download saves.",
            foreground="#833",
        )
        warn.pack(anchor="w")

        self.after(100, self.rescan)

    def _set_all(self, value: bool) -> None:
        for var in self._vars:
            var.set(value)

    def add_folder(self) -> None:
        path = filedialog.askdirectory(title="Add Undertale-related folder to wipe list")
        if not path:
            return
        self.targets.append(
            WipeTarget(
                path=Path(path),
                category="Custom",
                description="Manually added folder",
            )
        )
        self._render()

    def rescan(self) -> None:
        scan = discover_undertale_data(include_game_install=self.include_game.get())
        # Keep manually added custom targets
        custom = [t for t in self.targets if t.category == "Custom"]
        self.targets = scan.targets + custom
        self._render()
        total = sum(t.size_bytes() for t in self.targets if t.exists)
        msg = f"Found {len(self.targets)} location(s), about {_fmt_size(total)}."
        if scan.notes:
            msg += "  " + " ".join(scan.notes)
        self.status.set(msg)

    def _render(self) -> None:
        for child in self.inner.winfo_children():
            child.destroy()
        self._vars.clear()
        if not self.targets:
            ttk.Label(self.inner, text="Nothing found.").pack(anchor="w", padx=8, pady=12)
            return
        for t in self.targets:
            var = tk.BooleanVar(value=t.exists and not t.optional_game_install)
            self._vars.append(var)
            row = ttk.Frame(self.inner)
            row.pack(fill="x", padx=4, pady=3)
            ttk.Checkbutton(row, variable=var).pack(side="left")
            text = (
                f"[{t.category}] {t.path}\n"
                f"{t.description}  ·  {_fmt_size(t.size_bytes())}"
                + ("" if t.exists else "  (missing)")
            )
            ttk.Label(row, text=text, justify="left").pack(side="left", padx=6)

    def wipe_selected(self) -> None:
        chosen = [t for t, v in zip(self.targets, self._vars) if v.get() and t.exists]
        if not chosen:
            messagebox.showinfo("Nothing selected", "Select at least one existing path.")
            return

        lines = "\n".join(f" • {t.path}" for t in chosen)
        total = sum(t.size_bytes() for t in chosen)
        ok = messagebox.askokcancel(
            "Confirm permanent delete",
            "Permanently delete these Undertale data locations?\n\n"
            f"{lines}\n\n"
            f"Total ~ {_fmt_size(total)}\n\n"
            "This cannot be undone.",
        )
        if not ok:
            return

        # Second confirm if wiping game install
        if any(t.optional_game_install for t in chosen):
            ok2 = messagebox.askokcancel(
                "Delete the game itself?",
                "You included the Undertale install folder.\n"
                "That removes the game until you reinstall from Steam.\n\nContinue?",
            )
            if not ok2:
                return

        typed = _AskConfirm(self, "Type WIPE to confirm").result
        if typed != "WIPE":
            messagebox.showinfo("Cancelled", "Wipe cancelled.")
            return

        deleted, errors = wipe_targets(chosen)
        self.rescan()
        if errors:
            messagebox.showerror(
                "Finished with errors",
                f"Deleted {deleted} item(s).\n\nErrors:\n" + "\n".join(errors),
            )
        else:
            messagebox.showinfo(
                "Done",
                f"Deleted {deleted} Undertale data location(s).\n\n"
                "If Steam Cloud is on, disable it or the saves may come back.",
            )


class _AskConfirm(tk.Toplevel):
    def __init__(self, master, prompt: str):
        super().__init__(master)
        self.title("Confirm")
        self.result = ""
        self.resizable(False, False)
        ttk.Label(self, text=prompt, padding=12).pack()
        self.var = tk.StringVar()
        entry = ttk.Entry(self, textvariable=self.var, width=28)
        entry.pack(padx=12, pady=4)
        entry.focus_set()
        btns = ttk.Frame(self)
        btns.pack(pady=8)
        ttk.Button(btns, text="OK", command=self._ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Return>", lambda _e: self._ok())
        self.transient(master)
        self.grab_set()
        self.wait_window(self)

    def _ok(self) -> None:
        self.result = self.var.get().strip()
        self.destroy()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Wipe Undertale data on this PC")
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Print discovered paths and exit",
    )
    parser.add_argument(
        "--include-game",
        action="store_true",
        help="With --scan-only, also list the game install folder",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.scan_only:
        scan = discover_undertale_data(include_game_install=args.include_game)
        for t in scan.targets:
            print(f"[{t.category}] {t.path}  ({_fmt_size(t.size_bytes())})")
        for note in scan.notes:
            print(f"# {note}")
        return 0

    if not sys.platform.startswith("win"):
        # Still allow scan/GUI on other OS for Steam deck / proton paths
        pass

    app = WiperApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
