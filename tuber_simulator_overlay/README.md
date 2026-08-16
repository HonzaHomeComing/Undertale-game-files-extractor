# Tuber Simulator Overlay Save Editor

Floating Android app (“Appear on top of other apps”) for **PewDiePie’s Tuber Simulator**
(`com.outerminds.tubular`) on a **normal phone — no BlueStacks, no root required** for the
Apply & Restart flow.

## Install on your phone

1. Uninstall the old overlay if present.
2. Download:  
   **https://github.com/HonzaHomeComing/Undertale-game-files-extractor/raw/cursor/tuber-simulator-overlay-cb61/tuber_simulator_overlay/dist/TuberSaveOverlay-debug.apk**
3. Install → open app → allow:
   - **Display over other apps**
   - **All files access**
4. Tap **Start overlay** → open Tuber Simulator → tap the **red bubble**.
5. Set Bux / Gems / etc. → tap **APPLY & RESTART GAME**.

That will:

- Patch any editable text saves under `Android/data/com.outerminds.tubular/`
- Export your edited XML to `Download/TuberSaveOverlay/playerprefs_edited.xml`
- Send the game Home → kill its background process → relaunch it

## Important limit (honest)

Unity **PlayerPrefs** (the usual Bux/Gems file) live in a **private** folder:

`/data/data/com.outerminds.tubular/shared_prefs/…playerprefs….xml`

Android blocks that folder on stock phones. If the game only stores currency there,
phone-mode Apply & Restart still **restarts** the game and **exports** the XML, but
cannot rewrite the private prefs without root.

Optional root (Magisk / BlueStacks Root ON) unlocks live Pull/Push of those prefs.

## Build

Open `tuber_simulator_overlay/` in Android Studio (API 26+), or use the prebuilt APK under
`tuber_simulator_overlay/dist/TuberSaveOverlay-debug.apk`.
