package com.sloughel.bilingualsubtitle

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import com.sloughel.bilingualsubtitle.overlay.OverlayManager
import com.sloughel.bilingualsubtitle.ui.ControlScreen

class MainActivity : ComponentActivity() {

    private companion object {
        const val PREFS = "settings"
    }

    private var pendingMode: CaptureMode = CaptureMode.MIC
    private var pendingHost: String = "192.168.1.100"
    private var pendingPort: Int = 8000
    private var modeBeforeProjection: CaptureMode? = null

    /** 麦克风/通知权限申请（媒体模式还需随后申请 MediaProjection）。 */
    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) {
        val audioGranted = ContextCompat.checkSelfPermission(
            this, Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED
        if (!audioGranted) {
            AppState.setError("未授予麦克风权限，无法采集音频")
            AppState.log("RECORD_AUDIO 被拒绝")
            return@registerForActivityResult
        }
        if (pendingMode == CaptureMode.MEDIA) {
            requestProjection()
        } else {
            startCaptureService(CaptureMode.MIC)
        }
    }

    private val projectionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val data = result.data
        if (result.resultCode == Activity.RESULT_OK && data != null) {
            startCaptureService(CaptureMode.MEDIA, data, result.resultCode)
        } else {
            AppState.setError("媒体声音授权被拒绝；可改用麦克风模式（FM-06）")
            AppState.log("MediaProjection 授权被拒绝")
            // 若是在采集中切换模式被取消，恢复到服务端实际使用的模式
            val revert = modeBeforeProjection
            if (revert != null && AppState.state.value.capturing) {
                AppState.update { it.copy(mode = revert) }
            }
        }
        modeBeforeProjection = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val overlayAllowed = Settings.canDrawOverlays(this)
        AppState.update {
            it.copy(
                host = prefs.getString("host", "192.168.1.100") ?: "192.168.1.100",
                port = prefs.getInt("port", 8000).toString(),
                mode = CaptureMode.entries.firstOrNull { m ->
                    m.wire == prefs.getString("mode", CaptureMode.MIC.wire)
                } ?: CaptureMode.MIC,
                overlayAlpha = prefs.getFloat("alpha", 0.85f),
                overlayEnabled = prefs.getBoolean("overlay", false) && overlayAllowed,
            )
        }

        setContent {
            val state by AppState.state.collectAsState()
            MaterialTheme {
                Surface(color = MaterialTheme.colorScheme.background) {
                    ControlScreen(
                        state = state,
                        onStart = ::onStartClicked,
                        onModeSelected = ::onModeSelected,
                        onStop = ::stopCaptureService,
                        onOverlayToggle = ::onOverlayToggled,
                        onAlphaChange = ::onAlphaChanged,
                        onRequestOverlayPermission = ::openOverlaySettings,
                    )
                }
            }
        }
    }

    // ------------------------------------------------------------------
    private fun onStartClicked(host: String, port: Int, mode: CaptureMode, overlay: Boolean) {
        if (host.isBlank()) {
            AppState.setError("请填写服务器 IP 或主机名")
            return
        }
        if (port !in 1..65535) {
            AppState.setError("端口应在 1~65535 之间")
            return
        }
        AppState.setError(null)
        pendingHost = host
        pendingPort = port
        pendingMode = mode
        AppState.update { it.copy(host = host, port = port.toString(), mode = mode) }

        if (overlay && !Settings.canDrawOverlays(this)) {
            AppState.setOverlay(false)
            AppState.setError("请先授予悬浮窗权限，再次点击开始")
            openOverlaySettings()
            return
        }

        val missing = mutableListOf<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            missing += Manifest.permission.RECORD_AUDIO
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            missing += Manifest.permission.POST_NOTIFICATIONS
        }

        if (missing.isNotEmpty()) {
            permissionLauncher.launch(missing.toTypedArray())
        } else if (mode == CaptureMode.MEDIA) {
            requestProjection()
        } else {
            startCaptureService(CaptureMode.MIC)
        }
    }

    private fun requestProjection() {
        val manager = getSystemService(MediaProjectionManager::class.java)
        projectionLauncher.launch(manager.createScreenCaptureIntent())
    }

    /**
     * 采集中点击另一个模式：真正切换采集模式。
     *
     * - 切到麦克风：直接让前台服务重启采集；
     * - 切到媒体声音：需要新的 MediaProjection 授权，授权后服务重启采集。
     * 未在采集中时只更新选择。
     */
    private fun onModeSelected(mode: CaptureMode, host: String, port: Int) {
        val state = AppState.state.value
        if (mode == state.mode) return
        val previous = state.mode
        AppState.update { it.copy(mode = mode) }
        if (!state.capturing) return

        pendingMode = mode
        pendingHost = host
        pendingPort = port
        getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString("host", host)
            .putInt("port", port)
            .putString("mode", mode.wire)
            .apply()

        if (mode == CaptureMode.MEDIA) {
            modeBeforeProjection = previous
            requestProjection()
        } else {
            startCaptureService(CaptureMode.MIC)
        }
    }

    private fun startCaptureService(mode: CaptureMode, data: Intent? = null, resultCode: Int = 0) {
        getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString("host", pendingHost)
            .putInt("port", pendingPort)
            .putString("mode", mode.wire)
            .putBoolean("overlay", AppState.state.value.overlayEnabled)
            .apply()

        val intent = Intent(this, CaptureService::class.java).apply {
            action = CaptureService.ACTION_START
            putExtra(CaptureService.EXTRA_MODE, mode.wire)
            putExtra(CaptureService.EXTRA_HOST, pendingHost)
            putExtra(CaptureService.EXTRA_PORT, pendingPort)
            if (data != null) {
                putExtra(CaptureService.EXTRA_RESULT_CODE, resultCode)
                putExtra(CaptureService.EXTRA_RESULT_DATA, data)
            }
        }
        ContextCompat.startForegroundService(this, intent)
        AppState.setCapturing(true)
    }

    private fun stopCaptureService() {
        startService(Intent(this, CaptureService::class.java).setAction(CaptureService.ACTION_STOP))
    }

    private fun onOverlayToggled(enabled: Boolean) {
        if (enabled && !Settings.canDrawOverlays(this)) {
            AppState.setOverlay(false)
            AppState.setError("请先授予悬浮窗权限")
            openOverlaySettings()
            return
        }
        AppState.setOverlay(enabled)
        getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putBoolean("overlay", enabled)
            .apply()
        if (enabled) {
            if (!OverlayManager.show(this)) {
                AppState.setOverlay(false)
                AppState.setError("悬浮窗权限未授予")
            }
        } else {
            OverlayManager.hide()
        }
    }

    private fun onAlphaChanged(alpha: Float) {
        AppState.setOverlayAlpha(alpha)
        OverlayManager.setAlpha(alpha)
        getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putFloat("alpha", alpha)
            .apply()
    }

    private fun openOverlaySettings() {
        startActivity(
            Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:$packageName")
            )
        )
    }
}
