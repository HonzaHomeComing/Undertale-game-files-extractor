"""CLI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Undertale File Extractor — browse and download game assets"
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Optional Undertale folder or data.win to open immediately",
    )
    parser.add_argument(
        "--extract-all",
        metavar="OUT_DIR",
        help="Extract all assets to OUT_DIR without opening the GUI",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.extract_all:
        from .parser import load_undertale_assets

        if not args.path:
            print("error: path to Undertale / data.win is required with --extract-all", file=sys.stderr)
            return 2
        result = load_undertale_assets(args.path)
        out = Path(args.extract_all)
        for asset in result.assets:
            dest = out / asset.kind.value.lower()
            path = asset.export_to(dest, overwrite=False)
            print(path)
        print(f"Extracted {len(result.assets)} files to {out}")
        return 0

    from .gui import UndertaleExtractorApp

    app = UndertaleExtractorApp()
    if args.path:
        app.after(200, lambda: app._load_async(args.path))
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
