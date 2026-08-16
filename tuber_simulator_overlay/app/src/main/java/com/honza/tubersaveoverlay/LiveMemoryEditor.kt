package com.honza.tubersaveoverlay

import android.content.Context
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Live RAM patcher for rooted BlueStacks.
 * Dumps writable regions of the game process, search/replaces int/long/float/double.
 */
object LiveMemoryEditor {
    data class Result(
        val ok: Boolean,
        val message: String,
        val replacements: Int,
        val detail: String = "",
    )

    fun findGamePid(): Int? {
        val cmds = listOf(
            "pidof ${GameSaveAccess.PACKAGE}",
            "pidof -s ${GameSaveAccess.PACKAGE}",
            "toybox pidof ${GameSaveAccess.PACKAGE}",
            "ps -A -o PID,NAME 2>/dev/null | grep ${GameSaveAccess.PACKAGE}",
            "ps -A 2>/dev/null | grep ${GameSaveAccess.PACKAGE} | grep -v grep",
            "ps 2>/dev/null | grep ${GameSaveAccess.PACKAGE} | grep -v grep",
            "ls /proc 2>/dev/null | while read p; do " +
                "cmdline=\$(cat /proc/\$p/cmdline 2>/dev/null | tr '\\0' ' '); " +
                "echo \"\$cmdline\" | grep -q ${GameSaveAccess.PACKAGE} && echo \$p && break; done",
        )
        for (c in cmds) {
            val r = GameSaveAccess.runSu(c, timeoutMs = 5_000)
            val pid = Regex("(?<!\\d)(\\d{2,7})(?!\\d)").findAll(r.output)
                .mapNotNull { it.groupValues[1].toIntOrNull() }
                .firstOrNull { it > 1 }
            if (pid != null) return pid
        }
        return null
    }

    fun diagnose(context: Context): String {
        val sb = StringBuilder()
        if (!GameSaveAccess.hasRoot(2_000)) return "No root (su failed)."
        sb.append("root=yes")
        val pid = findGamePid()
        if (pid == null) return "$sb · pid=NOT FOUND (open game past splash)"
        sb.append(" · pid=").append(pid)
        GameSaveAccess.runSu("echo 0 > /proc/sys/kernel/yama/ptrace_scope 2>/dev/null", 2_000)
        val maps = GameSaveAccess.runSu("wc -l < /proc/$pid/maps 2>/dev/null; head -1 /proc/$pid/maps", 4_000)
        sb.append(" · maps=").append(maps.output.replace('\n', ' ').take(80))
        val work = workDir(context)
        work.mkdirs()
        val probe = File(work, "probe.bin")
        GameSaveAccess.runSu("kill -STOP $pid 2>/dev/null; true", 2_000)
        val dump = GameSaveAccess.runSu(
            "dd if=/proc/$pid/mem of=${probe.absolutePath} bs=4096 skip=0 count=1 2>&1; chmod 666 ${probe.absolutePath}; ls -l ${probe.absolutePath}",
            8_000,
        )
        GameSaveAccess.runSu("kill -CONT $pid 2>/dev/null; true", 2_000)
        sb.append(" · mem_dump=").append(if (probe.exists() && probe.length() > 0) "OK(${probe.length()})" else "FAIL")
        sb.append(" · dd=").append(dump.output.take(120).replace('\n', ' '))
        val regions = parseRegions(
            GameSaveAccess.runSu("cat /proc/$pid/maps", 6_000).output,
        )
        sb.append(" · rw_regions=").append(regions.size)
        return sb.toString()
    }

