package id.rs.apm.frista

// Contoh integrasi verifikasi wajah FRISTA di APM Android (Kotlin).
//
// Cara pakai dari layar pendaftaran APM:
//
//   val launcher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
//       val json = res.data?.getStringExtra(FristaActivity.EXTRA_RESULT) ?: return@registerForActivityResult
//       val result = JSONObject(json)
//       when (result.optString("status")) {
//           "OK", "ALREADY_REGISTERED" -> lanjutBuatSep()   // ambil nokartu lengkap via GET /api/sessions/<id>
//           "UNREGISTERED" -> arahkanKeLoketPetugas()
//           else -> tampilkanGagal(result.optString("message"))
//       }
//   }
//   launcher.launch(FristaActivity.intent(this, nikAtauNoKartu))
//
// AndroidManifest.xml:
//   <uses-permission android:name="android.permission.INTERNET" />
//   <uses-permission android:name="android.permission.CAMERA" />
//   <activity android:name=".frista.FristaActivity" android:screenOrientation="portrait" />
//
// build.gradle: implementation("com.squareup.okhttp3:okhttp:4.12.0")

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class FristaActivity : AppCompatActivity() {

    companion object {
        // Alamat server Django (WAJIB HTTPS agar kamera di WebView bisa dipakai)
        const val SERVER = "https://frista.rs.local"
        // Kunci APM: hanya bisa membuat sesi & membaca hasil. Akun VClaim TIDAK ada di APK.
        const val APM_API_KEY = "ganti-kunci-apm"

        const val EXTRA_MEMBER_ID = "member_id"
        const val EXTRA_RESULT = "frista_result"
        const val EXTRA_SESSION_ID = "frista_session_id"

        fun intent(ctx: Context, memberId: String) =
            Intent(ctx, FristaActivity::class.java).putExtra(EXTRA_MEMBER_ID, memberId)
    }

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(40, TimeUnit.SECONDS)
        .build()

    private lateinit var webView: WebView
    private var sessionId: String? = null
    private var delivered = false

    private val cameraPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) startSession() else finishWith(errorJson("CAMERA_DENIED", "Izin kamera ditolak"))
        }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        webView = WebView(this)
        setContentView(webView)

        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.settings.mediaPlaybackRequiresUserGesture = false
        webView.addJavascriptInterface(Bridge(), "AndroidFrista")
        webView.webChromeClient = object : WebChromeClient() {
            // Izinkan kamera HANYA untuk halaman dari server kita sendiri
            override fun onPermissionRequest(request: PermissionRequest) {
                runOnUiThread {
                    if (request.origin.toString().startsWith(SERVER)) {
                        request.grant(arrayOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE))
                    } else {
                        request.deny()
                    }
                }
            }
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startSession()
        } else {
            cameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    /** Langkah 1: minta sesi ke server, lalu buka halaman kamera. */
    private fun startSession() {
        val memberId = intent.getStringExtra(EXTRA_MEMBER_ID).orEmpty()
        lifecycleScope.launch {
            val resp = withContext(Dispatchers.IO) {
                runCatching {
                    val body = JSONObject().put("member_id", memberId).toString()
                        .toRequestBody("application/json".toMediaType())
                    val req = Request.Builder().url("$SERVER/api/sessions")
                        .header("X-APM-Key", APM_API_KEY).post(body).build()
                    http.newCall(req).execute().use { JSONObject(it.body!!.string()) }
                }
            }
            val json = resp.getOrNull()
            if (json == null || json.optString("status") != "OK") {
                val msg = json?.optString("message") ?: "Server verifikasi tidak dapat dihubungi"
                finishWith(errorJson("SESSION_ERROR", msg))
                return@launch
            }
            sessionId = json.getString("session_id")
            webView.loadUrl(json.getString("capture_url"))
        }
    }

    /** Langkah 2: halaman kamera memanggil AndroidFrista.onResult(json) saat selesai. */
    inner class Bridge {
        @JavascriptInterface
        fun onResult(json: String) {
            val result = runCatching { JSONObject(json) }.getOrNull() ?: return
            // Abaikan hasil sementara (masih boleh coba ulang)
            if (result.has("final") && !result.optBoolean("final")) return
            runOnUiThread {
                Toast.makeText(this@FristaActivity, result.optString("message"), Toast.LENGTH_SHORT).show()
                webView.postDelayed({ finishWith(json) }, 1500)
            }
        }
    }

    private fun finishWith(json: String) {
        if (delivered) return
        delivered = true
        setResult(Activity.RESULT_OK, Intent()
            .putExtra(EXTRA_RESULT, json)
            .putExtra(EXTRA_SESSION_ID, sessionId))
        finish()
    }

    private fun errorJson(status: String, message: String) =
        JSONObject().put("status", status).put("message", message).put("final", true).toString()

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }
}
