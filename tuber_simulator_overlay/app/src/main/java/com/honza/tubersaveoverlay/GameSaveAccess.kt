package com.honza.tubersaveoverlay

import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * Root helpers for Tuber Simulator PlayerPrefs (BlueStacks Root ON / Magisk).
 *
 * Unity caches prefs in memory — we force-stop FIRST, then write the XML, then
 * the UI relaunches so the game loads the new values.
 */
object GameSaveAccess {
    const val PACKAGE = "com.outerminds.tubular"

    private val CANDIDATE_PATHS = listOf(
        "/data/data/$PACKAGE/shared_prefs/$PACKAGE.v2.playerprefs.xml",
        "/data/data/$PACKAGE/shared_prefs/$PACKAGE.playerprefs.xml",
        "/data/user/0/$PACKAGE/shared_prefs/$PACKAGE.v2.playerprefs.xml",
        "/data/user/0/$PACKAGE/shared_prefs/$PACKAGE.playerprefs.xml",
        "/data/data/$PACKAGE/shared_prefs/${PACKAGE}_preferences.xml",
    )

    val DEFAULT_KEYS: List<PrefEntry> = listOf(
        PrefEntry("Bux", PrefType.INT, "0"),
        PrefEntry("SoftBux", PrefType.INT, "0"),
        PrefEntry("Money", PrefType.INT, "0"),
        PrefEntry("Cash", PrefType.INT, "0"),
        PrefEntry("Gems", PrefType.INT, "0"),
        PrefEntry("HardCurrency", PrefType.INT, "0"),
        PrefEntry("PremiumCurrency", PrefType.INT, "0"),
        PrefEntry("Knowledge", PrefType.INT, "0"),
        PrefEntry("BrainPoints", PrefType.INT, "0"),
        PrefEntry("Subscribers", PrefType.INT, "0"),
        PrefEntry("TotalSubscribers", PrefType.INT, "0"),
        PrefEntry("SubCount", PrefType.INT, "0"),
        PrefEntry("Views", PrefType.INT, "0"),
        PrefEntry("TotalViews", PrefType.INT, "0"),
        PrefEntry("ChannelLevel", PrefType.INT, "1"),
        PrefEntry("PlayerLevel", PrefType.INT, "1"),
        PrefEntry("ItemCount", PrefType.INT, "0"),
        PrefEntry("InventoryCount", PrefType.INT, "0"),
        PrefEntry("FurnitureUnlocked", PrefType.INT, "0"),
        PrefEntry("PropsUnlocked", PrefType.INT, "0"),
    )

    private val rootCache = AtomicReference<Boolean?>(null)
    private val prefsPathCache = AtomicReference<String?>(null)

    fun hasRootCached(): Boolean = rootCache.get() == true

    fun hasRoot(timeoutMs: Long = 1_500): Boolean {
        rootCache.get()?.let { return it }
        val ok = probeRoot(timeoutMs)
        rootCache.set(ok)
        return ok
    }

    fun invalidateRootCache() {
        rootCache.set(null)
        prefsPathCache.set(null)
    }

    private fun probeRoot(timeoutMs: Long): Boolean {
        val res = runSu("id", timeoutMs)
        return res.success && res.output.contains("uid=0")
    }

    fun findPrefsPath(): String? {
        prefsPathCache.get()?.let { cached ->
            if (runSu("ls \"$cached\"", timeoutMs = 2_000).success) return cached
        }
        for (p in CANDIDATE_PATHS) {
            if (runSu("ls \"$p\"", timeoutMs = 2_000).success) {
                prefsPathCache.set(p)
                return p
            }
        }
        val find = runSu(
            "find /data/data/$PACKAGE /data/user/0/$PACKAGE -name '*playerprefs*' -o -name '*PlayerPrefs*' 2>/dev/null | head -20",
            timeoutMs = 6_000,
        )
        val hit = find.output.lineSequence()
            .map { it.trim() }
            .firstOrNull { it.endsWith(".xml", ignoreCase = true) }
        if (hit != null) prefsPathCache.set(hit)
        return hit
    }

    fun pullPrefsXml(): Pair<Boolean, String> {
        val path = findPrefsPath()
            ?: return false to "No playerprefs XML found. Open Tuber Simulator once, then pull again."
        val res = runSu("cat \"$path\"", timeoutMs = 6_000)
        if (!res.success || res.output.isBlank()) {
            return false to "Root read failed: ${res.output.ifBlank { "empty" }}"
        }
        return true to res.output
    }

