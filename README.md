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
4. **Click any file** to download it to your Downloads folder

### Extra buttons
- **Download Folder…** — change where clicked files are saved  
- **Save As…** — pick a custom save location  
- **Export All Visible** — save everything currently on screen  

## Optional: extract everything without the window
```bat
python UndertaleExtractor.py "C:\Program Files (x86)\Steam\steamapps\common\Undertale" --extract-all exported
```

## Notes
- You need your own copy of Undertale (game files are not included)
- Works with `data.win` from the Windows / Steam version
