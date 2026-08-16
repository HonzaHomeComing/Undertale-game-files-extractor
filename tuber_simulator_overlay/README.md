# Tuber Simulator Overlay (BlueStacks root)

Floating cheat overlay for **PewDiePie’s Tuber Simulator** on **rooted BlueStacks**.

## Install in BlueStacks

1. BlueStacks → **Settings → Advanced → Root ON** → restart BlueStacks  
2. Install:  
   **https://github.com/HonzaHomeComing/Undertale-game-files-extractor/raw/cursor/tuber-simulator-overlay-cb61/tuber_simulator_overlay/dist/TuberSaveOverlay-debug.apk**  
3. Allow **Display over other apps** → **Start overlay**  
4. Open Tuber Simulator → tap the **red bubble**  
5. Menu **auto-loads the live save** → edit Bux/Gems/etc → **APPLY LIVE SAVE & RESTART**

## What Apply does

1. Force-stops the game (so it can’t overwrite the file)  
2. Merges your edits into the real `playerprefs` XML under `/data/data/…`  
3. Writes the file with root + fixes ownership  
4. Relaunches the game  

Unity keeps prefs in memory, so a **quick restart is required** for new values to show. You edit while the overlay is open over the game; Apply does the stop/write/relaunch.

## Magisk phone

Same APK works if Magisk root is granted to the overlay — same Apply flow.
