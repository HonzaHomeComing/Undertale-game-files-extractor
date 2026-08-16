# Tuber Simulator Overlay Save Editor

Floating Android app (“Appear on top of other apps”) that lets you edit a
**PewDiePie’s Tuber Simulator** Unity `PlayerPrefs` XML save while the game
(or any app) is open.

Package id of the game: `com.outerminds.tubular`

## Install on your phone (no PC)

1. On your phone, open this APK link:  
   **https://github.com/HonzaHomeComing/Undertale-game-files-extractor/raw/cursor/tuber-simulator-overlay-cb61/tuber_simulator_overlay/dist/TuberSaveOverlay-debug.apk**
2. Download it.
3. Open the downloaded file → **Install**  
   (If blocked: Settings → allow **Install unknown apps** for your browser/Files.)
4. Open **Tuber Save Overlay** → allow **Display over other apps** → **Start overlay**.
5. Use the red bubble on top of other apps.

## What this is

- A **bubble overlay** you can drag around the screen
- Tap the bubble → editor panel opens on top of other apps
- Load a `.xml` PlayerPrefs dump, change values, save it back
- Quick fields for common names (Bux / Knowledge / Subscribers / Views) plus a
  full key list from whatever is in your file

## Important limits

1. **Cloud sync** — Tuber Simulator also stores progress on Outerminds’ servers.
   Local edits can be overwritten when you Link Account / sync. Force-stop the
   game before replacing the save, then open offline if you can.
2. **Save location (needs root or ADB)** — Unity stores prefs at roughly:
   ```
   /data/data/com.outerminds.tubular/shared_prefs/com.outerminds.tubular.v2.playerprefs.xml
   ```
   On modern Android you normally cannot open that folder without root / `adb`.
3. This tool edits **your exported copy**. It does not inject into a running
   process or bypass server checks.

## Build from source (PC / Android Studio)

1. Open `tuber_simulator_overlay/` in **Android Studio**
2. Build → Run on your phone (API 26+)
3. Grant **Display over other apps** when asked
4. Tap **Start overlay**

APK also lives at: `tuber_simulator_overlay/dist/TuberSaveOverlay-debug.apk`
