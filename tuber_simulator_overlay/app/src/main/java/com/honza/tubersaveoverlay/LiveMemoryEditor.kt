package com.honza.tubersaveoverlay

import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Live RAM patcher (rooted BlueStacks).
 *
 * Outerminds often rejects tampered PlayerPrefs on restart (splash hang).
 * Editing numbers in the running process avoids that load check.
 */
object LiveMemoryEditor {
    data class Result(val ok: Boolean, val message: String, val replacements: Int)

    fun findGamePid(): Int? {
        val cmds = listOf(
            "pidof ${GameSaveAccess.PACKAGE}",
            "toybox pidof ${GameSaveAccess.PACKAGE}",
            "ps -A 2>/dev/null | grep ${GameSaveAccess.PACKAGE} | grep -v grep",
            "ps 2>/dev/null | grep ${GameSaveAccess.PACKAGE} | grep -v grep",
        )
        for (c in cmds) {
            val r = GameSaveAccess.runSu(c, timeoutMs = 4_000)
            val pid = Regex("\\b(\\d{2,7})\\b").findAll(r.output)
                .map { it.groupValues[1].toInt() }
                .firstOrNull { it > 1 }
            if (pid != null) return pid
        }
        return null
    }

    fun replaceMany(changes: List<Pair<Long, Long>>): Result {
        val pid = findGamePid()
            ?: return Result(
                false,
                "Game not running in memory. Get PAST the OUTERMINDS splash into the game, then try LIVE APPLY.",
                0,
            )
        if (!GameSaveAccess.hasRoot(2_000)) return Result(false, "Need root", 0)

        val distinct = changes.filter { it.first != it.second }.distinct()
        if (distinct.isEmpty()) return Result(true, "Nothing to change", 0)

        var total = 0
        val parts = mutableListOf<String>()
        for ((oldV, newV) in distinct) {
            val r = replaceInPid(pid, oldV, newV)
            total += r.replacements
            parts += "$oldV→$newV (${r.replacements})"
            if (r.message.contains("Can't read")) return r
        }
        return if (total > 0) {
            Result(true, "LIVE RAM OK (pid $pid): $total hits — ${parts.joinToString(" · ")}. Do NOT restart.", total)
        } else {
            Result(
                false,
                "No RAM matches. In-game currency must match the OLD field values exactly " +
                    "(pull/snapshot). Change only the NEW numbers, then LIVE APPLY. ${parts.joinToString(" · ")}",
                0,
            )
        }
    }

    private fun replaceInPid(pid: Int, oldValue: Long, newValue: Long, maxHits: Int = 48): Result {
        val maps = GameSaveAccess.runSu("cat /proc/$pid/maps", timeoutMs = 6_000)
        if (!maps.success) return Result(false, "Can't read maps: ${maps.output}", 0)

        val regions = parseRegions(maps.output)
        val work = File("/data/local/tmp/tuber_mem")
        GameSaveAccess.runSu("rm -rf ${work.absolutePath}; mkdir -p ${work.absolutePath}; chmod 777 ${work.absolutePath}", 3_000)

        var total = 0
        val patterns = patternsFor(oldValue, newValue)

        for ((i, reg) in regions.withIndex()) {
            if (total >= maxHits) break
            val size = reg.end - reg.start
            if (size < 4 || size > 32L * 1024 * 1024) continue

            val aligned = reg.start and -4096L
            val skipPages = aligned / 4096L
            val pageCount = ((reg.end - aligned) + 4095L) / 4096L
            val remote = "${work.absolutePath}/c$i.bin"

            GameSaveAccess.runSu(
                "dd if=/proc/$pid/mem of=$remote bs=4096 skip=$skipPages count=$pageCount 2>/dev/null; chmod 666 $remote",
                timeoutMs = 30_000,
            )

            val local = File.createTempFile("tuber_c", ".bin")
            if (!pullFile(remote, local) || local.length() < 4) {
                local.delete()
                continue
            }

            val data = local.readBytes()
            val bias = (reg.start - aligned).toInt().coerceAtLeast(0)
            var hits = 0
            val mutable = data.copyOf()
            for ((oldB, newB) in patterns) {
                var from = bias
                val end = minOf(mutable.size, bias + size.toInt())
                while (from <= end - oldB.size && total + hits < maxHits) {
                    val at = indexOf(mutable, oldB, from, end)
                    if (at < 0) break
                    System.arraycopy(newB, 0, mutable, at, newB.size)
                    hits++
                    from = at + newB.size
                }
            }
            if (hits == 0) {
                local.delete()
                continue
            }
            local.writeBytes(mutable)
            pushFile(local, remote)
            local.delete()

            GameSaveAccess.runSu(
                "dd if=$remote of=/proc/$pid/mem bs=4096 seek=$skipPages conv=notrunc 2>/dev/null",
                timeoutMs = 30_000,
            )
            total += hits
        }

        GameSaveAccess.runSu("rm -rf ${work.absolutePath}", 3_000)
        return Result(total > 0, if (total > 0) "ok" else "none", total)
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
            if (path.endsWith(".so") || path.startsWith("/system") || path.startsWith("/vendor")) continue
            if (path.contains("font") || path.contains("apk")) continue
            val segs = cols[0].split('-')
            if (segs.size != 2) continue
            val start = segs[0].toLongOrNull(16) ?: continue
            val end = segs[1].toLongOrNull(16) ?: continue
            if (end > start) out += Region(start, end)
        }
        return out.sortedBy { it.end - it.start }.take(60)
    }

    private fun patternsFor(oldV: Long, newV: Long): List<Pair<ByteArray, ByteArray>> {
        val list = ArrayList<Pair<ByteArray, ByteArray>>(3)
        if (oldV in Int.MIN_VALUE.toLong()..Int.MAX_VALUE.toLong() &&
            newV in Int.MIN_VALUE.toLong()..Int.MAX_VALUE.toLong()
        ) {
            list += le32(oldV.toInt()) to le32(newV.toInt())
        }
        list += le64(oldV) to le64(newV)
        if (oldV in -100_000_000..100_000_000 && newV in -100_000_000..100_000_000) {
            list += leF(oldV.toFloat()) to leF(newV.toFloat())
        }
        return list
    }

    private fun le32(v: Int) = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(v).array()
    private fun le64(v: Long) = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putLong(v).array()
    private fun leF(v: Float) = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putFloat(v).array()

    private fun indexOf(data: ByteArray, pat: ByteArray, from: Int, endExclusive: Int): Int {
        val last = endExclusive - pat.size
        outer@ for (i in from..last) {
            for (j in pat.indices) if (data[i + j] != pat[j]) continue@outer
            return i
        }
        return -1
    }

    private fun pullFile(remote: String, local: File): Boolean = try {
        val p = ProcessBuilder("su", "-c", "cat \"$remote\"").redirectErrorStream(true).start()
        local.outputStream().use { p.inputStream.copyTo(it) }
        p.waitFor() == 0 && local.length() > 0
    } catch (_: Exception) {
        false
    }

    private fun pushFile(local: File, remote: String): Boolean = try {
        val p = ProcessBuilder("su", "-c", "cat > \"$remote\"").redirectErrorStream(true).start()
        local.inputStream().use { inp -> p.outputStream.use { out -> inp.copyTo(out) } }
        p.outputStream.close()
        p.waitFor() == 0
    } catch (_: Exception) {
        false
    }
}
