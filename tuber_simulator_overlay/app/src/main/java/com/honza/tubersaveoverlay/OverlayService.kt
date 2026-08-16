package com.honza.tubersaveoverlay

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.PixelFormat
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.provider.OpenableColumns
import android.view.ContextThemeWrapper
import android.view.Gravity
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationCompat
import com.honza.tubersaveoverlay.databinding.OverlayBubbleBinding
import com.honza.tubersaveoverlay.databinding.OverlayPanelBinding
import java.io.File
import kotlin.math.abs
import kotlin.random.Random

class OverlayService : Service() {
    companion object {
        const val ACTION_START = "com.honza.tubersaveoverlay.START"
        const val ACTION_STOP = "com.honza.tubersaveoverlay.STOP"
        const val ACTION_FILE_LOADED = "com.honza.tubersaveoverlay.FILE_LOADED"
        const val EXTRA_URI = "uri"
        const val EXTRA_MODE = "mode"
        @Volatile
        var isRunning: Boolean = false
            private set
    }

    private lateinit var windowManager: WindowManager
    private val mainHandler = Handler(Looper.getMainLooper())
    private var bubbleView: View? = null
    private var panelView: View? = null
    private var bubbleBinding: OverlayBubbleBinding? = null
    private var panelBinding: OverlayPanelBinding? = null
    private var bubbleParams: WindowManager.LayoutParams? = null

