# Tuber Simulator Overlay — live RAM (BlueStacks root)

## Why restart failed
Writing PlayerPrefs + restart trips Outerminds’ load check → stuck on OUTERMINDS splash.
**LIVE APPLY IN RAM** patches the running game process and does **not** restart.

## Flow
1. BlueStacks **Root ON**
2. Install APK, Start overlay
3. If stuck on splash → **FRESH START** (clears data) → open game until you’re in-world
4. Type the numbers you **see on screen** into the fields → **Snapshot fields as OLD values**
5. Change fields to the new amounts → **LIVE APPLY IN RAM (no restart)**
6. Stay in the game — don’t restart

## Download
https://github.com/HonzaHomeComing/Undertale-game-files-extractor/raw/cursor/tuber-simulator-overlay-cb61/tuber_simulator_overlay/dist/TuberSaveOverlay-debug.apk
