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
import android.os.IBinder
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
import kotlin.math.abs

class OverlayService : Service() {
    companion object {
        const val ACTION_START = "com.honza.tubersaveoverlay.START"
        const val ACTION_STOP = "com.honza.tubersaveoverlay.STOP"
        const val ACTION_FILE_LOADED = "com.honza.tubersaveoverlay.FILE_LOADED"
        const val EXTRA_URI = "uri"
        const val EXTRA_MODE = "mode" // load | save
        @Volatile
        var isRunning: Boolean = false
            private set
    }

    private lateinit var windowManager: WindowManager
    private var bubbleView: View? = null
    private var panelView: View? = null
    private var bubbleBinding: OverlayBubbleBinding? = null
    private var panelBinding: OverlayPanelBinding? = null
    private var bubbleParams: WindowManager.LayoutParams? = null
    private var panelParams: WindowManager.LayoutParams? = null

    private var entries: MutableList<PrefEntry> = mutableListOf()
    private var loadedName: String = "No file loaded"

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                tearDown()
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_FILE_LOADED -> {
                val uri = intent.getParcelableExtra<Uri>(EXTRA_URI) ?: return START_STICKY
                val mode = intent.getStringExtra(EXTRA_MODE) ?: "load"
                if (mode == "save") {
                    writeToUri(uri)
                } else {
                    readFromUri(uri)
                }
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

    private fun startAsForeground() {
        val channelId = "tuber_overlay"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(
                NotificationChannel(
                    channelId,
                    getString(R.string.overlay_channel),
                    NotificationManager.IMPORTANCE_LOW,
                ),
            )
        }
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification: Notification = NotificationCompat.Builder(this, channelId)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.overlay_running))
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
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 40
            y = 200
        }
        bubbleParams = params
        attachDrag(bubbleView!!, params) { togglePanel() }
        windowManager.addView(bubbleView, params)
    }

    private fun togglePanel() {
        if (panelView != null) {
            hidePanel()
        } else {
            showPanel()
        }
    }

    private fun showPanel() {
        if (panelView != null) return
        panelBinding = OverlayPanelBinding.inflate(LayoutInflater.from(this))
        panelView = panelBinding!!.root
        val params = WindowManager.LayoutParams(
            dp(320),
            dp(420),
            overlayType(),
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 48
            y = 280
            softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE
        }
        panelParams = params
        val b = panelBinding!!
        b.fileLabel.text = loadedName
        b.btnClosePanel.setOnClickListener { hidePanel() }
        b.btnLoad.setOnClickListener { requestFile(load = true) }
        b.btnSave.setOnClickListener { requestFile(load = false) }
        b.btnApplyQuick.setOnClickListener { applyQuickFields() }
        refreshQuickFields()
        rebuildKeyEditors()
        windowManager.addView(panelView, params)
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
        panelParams = null
    }

    private fun requestFile(load: Boolean) {
        // Overlay can't host SAF pickers reliably — bounce through MainActivity helper.
        val intent = Intent(this, FilePickActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            putExtra(EXTRA_MODE, if (load) "load" else "save")
        }
        startActivity(intent)
        Toast.makeText(
            this,
            if (load) "Pick your playerprefs XML" else "Choose where to save XML",
            Toast.LENGTH_SHORT,
        ).show()
    }

    private fun readFromUri(uri: Uri) {
        try {
            contentResolver.openInputStream(uri)?.use { input ->
                val text = input.bufferedReader().readText()
                entries = PlayerPrefsXml.parse(text)
                loadedName = queryName(uri) ?: uri.lastPathSegment ?: "loaded.xml"
                panelBinding?.fileLabel?.text = loadedName
                refreshQuickFields()
                rebuildKeyEditors()
                Toast.makeText(this, "Loaded ${entries.size} keys", Toast.LENGTH_SHORT).show()
            }
        } catch (e: Exception) {
            Toast.makeText(this, "Load failed: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun writeToUri(uri: Uri) {
        try {
            syncKeyEditorsFromUi()
            val xml = PlayerPrefsXml.toXml(entries)
            contentResolver.openOutputStream(uri, "wt")?.use { out ->
                out.write(xml.toByteArray(Charsets.UTF_8))
            }
            Toast.makeText(this, "Saved XML", Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            Toast.makeText(this, "Save failed: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun queryName(uri: Uri): String? {
        contentResolver.query(uri, null, null, null, null)?.use { c ->
            val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (idx >= 0 && c.moveToFirst()) return c.getString(idx)
        }
        return null
    }

    private fun refreshQuickFields() {
        val b = panelBinding ?: return
        b.fieldBux.setText(PlayerPrefsXml.findByNeedles(entries, "bux", "money", "cash")?.value)
        b.fieldKnowledge.setText(PlayerPrefsXml.findByNeedles(entries, "knowledge", "iq", "brain")?.value)
        b.fieldSubs.setText(PlayerPrefsXml.findByNeedles(entries, "subscriber", "subs")?.value)
        b.fieldViews.setText(PlayerPrefsXml.findByNeedles(entries, "view", "views")?.value)
    }

    private fun applyQuickFields() {
        val b = panelBinding ?: return
        syncKeyEditorsFromUi()
        val n = PlayerPrefsXml.applyQuick(
            entries,
            b.fieldBux.text?.toString(),
            b.fieldKnowledge.text?.toString(),
            b.fieldSubs.text?.toString(),
            b.fieldViews.text?.toString(),
        )
        rebuildKeyEditors()
        Toast.makeText(this, "Updated $n matching key(s)", Toast.LENGTH_SHORT).show()
    }

    private val keyEditors = mutableMapOf<String, EditText>()

    private fun rebuildKeyEditors() {
        val container = panelBinding?.keysContainer ?: return
        container.removeAllViews()
        keyEditors.clear()
        if (entries.isEmpty()) {
            val tv = TextView(this).apply {
                text = "Load an XML first."
                setTextColor(getColor(R.color.muted))
                textSize = 12f
            }
            container.addView(tv)
            return
        }
        for (e in entries.take(80)) {
            val label = TextView(this).apply {
                text = "${e.name} (${e.type.name.lowercase()})"
                setTextColor(getColor(R.color.muted))
                textSize = 10f
            }
            val edit = EditText(this).apply {
                setText(e.value)
                setTextColor(getColor(R.color.ink))
                textSize = 13f
                setSingleLine()
            }
            keyEditors[e.name] = edit
            container.addView(label)
            container.addView(edit)
        }
        if (entries.size > 80) {
            container.addView(
                TextView(this).apply {
                    text = "…and ${entries.size - 80} more keys (still saved)."
                    setTextColor(getColor(R.color.muted))
                    textSize = 11f
                },
            )
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
                    if (abs(dx) > 6 || abs(dy) > 6) moved = true
                    params.x = startX + dx
                    params.y = startY + dy
                    windowManager.updateViewLayout(v, params)
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
