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
 * No-root helpers for Tuber Simulator.
 *
 * Real currency (Bux/Gems) is almost always in
 * `/data/data/com.outerminds.tubular/shared_prefs/` — **not writable without root**.
 *
 * This only patches files under the game's **Android/data** folder when the OEM
 * still allows it. Exporting to Download does NOT affect the game.
 */
object NoRootSaveAccess {
    const val PACKAGE = GameSaveAccess.PACKAGE

    data class SaveHit(val file: File, val label: String)

    data class ApplyResult(
        val gameEdits: Int,
        val androidDataReachable: Boolean,
        val exportPath: String?,
        val restartMsg: String,
        val summary: String,
        val changedGame: Boolean,
    )

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

    /** Only the game's external sandbox — never Download/ (our own exports). */
    fun gameAndroidDataRoots(): List<File> {
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
            )
        }.distinctBy { it.absolutePath }
    }

    fun diagnoseAndroidData(): String {
        val roots = gameAndroidDataRoots()
        val existing = roots.filter { it.exists() }
        if (existing.isEmpty()) {
            return "Android/data/$PACKAGE not visible (normal on Android 11+ without root)."
        }
        val readable = existing.count { it.canRead() && it.listFiles() != null }
        val files = findGameSaves().size
        return "Android/data visible=${existing.size}, listable=$readable, candidate files=$files"
    }

    fun findGameSaves(): List<SaveHit> {
        val out = mutableListOf<SaveHit>()
        val interestingExt = setOf(
            "json", "xml", "txt", "dat", "save", "bin", "bytes", "playerprefs", "prefs",
        )
        for (root in gameAndroidDataRoots()) {
            if (!root.exists()) continue
            val listed = root.listFiles()
            if (listed == null) continue
            try {
                root.walkTopDown()
                    .maxDepth(12)
                    .filter { it.isFile && it.canRead() && it.length() in 8..(12_000_000) }
                    .forEach { f ->
                        val name = f.name.lowercase()
                        val ext = f.extension.lowercase()
                        val looksUseful = ext in interestingExt ||
                            name.contains("save") ||
                            name.contains("player") ||
                            name.contains("prefs") ||
                            name.contains("profile") ||
                            name.contains("user") ||
                            name.contains("data") ||
                            name.contains("bux") ||
                            name.endsWith(".bytes")
                        if (!looksUseful) return@forEach
                        if (ext in setOf("png", "jpg", "jpeg", "ogg", "mp3", "mp4", "wav", "webp")) {
                            return@forEach
                        }
                        val label = try {
                            f.relativeTo(root).path
                        } catch (_: Exception) {
                            f.name
                        }
                        out += SaveHit(f, label)
                    }
            } catch (_: Exception) {
            }
        }
        return out.distinctBy { it.file.absolutePath }
    }

    /** @deprecated use findGameSaves — kept so older call sites compile if any remain */
    fun findEditableSaves(): List<SaveHit> = findGameSaves()

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

    fun tryWritePrefsMirror(xml: String): String? {
        for (root in gameAndroidDataRoots()) {
            if (!root.exists() && !root.mkdirs()) continue
            if (root.listFiles() == null && !root.canWrite()) continue
            val filesDir = File(root, "files")
            filesDir.mkdirs()
            val targets = listOf(
                File(filesDir, "playerprefs_edited.xml"),
                File(filesDir, "$PACKAGE.v2.playerprefs.xml"),
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

    fun patchSaves(
        saves: List<SaveHit>,
        values: Map<String, String>,
    ): Pair<Int, String> {
        if (saves.isEmpty()) {
            return 0 to "No writable game files under Android/data/$PACKAGE"
        }
        var total = 0
        val notes = mutableListOf<String>()
        for (hit in saves) {
            val f = hit.file
            // Never treat our Download exports as game saves (path guard).
            if (f.absolutePath.contains("/Download/TuberSaveOverlay", ignoreCase = true)) {
                continue
            }
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
        return total to notes.joinToString("; ").ifBlank { "no game-file changes" }
    }

    fun applyAndRestart(context: Context, values: Map<String, String>, xml: String): ApplyResult {
        val saves = findGameSaves()
        val reachable = gameAndroidDataRoots().any { it.exists() && it.listFiles() != null }
        val (count, note) = patchSaves(saves, values)
        val export = exportXmlToDownloads(xml)
        val mirror = tryWritePrefsMirror(xml)
        val restart = restartGame(context)

        val changed = count > 0
        val summary = if (changed) {
            buildString {
                append("CHANGED GAME: $count edits ($note). $restart")
                if (export != null) append(" · backup XML in Download/TuberSaveOverlay")
            }
        } else {
            buildString {
                append("DID NOT CHANGE GAME VALUES. ")
                append(diagnoseAndroidData())
                append(" · ")
                append(note)
                if (mirror != null) append(" · wrote unused mirror $mirror")
                if (export != null) append(" · exported XML only to Download (game ignores this)")
                append(" · $restart")
                append(" · Bux/Gems need Magisk ROOT (or BlueStacks Root ON).")
            }
        }
        return ApplyResult(
            gameEdits = count,
            androidDataReachable = reachable,
            exportPath = export?.absolutePath,
            restartMsg = restart,
            summary = summary,
            changedGame = changed,
        )
    }

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
