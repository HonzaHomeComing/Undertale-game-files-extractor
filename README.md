# Undertale Game File Extractor

A windowed desktop app that opens your Undertale install, extracts the packed game assets from `data.win`, lets you scroll through them, and downloads any file you click.

## Features

- Open an Undertale folder (or `data.win` / `game.unx` directly)
- Browse **sprites**, **textures**, **backgrounds**, **audio**, **music**, and **fonts**
- Scrollable thumbnail grid with search and category filters
- **Click a file to download it** to your Downloads folder
- Export all visible files at once
- Also picks up loose `mus_*.ogg` files next to the game data

## Requirements

- Python 3.10+
- A legal copy of Undertale (this tool does not include game files)
- Tkinter (usually bundled with Python; on Linux: `sudo apt install python3-tk`)

## Install

```bash
pip install -r requirements.txt
```

## Run the app

```bash
python run.py
```

Or:

```bash
python -m undertale_extractor
```

1. Click **Open Undertale Folder**
2. Select the folder that contains `data.win` (Steam: usually `…/steamapps/common/Undertale`)
3. Scroll through the extracted files
4. Click any image or audio file to download it

You can change the download folder with **Download Folder…**, or use **Save As…** for a one-off location.

## CLI bulk extract

```bash
python -m undertale_extractor "/path/to/Undertale" --extract-all ./exported
```

## Tests

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```

## Notes

- Works with Undertale’s GameMaker `data.win` (Windows) and `game.unx` (Linux)
- Sprites are cropped from texture pages using the game’s TPAG data
- For personal / modding use with a game you own
