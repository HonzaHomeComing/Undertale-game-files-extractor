package com.honza.tubersaveoverlay

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.Settings
import java.io.File

/**
 * No-root save access for Tuber Simulator on a normal phone.
 *
 * Unity PlayerPrefs live under /data/data/... and stay locked without root.
 * This path:
 *  1) Patches any readable/writable text saves under Android/data/<package>/
 *  2) Always exports the edited PlayerPrefs XML to Download/TuberSaveOverlay/
 *  3) Restarts the game (Home → kill background → launch) without su
 */
object NoRootSaveAccess {
    const val PACKAGE = GameSaveAccess.PACKAGE

    data class SaveHit(val file: File, val label: String)

    fun hasAllFilesAccess(): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            Environment.isExternalStorageManager()
        } else {
            true
        }

    fun allFilesAccessIntent(context: Context): Intent {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            Intent(
                Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                Uri.parse("package:${context.packageName}"),
            )
        } else {
            Intent(
                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:${context.packageName}"),
            )
        }
    }

    fun gameExternalRoots(): List<File> {
        val bases = mutableListOf<File>()
        Environment.getExternalStorageDirectory()?.let { bases += it }
        try {
            bases += File("/storage/emulated/0")
            bases += File("/sdcard")
        } catch (_: Exception) {
        }
        return bases.distinctBy { it.absolutePath }.flatMap { base ->
            listOf(
                File(base, "Android/data/$PACKAGE"),
                File(base, "Android/media/$PACKAGE"),
                File(base, "Download/TuberSaveOverlay"),
                File(base, "Download/TuberSimulator"),
            )
        }.distinctBy { it.absolutePath }
    }

    fun findEditableSaves(): List<SaveHit> {
        val out = mutableListOf<SaveHit>()
        val interestingExt = setOf(
            "json", "xml", "txt", "dat", "save", "bin", "bytes", "playerprefs", "prefs",
        )
        for (root in gameExternalRoots()) {
            if (!root.exists()) continue
            root.walkTopDown()
                .maxDepth(10)
                .filter { it.isFile && it.canRead() && it.length() in 8..(8_000_000) }
                .forEach { f ->
                    val name = f.name.lowercase()
                    val ext = f.extension.lowercase()
                    val looksUseful = ext in interestingExt ||
                        name.contains("save") ||
                        name.contains("player") ||
                        name.contains("prefs") ||
                        name.contains("profile") ||
                        name.contains("user") ||
                        name.contains("data")
                    if (!looksUseful) return@forEach
                    if (ext in setOf("png", "jpg", "jpeg", "ogg", "mp3", "mp4", "wav", "webp")) {
                        return@forEach
                    }
                    val label = try {
                        f.relativeTo(root).path
                    } catch (_: Exception) {
                        f.name
                    }
                    out += SaveHit(f, "${root.name}/$label")
                }
        }
        return out.distinctBy { it.file.absolutePath }
    }

    fun exportXmlToDownloads(xml: String): File? {
        val base = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            ?: File("/sdcard/Download")
        val dir = File(base, "TuberSaveOverlay")
        if (!dir.exists() && !dir.mkdirs()) return null
        val out = File(dir, "playerprefs_edited.xml")
        return try {
            out.writeText(xml)
            out
        } catch (_: Exception) {
            null
        }
    }

    /** Best-effort mirror into the game's external files folder (Unity persistentDataPath). */
    fun tryWritePrefsMirror(xml: String): String? {
        for (root in gameExternalRoots().filter { it.path.contains("Android/data") }) {
            val filesDir = File(root, "files")
            if (!filesDir.exists()) filesDir.mkdirs()
            if (!filesDir.exists()) continue
            val targets = listOf(
                File(filesDir, "playerprefs_edited.xml"),
                File(filesDir, "$PACKAGE.v2.playerprefs.xml"),
                File(root, "shared_prefs/$PACKAGE.v2.playerprefs.xml"),
            )
            for (t in targets) {
                try {
                    t.parentFile?.mkdirs()
                    t.writeText(xml)
                    if (t.exists() && t.length() > 0) return t.absolutePath
                } catch (_: Exception) {
                }
            }
        }
        return null
    }

    /**
     * Patch numeric literals next to currency-like keys in text files.
     * Returns how many replacements were made across files.
     */
    fun patchSaves(
        saves: List<SaveHit>,
        values: Map<String, String>,
    ): Pair<Int, String> {
        if (saves.isEmpty()) {
            return 0 to "No editable saves under Android/data/$PACKAGE (currency is usually root-only PlayerPrefs)."
        }
        var total = 0
        val notes = mutableListOf<String>()
        for (hit in saves) {
            val f = hit.file
            if (!f.canWrite() && !f.setWritable(true)) {
                notes += "skip ${hit.label} (not writable)"
                continue
            }
            val original = try {
                f.readText(Charsets.UTF_8)
            } catch (_: Exception) {
                try {
                    f.readBytes().toString(Charsets.ISO_8859_1)
                } catch (e: Exception) {
                    notes += "skip ${hit.label}: ${e.message}"
                    continue
                }
            }
            val printable = original.count { it.code in 32..126 || it in "\r\n\t" }
            if (printable < original.length * 0.7) {
                notes += "skip ${hit.label} (binary)"
                continue
            }
            var text = original
            var fileChanges = 0
            for ((needle, raw) in values) {
                if (raw.isBlank()) continue
                val n = patchKeyValue(text, needle, raw)
                if (n.first > 0) {
                    text = n.second
                    fileChanges += n.first
                }
            }
            for ((word, raw) in values) {
                if (raw.isBlank()) continue
                val n = patchNearbyNumbers(text, word, raw)
                if (n.first > 0) {
                    text = n.second
                    fileChanges += n.first
                }
            }
            if (fileChanges > 0 && text != original) {
                try {
                    f.writeText(text)
                    total += fileChanges
                    notes += "${hit.label}: $fileChanges edits"
                } catch (e: Exception) {
                    notes += "write fail ${hit.label}: ${e.message}"
                }
            }
        }
        return total to notes.joinToString("; ").ifBlank { "no changes written" }
    }

    /**
     * Full no-root apply: patch external saves, export XML, restart game.
     */
    fun applyAndRestart(context: Context, values: Map<String, String>, xml: String): String {
        val saves = findEditableSaves()
        val (count, note) = patchSaves(saves, values)
        val export = exportXmlToDownloads(xml)
        val mirror = tryWritePrefsMirror(xml)
        val restart = restartGame(context)
        val parts = mutableListOf<String>()
        if (count > 0) {
            parts += "Patched $count values ($note)"
        } else {
            parts += note
        }
        if (export != null) parts += "XML → ${export.absolutePath}"
        if (mirror != null) parts += "Mirror → $mirror"
        parts += restart
        if (count == 0) {
            parts += "Tip: this game keeps Bux/Gems in a private folder Android locks without root."
        }
        return parts.joinToString(" · ")
    }

    /** JSON/"key": 123 or XML name="key" value="123" style. */
    private fun patchKeyValue(text: String, key: String, newValue: String): Pair<Int, String> {
        var count = 0
        var out = text
        val patterns = listOf(
            Regex("(\"${Regex.escape(key)}\"\\s*:\\s*)(-?\\d+(\\.\\d+)?)", RegexOption.IGNORE_CASE),
            Regex("('${Regex.escape(key)}'\\s*:\\s*)(-?\\d+(\\.\\d+)?)", RegexOption.IGNORE_CASE),
            Regex(
                "(name=\"[^\"]*${Regex.escape(key)}[^\"]*\"\\s+value=\")(-?\\d+(\\.\\d+)?)(\")",
                RegexOption.IGNORE_CASE,
            ),
            Regex(
                "(<int\\s+name=\"[^\"]*${Regex.escape(key)}[^\"]*\"\\s+value=\")(-?\\d+)(\")",
                RegexOption.IGNORE_CASE,
            ),
        )
        for (re in patterns) {
            out = re.replace(out) {
                count++
                it.groupValues[1] + newValue + it.groupValues.getOrElse(3) { "" }
            }
        }
        return count to out
    }

    private fun patchNearbyNumbers(text: String, word: String, newValue: String): Pair<Int, String> {
        val re = Regex(
            "([\"']?[A-Za-z0-9_]*${Regex.escape(word)}[A-Za-z0-9_]*[\"']?\\s*[:=]\\s*)(-?\\d+(\\.\\d+)?)",
            RegexOption.IGNORE_CASE,
        )
        var count = 0
        val out = re.replace(text) {
            count++
            it.groupValues[1] + newValue
        }
        return count to out
    }

    /**
     * Restart without root: go Home → kill background processes → launch game.
     */
    fun restartGame(context: Context): String {
        val pm = context.packageManager
        val launch = pm.getLaunchIntentForPackage(PACKAGE)
            ?: return "Tuber Simulator not installed."
        try {
            val home = Intent(Intent.ACTION_MAIN).apply {
                addCategory(Intent.CATEGORY_HOME)
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            }
            context.startActivity(home)
        } catch (_: Exception) {
        }
        try {
            Thread.sleep(450)
        } catch (_: InterruptedException) {
        }
        try {
            val am = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
            am.killBackgroundProcesses(PACKAGE)
        } catch (_: Exception) {
        }
        try {
            Thread.sleep(550)
        } catch (_: InterruptedException) {
        }
        launch.addFlags(
            Intent.FLAG_ACTIVITY_NEW_TASK or
                Intent.FLAG_ACTIVITY_CLEAR_TOP or
                Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED or
                Intent.FLAG_ACTIVITY_CLEAR_TASK,
        )
        return try {
            context.startActivity(launch)
            "Game relaunched"
        } catch (e: Exception) {
            "Could not relaunch: ${e.message}"
        }
    }
}
