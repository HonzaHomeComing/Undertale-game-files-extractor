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

### Enter rooms in-game
1. Close Undertale completely  
2. In the app, open the **Rooms** category  
3. Click the room you want (or press **Enter Room In-Game**)  
4. Start Undertale → **Continue**

Your save (`%LOCALAPPDATA%\UNDERTALE\file0`) is updated and a `.bak` backup is created.  
Some rooms trigger the Annoying Dog screen (dogcheck) — that’s normal for unused rooms.

If the save folder isn’t found automatically, click **Save Folder…** and select:
```
C:\Users\<you>\AppData\Local\UNDERTALE
```


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