    /**
     * Live apply for rooted BlueStacks:
     * 1) force-stop game (so it cannot overwrite the file)
     * 2) pull current prefs from disk
     * 3) merge [fieldValues] + [overlayEntries] into that save
     * 4) write XML back with correct ownership
     *
     * Caller must relaunch the game afterward.
     */
    fun applyLiveEdits(
        fieldValues: Map<String, String>,
        overlayEntries: List<PrefEntry>,
        workingFile: File,
    ): Pair<Boolean, String> {
        if (!hasRoot(2_000)) {
            return false to "No root. BlueStacks → Settings → Advanced → Root ON, then restart BlueStacks."
        }

        // Stop FIRST — Unity often flushes old prefs on exit and would wipe our write.
        runSu("am force-stop $PACKAGE", timeoutMs = 4_000)
        try {
            Thread.sleep(700)
        } catch (_: InterruptedException) {
        }

        var path = findPrefsPath()
        if (path == null) {
            // Game never created prefs yet — create under the usual v2 path.
            path = CANDIDATE_PATHS.first()
            runSu("mkdir -p \"$(dirname \"$path\")\"", timeoutMs = 3_000)
        }

        val pull = runSu("cat \"$path\"", timeoutMs = 6_000)
        val liveEntries: MutableList<PrefEntry> = if (pull.success && pull.output.contains("<")) {
            PlayerPrefsXml.parse(pull.output)
        } else {
            mutableListOf()
        }
        ensureDefaults(liveEntries)

        // Overlay key editors win when present.
        for (e in overlayEntries) {
            val hit = liveEntries.firstOrNull { it.name.equals(e.name, ignoreCase = true) }
            if (hit != null) {
                if (e.value.isNotBlank()) hit.value = e.value
            } else if (e.value.isNotBlank()) {
                liveEntries += e.copy()
            }
        }

        // Quick fields / glitch map — stamp matching keys and ensure currency aliases exist.
        for ((key, raw) in fieldValues) {
            if (raw.isBlank()) continue
            var hit = liveEntries.firstOrNull { it.name.equals(key, ignoreCase = true) }
            if (hit == null) {
                hit = liveEntries.firstOrNull { it.name.contains(key, ignoreCase = true) }
            }
            if (hit != null) {
                hit.value = raw.trim()
            } else {
                liveEntries += PrefEntry(key, PrefType.INT, raw.trim())
            }
        }

        // Stamp common currency needles from map onto every similar key name.
        stampNeedles(liveEntries, fieldValues)
        clampEntriesForStability(liveEntries)

        val xml = PlayerPrefsXml.toXml(liveEntries)
        workingFile.parentFile?.mkdirs()
        workingFile.writeText(xml)
        workingFile.setReadable(true, false)
        workingFile.setWritable(true, false)

        val abs = workingFile.absolutePath
        val writeScript = buildString {
            append("cp \"$abs\" \"$path\"; ")
            append("chmod 660 \"$path\"; ")
            append("APPUID=\$(stat -c %u /data/data/$PACKAGE 2>/dev/null); ")
            append("if [ -z \"\$APPUID\" ]; then APPUID=\$(dumpsys package $PACKAGE 2>/dev/null | grep userId= | head -1 | sed 's/.*userId=\\([0-9]*\\).*/\\1/'); fi; ")
            append("if [ -n \"\$APPUID\" ]; then chown \"\$APPUID\":\"\$APPUID\" \"$path\"; fi; ")
            append("restorecon \"$path\" 2>/dev/null; ")
            append("ls -l \"$path\"")
        }
        val res = runSu(writeScript, timeoutMs = 10_000)
        return if (res.success || res.output.contains(path.substringAfterLast('/'))) {
            true to "Wrote ${liveEntries.size} keys → $path"
        } else {
            val b64 = android.util.Base64.encodeToString(xml.toByteArray(Charsets.UTF_8), android.util.Base64.NO_WRAP)
            val alt = runSu(
                "echo '$b64' | base64 -d > \"$path\"; chmod 660 \"$path\"; ls -l \"$path\"",
                timeoutMs = 10_000,
            )
            if (alt.success) {
                true to "Wrote ${liveEntries.size} keys (base64) → $path"
            } else {
                false to "Push failed: ${res.output} | ${alt.output}"
            }
        }
    }

