package com.sloughel.bilingualsubtitle.audio

import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.util.Log

/**
 * 媒体声音采集（FR-02）：MediaProjection + AudioPlaybackCapture（Android 10+）。
 *
 * 目标应用若声明 `allowAudioPlaybackCapture=false`（常见于 DRM 内容），
 * 采集到的将是静音，由客户端提示降级为麦克风模式（FM-06）。
 */
@SuppressLint("MissingPermission")
class MediaSource(private val projection: MediaProjection) : AudioSource {
    private var record: AudioRecord? = null
    private var thread: Thread? = null

    @Volatile
    private var running = false

    override fun start(onFrame: (ShortArray) -> Unit) {
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

        val accumulator = FrameAccumulator(onFrame)
        thread = Thread({
            val buffer = ShortArray(FRAME_SAMPLES)
            while (running) {
                val n = rec.read(buffer, 0, buffer.size)
                if (n > 0) {
                    accumulator.push(buffer, n)
                } else if (n == AudioRecord.ERROR_DEAD_OBJECT || n == AudioRecord.ERROR_INVALID_OPERATION) {
                    Log.w(TAG, "AudioRecord 错误：$n")
                    break
                }
            }
            Log.i(TAG, "媒体声音采集线程结束")
        }, "media-capture").apply { start() }
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
    }

    private companion object {
        const val TAG = "MediaSource"
    }
}
