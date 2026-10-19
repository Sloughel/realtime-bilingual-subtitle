package com.sloughel.bilingualsubtitle.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sloughel.bilingualsubtitle.AppState
import com.sloughel.bilingualsubtitle.BuildConfig
import com.sloughel.bilingualsubtitle.CaptureMode
import com.sloughel.bilingualsubtitle.UiState

/** 控制页（FR-09、FR-10、FR-12、UC-01）。 */
@Composable
fun ControlScreen(
    state: UiState,
    onStart: (String, Int, CaptureMode, Boolean) -> Unit,
    onStop: () -> Unit,
    onOverlayToggle: (Boolean) -> Unit,
    onAlphaChange: (Float) -> Unit,
    onRequestOverlayPermission: () -> Unit,
) {
    var host by remember { mutableStateOf(state.host.ifBlank { "192.168.1.100" }) }
    var port by remember { mutableStateOf(state.port) }
    var mode by remember { mutableStateOf(state.mode) }
    var overlay by remember { mutableStateOf(state.overlayEnabled) }

    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(
            Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("实时双语字幕", style = MaterialTheme.typography.headlineSmall)
            Text(
                "客户端 v${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                "手机采集音频 → 主机识别与翻译 → 悬浮窗显示",
                style = MaterialTheme.typography.bodySmall,
            )

            // ---------- 服务器 ----------
            Card(Modifier.fillMaxWidth()) {
                Column(
                    Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Text("服务器", style = MaterialTheme.typography.titleMedium)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(
                            value = host,
                            onValueChange = { host = it },
                            label = { Text("主机 IP") },
                            singleLine = true,
                            modifier = Modifier.weight(1f),
                        )
                        OutlinedTextField(
                            value = port,
                            onValueChange = { port = it.filter(Char::isDigit).take(5) },
                            label = { Text("端口") },
                            singleLine = true,
                            modifier = Modifier.width(104.dp),
                        )
                    }
                    Text("语言：英语 → 中文（v1.0）", style = MaterialTheme.typography.bodySmall)
                }
            }

            // ---------- 采集模式 ----------
            Card(Modifier.fillMaxWidth()) {
                Column(
                    Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Text("采集模式", style = MaterialTheme.typography.titleMedium)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (mode == CaptureMode.MIC) {
                            Button(onClick = {
                                mode = CaptureMode.MIC
                                AppState.update { it.copy(mode = CaptureMode.MIC) }
                            }, modifier = Modifier.weight(1f)) {
                                Text("麦克风")
                            }
                        } else {
                            OutlinedButton(onClick = {
                                mode = CaptureMode.MIC
                                AppState.update { it.copy(mode = CaptureMode.MIC) }
                            }, modifier = Modifier.weight(1f)) {
                                Text("麦克风")
                            }
                        }
                        if (mode == CaptureMode.MEDIA) {
                            Button(onClick = {
                                mode = CaptureMode.MEDIA
                                AppState.update { it.copy(mode = CaptureMode.MEDIA) }
                            }, modifier = Modifier.weight(1f)) {
                                Text("媒体声音")
                            }
                        } else {
                            OutlinedButton(onClick = {
                                mode = CaptureMode.MEDIA
                                AppState.update { it.copy(mode = CaptureMode.MEDIA) }
                            }, modifier = Modifier.weight(1f)) {
                                Text("媒体声音")
                            }
                        }
                    }
                    if (mode == CaptureMode.MEDIA) {
                        Text(
                            "将采集其他应用播放的声音，需要系统授权。部分应用（含 DRM 内容）禁止捕获，"
                                + "此时可改用麦克风模式。",
                            style = MaterialTheme.typography.bodySmall,
                        )
                        Text(
                            if (state.capturing) {
                                "媒体音量：${(state.captureLevel * 100).toInt()}%"
                            } else {
                                "媒体音量：未开始"
                            },
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                }
            }

            // ---------- 悬浮窗 ----------
            Card(Modifier.fillMaxWidth()) {
                Column(
                    Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text("悬浮窗字幕", style = MaterialTheme.typography.titleMedium)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("显示在其他应用上层", modifier = Modifier.weight(1f))
                        Switch(
                            checked = overlay,
                            onCheckedChange = {
                                overlay = it
                                onOverlayToggle(it)
                            },
                        )
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            "透明度",
                            style = MaterialTheme.typography.bodyMedium,
                            modifier = Modifier.width(72.dp),
                        )
                        Slider(
                            value = state.overlayAlpha,
                            onValueChange = onAlphaChange,
                            valueRange = 0.3f..1f,
                            modifier = Modifier.weight(1f),
                        )
                    }
                    TextButton(onClick = onRequestOverlayPermission) {
                        Text("前往系统设置授予悬浮窗权限")
                    }
                }
            }

            // ---------- 开始 / 停止 ----------
            Button(
                onClick = {
                    if (state.capturing) {
                        onStop()
                    } else {
                        onStart(host.trim(), port.toIntOrNull() ?: 8000, mode, overlay)
                    }
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(52.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (state.capturing) {
                        MaterialTheme.colorScheme.error
                    } else {
                        MaterialTheme.colorScheme.primary
                    }
                ),
            ) {
                Text(if (state.capturing) "停止" else "开始")
            }

            // ---------- 状态 ----------
            Card(Modifier.fillMaxWidth()) {
                Column(
                    Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text("状态", style = MaterialTheme.typography.titleMedium)
                    Text(
                        state.connection.label + if (state.capturing) " · 采集中" else "",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Text(state.statusText, style = MaterialTheme.typography.bodySmall)
                    if (state.mode == CaptureMode.MEDIA && state.capturing) {
                        Text(
                            "媒体音量：${(state.captureLevel * 100).toInt()}%",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                    state.mediaHint?.let {
                        Text(
                            it,
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                    if (state.lastSource.isNotBlank()) {
                        Spacer(Modifier.height(4.dp))
                        Text(state.lastSource, style = MaterialTheme.typography.bodyLarge)
                        Text(
                            state.lastTranslation + if (state.lastDegraded) "（翻译失败）" else "",
                            color = MaterialTheme.colorScheme.primary,
                            style = MaterialTheme.typography.bodyLarge,
                        )
                    }
                    state.error?.let {
                        Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                    }
                    Text("已收到 ${state.sentenceCount} 句", style = MaterialTheme.typography.bodySmall)
                }
            }

            // ---------- 日志 ----------
            if (state.logs.isNotEmpty()) {
                Card(Modifier.fillMaxWidth()) {
                    Column(
                        Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(2.dp),
                    ) {
                        Text("日志", style = MaterialTheme.typography.titleMedium)
                        state.logs.forEach { line ->
                            Text(
                                line,
                                style = MaterialTheme.typography.bodySmall,
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                            )
                        }
                    }
                }
            }
        }
    }
}