    fun replaceMany(context: Context, changes: List<Pair<Long, Long>>): Result {
        if (!GameSaveAccess.hasRoot(2_000)) {
            return Result(false, "Need root — BlueStacks Root ON + grant su to overlay.", 0)
        }
        GameSaveAccess.runSu("echo 0 > /proc/sys/kernel/yama/ptrace_scope 2>/dev/null", 2_000)

        val pid = findGamePid()
            ?: return Result(
                false,
                "Can't find game PID. Open Tuber Simulator fully (past OUTERMINDS), keep it in foreground, retry.",
                0,
                diagnose(context),
            )

        val distinct = changes.filter { it.first != it.second }.distinct()
        if (distinct.isEmpty()) return Result(true, "Nothing to change (OLD=NEW).", 0)

        // Probe mem access once
        val work = workDir(context)
        work.mkdirs()
        val probe = File(work, "probe.bin")
        GameSaveAccess.runSu("kill -STOP $pid 2>/dev/null; true", 2_000)
        GameSaveAccess.runSu(
            "dd if=/proc/$pid/mem of=${probe.absolutePath} bs=4096 count=1 2>/dev/null; chmod 666 ${probe.absolutePath}",
            8_000,
        )
        if (!probe.exists() || probe.length() == 0L) {
            GameSaveAccess.runSu("kill -CONT $pid 2>/dev/null; true", 2_000)
            return Result(
                false,
                "Root can't read /proc/$pid/mem on this BlueStacks build. Try BlueStacks 5 Nougat/Pie instance, or another rooted emulator.",
                0,
                diagnose(context),
            )
        }

        val mapsOut = GameSaveAccess.runSu("cat /proc/$pid/maps", 8_000)
        val regions = parseRegions(mapsOut.output)
        if (regions.isEmpty()) {
            GameSaveAccess.runSu("kill -CONT $pid 2>/dev/null; true", 2_000)
            return Result(false, "No writable memory maps for pid $pid.", 0, mapsOut.output.take(200))
        }

        var total = 0
        var dumpsOk = 0
        val parts = mutableListOf<String>()

        try {
            for ((oldV, newV) in distinct) {
                val r = replaceInPid(context, pid, regions, oldV, newV)
                total += r.replacements
                dumpsOk += r.detail.toIntOrNull() ?: 0
                parts += "$oldV→$newV:${r.replacements}"
            }
        } finally {
            GameSaveAccess.runSu("kill -CONT $pid 2>/dev/null; true", 2_000)
        }

        // cleanup chunks
        work.listFiles()?.forEach { if (it.name.startsWith("c")) it.delete() }
        probe.delete()

        return if (total > 0) {
            Result(
                true,
                "LIVE RAM OK (pid $pid): $total hit(s) — ${parts.joinToString(" · ")}. Do NOT restart.",
                total,
            )
        } else {
            Result(
                false,
                "No matches for ${parts.joinToString()}. " +
                    "Type the EXACT number shown in-game into the field, tap Snapshot, change it, LIVE APPLY. " +
                    "Scanned ${regions.size} regions.",
                0,
                "pid=$pid dumps=$dumpsOk regions=${regions.size}",
            )
        }
    }

    private fun replaceInPid(
        context: Context,
        pid: Int,
        regions: List<Region>,
        oldValue: Long,
        newValue: Long,
        maxHits: Int = 80,
    ): Result {
        val work = workDir(context)
        val patterns = patternsFor(oldValue, newValue)
        var total = 0
        var dumps = 0

        for ((i, reg) in regions.withIndex()) {
            if (total >= maxHits) break
            val size = reg.end - reg.start
            if (size < 4L || size > 24L * 1024 * 1024) continue

            val aligned = reg.start and -4096L
            val skipPages = aligned / 4096L
            val pageCount = ((reg.end - aligned) + 4095L) / 4096L
            val chunk = File(work, "c${i}_${oldValue}.bin")

            GameSaveAccess.runSu(
                "dd if=/proc/$pid/mem of=${chunk.absolutePath} bs=4096 skip=$skipPages count=$pageCount 2>/dev/null; " +
                    "chmod 666 ${chunk.absolutePath}",
                timeoutMs = 45_000,
            )
            if (!chunk.exists() || chunk.length() < 4L) continue
            dumps++

            val data = try {
                chunk.readBytes()
            } catch (_: Exception) {
                continue
            }
            val bias = (reg.start - aligned).toInt().coerceAtLeast(0)
            val spanEnd = minOf(data.size, (bias + size).toInt().coerceAtLeast(bias + 4))
            var hits = 0
            val mutable = data.copyOf()
            for ((oldB, newB) in patterns) {
                var from = bias
                while (from <= spanEnd - oldB.size && total + hits < maxHits) {
                    val at = indexOf(mutable, oldB, from, spanEnd)
                    if (at < 0) break
                    System.arraycopy(newB, 0, mutable, at, newB.size)
                    hits++
                    from = at + newB.size
                }
            }
            if (hits == 0) {
                chunk.delete()
                continue
            }
            try {
                chunk.writeBytes(mutable)
            } catch (_: Exception) {
                chunk.delete()
                continue
            }
            GameSaveAccess.runSu(
                "dd if=${chunk.absolutePath} of=/proc/$pid/mem bs=4096 seek=$skipPages conv=notrunc 2>/dev/null",
                timeoutMs = 45_000,
            )
            total += hits
            chunk.delete()
        }
        return Result(total > 0, "ok", total, dumps.toString())
    }

