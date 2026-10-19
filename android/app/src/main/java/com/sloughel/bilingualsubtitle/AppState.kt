package com.sloughel.bilingualsubtitle

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

enum class CaptureMode(val wire: String, val label: String) {
    MIC("mic", "麦克风"),
    MEDIA("media", "媒体声音"),
}

enum class ConnectionState(val label: String) {
    DISCONNECTED("未连接"),
    CONNECTING("连接中…"),
    CONNECTED("已连接"),
    RECONNECTING("重连中…"),
    FAILED("连接失败"),
}

data class UiState(
    val connection: ConnectionState = ConnectionState.DISCONNECTED,
    val capturing: Boolean = false,
    val mode: CaptureMode = CaptureMode.MIC,
    val host: String = "",
    val port: String = "8000",
    val statusText: String = "未连接",
    val lastSource: String = "",
    val lastTranslation: String = "",
    val lastDegraded: Boolean = false,
    val error: String? = null,
    val overlayEnabled: Boolean = false,
    val overlayAlpha: Float = 0.85f,
    val logs: List<String> = emptyList(),
    val sentenceCount: Int = 0,
)

/**
 * 进程内 UI 状态。Activity 与前台服务（含 OkHttp 回调线程）都通过它通信，
 * StateFlow 本身线程安全。
 */
object AppState {
    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    fun update(transform: (UiState) -> UiState) {
        _state.update(transform)
    }

    fun setConnection(connection: ConnectionState, detail: String? = null) {
        update { it.copy(connection = connection, statusText = detail ?: connection.label) }
    }

    fun setCapturing(capturing: Boolean) {
        update { it.copy(capturing = capturing) }
    }

    fun setSubtitle(source: String, translation: String, degraded: Boolean) {
        update {
            it.copy(
                lastSource = source,
                lastTranslation = translation,
                lastDegraded = degraded,
                error = null,
                sentenceCount = it.sentenceCount + 1,
            )
        }
    }

    fun setError(message: String?) {
        update { it.copy(error = message) }
    }

    fun setOverlay(enabled: Boolean) {
        update { it.copy(overlayEnabled = enabled) }
    }

    fun setOverlayAlpha(alpha: Float) {
        update { it.copy(overlayAlpha = alpha.coerceIn(0.2f, 1f)) }
    }

    fun log(message: String) {
        update {
            val stamp = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())
            it.copy(logs = (it.logs + "[$stamp] $message").takeLast(8))
        }
    }
}
