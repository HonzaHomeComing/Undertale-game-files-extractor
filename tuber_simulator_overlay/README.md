# Tuber Simulator Overlay Save Editor

Floating Android app (“Appear on top of other apps”) that lets you edit a
**PewDiePie’s Tuber Simulator** Unity `PlayerPrefs` XML save while the game
(or any app) is open.

Package id of the game: `com.outerminds.tubular`

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

## How to use

### A. Get a save file onto the phone

**With a PC + USB debugging:**
```bash
adb shell "run-as com.outerminds.tubular cat shared_prefs/com.outerminds.tubular.v2.playerprefs.xml" > playerprefs.xml
# (run-as only works if the game is debuggable — often it is not)

# Rooted device:
adb pull /data/data/com.outerminds.tubular/shared_prefs/com.outerminds.tubular.v2.playerprefs.xml
```

**With root file manager:** copy the `*.playerprefs.xml` from the game’s
`shared_prefs` into Downloads.

### B. Install this app

1. Open `tuber_simulator_overlay/` in **Android Studio**
2. Build → Run on your phone (API 26+)
3. Grant **Display over other apps** when asked
4. Tap **Start overlay**

### C. Edit

1. Bubble appears on top of everything
2. Tap bubble → **Load XML** → pick your exported prefs
3. Edit quick fields or any key in the list
4. **Save XML** → write the file (Downloads or same path)
5. Copy the file back over the game’s prefs (root/ADB), force-stop the game, reopen

## Project layout

```
tuber_simulator_overlay/
  app/src/main/java/com/honza/tubersaveoverlay/
    MainActivity.kt          # permission + start/stop
    OverlayService.kt        # floating bubble + panel
    PlayerPrefsXml.kt        # parse / write Unity PlayerPrefs XML
  README.md
```

## Build

Requires Android Studio Hedgehog+ / JDK 17.

```bash
cd tuber_simulator_overlay
./gradlew :app:assembleDebug
```

APK: `app/build/outputs/apk/debug/app-debug.apk`
