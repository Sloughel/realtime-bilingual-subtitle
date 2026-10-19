package com.sloughel.bilingualsubtitle.net

import android.util.Log
import com.sloughel.bilingualsubtitle.ConnectionState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * WebSocket 客户端（FR-03/04/11，协议见 03 文档）。
 *
 * 握手顺序：连接 → 服务端 hello → 客户端 config → config_ack → start → 音频帧。
 * 断线后按 1/2/4/8/10s 退避重连，携带同一 session_id（服务端会话保留 30s）。
 */
class WsClient(
    private val host: String,
    private val port: Int,
    private val mode: String,
    private val srcLang: String = "en",
    private val tgtLang: String = "zh",
    private val listener: Listener,
) {
    interface Listener {
        fun onConnection(state: ConnectionState, detail: String)
        fun onSubtitle(source: String, translation: String, degraded: Boolean)
        fun onServerError(code: Int, message: String)
        fun onLog(message: String)
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val sessionId: String = UUID.randomUUID().toString()
    private val client = OkHttpClient.Builder()
        .pingInterval(10, TimeUnit.SECONDS)   // 协议级心跳；对端不回 pong 会自动失败并触发重连
        .build()

    private var webSocket: WebSocket? = null
    private var reconnectJob: Job? = null
    private var attempt = 0
    private var seq = 0

    @Volatile
    private var closed = false

    @Volatile
    private var ready = false

    fun connect() {
        if (closed) return
        listener.onConnection(ConnectionState.CONNECTING, "连接 $host:$port …")
        val url = "ws://$host:$port/ws?protocol_version=${Protocol.PROTOCOL_VERSION}&session_id=$sessionId"
        val request = Request.Builder().url(url).build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                listener.onLog("WebSocket 已建立")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleText(text)
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                Log.d(TAG, "忽略服务端二进制消息（${bytes.size} 字节）")
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                ready = false
                if (closed) return
                listener.onLog("连接失败：${t.message ?: t.javaClass.simpleName}")
                scheduleReconnect()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                ready = false
                if (closed) return
                listener.onLog("连接关闭：$code $reason")
                scheduleReconnect()
            }
        })
    }

    private fun handleText(text: String) {
        val obj = runCatching { JSONObject(text) }.getOrNull() ?: return
        when (obj.optString("type")) {
            "hello" -> {
                listener.onLog("握手完成，服务端版本 ${obj.optString("server_version")}")
                sendConfig()
            }

            "config_ack" -> {
                obj.optJSONArray("warnings")?.let { arr ->
                    for (i in 0 until arr.length()) listener.onLog("服务端提示：${arr.optString(i)}")
                }
                if (obj.optBoolean("ok", false)) {
                    ready = true
                    attempt = 0
                    send(JSONObject().put("type", "start").toString())
                    listener.onConnection(ConnectionState.CONNECTED, "已连接，正在采集")
                } else {
                    listener.onServerError(1001, "服务端拒绝了配置")
                }
            }

            "subtitle" -> listener.onSubtitle(
                obj.optString("source"),
                obj.optString("translation"),
                obj.optBoolean("translation_degraded", false),
            )

            "error" -> listener.onServerError(obj.optInt("code", 0), obj.optString("message"))

            "status" -> listener.onLog("服务端状态：${obj.optString("message")}")

            "pong" -> Unit
        }
    }

    private fun sendConfig() {
        val config = JSONObject()
            .put("type", "config")
            .put("mode", mode)
            .put("src_lang", srcLang)
            .put("tgt_lang", tgtLang)
            .put("sample_rate", Protocol.SAMPLE_RATE)
            .put("chunk_ms", Protocol.CHUNK_MS)
        send(config.toString())
    }

    private fun send(text: String) {
        webSocket?.send(text)
    }

    /** 发送一帧 3200 字节 PCM16；未就绪（未收到 config_ack）时丢弃。 */
    fun sendAudio(pcm: ByteArray) {
        if (!ready) return
        val ws = webSocket ?: return
        seq += 1
        ws.send(Protocol.encodeAudioFrame(seq, pcm).toByteString())
    }

    /** 请求服务端冲刷当前句（对应 stop 控制消息）。 */
    fun sendStop() {
        ready = false
        send(JSONObject().put("type", "stop").toString())
    }

    private fun scheduleReconnect() {
        if (closed) return
        reconnectJob?.cancel()
        val delayMs = when (attempt) {
            0 -> 1_000L
            1 -> 2_000L
            2 -> 4_000L
            3 -> 8_000L
            else -> 10_000L
        }
        attempt += 1
        listener.onConnection(
            ConnectionState.RECONNECTING,
            "${delayMs / 1000}s 后重连（第 $attempt 次）"
        )
        reconnectJob = scope.launch {
            delay(delayMs)
            if (!closed) connect()
        }
    }

    fun close() {
        if (closed) return
        closed = true
        ready = false
        reconnectJob?.cancel()
        runCatching { webSocket?.close(1000, "client stop") }
        webSocket = null
        scope.cancel()
        runCatching { client.dispatcher.executorService.shutdown() }
        listener.onConnection(ConnectionState.DISCONNECTED, "未连接")
    }

    private companion object {
        const val TAG = "WsClient"
    }
}
