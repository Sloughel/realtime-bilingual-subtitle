package com.sloughel.bilingualsubtitle

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import com.sloughel.bilingualsubtitle.audio.AudioSource
import com.sloughel.bilingualsubtitle.audio.MediaSource
import com.sloughel.bilingualsubtitle.audio.MicSource
import com.sloughel.bilingualsubtitle.net.WsClient
import com.sloughel.bilingualsubtitle.overlay.OverlayManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * 前台服务（2.8）：持有 WebSocket 连接与音频源，退出 Activity 后继续采集。
 *
 * 生命周期：ACTION_START → 连接 → 收到 config_ack 后启动音频源 → 逐帧发送；
 * ACTION_STOP → 发送 stop 冲刷当前句 → 关闭连接 → 停止前台服务。
 */
class CaptureService : Service(), WsClient.Listener {

    companion object {
        const val ACTION_START = "com.sloughel.bilingualsubtitle.START"
        const val ACTION_STOP = "com.sloughel.bilingualsubtitle.STOP"
        const val EXTRA_MODE = "mode"
        const val EXTRA_HOST = "host"
        const val EXTRA_PORT = "port"
        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_RESULT_DATA = "result_data"

        private const val CHANNEL_ID = "capture"
        private const val NOTIFICATION_ID = 1001
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var ws: WsClient? = null
    private var source: AudioSource? = null
    private var projection: MediaProjection? = null
    private var projectionCallback: MediaProjection.Callback? = null
    private var mode: CaptureMode = CaptureMode.MIC
    private var stopping = false
    private var switching = false
    private var mediaWatchdog: Job? = null
    private var mediaStartedAt = 0L
    private var lastSoundAt = 0L
    private var lastLevelUiAt = 0L
    private var mediaHintSent = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> stopCapture()
            ACTION_START -> {
                if (switching) {
                    AppState.log("正在切换模式，请稍候")
                } else if (ws != null) {
                    // 采集中再次收到开始（用户切换了模式）：重启采集并应用新模式
                    restartCapture(intent)
                } else {
                    startCapture(intent)
                }
            }
        }
        return START_NOT_STICKY
    }

    // ------------------------------------------------------------------
    private fun startCapture(intent: Intent) {
        if (ws != null) {
            AppState.setError("已在采集中，请先停止")
            return
        }
        stopping = false
        mode = if (intent.getStringExtra(EXTRA_MODE) == CaptureMode.MEDIA.wire) {
            CaptureMode.MEDIA
        } else {
            CaptureMode.MIC
        }
        AppState.update { it.copy(mode = mode) }

        if (!startAsForeground()) {
            fail("前台服务启动失败，请检查通知权限")
            return
        }

        if (mode == CaptureMode.MEDIA) {
            val code = intent.getIntExtra(EXTRA_RESULT_CODE, 0)
            @Suppress("DEPRECATION")
            val data: Intent? = if (Build.VERSION.SDK_INT >= 33) {
                intent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
            } else {
                intent.getParcelableExtra(EXTRA_RESULT_DATA)
            }
            if (code == 0 || data == null) {
                fail("未获得媒体声音授权（FM-06）")
                return
            }
            val manager = getSystemService(MediaProjectionManager::class.java)
            val activeProjection = manager.getMediaProjection(code, data)
            if (activeProjection == null) {
                fail("媒体声音授权无效")
                return
            }
            projection = activeProjection
            val callback = object : MediaProjection.Callback() {
                override fun onStop() {
                    AppState.setError("系统已停止媒体声音捕获，可切换为麦克风模式（FM-06）")
                    stopCapture()
                }
            }
            projectionCallback = callback
            activeProjection.registerCallback(callback, Handler(Looper.getMainLooper()))
        }

        val host = intent.getStringExtra(EXTRA_HOST) ?: "127.0.0.1"
        val port = intent.getIntExtra(EXTRA_PORT, 8000)
        val client = WsClient(host, port, mode.wire, listener = this)
        ws = client
        client.connect()
        AppState.setCapturing(true)
        AppState.log("启动采集：${mode.label} → $host:$port")
    }

    private fun startAsForeground(): Boolean {
        val manager = getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL_ID) == null) {
            manager.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    getString(R.string.notification_channel),
                    NotificationManager.IMPORTANCE_LOW,
                )
            )
        }
        val stopIntent = Intent(this, CaptureService::class.java).setAction(ACTION_STOP)
        val pending = PendingIntent.getService(
            this, 2, stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("实时双语字幕")
            .setContentText("正在采集并发送音频…")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .addAction(0, "停止", pending)
            .build()
        return runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val type = if (mode == CaptureMode.MEDIA) {
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
                } else {
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
                }
                startForeground(NOTIFICATION_ID, notification, type)
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
        }.onFailure {
            AppState.log("startForeground 失败：${it.message}")
        }.isSuccess
    }

    // ------------------------------------------------------------------
    private fun startAudio() {
        if (source != null) return
        val audioSource: AudioSource = when (mode) {
            CaptureMode.MIC -> MicSource()
            CaptureMode.MEDIA -> {
                val active = projection
                if (active == null) {
                    fail("媒体声音捕获未授权")
                    return
                }
                MediaSource(active) { level -> onMediaLevel(level) }
            }
        }
        try {
            audioSource.start { samples -> sendFrame(samples) }
        } catch (t: Throwable) {
            fail("采集启动失败：${t.message}")
            return
        }
        source = audioSource
        AppState.setCapturing(true)
        AppState.log("开始采集（${mode.label}）")
        if (mode == CaptureMode.MEDIA) startMediaWatchdog()
        if (AppState.state.value.overlayEnabled && !OverlayManager.show(this)) {
            AppState.setOverlay(false)
            AppState.setError("未获得悬浮窗权限，字幕仅在应用内显示")
        }
    }

    /** 媒体声音电平回调（采集线程，约 5 次/秒）。 */
    private fun onMediaLevel(level: Float) {
        val now = SystemClock.elapsedRealtime()
        if (now - lastLevelUiAt >= 200) {
            lastLevelUiAt = now
            AppState.setCaptureLevel(level)
        }
        if (level > 0.003f) {
            lastSoundAt = now
            if (mediaHintSent) {
                mediaHintSent = false
                AppState.setMediaHint(null)
                AppState.log("已检测到媒体声音")
            }
        }
    }

    /**
     * 8 秒内未捕获到非静音数据时给出可操作提示（FM-06）。
     * 常见原因：目标应用禁止音频捕获、DRM 内容、或尚未开始播放。
     */
    private fun startMediaWatchdog() {
        mediaWatchdog?.cancel()
        mediaStartedAt = SystemClock.elapsedRealtime()
        lastSoundAt = mediaStartedAt
        mediaHintSent = false
        mediaWatchdog = scope.launch {
            delay(8_000)
            while (isActive && mode == CaptureMode.MEDIA && source != null) {
                val now = SystemClock.elapsedRealtime()
                if (now - maxOf(mediaStartedAt, lastSoundAt) >= 8_000 && !mediaHintSent) {
                    mediaHintSent = true
                    AppState.setMediaHint(
                        "8 秒内未捕获到媒体声音。系统图库、部分系统应用与 DRM 内容会禁止音频捕获；" +
                            "请换用第三方播放器（如 VLC）或浏览器播放视频。仍无声音时请改用麦克风模式。"
                    )
                    AppState.log("媒体声音持续为 0：目标应用可能禁止捕获（FM-06）")
                }
                delay(1_000)
            }
        }
    }

    private fun stopAudio() {
        mediaWatchdog?.cancel()
        mediaWatchdog = null
        source?.stop()
        source = null
        AppState.setCaptureLevel(0f)
        AppState.setMediaHint(null)
        mediaHintSent = false
    }

    private fun sendFrame(samples: ShortArray) {
        if (samples.size != 1600) return
        val pcm = ByteArray(samples.size * 2)
        var i = 0
        for (sample in samples) {
            val v = sample.toInt()
            pcm[i++] = (v and 0xFF).toByte()
            pcm[i++] = ((v shr 8) and 0xFF).toByte()
        }
        ws?.sendAudio(pcm)
    }

    // ------------------------------------------------------------------
    /**
     * 采集中切换模式：先停掉旧连接与音频源，再按新配置重新开始。
     * 媒体→麦克风无需新授权；麦克风→媒体由 Activity 先取得新的 MediaProjection 再调用。
     */
    private fun restartCapture(intent: Intent) {
        if (switching) {
            AppState.log("正在切换模式，请稍候")
            return
        }
        switching = true
        val target = if (intent.getStringExtra(EXTRA_MODE) == CaptureMode.MEDIA.wire) {
            CaptureMode.MEDIA
        } else {
            CaptureMode.MIC
        }
        AppState.log("切换采集模式 → ${target.label}，正在重启…")
        AppState.setMediaHint(null)
        AppState.setCaptureLevel(0f)

        val old = ws
        ws = null
        stopAudio()
        scope.launch {
            old?.sendStop()
            delay(300)
            old?.close()
            stopProjection()
            startCapture(intent)
            switching = false
        }
    }

    /** 注销回调后再停止投影，避免 stop() 触发 onStop 把新会话一起停掉。 */
    private fun stopProjection() {
        val active = projection ?: return
        projectionCallback?.let { callback ->
            runCatching { active.unregisterCallback(callback) }
        }
        projectionCallback = null
        runCatching { active.stop() }
        projection = null
    }

    private fun stopCapture() {
        if (stopping) return
        stopping = true
        stopAudio()
        AppState.setCapturing(false)
        val client = ws
        ws = null
        if (client == null) {
            finishService()
            return
        }
        scope.launch {
            client.sendStop()      // 请求服务端冲刷当前句（FR-09）
            delay(400)
            client.close()
            stopProjection()
            finishService()
        }
    }

    private fun finishService() {
        AppState.setConnection(ConnectionState.DISCONNECTED, "未连接")
        AppState.setCapturing(false)
        switching = false
        runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
        stopSelf()
    }

    private fun fail(message: String) {
        AppState.setError(message)
        AppState.log(message)
        stopAudio()
        ws?.close()
        ws = null
        stopProjection()
        switching = false
        AppState.setCapturing(false)
        runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
        stopSelf()
    }

    override fun onDestroy() {
        stopAudio()
        ws?.close()
        ws = null
        stopProjection()
        scope.cancel()
        super.onDestroy()
    }

    // ------------------------------------------------------------------
    // WsClient.Listener（OkHttp 线程回调）
    // ------------------------------------------------------------------
    override fun onConnection(state: ConnectionState, detail: String) {
        AppState.setConnection(state, detail)
        when (state) {
            // 首次连接或麦克风模式重连后启动/恢复音频源；已在采集时保持不动
            ConnectionState.CONNECTED -> {
                if (source == null) startAudio() else AppState.log("连接恢复，继续发送音频")
            }
            // 重连中不要停止音频源：媒体模式下 MediaProjection 只允许创建一个
            // VirtualDisplay，释放后重建会失败并被 fail() 关闭连接。WsClient 在
            // 未收到 config_ack 时会丢弃音频帧，恢复后同一音源继续发送。
            ConnectionState.RECONNECTING -> AppState.log("连接中断，正在重连（采集保持）")
            ConnectionState.DISCONNECTED, ConnectionState.FAILED -> {
                stopAudio()
                AppState.setCapturing(false)
            }
            else -> Unit
        }
    }

    override fun onSubtitle(source: String, translation: String, degraded: Boolean) {
        AppState.setSubtitle(source, translation, degraded)
        OverlayManager.update(
            source,
            if (degraded && translation == source) "$translation（翻译失败）" else translation,
        )
    }

    override fun onServerError(code: Int, message: String) {
        AppState.setError("[$code] $message")
        AppState.log("服务端错误 $code：$message")
    }

    override fun onLog(message: String) {
        AppState.log(message)
    }
}
