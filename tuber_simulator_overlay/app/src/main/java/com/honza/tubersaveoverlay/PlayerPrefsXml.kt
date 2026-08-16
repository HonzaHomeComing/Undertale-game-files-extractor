package com.honza.tubersaveoverlay

/**
 * Unity Android PlayerPrefs XML (SharedPreferences) helper.
 *
 * Typical path:
 * /data/data/com.outerminds.tubular/shared_prefs/com.outerminds.tubular.v2.playerprefs.xml
 */
data class PrefEntry(
    val name: String,
    var type: PrefType,
    var value: String,
)

enum class PrefType { INT, FLOAT, LONG, BOOLEAN, STRING }

object PlayerPrefsXml {
    private val entryRegex = Regex(
        """<(int|float|long|boolean|string)\s+name="([^"]+)"(?:\s+value="([^"]*)")?\s*(?:/>|>(.*?)</string>)""",
        setOf(RegexOption.DOT_MATCHES_ALL, RegexOption.IGNORE_CASE),
    )

    fun parse(xml: String): MutableList<PrefEntry> {
        val out = mutableListOf<PrefEntry>()
        for (m in entryRegex.findAll(xml)) {
            val typeRaw = m.groupValues[1].lowercase()
            val name = m.groupValues[2]
            val attrValue = m.groupValues[3]
            val stringBody = m.groupValues[4]
            val type = when (typeRaw) {
                "int" -> PrefType.INT
                "float" -> PrefType.FLOAT
                "long" -> PrefType.LONG
                "boolean" -> PrefType.BOOLEAN
                else -> PrefType.STRING
            }
            val value = if (type == PrefType.STRING) stringBody else attrValue
            out += PrefEntry(name, type, value)
        }
        return out
    }

    fun toXml(entries: List<PrefEntry>): String {
        val body = buildString {
            for (e in entries) {
                val safeName = e.name
                    .replace("&", "&amp;")
                    .replace("\"", "&quot;")
                when (e.type) {
                    PrefType.STRING -> {
                        val safe = e.value
                            .replace("&", "&amp;")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;")
                        append("    <string name=\"").append(safeName).append("\">")
                            .append(safe).append("</string>\n")
                    }
                    PrefType.BOOLEAN -> {
                        val v = when (e.value.lowercase()) {
                            "1", "true", "yes" -> "true"
                            else -> "false"
                        }
                        append("    <boolean name=\"").append(safeName)
                            .append("\" value=\"").append(v).append("\" />\n")
                    }
                    PrefType.FLOAT -> {
                        append("    <float name=\"").append(safeName)
                            .append("\" value=\"").append(e.value).append("\" />\n")
                    }
                    PrefType.LONG -> {
                        append("    <long name=\"").append(safeName)
                            .append("\" value=\"").append(e.value).append("\" />\n")
                    }
                    PrefType.INT -> {
                        append("    <int name=\"").append(safeName)
                            .append("\" value=\"").append(e.value).append("\" />\n")
                    }
                }
            }
        }
        return "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n" +
            "<map>\n" + body + "</map>\n"
    }

    fun findByNeedles(entries: List<PrefEntry>, vararg needles: String): PrefEntry? {
        val lower = needles.map { it.lowercase() }
        return entries.firstOrNull { e ->
            val n = e.name.lowercase()
            lower.any { n.contains(it) }
        }
    }

    fun applyQuick(
        entries: MutableList<PrefEntry>,
        bux: String?,
        knowledge: String?,
        subs: String?,
        views: String?,
    ): Int {
        var changed = 0
        fun set(needles: Array<String>, raw: String?) {
            if (raw.isNullOrBlank()) return
            val hit = findByNeedles(entries, *needles) ?: return
            val trimmed = raw.trim()
            when (hit.type) {
                PrefType.FLOAT -> if (trimmed.toFloatOrNull() == null) return
                PrefType.STRING -> Unit
                else -> if (trimmed.toLongOrNull() == null) return
            }
            hit.value = trimmed
            changed++
        }
        set(arrayOf("bux", "money", "cash", "coin", "softcurrency"), bux)
        set(arrayOf("knowledge", "iq", "brain"), knowledge)
        set(arrayOf("subscriber", "subs", "followers"), subs)
        set(arrayOf("view", "views", "totalview"), views)
        return changed
    }
}