    private var entries: MutableList<PrefEntry> = mutableListOf()
    private var loadedName: String = "Working save: empty — Pull (root) or Load XML"
    private var ignoreBubbleTapUntil = 0L
    private val keyEditors = mutableMapOf<String, EditText>()
    /** In-game numbers at snapshot time — used as RAM search targets for LIVE APPLY. */
    private var ramBaseline: Map<String, Long> = emptyMap()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        // Start with default cheat keys so the menu is usable immediately.
        entries = GameSaveAccess.DEFAULT_KEYS.map { it.copy() }.toMutableList()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                tearDown()
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_FILE_LOADED -> {
                val uri = intent.parcelableUri(EXTRA_URI) ?: return START_STICKY
                val mode = intent.getStringExtra(EXTRA_MODE) ?: "load"
                if (mode == "save") writeToUri(uri) else readFromUri(uri)
                // Re-show panel after file picker (picker used to eat the menu).
                mainHandler.postDelayed({
                    if (panelView == null) showPanel()
                    ignoreBubbleTapUntil = System.currentTimeMillis() + 600
                }, 250)
                return START_STICKY
            }
            else -> {
                if (!isRunning) {
                    try {
                        startAsForeground()
                        showBubble()
                        isRunning = true
                    } catch (e: Exception) {
                        toast("Overlay failed: ${e.message}")
                        stopSelf()
                        return START_NOT_STICKY
                    }
                }
            }
        }
        return START_STICKY
    }

    private fun Intent.parcelableUri(key: String): Uri? =
        if (Build.VERSION.SDK_INT >= 33) {
            getParcelableExtra(key, Uri::class.java)
        } else {
            @Suppress("DEPRECATION")
            getParcelableExtra(key)
        }

    private fun startAsForeground() {
        val channelId = "tuber_overlay"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel(channelId, getString(R.string.overlay_channel), NotificationManager.IMPORTANCE_LOW),
            )
        }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification: Notification = NotificationCompat.Builder(this, channelId)
            .setContentTitle(getString(R.string.app_name))
            .setContentText("Tap the red bubble for the cheat menu")
            .setSmallIcon(R.drawable.ic_bubble)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(
                42,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
            )
        } else {
            startForeground(42, notification)
        }
    }

    private fun overlayType(): Int =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }

    /** Service context has no theme — Material/?attr layouts crash without this. */
    private fun overlayInflater(): LayoutInflater {
        val themed = ContextThemeWrapper(this, R.style.Theme_TuberSaveOverlay)
        return LayoutInflater.from(themed)
    }

    private fun showBubble() {
        if (bubbleView != null) return
        try {
            bubbleBinding = OverlayBubbleBinding.inflate(overlayInflater())
            bubbleView = bubbleBinding!!.root
            val params = WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                overlayType(),
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                    WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
                PixelFormat.TRANSLUCENT,
            ).apply {
                gravity = Gravity.TOP or Gravity.START
                x = 36
                y = 220
            }
            bubbleParams = params
            attachDrag(bubbleView!!, params) {
                if (System.currentTimeMillis() < ignoreBubbleTapUntil) return@attachDrag
                if (panelView == null) {
                    try {
                        showPanel()
                    } catch (e: Exception) {
                        toast("Menu failed: ${e.message}")
                    }
                }
            }
            windowManager.addView(bubbleView, params)
            toast("Red bubble ready — tap it")
        } catch (e: Exception) {
            bubbleView = null
            bubbleBinding = null
            throw e
        }
    }

    private fun showPanel() {
        if (panelView != null) return
        panelBinding = OverlayPanelBinding.inflate(overlayInflater())
        panelView = panelBinding!!.root

        val params = WindowManager.LayoutParams(
            dp(340),
            dp(520),
            overlayType(),
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 28
            y = 100
            softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_PAN
        }

        val b = panelBinding!!
        b.fileLabel.text = loadedName
        b.statusLine.text = "Checking BlueStacks root…"
        b.btnClosePanel.setOnClickListener {
            hidePanel()
            ignoreBubbleTapUntil = System.currentTimeMillis() + 400
        }
        b.btnLoad.setOnClickListener { requestFile(load = true) }
        b.btnSave.setOnClickListener { requestFile(load = false) }
        b.btnApplyRestart.setOnClickListener { applyLiveRam() }
        b.btnApplyFields.setOnClickListener { applyDiskAndRestart() }
        b.btnPullRoot.setOnClickListener { snapshotOldValues() }
        b.btnPushRoot.setOnClickListener { pushRoot(relaunch = false) }
        b.btnUnstick.setOnClickListener { freshStartGame() }
        b.btnGlitchMax.setOnClickListener { glitchPreset("max", restart = false) }
        b.btnGlitchOverflow.setOnClickListener { glitchPreset("overflow", restart = false) }
        b.btnGlitchNeg.setOnClickListener { glitchPreset("neg", restart = false) }
        b.btnGlitchChaos.setOnClickListener { glitchPreset("chaos", restart = false) }

        // Show build so user can confirm they installed the new APK
        b.statusLine.text = "v${BuildConfig.VERSION_NAME} — get IN-GAME, Snapshot what you SEE, change, LIVE APPLY."

        refreshQuickFields()
        rebuildKeyEditors()
        windowManager.addView(panelView, params)
        toast("Cheat menu open — use X to close")

        // Rooted BlueStacks: auto-pull live save snapshot (for RAM search baselines).
        Thread {
            GameSaveAccess.invalidateRootCache()
            val rooted = GameSaveAccess.hasRoot()
            if (!rooted) {
                mainHandler.post {
                    if (panelBinding !== b) return@post
                    b.statusLine.text = "No root — BlueStacks Settings → Advanced → Root ON, restart BS."
                }
                return@Thread
            }
            mainHandler.post {
                if (panelBinding !== b) return@post
                b.statusLine.text = "ROOT OK — snapshotting save for LIVE RAM…"
            }
            val (ok, payload) = GameSaveAccess.pullPrefsXml()
            mainHandler.post {
                if (panelBinding !== b) return@post
                if (ok) {
                    entries = PlayerPrefsXml.parse(payload)
                    GameSaveAccess.ensureDefaults(entries)
                    workingFile().writeText(payload)
                    loadedName = "Snapshot (${entries.size} keys)"
                    b.fileLabel.text = loadedName
                    refreshQuickFields()
                    rebuildKeyEditors()
                    // Do NOT set ramBaseline from disk — disk ≠ on-screen RAM after security/fresh start.
                    b.statusLine.text =
                        "v${BuildConfig.VERSION_NAME}: type numbers you SEE → Snapshot → change → LIVE APPLY. Do not restart."
                    toast("v${BuildConfig.VERSION_NAME} — Snapshot on-screen values first")
                } else {
                    b.statusLine.text =
                        "v${BuildConfig.VERSION_NAME}: open game in-world, type what you SEE, Snapshot, LIVE APPLY."
                }
            }
        }.start()
    }

    private fun hidePanel() {
        panelView?.let {
            try {
                windowManager.removeView(it)
            } catch (_: Exception) {
            }
        }
        panelView = null
        panelBinding = null
        keyEditors.clear()
    }

    private fun setStatus(msg: String) {
        panelBinding?.statusLine?.text = msg
    }

    private fun toast(msg: String) {
        mainHandler.post {
            Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
        }
    }

    private fun toastLong(msg: String) {
        mainHandler.post {
            // Double toast so the reason is hard to miss on BlueStacks
            Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
            mainHandler.postDelayed({
                Toast.makeText(this, msg.take(60), Toast.LENGTH_LONG).show()
            }, 2500)
        }
    }

    private fun requestFile(load: Boolean) {
        // Keep menu; picker activity used to make it look like the menu "vanished".
        ignoreBubbleTapUntil = System.currentTimeMillis() + 1500
        val intent = Intent(this, FilePickActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            putExtra(EXTRA_MODE, if (load) "load" else "save")
        }
        startActivity(intent)
    }

    private fun readFromUri(uri: Uri) {
        try {
            contentResolver.openInputStream(uri)?.use { input ->
                val text = input.bufferedReader().readText()
                entries = PlayerPrefsXml.parse(text)
                GameSaveAccess.ensureDefaults(entries)
                loadedName = "Loaded: " + (queryName(uri) ?: "file")
                panelBinding?.fileLabel?.text = loadedName
                refreshQuickFields()
                rebuildKeyEditors()
                setStatus("Loaded ${entries.size} keys")
                toast("Loaded ${entries.size} keys")
            }
        } catch (e: Exception) {
            toast("Load failed: ${e.message}")
        }
    }

    private fun writeToUri(uri: Uri) {
        try {
            syncKeyEditorsFromUi()
            applyFieldValuesIntoEntries()
            val xml = PlayerPrefsXml.toXml(entries)
            contentResolver.openOutputStream(uri, "wt")?.use { it.write(xml.toByteArray(Charsets.UTF_8)) }
            // Also keep a local working copy
            workingFile().writeText(xml)
            setStatus("Exported XML")
            toast("Exported XML")
        } catch (e: Exception) {
            toast("Save failed: ${e.message}")
        }
    }

    private fun workingFile(): File = File(filesDir, "working_playerprefs.xml")

    private fun queryName(uri: Uri): String? {
        contentResolver.query(uri, null, null, null, null)?.use { c ->
            val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (idx >= 0 && c.moveToFirst()) return c.getString(idx)
        }
        return null
    }

    private fun refreshQuickFields() {
        val b = panelBinding ?: return
        fun needle(vararg n: String) = PlayerPrefsXml.findByNeedles(entries, *n)?.value.orEmpty()
        b.fieldBux.setText(needle("bux", "money", "cash", "soft"))
        b.fieldGems.setText(needle("gem", "premium", "hardcurrency"))
        b.fieldKnowledge.setText(needle("knowledge", "brain", "iq"))
        b.fieldSubs.setText(needle("subscriber", "subs", "followers"))
        b.fieldViews.setText(needle("view"))
        b.fieldLevel.setText(needle("level", "rank"))
        b.fieldItems.setText(needle("item", "inventory"))
        b.fieldFurniture.setText(needle("furniture", "prop", "unlock"))
    }

    private fun applyFieldValuesIntoEntries() {
        val b = panelBinding ?: return
        fun set(needles: Array<String>, raw: String?) {
            if (raw.isNullOrBlank()) return
            var hit = PlayerPrefsXml.findByNeedles(entries, *needles)
            if (hit == null) {
                hit = PrefEntry(needles.first().replaceFirstChar { it.uppercase() }, PrefType.INT, raw.trim())
                entries += hit
            } else {
                hit.value = raw.trim()
            }
        }
        set(arrayOf("bux", "money", "cash", "soft"), b.fieldBux.text?.toString())
        set(arrayOf("gem", "premium", "hardcurrency"), b.fieldGems.text?.toString())
        set(arrayOf("knowledge", "brain", "iq"), b.fieldKnowledge.text?.toString())
        set(arrayOf("subscriber", "subs", "followers"), b.fieldSubs.text?.toString())
        set(arrayOf("view"), b.fieldViews.text?.toString())
        set(arrayOf("level", "rank"), b.fieldLevel.text?.toString())
        set(arrayOf("item", "inventory"), b.fieldItems.text?.toString())
        set(arrayOf("furniture", "prop", "unlock"), b.fieldFurniture.text?.toString())
    }

    private fun applyAllFields() {
        syncKeyEditorsFromUi()
        applyFieldValuesIntoEntries()
        // Stamp every default-ish numeric key that matches
        for (e in entries) {
            val n = e.name.lowercase()
            when {
                listOf("bux", "money", "cash", "soft").any { n.contains(it) } ->
                    panelBinding?.fieldBux?.text?.toString()?.takeIf { it.isNotBlank() }?.let { e.value = it }
                listOf("gem", "premium", "hard").any { n.contains(it) } ->
                    panelBinding?.fieldGems?.text?.toString()?.takeIf { it.isNotBlank() }?.let { e.value = it }
                listOf("knowledge", "brain").any { n.contains(it) } ->
                    panelBinding?.fieldKnowledge?.text?.toString()?.takeIf { it.isNotBlank() }?.let { e.value = it }
                listOf("sub", "follower").any { n.contains(it) } ->
                    panelBinding?.fieldSubs?.text?.toString()?.takeIf { it.isNotBlank() }?.let { e.value = it }
                n.contains("view") ->
                    panelBinding?.fieldViews?.text?.toString()?.takeIf { it.isNotBlank() }?.let { e.value = it }
            }
        }
        workingFile().writeText(PlayerPrefsXml.toXml(entries))
        rebuildKeyEditors()
        refreshQuickFields()
        setStatus("Applied into ${entries.size} keys (memory only)")
        toast("Keys updated — tap APPLY & RESTART GAME")
    }

    private fun collectValuesMap(): Map<String, String> {
        val b = panelBinding
        val map = linkedMapOf<String, String>()
        fun put(key: String, raw: String?) {
            val v = raw?.trim().orEmpty()
            if (v.isNotEmpty()) map[key] = v
        }
        if (b != null) {
            put("Bux", b.fieldBux.text?.toString())
            put("SoftBux", b.fieldBux.text?.toString())
            put("Money", b.fieldBux.text?.toString())
            put("Cash", b.fieldBux.text?.toString())
            put("Gems", b.fieldGems.text?.toString())
            put("HardCurrency", b.fieldGems.text?.toString())
            put("PremiumCurrency", b.fieldGems.text?.toString())
            put("Knowledge", b.fieldKnowledge.text?.toString())
            put("BrainPoints", b.fieldKnowledge.text?.toString())
            put("Subscribers", b.fieldSubs.text?.toString())
            put("TotalSubscribers", b.fieldSubs.text?.toString())
            put("Views", b.fieldViews.text?.toString())
            put("TotalViews", b.fieldViews.text?.toString())
            put("ChannelLevel", b.fieldLevel.text?.toString())
            put("PlayerLevel", b.fieldLevel.text?.toString())
            put("ItemCount", b.fieldItems.text?.toString())
            put("InventoryCount", b.fieldItems.text?.toString())
            put("FurnitureUnlocked", b.fieldFurniture.text?.toString())
            put("bux", b.fieldBux.text?.toString())
            put("gem", b.fieldGems.text?.toString())
            put("knowledge", b.fieldKnowledge.text?.toString())
            put("subscriber", b.fieldSubs.text?.toString())
            put("view", b.fieldViews.text?.toString())
        }
        for (e in entries) {
            if (e.value.isNotBlank()) map.putIfAbsent(e.name, e.value)
        }
        return map
    }

    private fun captureRamBaseline() {
        val b = panelBinding
        val map = linkedMapOf<String, Long>()
        fun put(key: String, raw: String?) {
            val v = raw?.trim()?.toLongOrNull() ?: return
            map[key] = v
        }
        if (b != null) {
            put("bux", b.fieldBux.text?.toString())
            put("gems", b.fieldGems.text?.toString())
            put("knowledge", b.fieldKnowledge.text?.toString())
            put("subs", b.fieldSubs.text?.toString())
            put("views", b.fieldViews.text?.toString())
            put("level", b.fieldLevel.text?.toString())
            put("items", b.fieldItems.text?.toString())
            put("furniture", b.fieldFurniture.text?.toString())
        }
        ramBaseline = map
    }

    /** Patch running game memory — no restart (avoids splash security). */
    private fun applyLiveRam() {
        setStatus("LIVE RAM — diagnosing + scanning (keep game open)…")
        toast("Live RAM… keep game on screen")
        Thread {
            if (!GameSaveAccess.hasRoot()) {
                mainHandler.post {
                    setStatus("Need BlueStacks Root ON")
                    toast("Root required")
                }
                return@Thread
            }
            val diag = LiveMemoryEditor.diagnose(this)
            mainHandler.post { setStatus("Scan: $diag") }

            var news = mapOf<String, Long>()
            syncOnMain {
                syncKeyEditorsFromUi()
                applyFieldValuesIntoEntries()
                val b = panelBinding
                val m = linkedMapOf<String, Long>()
                fun put(key: String, raw: String?) {
                    val v = raw?.trim()?.toLongOrNull() ?: return
                    m[key] = v
                }
                if (b != null) {
                    put("bux", b.fieldBux.text?.toString())
                    put("gems", b.fieldGems.text?.toString())
                    put("knowledge", b.fieldKnowledge.text?.toString())
                    put("subs", b.fieldSubs.text?.toString())
                    put("views", b.fieldViews.text?.toString())
                    put("level", b.fieldLevel.text?.toString())
                    put("items", b.fieldItems.text?.toString())
                    put("furniture", b.fieldFurniture.text?.toString())
                }
                news = m
            }
            if (ramBaseline.isEmpty()) {
                mainHandler.post {
                    setStatus(
                        "No OLD snapshot. Type the numbers you SEE in-game → Snapshot → change → LIVE APPLY. ($diag)",
                    )
                    toast("Snapshot OLD values first")
                }
                return@Thread
            }
            val changes = mutableListOf<Pair<Long, Long>>()
            for ((k, newV) in news) {
                val oldV = ramBaseline[k] ?: continue
                if (oldV != newV) changes += oldV to newV
            }
            if (changes.isEmpty()) {
                mainHandler.post {
                    setStatus("Change a field away from snapshot first. Snapshot=${ramBaseline}. $diag")
                    toast("Change a value first")
                }
                return@Thread
            }
            mainHandler.post {
                setStatus("Patching RAM ${changes.joinToString { "${it.first}→${it.second}" }} …")
            }
            val result = LiveMemoryEditor.replaceMany(this, changes)
            mainHandler.post {
                val msg = buildString {
                    append("v${BuildConfig.VERSION_NAME}: ")
                    append(result.message)
                    if (result.detail.isNotBlank()) append(" | ").append(result.detail)
                }
                setStatus(msg)
                toastLong(if (result.ok) "SUCCESS: ${result.message}" else "FAIL: ${result.message}")
                if (result.ok) ramBaseline = news
            }
        }.start()
    }

    /** Disk write + restart — often trips Outerminds splash security. */
    private fun applyDiskAndRestart() {
        setStatus("Disk write + restart (may trip security)…")
        toast("Disk apply…")
        Thread {
            var values: Map<String, String> = emptyMap()
            var snapshot: List<PrefEntry> = emptyList()
            syncOnMain {
                syncKeyEditorsFromUi()
                applyFieldValuesIntoEntries()
                values = collectValuesMap()
                snapshot = entries.map { it.copy() }
            }
            if (!GameSaveAccess.hasRoot()) {
                mainHandler.post {
                    setStatus("Need root")
                    toast("Root required")
                }
                return@Thread
            }
            val (ok, msg) = GameSaveAccess.applyLiveEdits(values, snapshot, workingFile())
            mainHandler.post {
                if (!ok) {
                    setStatus(msg)
                    toast(msg)
                    return@post
                }
                relaunchGame()
                setStatus("Disk written — $msg (if splash hangs, use FRESH START then LIVE APPLY)")
                toast("Restarted — if stuck, Fresh Start")
            }
        }.start()
    }

    private fun freshStartGame() {
        setStatus("FRESH START — clearing game data to bypass splash lock…")
        toast("Clearing game data…")
        Thread {
            val (ok, msg) = GameSaveAccess.freshStartClearData()
            mainHandler.post {
                setStatus(msg)
                toast(if (ok) "Cleared — open game" else msg)
            }
            if (ok) {
                Thread.sleep(500)
                mainHandler.post { relaunchGame() }
            }
        }.start()
    }

    /** @deprecated name kept for any leftover refs */
    private fun applyAndRestartGame() = applyLiveRam()

    private fun unstickGame() = freshStartGame()

    private fun glitchPreset(kind: String, restart: Boolean = false) {
        val b = panelBinding ?: return
        // Only fill fields — user must Snapshot on-screen values BEFORE this, then LIVE APPLY.
        when (kind) {
            "max" -> {
                b.fieldBux.setText("9999999")
                b.fieldGems.setText("999999")
                b.fieldKnowledge.setText("999999")
                b.fieldSubs.setText("1000000")
                b.fieldViews.setText("10000000")
                b.fieldLevel.setText("25")
                b.fieldItems.setText("500")
                b.fieldFurniture.setText("200")
            }
            "overflow" -> {
                b.fieldBux.setText("99999999")
                b.fieldGems.setText("9999999")
                b.fieldKnowledge.setText("9999999")
                b.fieldSubs.setText("50000000")
                b.fieldViews.setText("50000000")
                b.fieldLevel.setText("30")
                b.fieldItems.setText("1000")
                b.fieldFurniture.setText("500")
            }
            "neg" -> {
                b.fieldBux.setText("0")
                b.fieldGems.setText("0")
                b.fieldKnowledge.setText("0")
                b.fieldSubs.setText("0")
                b.fieldViews.setText("0")
                b.fieldLevel.setText("1")
                b.fieldItems.setText("0")
                b.fieldFurniture.setText("0")
            }
            "chaos" -> {
                fun r(max: Int) = Random.nextInt(0, max).toString()
                b.fieldBux.setText(r(5_000_000))
                b.fieldGems.setText(r(500_000))
                b.fieldKnowledge.setText(r(500_000))
                b.fieldSubs.setText(r(2_000_000))
                b.fieldViews.setText(r(10_000_000))
                b.fieldLevel.setText(Random.nextInt(1, 35).toString())
                b.fieldItems.setText(r(500))
                b.fieldFurniture.setText(r(200))
            }
        }
        syncKeyEditorsFromUi()
        applyFieldValuesIntoEntries()
        GameSaveAccess.clampEntriesForStability(entries)
        rebuildKeyEditors()
        refreshQuickFields()
        setStatus("SAFE RICH filled — now tap LIVE APPLY (only after Snapshot of on-screen values).")
        toast("Snapshot first if you haven't, then LIVE APPLY")
        // Do not auto-apply — wrong baseline was the main failure cause.
    }

    /** Lock the numbers currently in the fields as the in-game OLD values for RAM search. */
    private fun snapshotOldValues() {
        syncKeyEditorsFromUi()
        applyFieldValuesIntoEntries()
        captureRamBaseline()
        setStatus(
            "Snapshot locked: " +
                ramBaseline.entries.joinToString { "${it.key}=${it.value}" }.ifBlank { "(empty)" } +
                " — now change fields to NEW values and tap LIVE APPLY.",
        )
        toast("OLD values snapshotted")
    }

    private fun pullSaves() {
        snapshotOldValues()
        Thread {
            if (GameSaveAccess.hasRoot()) {
                val (ok, payload) = GameSaveAccess.pullPrefsXml()
                mainHandler.post {
                    if (!ok) {
                        setStatus(payload + " — you can still type on-screen values and Snapshot.")
                        return@post
                    }
                    entries = PlayerPrefsXml.parse(payload)
                    GameSaveAccess.ensureDefaults(entries)
                    workingFile().writeText(payload)
                    loadedName = "Pulled (${entries.size} keys)"
                    panelBinding?.fileLabel?.text = loadedName
                    refreshQuickFields()
                    rebuildKeyEditors()
                    setStatus(
                        "Pulled disk keys (may differ from screen). " +
                            "Type what you SEE → Snapshot → change → LIVE APPLY.",
                    )
                    toast("Type on-screen values, then Snapshot")
                }
            }
        }.start()
    }

    private fun pushRoot(relaunch: Boolean) {
        Thread {
            if (!GameSaveAccess.hasRoot()) {
                mainHandler.post {
                    setStatus("Need BlueStacks Root ON.")
                    toast("Root required")
                }
                return@Thread
            }
            var values: Map<String, String> = emptyMap()
            var snapshot: List<PrefEntry> = emptyList()
            syncOnMain {
                syncKeyEditorsFromUi()
                applyFieldValuesIntoEntries()
                values = collectValuesMap()
                snapshot = entries.map { it.copy() }
            }
            mainHandler.post {
                setStatus(if (relaunch) "Writing + restarting…" else "Writing live save…")
            }
            val (ok, msg) = GameSaveAccess.applyLiveEdits(values, snapshot, workingFile())
            if (!ok) {
                mainHandler.post {
                    setStatus(msg)
                    toast(msg)
                }
                return@Thread
            }
            if (relaunch) {
                Thread.sleep(400)
                mainHandler.post {
                    relaunchGame()
                    setStatus("Saved — game restarted. $msg")
                    toast("Saved — game restarted")
                }
            } else {
                mainHandler.post {
                    setStatus("Saved (game stopped). Open Tuber Simulator. $msg")
                    toast("Saved. Open game manually.")
                }
            }
        }.start()
    }

    private fun relaunchGame() {
        try {
            val launch = packageManager.getLaunchIntentForPackage(GameSaveAccess.PACKAGE)
            if (launch != null) {
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(launch)
                return
            }
        } catch (_: Exception) {
        }
        // Fallback for some BlueStacks builds
        Thread {
            GameSaveAccess.runSu(
                "monkey -p ${GameSaveAccess.PACKAGE} -c android.intent.category.LAUNCHER 1",
            )
        }.start()
    }

    private fun syncOnMain(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            block()
        } else {
            val lock = Object()
            var done = false
            mainHandler.post {
                block()
                synchronized(lock) {
                    done = true
                    lock.notifyAll()
                }
            }
            synchronized(lock) {
                while (!done) lock.wait(2000)
            }
        }
    }

    private fun rebuildKeyEditors() {
        val container = panelBinding?.keysContainer ?: return
        container.removeAllViews()
        keyEditors.clear()
        val interesting = entries.filter { e ->
            val n = e.name.lowercase()
            listOf(
                "bux", "money", "cash", "gem", "knowledge", "brain", "sub", "view",
                "level", "item", "inventory", "furniture", "prop", "unlock", "currency",
            ).any { n.contains(it) }
        }.ifEmpty { entries.take(40) }

        for (e in interesting.take(60)) {
            container.addView(
                TextView(this).apply {
                    text = "${e.name} (${e.type.name.lowercase()})"
                    setTextColor(getColor(R.color.muted))
                    textSize = 10f
                },
            )
            val edit = EditText(this).apply {
                setText(e.value)
                setTextColor(getColor(R.color.ink))
                textSize = 13f
                setSingleLine()
            }
            keyEditors[e.name] = edit
            container.addView(edit)
        }
    }

    private fun syncKeyEditorsFromUi() {
        for (e in entries) {
            val ed = keyEditors[e.name] ?: continue
            e.value = ed.text?.toString() ?: e.value
        }
    }

    private fun attachDrag(
        view: View,
        params: WindowManager.LayoutParams,
        onTap: () -> Unit,
    ) {
        var downX = 0f
        var downY = 0f
        var startX = 0
        var startY = 0
        var moved = false
        view.setOnTouchListener { v, event ->
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX
                    downY = event.rawY
                    startX = params.x
                    startY = params.y
                    moved = false
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = (event.rawX - downX).toInt()
                    val dy = (event.rawY - downY).toInt()
                    if (abs(dx) > 8 || abs(dy) > 8) moved = true
                    params.x = startX + dx
                    params.y = startY + dy
                    try {
                        windowManager.updateViewLayout(v, params)
                    } catch (_: Exception) {
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    if (!moved) onTap()
                    true
                }
                else -> false
            }
        }
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private fun tearDown() {
        hidePanel()
        bubbleView?.let {
            try {
                windowManager.removeView(it)
            } catch (_: Exception) {
            }
        }
        bubbleView = null
        bubbleBinding = null
        bubbleParams = null
        isRunning = false
        stopForeground(STOP_FOREGROUND_REMOVE)
    }

    override fun onDestroy() {
        tearDown()
        super.onDestroy()
    }
}
