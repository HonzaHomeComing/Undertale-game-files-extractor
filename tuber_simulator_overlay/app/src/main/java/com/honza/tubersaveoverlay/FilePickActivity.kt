package com.honza.tubersaveoverlay

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

/**
 * Transparent trampoline activity so the overlay can open the system file picker
 * (SAF), then hand the Uri back to [OverlayService].
 */
class FilePickActivity : AppCompatActivity() {
    private val mode: String by lazy { intent.getStringExtra(OverlayService.EXTRA_MODE) ?: "load" }

    private val openDoc = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        finishWith(uri, "load")
    }

    private val createDoc = registerForActivityResult(ActivityResultContracts.CreateDocument("text/xml")) { uri ->
        finishWith(uri, "save")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (mode == "save") {
            createDoc.launch("tubular.playerprefs.xml")
        } else {
            openDoc.launch(arrayOf("text/*", "application/xml", "*/*"))
        }
    }

    private fun finishWith(uri: Uri?, modeOut: String) {
        if (uri != null) {
            try {
                contentResolver.takePersistableUriPermission(
                    uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
                )
            } catch (_: SecurityException) {
                // createDocument may not support persistable grants — still usable once.
            }
            val i = Intent(this, OverlayService::class.java).apply {
                action = OverlayService.ACTION_FILE_LOADED
                putExtra(OverlayService.EXTRA_URI, uri)
                putExtra(OverlayService.EXTRA_MODE, modeOut)
            }
            startService(i)
        }
        setResult(Activity.RESULT_OK)
        finish()
    }
}
