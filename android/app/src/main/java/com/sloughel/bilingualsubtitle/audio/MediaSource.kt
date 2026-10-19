package com.sloughel.bilingualsubtitle.audio

import android.annotation.SuppressLint
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.os.SystemClock
import android.util.Log
import kotlin.math.sqrt

/**
 * 媒体声音采集（FR-02）：MediaProjection + AudioPlaybackCapture（Android 10+）。
 *
 * 目标应用若声明 `allowAudioPlaybackCapture=false`（常见于 DRM 内容），
 * 采集到的将是静音，由客户端提示降级为麦克风模式（FM-06）。
 *
 * 兼容性处理：部分 OEM（含华为部分机型）在**纯音频**捕获时不会保持投影会话，
 * 导致 AudioRecord 持续读到静音。这里创建一个 1×1 的 VirtualDisplay 镜像到
 * 未读取的 Surface，仅用于保持会话活跃，不读取任何屏幕内容。
 */
@SuppressLint("MissingPermission")
class MediaSource(
    private val projection: MediaProjection,
    private val onLevel: (Float) -> Unit = {},
) : AudioSource {
    private var record: AudioRecord? = null
    private var thread: Thread? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null

    @Volatile
    private var running = false

    override fun start(onFrame: (ShortArray) -> Unit) {
        keepProjectionAlive()

        val captureConfig = AudioPlaybackCaptureConfiguration.Builder(projection)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .build()

        val format = AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(16000)
            .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
            .build()

        val rec = AudioRecord.Builder()
            .setAudioPlaybackCaptureConfig(captureConfig)
            .setAudioFormat(format)
            .setBufferSizeInBytes(FRAME_SAMPLES * 2 * 8)
            .build()

        check(rec.state == AudioRecord.STATE_INITIALIZED) { "媒体声音采集初始化失败" }
        record = rec
        running = true
        rec.startRecording()
        Log.i(
            TAG,
            "媒体捕获已启动：state=${rec.state} rate=16000 buffer=${FRAME_SAMPLES * 2 * 8}"
        )

        val accumulator = FrameAccumulator(onFrame)
        thread = Thread({
            val buffer = ShortArray(FRAME_SAMPLES)
            var lastLevelAt = 0L
            while (running) {
                val n = rec.read(buffer, 0, buffer.size)
                if (n > 0) {
                    accumulator.push(buffer, n)
                    val now = SystemClock.elapsedRealtime()
                    if (now - lastLevelAt >= 200) {
                        lastLevelAt = now
                        onLevel(rms(buffer, n))
                    }
                } else if (n == AudioRecord.ERROR_DEAD_OBJECT || n == AudioRecord.ERROR_INVALID_OPERATION) {
                    Log.w(TAG, "AudioRecord 错误：$n")
                    break
                }
            }
            Log.i(TAG, "媒体声音采集线程结束")
        }, "media-capture").apply { start() }
    }

    /** 1×1 虚拟显示：仅用于保持投影会话，Surface 内容不会被读取。 */
    private fun keepProjectionAlive() {
        runCatching {
            val reader = ImageReader.newInstance(1, 1, PixelFormat.RGBA_8888, 1)
            imageReader = reader
            virtualDisplay = projection.createVirtualDisplay(
                "bilingual-subtitle-audio",
                1,
                1,
                160,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                reader.surface,
                null,
                null,
            )
            Log.i(TAG, "已创建 1×1 VirtualDisplay（保持捕获会话）")
        }.onFailure {
            Log.w(TAG, "创建 VirtualDisplay 失败（继续尝试音频捕获）：${it.message}")
        }
    }

    private fun rms(buffer: ShortArray, length: Int): Float {
        var sum = 0.0
        for (i in 0 until length) {
            val v = buffer[i].toDouble()
            sum += v * v
        }
        return (sqrt(sum / length) / 32768.0).toFloat()
    }

    override fun stop() {
        running = false
        thread?.let { runCatching { it.join(500) } }
        thread = null
        record?.let {
            runCatching { if (it.recordingState == AudioRecord.RECORDSTATE_RECORDING) it.stop() }
            runCatching { it.release() }
        }
        record = null
        runCatching { virtualDisplay?.release() }
        virtualDisplay = null
        runCatching { imageReader?.close() }
        imageReader = null
    }

    private companion object {
        const val TAG = "MediaSource"
    }
}
