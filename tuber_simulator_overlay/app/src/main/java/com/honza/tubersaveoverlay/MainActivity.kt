package com.honza.tubersaveoverlay

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.honza.tubersaveoverlay.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.btnOverlaySettings.setOnClickListener { openOverlaySettings() }
        binding.btnAllFiles.setOnClickListener { openAllFilesSettings() }
        binding.btnStart.setOnClickListener { startOverlay() }
        binding.btnStop.setOnClickListener { stopOverlay() }
        refreshStatus()
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    private fun canDrawOverlays(): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            Settings.canDrawOverlays(this)
        } else {
            true
        }

    private fun refreshStatus() {
        val overlay = canDrawOverlays()
        val files = NoRootSaveAccess.hasAllFilesAccess()
        binding.status.text = buildString {
            when {
                !overlay -> append(getString(R.string.need_overlay))
                OverlayService.isRunning -> append("Overlay running — look for the red bubble.")
                else -> append("Ready. Grant overlay permission, then Start overlay.")
            }
            append('\n')
            append(if (files) "All-files access: OK" else "All-files access: optional (button below)")
            append('\n')
            append(
                when (GameSaveAccess.hasRootCached()) {
                    true -> "Root: OK"
                    else -> "Root: not required for phone mode"
                },
            )
        }
        // Probe root off the UI thread (su can hang on non-root phones).
        Thread {
            val rooted = GameSaveAccess.hasRoot()
            runOnUiThread {
                if (!::binding.isInitialized) return@runOnUiThread
                val base = binding.status.text?.toString().orEmpty()
                    .lineSequence()
                    .filterNot { it.startsWith("Root:") }
                    .joinToString("\n")
                binding.status.text = base + "\nRoot: " + if (rooted) "OK" else "not required for phone mode"
            }
        }.start()
    }

    private fun openOverlaySettings() {
        try {
            startActivity(
                Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:$packageName"),
                ),
            )
        } catch (e: Exception) {
            Toast.makeText(this, "Open Settings → Apps → this app → Display over other apps", Toast.LENGTH_LONG).show()
        }
    }

    private fun openAllFilesSettings() {
        try {
            startActivity(NoRootSaveAccess.allFilesAccessIntent(this))
        } catch (e: Exception) {
            Toast.makeText(this, "Open Settings → Apps → this app → Files / storage → Allow all", Toast.LENGTH_LONG).show()
        }
    }

    private fun startOverlay() {
        if (!canDrawOverlays()) {
            Toast.makeText(this, R.string.need_overlay, Toast.LENGTH_LONG).show()
            openOverlaySettings()
            return
        }
        try {
            val intent = Intent(this, OverlayService::class.java).setAction(OverlayService.ACTION_START)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(intent)
            } else {
                startService(intent)
            }
            Toast.makeText(this, "Overlay started — look for the red bubble", Toast.LENGTH_LONG).show()
        } catch (e: Exception) {
            Toast.makeText(this, "Start failed: ${e.message}", Toast.LENGTH_LONG).show()
        }
        binding.status.text = "Overlay starting — look for the red bubble on screen."
        binding.root.postDelayed({ refreshStatus() }, 400)
    }

    private fun stopOverlay() {
        try {
            startService(
                Intent(this, OverlayService::class.java).setAction(OverlayService.ACTION_STOP),
            )
        } catch (_: Exception) {
        }
        refreshStatus()
    }
}
