# Tuber Simulator Overlay Save Editor

Floating overlay for **PewDiePie’s Tuber Simulator** (`com.outerminds.tubular`).

## Hard limit (read this)

Bux / Gems / Knowledge live in Unity **PlayerPrefs**:

`/data/data/com.outerminds.tubular/shared_prefs/…playerprefs….xml`

On a **normal non-root phone**, Android blocks that folder. The overlay can restart
the game and export an XML to `Download/`, but **the game will not use that file**.
A status like “Patched … Download/TuberSaveOverlay” means only our export was
edited — **not** the live save.

**Ways that actually change values:**

1. **Phone with Magisk / KernelSU root** → Pull → edit → APPLY & RESTART  
2. **BlueStacks** → Settings → Advanced → **Root ON** → same flow  

## Install

https://github.com/HonzaHomeComing/Undertale-game-files-extractor/raw/cursor/tuber-simulator-overlay-cb61/tuber_simulator_overlay/dist/TuberSaveOverlay-debug.apk

1. Allow **Display over other apps**
2. **Start overlay** → red bubble → cheat menu
3. With root: **Scan / pull saves** → set values → **APPLY & RESTART GAME**

## Build

Open `tuber_simulator_overlay/` in Android Studio (API 26+).
