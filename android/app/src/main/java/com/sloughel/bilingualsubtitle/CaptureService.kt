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
import androidx.core.app.NotificationCompat
import com.sloughel.bilingualsubtitle.audio.AudioSource
import com.sloughel.bilingualsubtitle.audio.MediaSource
import com.sloughel.bilingualsubtitle.audio.MicSource
import com.sloughel.bilingualsubtitle.net.WsClient
import com.sloughel.bilingualsubtitle.overlay.OverlayManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
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
    private var mode: CaptureMode = CaptureMode.MIC
    private var stopping = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> stopCapture()
            ACTION_START -> startCapture(intent)
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
            projection = manager.getMediaProjection(code, data)
            projection?.registerCallback(object : MediaProjection.Callback() {
                override fun onStop() {
                    AppState.setError("系统已停止媒体声音捕获，可切换为麦克风模式（FM-06）")
                    stopCapture()
                }
            }, Handler(Looper.getMainLooper()))
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
                MediaSource(active)
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
        if (AppState.state.value.overlayEnabled && !OverlayManager.show(this)) {
            AppState.setOverlay(false)
            AppState.setError("未获得悬浮窗权限，字幕仅在应用内显示")
        }
    }

    private fun stopAudio() {
        source?.stop()
        source = null
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
            projection?.let { runCatching { it.stop() } }
            projection = null
            finishService()
        }
    }

    private fun finishService() {
        AppState.setConnection(ConnectionState.DISCONNECTED, "未连接")
        AppState.setCapturing(false)
        runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
        stopSelf()
    }

    private fun fail(message: String) {
        AppState.setError(message)
        AppState.log(message)
        stopAudio()
        ws?.close()
        ws = null
        projection?.let { runCatching { it.stop() } }
        projection = null
        AppState.setCapturing(false)
        runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
        stopSelf()
    }

    override fun onDestroy() {
        stopAudio()
        ws?.close()
        ws = null
        projection?.let { runCatching { it.stop() } }
        projection = null
        scope.cancel()
        super.onDestroy()
    }

    // ------------------------------------------------------------------
    // WsClient.Listener（OkHttp 线程回调）
    // ------------------------------------------------------------------
    override fun onConnection(state: ConnectionState, detail: String) {
        AppState.setConnection(state, detail)
        when (state) {
            ConnectionState.CONNECTED -> startAudio()
            // 重连中暂停音频源，但保留服务与“停止”按钮；重连成功后自动恢复
            ConnectionState.RECONNECTING -> stopAudio()
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
