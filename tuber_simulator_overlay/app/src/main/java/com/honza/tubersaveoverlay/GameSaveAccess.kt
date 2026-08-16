package com.honza.tubersaveoverlay

import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * Root helpers to read/write Tuber Simulator PlayerPrefs.
 * Never call [hasRoot] / [runSu] on the main thread — `su` can hang on non-root phones.
 */
object GameSaveAccess {
    const val PACKAGE = "com.outerminds.tubular"

    private val CANDIDATE_PATHS = listOf(
        "/data/data/$PACKAGE/shared_prefs/$PACKAGE.v2.playerprefs.xml",
        "/data/data/$PACKAGE/shared_prefs/$PACKAGE.playerprefs.xml",
        "/data/user/0/$PACKAGE/shared_prefs/$PACKAGE.v2.playerprefs.xml",
        "/data/user/0/$PACKAGE/shared_prefs/$PACKAGE.playerprefs.xml",
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

    /** Instant: cached value or false (assume no root until probed off the UI thread). */
    fun hasRootCached(): Boolean = rootCache.get() == true

    /**
     * Probe root with a short timeout. Safe to call from a background thread only.
     * On phones without root, `su` often hangs — we kill it after [timeoutMs].
     */
    fun hasRoot(timeoutMs: Long = 700): Boolean {
        rootCache.get()?.let { return it }
        val ok = probeRoot(timeoutMs)
        rootCache.set(ok)
        return ok
    }

    fun invalidateRootCache() {
        rootCache.set(null)
    }

    private fun probeRoot(timeoutMs: Long): Boolean {
        val res = runSu("id", timeoutMs)
        return res.success && res.output.contains("uid=0")
    }

    fun findPrefsPath(): String? {
        for (p in CANDIDATE_PATHS) {
            if (runSu("ls \"$p\"").success) return p
        }
        val find = runSu("find /data/data/$PACKAGE/shared_prefs -name '*playerprefs*' 2>/dev/null | head -5")
        return find.output.lineSequence().map { it.trim() }.firstOrNull { it.endsWith(".xml") }
    }

    fun pullPrefsXml(): Pair<Boolean, String> {
        val path = findPrefsPath()
            ?: return false to "No playerprefs XML found (need root + game installed)."
        val res = runSu("cat \"$path\"")
        if (!res.success || res.output.isBlank()) {
            return false to "Root read failed: ${res.output.ifBlank { "empty" }}"
        }
        return true to res.output
    }

    /**
     * Write prefs into the game, force-stop it. Caller should relaunch the app afterward.
     */
    fun pushPrefsAndStopGame(xml: String, workingFile: File): Pair<Boolean, String> {
        val path = findPrefsPath()
            ?: return false to "No playerprefs path (need root)."
        workingFile.parentFile?.mkdirs()
        workingFile.writeText(xml)
        workingFile.setReadable(true, false)

        val script = """
            cp "${workingFile.absolutePath}" "$path"
            chmod 660 "$path"
            am force-stop $PACKAGE
            sleep 0.4
            """.trimIndent().replace("\n", "; ")
        val res = runSu(script, timeoutMs = 8_000)
        return if (res.success) {
            true to "Saved. Restarting Tuber Simulator…"
        } else {
            runSu("am force-stop $PACKAGE", timeoutMs = 3_000)
            false to "Push failed: ${res.output}"
        }
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

    data class SuResult(val success: Boolean, val output: String)

    fun runSu(command: String, timeoutMs: Long = 5_000): SuResult {
        return try {
            val p = ProcessBuilder("su", "-c", command)
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
                return SuResult(false, "su timed out (${timeoutMs}ms)")
            }
            val out = BufferedReader(InputStreamReader(p.inputStream)).readText().trim()
            SuResult(p.exitValue() == 0, out)
        } catch (e: Exception) {
            SuResult(false, e.message ?: "su missing")
        }
    }
}
