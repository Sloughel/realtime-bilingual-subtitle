package com.sloughel.bilingualsubtitle.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log

/**
 * 麦克风采集（FR-01）。
 *
 * 优先以 16kHz 直接录音（ADR-06：客户端完成重采样）；若设备不支持，
 * 回退 48k/44.1k 并用 [LinearResampler] 转成 16kHz。
 */
class MicSource : AudioSource {
    private var record: AudioRecord? = null
    private var thread: Thread? = null

    @Volatile
    private var running = false

    @SuppressLint("MissingPermission")
    override fun start(onFrame: (ShortArray) -> Unit) {
        val (rate, rec) = openRecord()
        record = rec
        running = true
        rec.startRecording()

        val resampler = if (rate != 16000) LinearResampler(rate) else null
        val accumulator = FrameAccumulator(onFrame)
        val chunk = if (rate == 16000) FRAME_SAMPLES else rate / 10

        thread = Thread({
            val buffer = ShortArray(chunk)
            while (running) {
                val n = rec.read(buffer, 0, buffer.size)
                if (n > 0) {
                    if (resampler != null) {
                        accumulator.push(resampler.process(buffer, n))
                    } else {
                        accumulator.push(buffer, n)
                    }
                } else if (n == AudioRecord.ERROR_DEAD_OBJECT || n == AudioRecord.ERROR_INVALID_OPERATION) {
                    Log.w(TAG, "AudioRecord 错误：$n")
                    break
                }
            }
            Log.i(TAG, "麦克风采集线程结束")
        }, "mic-capture").apply { start() }
    }

    @SuppressLint("MissingPermission")
    private fun openRecord(): Pair<Int, AudioRecord> {
        for (rate in intArrayOf(16000, 48000, 44100)) {
            val minBuffer = AudioRecord.getMinBufferSize(
                rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
            )
            if (minBuffer <= 0) continue
            val bufferSize = maxOf(minBuffer, rate / 10 * 2 * 2)  // 至少 100ms
            val rec = try {
                AudioRecord(
                    MediaRecorder.AudioSource.MIC,
                    rate,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    bufferSize
                )
            } catch (t: Throwable) {
                Log.w(TAG, "以 $rate Hz 初始化失败：${t.message}")
                null
            }
            if (rec != null && rec.state == AudioRecord.STATE_INITIALIZED) {
                Log.i(TAG, "麦克风采集采样率：$rate Hz")
                return rate to rec
            }
            rec?.release()
        }
        throw IllegalStateException("无法初始化麦克风：设备不支持 16k/48k/44.1k 采集")
    }

    override fun stop() {
        running = false
        thread?.let {
            runCatching { it.join(500) }
        }
        thread = null
        record?.let {
            runCatching { if (it.recordingState == AudioRecord.RECORDSTATE_RECORDING) it.stop() }
            runCatching { it.release() }
        }
        record = null
    }

    private companion object {
        const val TAG = "MicSource"
    }
}