    private fun stampNeedles(entries: MutableList<PrefEntry>, fieldValues: Map<String, String>) {
        fun stamp(needles: List<String>, raw: String?) {
            if (raw.isNullOrBlank()) return
            for (e in entries) {
                val n = e.name.lowercase()
                if (needles.any { n.contains(it) }) e.value = raw.trim()
            }
        }
        stamp(listOf("bux", "money", "cash", "soft"), fieldValues["Bux"] ?: fieldValues["bux"])
        stamp(listOf("gem", "premium", "hardcurrency", "hard"), fieldValues["Gems"] ?: fieldValues["gem"])
        stamp(listOf("knowledge", "brain"), fieldValues["Knowledge"] ?: fieldValues["knowledge"])
        stamp(listOf("sub", "follower"), fieldValues["Subscribers"] ?: fieldValues["subscriber"])
        stamp(listOf("view"), fieldValues["Views"] ?: fieldValues["view"])
        stamp(listOf("level", "rank"), fieldValues["ChannelLevel"] ?: fieldValues["PlayerLevel"])
        stamp(listOf("item", "inventory"), fieldValues["ItemCount"] ?: fieldValues["item"])
        stamp(listOf("furniture", "prop", "unlock"), fieldValues["FurnitureUnlocked"] ?: fieldValues["furniture"])
    }

    /** @deprecated use applyLiveEdits */
    fun pushPrefsAndStopGame(xml: String, workingFile: File): Pair<Boolean, String> {
        workingFile.writeText(xml)
        runSu("am force-stop $PACKAGE", timeoutMs = 4_000)
        try {
            Thread.sleep(500)
        } catch (_: InterruptedException) {
        }
        val path = findPrefsPath() ?: CANDIDATE_PATHS.first()
        workingFile.setReadable(true, false)
        val abs = workingFile.absolutePath
        val res = runSu("cp \"$abs\" \"$path\"; chmod 660 \"$path\"; ls -l \"$path\"", timeoutMs = 8_000)
        return if (res.success) true to "Saved." else false to "Push failed: ${res.output}"
    }

    fun pushPrefsXml(xml: String, workingFile: File): Pair<Boolean, String> =
        pushPrefsAndStopGame(xml, workingFile)

    fun ensureDefaults(entries: MutableList<PrefEntry>) {
        val have = entries.map { it.name.lowercase() }.toHashSet()
        for (d in DEFAULT_KEYS) {
            if (d.name.lowercase() !in have) {
                entries += d.copy()
                have += d.name.lowercase()
            }
        }
    }

    /**
     * Clamp insane values that brick boot (e.g. channel level in the hundreds of millions).
     * Keeps rich currency, but caps levels/counts to ranges the game can load.
     */
    fun clampEntriesForStability(entries: MutableList<PrefEntry>): Int {
        var changed = 0
        for (e in entries) {
            if (e.type == PrefType.STRING || e.type == PrefType.BOOLEAN) continue
            val n = e.name.lowercase()
            val num = e.value.toLongOrNull()
                ?: e.value.toDoubleOrNull()?.toLong()
                ?: continue
            val capped = when {
                listOf("level", "rank", "tier", "prestige", "season").any { n.contains(it) } ->
                    num.coerceIn(1L, 40L)
                listOf("room", "slot", "chapter", "quest", "tutorial", "stage").any { n.contains(it) } ->
                    num.coerceIn(0L, 200L)
                listOf("bux", "money", "cash", "gem", "knowledge", "brain", "currency", "coin")
                    .any { n.contains(it) } ->
                    num.coerceIn(0L, 9_999_999L)
                listOf("sub", "follower", "view").any { n.contains(it) } ->
                    num.coerceIn(0L, 50_000_000L)
                listOf("item", "inventory", "furniture", "prop", "unlock", "count", "amount")
                    .any { n.contains(it) } ->
                    num.coerceIn(0L, 5_000L)
                else -> num.coerceIn(-1_000L, 10_000_000L)
            }
            if (capped.toString() != e.value) {
                e.value = capped.toString()
                changed++
            }
        }
        return changed
    }

