package com.sloughel.bilingualsubtitle.audio

/** 客户端统一使用 16kHz 单声道 PCM16，每帧 100ms（1600 采样点）。 */
const val FRAME_SAMPLES = 1600

/** 音频源抽象（对应 02 文档 §5.2 的 AudioSource 策略模式）。 */
interface AudioSource {
    /** 开始采集；每凑满 [FRAME_SAMPLES] 个采样点回调一次。 */
    fun start(onFrame: (ShortArray) -> Unit)

    fun stop()
}

/** 把任意长度的 PCM16 累积成固定 1600 采样点的帧。 */
internal class FrameAccumulator(private val onFrame: (ShortArray) -> Unit) {
    private val pending = ShortArray(FRAME_SAMPLES)
    private var filled = 0

    fun push(samples: ShortArray, length: Int = samples.size) {
        var offset = 0
        while (offset < length) {
            val take = minOf(length - offset, FRAME_SAMPLES - filled)
            System.arraycopy(samples, offset, pending, filled, take)
            filled += take
            offset += take
            if (filled == FRAME_SAMPLES) {
                onFrame(pending.copyOf())
                filled = 0
            }
        }
    }
}
