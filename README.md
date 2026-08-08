# Undertale File Extractor (Windows)

One Python file that opens a window, extracts Undertale game files from `data.win`, lets you scroll through them, and **downloads a file when you click it**.

## File to run

**`UndertaleExtractor.py`** — this is the whole app.

## How to use on Windows

### 1. Install Python
Download Python 3.10+ from https://www.python.org/downloads/  
During setup, check **Add python.exe to PATH**.

### 2. Install packages
Open **Command Prompt** and run:
```bat
pip install Pillow customtkinter
```

### 3. Run the app
- Double-click `UndertaleExtractor.py`  
  **or**
```bat
python UndertaleExtractor.py
```

### 4. Open Undertale
1. Click **Open Undertale Folder**
2. Select your Undertale install folder (the one that contains `data.win`), for example:
   ```
   C:\Program Files (x86)\Steam\steamapps\common\Undertale
   ```
3. Scroll through sprites, textures, audio, music, etc.  
   Use **Prev / Next** — the app shows 36 files per page so it won’t freeze.
4. Open **Rooms** and **click a room to enter it in-game**  
   (close Undertale first, then open the game and press **Continue**)
5. Click other files (sprites/audio/…) to download them

### Enter rooms in-game (live)
1. Open your game folder in the extractor once  
   (this enables debug mode **and disables dogcheck** in `data.win`)
2. **Restart Undertale once**, then load your save and leave it running
3. Click a room in **Rooms** — you jump straight there

The dancing Annoying Dog is “dogcheck” (blocks invalid/secret rooms). The app patches it out automatically.  
A backup is saved as `data.win.dogcheckbak`.

If nothing happens, click the Undertale window and press **L**, or restart Undertale so the patches are active.


### Extra buttons
- **Download Folder…** — change where clicked files are saved  
- **Save As…** — pick a custom save location  
- **Export All Visible** — save everything currently on screen  

## Optional: extract everything without the window
```bat
python UndertaleExtractor.py "C:\Program Files (x86)\Steam\steamapps\common\Undertale" --extract-all exported
```

## Optional companion: PNG → Blender

`png_to_blender.py` turns PNG sprites into Blender-ready `.obj` models.

```bat
pip install Pillow
python png_to_blender.py my_sprite.png
```

Then in Blender: **File → Import → Wavefront (.obj)**

Or double-click `png_to_blender.py` for a small window. Modes: `voxels` (pixel cubes), `plane`, `height`.

## Optional: wipe Undertale data

`UndertaleDataWiper.py` finds and deletes Undertale saves, config, and Steam cloud data (`391540`) on this PC.

```bat
python UndertaleDataWiper.py
```

Review the listed paths, then type **WIPE** to confirm. Game install wipe is off by default.  
Turn off Steam Cloud for Undertale first, or Steam may restore saves.

Scan without deleting:
```bat
python UndertaleDataWiper.py --scan-only
```
