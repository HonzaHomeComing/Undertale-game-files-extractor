package com.honza.tubersaveoverlay

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.graphics.PixelFormat
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.provider.OpenableColumns
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
                    startAsForeground()
                    showBubble()
                    isRunning = true
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
        startForeground(42, notification)
    }

    private fun overlayType(): Int =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }

    private fun showBubble() {
        if (bubbleView != null) return
        bubbleBinding = OverlayBubbleBinding.inflate(LayoutInflater.from(this))
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
        // Bubble only OPENS the menu (X closes). Avoids flashy open/close toggles.
        attachDrag(bubbleView!!, params) {
            if (System.currentTimeMillis() < ignoreBubbleTapUntil) return@attachDrag
            if (panelView == null) showPanel()
        }
        windowManager.addView(bubbleView, params)
    }

    private fun showPanel() {
        if (panelView != null) return
        panelBinding = OverlayPanelBinding.inflate(LayoutInflater.from(this))
        panelView = panelBinding!!.root

        // Focusable so EditTexts work over the game; NOT_TOUCH_MODAL so game can still run under it.
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
        b.statusLine.text = if (GameSaveAccess.hasRoot()) {
            "ROOT OK — set values, tap APPLY & RESTART GAME."
        } else {
            "No root — turn ON Root in BlueStacks Settings → Advanced, then restart BS."
        }
        b.btnClosePanel.setOnClickListener {
            hidePanel()
            ignoreBubbleTapUntil = System.currentTimeMillis() + 400
        }
        b.btnLoad.setOnClickListener { requestFile(load = true) }
        b.btnSave.setOnClickListener { requestFile(load = false) }
        b.btnApplyRestart.setOnClickListener { applyAndRestartGame() }
        b.btnApplyFields.setOnClickListener { applyAllFields() }
        b.btnPullRoot.setOnClickListener { pullRoot() }
        b.btnPushRoot.setOnClickListener { pushRoot(relaunch = false) }
        b.btnGlitchMax.setOnClickListener { glitchPreset("max", restart = true) }
        b.btnGlitchOverflow.setOnClickListener { glitchPreset("overflow", restart = true) }
        b.btnGlitchNeg.setOnClickListener { glitchPreset("neg", restart = true) }
        b.btnGlitchChaos.setOnClickListener { glitchPreset("chaos", restart = true) }

        refreshQuickFields()
        rebuildKeyEditors()
        windowManager.addView(panelView, params)
        toast("Cheat menu open — use X to close")
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
        mainHandler.post { Toast.makeText(this, msg, Toast.LENGTH_SHORT).show() }
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

    /** Main action: write values → force-stop → relaunch Tuber Simulator. */
    private fun applyAndRestartGame() {
        if (!GameSaveAccess.hasRoot()) {
            setStatus("Need Root — BlueStacks Settings → Advanced → Root ON, then restart BS.")
            toast("Root required")
            return
        }
        setStatus("Applying + restarting Tuber Simulator…")
        toast("Applying + restarting…")
        Thread {
            syncOnMain {
                syncKeyEditorsFromUi()
                applyFieldValuesIntoEntries()
            }
            val xml = PlayerPrefsXml.toXml(entries)
            val wf = workingFile()
            wf.writeText(xml)
            val (ok, msg) = GameSaveAccess.pushPrefsAndStopGame(xml, wf)
            if (!ok) {
                mainHandler.post {
                    setStatus(msg)
                    toast(msg)
                }
                return@Thread
            }
            Thread.sleep(700)
            mainHandler.post {
                relaunchGame()
                setStatus("Done — game restarted with new values.")
                toast("Game restarted with new values")
            }
        }.start()
    }

    private fun glitchPreset(kind: String, restart: Boolean = true) {
        val b = panelBinding ?: return
        fun fill(v: String) {
            b.fieldBux.setText(v)
            b.fieldGems.setText(v)
            b.fieldKnowledge.setText(v)
            b.fieldSubs.setText(v)
            b.fieldViews.setText(v)
            b.fieldLevel.setText(if (kind == "neg") "-1" else v)
            b.fieldItems.setText(v)
            b.fieldFurniture.setText(v)
        }
        when (kind) {
            "max" -> fill("999999999")
            "overflow" -> fill(Int.MAX_VALUE.toString())
            "neg" -> fill("-999999")
            "chaos" -> {
                fun r() = Random.nextInt(-2_000_000, 2_000_000_000).toString()
                b.fieldBux.setText(r())
                b.fieldGems.setText(r())
                b.fieldKnowledge.setText(r())
                b.fieldSubs.setText(r())
                b.fieldViews.setText(r())
                b.fieldLevel.setText(r())
                b.fieldItems.setText(r())
                b.fieldFurniture.setText(r())
            }
        }
        // Apply into entries without the "memory only" toast spam
        syncKeyEditorsFromUi()
        applyFieldValuesIntoEntries()
        val smash = when (kind) {
            "max" -> "999999999"
            "overflow" -> Int.MAX_VALUE.toString()
            "neg" -> "-999999"
            else -> null
        }
        if (smash != null) {
            for (e in entries) {
                if (e.type != PrefType.STRING && e.type != PrefType.BOOLEAN) e.value = smash
            }
        } else {
            for (e in entries) {
                if (e.type != PrefType.STRING && e.type != PrefType.BOOLEAN) {
                    e.value = Random.nextInt(-5_000_000, Int.MAX_VALUE).toString()
                }
            }
        }
        workingFile().writeText(PlayerPrefsXml.toXml(entries))
        rebuildKeyEditors()
        refreshQuickFields()
        if (restart) {
            setStatus("GLITCH ($kind) → writing + restarting…")
            toast("Glitch + restarting…")
            Thread {
                val xml = PlayerPrefsXml.toXml(entries)
                val wf = workingFile()
                val (ok, msg) = GameSaveAccess.pushPrefsAndStopGame(xml, wf)
                if (!ok) {
                    mainHandler.post {
                        setStatus(msg)
                        toast(msg)
                    }
                    return@Thread
                }
                Thread.sleep(700)
                mainHandler.post {
                    relaunchGame()
                    setStatus("GLITCH ($kind) applied — game restarted.")
                    toast("Glitch applied — game restarted")
                }
            }.start()
        } else {
            setStatus("GLITCH ($kind) loaded — tap APPLY & RESTART")
            toast("Glitch ready")
        }
    }

    private fun pullRoot() {
        Thread {
            val (ok, payload) = GameSaveAccess.pullPrefsXml()
            mainHandler.post {
                if (!ok) {
                    setStatus(payload)
                    toast(payload)
                    return@post
                }
                entries = PlayerPrefsXml.parse(payload)
                GameSaveAccess.ensureDefaults(entries)
                workingFile().writeText(payload)
                loadedName = "Pulled live from game (${entries.size} keys)"
                panelBinding?.fileLabel?.text = loadedName
                refreshQuickFields()
                rebuildKeyEditors()
                setStatus("Pulled — edit values, then APPLY & RESTART GAME")
                toast("Pulled ${entries.size} keys")
            }
        }.start()
    }

    private fun pushRoot(relaunch: Boolean) {
        if (!GameSaveAccess.hasRoot()) {
            setStatus("Need Root — BlueStacks Settings → Advanced → Root ON.")
            toast("Root required")
            return
        }
        setStatus(if (relaunch) "Writing + restarting…" else "Writing prefs (no relaunch)…")
        Thread {
            syncOnMain {
                syncKeyEditorsFromUi()
                applyFieldValuesIntoEntries()
            }
            val xml = PlayerPrefsXml.toXml(entries)
            val wf = workingFile()
            wf.writeText(xml)
            val (ok, msg) = GameSaveAccess.pushPrefsAndStopGame(xml, wf)
            if (!ok) {
                mainHandler.post {
                    setStatus(msg)
                    toast(msg)
                }
                return@Thread
            }
            if (relaunch) {
                Thread.sleep(700)
                mainHandler.post {
                    relaunchGame()
                    setStatus("Saved — game restarted.")
                    toast("Saved — game restarted")
                }
            } else {
                mainHandler.post {
                    setStatus("Saved + stopped. Open Tuber Simulator manually.")
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
