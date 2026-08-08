#!/usr/bin/env python3
"""Rebuild UndertaleExtractor.py from the undertale_extractor package."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "undertale_extractor"
OUT = ROOT / "UndertaleExtractor.py"

# Order matters (dependencies first).
MODULES = [
    "assets.py",
    "binary.py",
    "teleport.py",
    "dogcheck.py",
    "live_teleport.py",
    "parser.py",
    "gui.py",
]

HEADER = '''"""
Undertale File Extractor
========================
Browse files and click Rooms to enter them while Undertale is open.

Open the folder to browse (does not lock or patch data.win).
Use Enable live patches (Undertale closed) for live room jumps.
Use Restore data.win if the game will not start after patching.

Windows: pip install Pillow customtkinter
         python UndertaleExtractor.py
"""

from __future__ import annotations

import argparse
import ctypes
import io
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

try:
    import customtkinter as ctk
    from PIL import Image, ImageDraw
except ImportError:
    print("Missing packages. Run: pip install Pillow customtkinter")
    input("Press Enter to exit...")
    raise SystemExit(1)

__version__ = "1.5.0"

'''

FOOTER = '''

def main(argv=None):
    argp = argparse.ArgumentParser(description="Undertale File Extractor")
    argp.add_argument("path", nargs="?")
    argp.add_argument("--extract-all", metavar="OUT_DIR")
    argp.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = argp.parse_args(argv)
    if args.extract_all:
        if not args.path:
            return 2
        result = load_undertale_assets(args.path)
        out = Path(args.extract_all)
        for asset in result.assets:
            if asset.is_room:
                continue
            print(asset.export_to(out / asset.kind.value.lower(), overwrite=False))
        return 0
    app = UndertaleExtractorApp()
    if args.path:
        app.after(200, lambda: app._load_async(args.path))
    app.mainloop()
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        try:
            messagebox.showerror("Undertale Extractor crashed", str(exc))
        except Exception:
            print(exc)
        raise
'''

SKIP_LINE = re.compile(
    r"^(from __future__ import .*|"
    r"from \.(assets|binary|teleport|dogcheck|live_teleport|parser|gui) import [^(].*|"
    r"import (os|re|io|sys|struct|shutil|tempfile|threading|time|ctypes|tkinter)( as .*)?|"
    r"from (pathlib|dataclasses|enum|collections\.abc|ctypes|tkinter) import .*|"
    r"from PIL import .*|"
    r"import customtkinter as ctk|"
    r"from ctypes import wintypes)$"
)


def strip_module(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    # Drop module docstring
    if lines and lines[0].startswith('"""'):
        if lines[0].count('"""') >= 2:
            i = 1
        else:
            i = 1
            while i < len(lines) and '"""' not in lines[i]:
                i += 1
            i += 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # Multi-line "from .x import (" blocks — must run before single-line skip
        if re.match(r"from \.\w+ import \(", stripped):
            while i < len(lines) and ")" not in lines[i]:
                i += 1
            i += 1
            continue
        if SKIP_LINE.match(stripped):
            i += 1
            continue
        if "from .dogcheck import" in line:
            i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out).strip() + "\n"


def main() -> None:
    chunks = [HEADER]
    for name in MODULES:
        body = strip_module((PKG / name).read_text(encoding="utf-8"))
        chunks.append(f"\n# --- {name} ---\n\n")
        chunks.append(body)
        chunks.append("\n")
    chunks.append(FOOTER)
    OUT.write_text("".join(chunks), encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
