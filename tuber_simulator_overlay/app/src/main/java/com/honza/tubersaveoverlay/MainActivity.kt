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
                else -> append("Ready. Grant permissions, then Start overlay.")
            }
            append('\n')
            append(if (files) "All-files access: OK" else "All-files access: NEEDED (for phone saves)")
            append('\n')
            append(
                if (GameSaveAccess.hasRoot()) {
                    "Root: OK (live PlayerPrefs push available)"
                } else {
                    "Root: no — Apply & Restart uses phone no-root mode"
                },
            )
        }
    }

    private fun openOverlaySettings() {
        val intent = Intent(
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            Uri.parse("package:$packageName"),
        )
        startActivity(intent)
    }

    private fun openAllFilesSettings() {
        startActivity(NoRootSaveAccess.allFilesAccessIntent(this))
    }

    private fun startOverlay() {
        if (!canDrawOverlays()) {
            Toast.makeText(this, R.string.need_overlay, Toast.LENGTH_LONG).show()
            openOverlaySettings()
            return
        }
        if (!NoRootSaveAccess.hasAllFilesAccess()) {
            Toast.makeText(this, R.string.need_all_files, Toast.LENGTH_LONG).show()
            openAllFilesSettings()
            // Still start overlay so they can use it after granting.
        }
        val intent = Intent(this, OverlayService::class.java).setAction(OverlayService.ACTION_START)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
        refreshStatus()
        Toast.makeText(this, "Overlay started", Toast.LENGTH_SHORT).show()
    }

    private fun stopOverlay() {
        startService(
            Intent(this, OverlayService::class.java).setAction(OverlayService.ACTION_STOP),
        )
        refreshStatus()
    }
}
