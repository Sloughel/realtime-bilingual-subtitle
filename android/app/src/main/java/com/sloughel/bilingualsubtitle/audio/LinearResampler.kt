package com.sloughel.bilingualsubtitle.audio

/**
 * 流式线性重采样：把源采样率单声道 PCM16 转为 16kHz。
 *
 * 仅在设备不支持以 16kHz 直接录音时使用（48000/44100 回退），
 * 跨块保留相位，避免每 100ms 产生一次断续。
 */
class LinearResampler(srcRate: Int, private val dstRate: Int = 16000) {
    private val step = srcRate.toDouble() / dstRate
    private var carry = ShortArray(1)
    private var pos = 0.0

    fun process(input: ShortArray, length: Int = input.size): ShortArray {
        if (length <= 0) return ShortArray(0)
        if (step == 1.0) return input.copyOf(length)

        val combined = ShortArray(carry.size + length)
        System.arraycopy(carry, 0, combined, 0, carry.size)
        System.arraycopy(input, 0, combined, carry.size, length)
        if (combined.size < 2) {
            carry = combined
            return ShortArray(0)
        }

        val out = ArrayList<Int>((length / step).toInt() + 2)
        var p = pos
        while (p + 1 < combined.size) {
            val idx = p.toInt()
            val frac = p - idx
            val a = combined[idx].toDouble()
            val b = combined[idx + 1].toDouble()
            out.add((a + (b - a) * frac).toInt())
            p += step
        }
        val consumed = p.toInt().coerceAtMost(combined.size - 1)
        carry = combined.copyOfRange(consumed, combined.size)
        pos = p - consumed
        return ShortArray(out.size) { i -> out[i].coerceIn(-32768, 32767).toShort() }
    }
}