    fun freshStartClearData(): Pair<Boolean, String> {
        if (!hasRoot(2_000)) return false to "Need root"
        runSu("am force-stop $PACKAGE", timeoutMs = 4_000)
        try {
            Thread.sleep(400)
        } catch (_: InterruptedException) {
        }
        // Wipe prefs (keeps APK) — clears splash security lock from bad save
        val wipe = runSu(
            "rm -rf /data/data/$PACKAGE/shared_prefs/* /data/user/0/$PACKAGE/shared_prefs/*; " +
                "pm clear $PACKAGE 2>/dev/null; echo CLEARED",
            timeoutMs = 8_000,
        )
        prefsPathCache.set(null)
        return if (wipe.output.contains("CLEARED") || wipe.success) {
            true to "Fresh save — game data cleared. Open game, get in-world, then use LIVE APPLY (no restart)."
        } else {
            false to "Clear failed: ${wipe.output}"
        }
    }

    /** Clamp prefs on disk without clearing everything (still may trip restart security). */
    fun unstickGame(workingFile: File): Pair<Boolean, String> {
        if (!hasRoot(2_000)) {
            return false to "Need root to fix the save."
        }
        runSu("am force-stop $PACKAGE", timeoutMs = 4_000)
        try {
            Thread.sleep(700)
        } catch (_: InterruptedException) {
        }

        var path = findPrefsPath()
        if (path == null) {
            path = CANDIDATE_PATHS.first()
            runSu("mkdir -p \"\$(dirname \"$path\")\"", timeoutMs = 3_000)
        }

        val pull = runSu("cat \"$path\"", timeoutMs = 6_000)
        val entries: MutableList<PrefEntry> = if (pull.success && pull.output.contains("<")) {
            PlayerPrefsXml.parse(pull.output)
        } else {
            mutableListOf()
        }
        ensureDefaults(entries)
        clampEntriesForStability(entries)

        fun setAll(needles: List<String>, v: String) {
            for (e in entries) {
                if (needles.any { e.name.lowercase().contains(it) }) e.value = v
            }
        }
        setAll(listOf("bux", "money", "cash", "soft"), "999999")
        setAll(listOf("gem", "premium", "hard"), "50000")
        setAll(listOf("knowledge", "brain"), "100000")
        setAll(listOf("sub", "follower"), "100000")
        setAll(listOf("view"), "1000000")
        setAll(listOf("level", "rank"), "15")
        clampEntriesForStability(entries)

        val xml = PlayerPrefsXml.toXml(entries)
        workingFile.parentFile?.mkdirs()
        workingFile.writeText(xml)
        workingFile.setReadable(true, false)
        val abs = workingFile.absolutePath
        val writeScript = buildString {
            append("cp \"$abs\" \"$path\"; ")
            append("chmod 660 \"$path\"; ")
            append("APPUID=\$(stat -c %u /data/data/$PACKAGE 2>/dev/null); ")
            append("if [ -n \"\$APPUID\" ]; then chown \"\$APPUID\":\"\$APPUID\" \"$path\"; fi; ")
            append("restorecon \"$path\" 2>/dev/null; ")
            append("ls -l \"$path\"")
        }
        val res = runSu(writeScript, timeoutMs = 10_000)
        return if (res.success || res.output.contains("playerprefs", ignoreCase = true)) {
            true to "Unstuck — level capped, safe rich values written (${entries.size} keys)."
        } else {
            false to "Unstick write failed: ${res.output}"
        }
    }

    data class SuResult(val success: Boolean, val output: String)

    fun runSu(command: String, timeoutMs: Long = 5_000): SuResult {
        // BlueStacks / Magisk variants
        val attempts = listOf(
            listOf("su", "-c", command),
            listOf("su", "0", command),
            listOf("/system/xbin/su", "-c", command),
            listOf("/system/bin/su", "-c", command),
        )
        var last = SuResult(false, "su missing")
        for (cmd in attempts) {
            try {
                val p = ProcessBuilder(cmd)
                    .redirectErrorStream(true)
                    .start()
                val finished = try {
                    p.waitFor(timeoutMs, TimeUnit.MILLISECONDS)
                } catch (_: Exception) {
                    false
                }
                if (!finished) {
                    try {
                        p.destroyForcibly()
                    } catch (_: Exception) {
                    }
                    last = SuResult(false, "su timed out (${timeoutMs}ms)")
                    continue
                }
                val out = BufferedReader(InputStreamReader(p.inputStream)).readText().trim()
                val ok = p.exitValue() == 0
                last = SuResult(ok, out)
                if (ok || out.contains("uid=0")) return last
                // If su existed and ran, don't try forever on auth failures
                if (out.isNotBlank() && !out.contains("not found", ignoreCase = true)) {
                    return last
                }
            } catch (e: Exception) {
                last = SuResult(false, e.message ?: "su missing")
            }
        }
        return last
    }
}