    private fun workDir(context: Context): File {
        val ext = context.getExternalFilesDir("tuber_mem")
        if (ext != null) {
            ext.mkdirs()
            // Make sure root can write here
            GameSaveAccess.runSu("chmod 777 \"${ext.absolutePath}\" 2>/dev/null", 2_000)
            return ext
        }
        val local = File(context.filesDir, "tuber_mem")
        local.mkdirs()
        GameSaveAccess.runSu("chmod 777 \"${local.absolutePath}\" 2>/dev/null", 2_000)
        return local
    }

    private data class Region(val start: Long, val end: Long)

    private fun parseRegions(maps: String): List<Region> {
        val out = ArrayList<Region>()
        for (line in maps.lineSequence()) {
            val cols = line.trim().split(Regex("\\s+"))
            if (cols.size < 2) continue
            val perms = cols[1]
            if (!perms.startsWith("rw")) continue
            val path = cols.getOrNull(cols.size - 1).orEmpty()
            if (path.endsWith(".so")) continue
            if (path.startsWith("/system") || path.startsWith("/vendor") || path.startsWith("/apex")) continue
            if (path.contains(".apk") || path.contains("font") || path.contains("/dev/ashmem/Cursor")) continue
            val segs = cols[0].split('-')
            if (segs.size != 2) continue
            val start = segs[0].toLongOrNull(16) ?: continue
            val end = segs[1].toLongOrNull(16) ?: continue
            if (end > start) out += Region(start, end)
        }
        // Prefer mid-sized heap-like regions
        return out
            .filter { val s = it.end - it.start; s in 4L..(24L * 1024 * 1024) }
            .sortedBy { kotlin.math.abs((it.end - it.start) - 2_000_000L) }
            .take(100)
    }

    private fun patternsFor(oldV: Long, newV: Long): List<Pair<ByteArray, ByteArray>> {
        val list = ArrayList<Pair<ByteArray, ByteArray>>(6)
        if (oldV in Int.MIN_VALUE.toLong()..Int.MAX_VALUE.toLong() &&
            newV in Int.MIN_VALUE.toLong()..Int.MAX_VALUE.toLong()
        ) {
            list += le32(oldV.toInt()) to le32(newV.toInt())
        }
        list += le64(oldV) to le64(newV)
        list += leF(oldV.toFloat()) to leF(newV.toFloat())
        list += leD(oldV.toDouble()) to leD(newV.toDouble())
        return list
    }

    private fun le32(v: Int) = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(v).array()
    private fun le64(v: Long) = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(v).array()
    private fun leF(v: Float) = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putFloat(v).array()
    private fun leD(v: Double) = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putDouble(v).array()

    private fun indexOf(data: ByteArray, pat: ByteArray, from: Int, endExclusive: Int): Int {
        val last = endExclusive - pat.size
        if (last < from) return -1
        outer@ for (i in from..last) {
            for (j in pat.indices) if (data[i + j] != pat[j]) continue@outer
            return i
        }
        return -1
    }
}
