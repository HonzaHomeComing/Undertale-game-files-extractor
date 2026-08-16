package com.honza.tubersaveoverlay

import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader

/**
 * Root helpers to read/write Tuber Simulator PlayerPrefs and force-stop the game
 * so values reload.
 */
object GameSaveAccess {
    const val PACKAGE = "com.outerminds.tubular"

    private val CANDIDATE_PATHS = listOf(
        "/data/data/$PACKAGE/shared_prefs/$PACKAGE.v2.playerprefs.xml",
        "/data/data/$PACKAGE/shared_prefs/$PACKAGE.playerprefs.xml",
        "/data/user/0/$PACKAGE/shared_prefs/$PACKAGE.v2.playerprefs.xml",
        "/data/user/0/$PACKAGE/shared_prefs/$PACKAGE.playerprefs.xml",
    )

    /** Keys we always ensure exist so the cheat menu has something to write. */
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

    fun hasRoot(): Boolean = runSu("id").success && runSu("id").output.contains("uid=0")

    fun findPrefsPath(): String? {
        for (p in CANDIDATE_PATHS) {
            if (runSu("ls \"$p\"").success) return p
        }
        // Fallback search
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

    fun pushPrefsXml(xml: String, workingFile: File): Pair<Boolean, String> {
        val path = findPrefsPath()
            ?: return false to "No playerprefs path (need root)."
        workingFile.writeText(xml)
        // Copy into place, fix ownership/mode, kill game so it reloads
        val script = """
            cp "${workingFile.absolutePath}" "$path"
            chmod 660 "$path"
            chown $(stat -c %u:%g $(dirname "$path")/..) "$path" 2>/dev/null || true
            am force-stop $PACKAGE
            """.trimIndent().replace("\n", "; ")
        val res = runSu(script)
        return if (res.success) {
            true to "Pushed to game and force-stopped. Reopen Tuber Simulator."
        } else {
            false to "Push failed: ${res.output}"
        }
    }

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

    fun runSu(command: String): SuResult {
        return try {
            val p = ProcessBuilder("su", "-c", command)
                .redirectErrorStream(true)
                .start()
            val out = BufferedReader(InputStreamReader(p.inputStream)).readText()
            val code = p.waitFor()
            SuResult(code == 0, out.trim())
        } catch (e: Exception) {
            SuResult(false, e.message ?: "su missing")
        }
    }
}
