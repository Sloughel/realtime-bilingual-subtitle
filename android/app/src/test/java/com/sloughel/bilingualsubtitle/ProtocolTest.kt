package com.sloughel.bilingualsubtitle

import com.sloughel.bilingualsubtitle.audio.LinearResampler
import com.sloughel.bilingualsubtitle.net.Protocol
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs
import kotlin.math.sin

/**
 * 与服务端协议一致性、重采样正确性的 JVM 单元测试。
 * 运行：`gradle testDebugUnitTest`
 */
class ProtocolTest {

    @Test
    fun audioFrameLayoutMatchesProtocolV1() {
        val pcm = ByteArray(Protocol.CHUNK_BYTES) { (it % 251).toByte() }
        val frame = Protocol.encodeAudioFrame(0x01020304, pcm)

        assertEquals(3206, frame.size)
        assertEquals(0xAF.toByte(), frame[0])
        assertEquals(1.toByte(), frame[1])
        // seq 为大端 uint32
        assertArrayEquals(byteArrayOf(0x01, 0x02, 0x03, 0x04), frame.copyOfRange(2, 6))
        assertArrayEquals(pcm, frame.copyOfRange(6, 3206))
    }

    @Test(expected = IllegalArgumentException::class)
    fun audioFrameRejectsWrongPayloadSize() {
        Protocol.encodeAudioFrame(1, ByteArray(1600))
    }

    @Test
    fun resampler48kTo16kProducesOneSecondPerSecond() {
        val resampler = LinearResampler(48000)
        var produced = 0
        // 10 块 × 4800 = 48000 个 48k 采样点 = 1 秒
        repeat(10) { chunk ->
            val input = ShortArray(4800) { i ->
                (sin((chunk * 4800 + i) * 2.0 * Math.PI * 440.0 / 48000.0) * 10000).toInt().toShort()
            }
            produced += resampler.process(input).size
        }
        // 目标 16000 ± 2（线性插值取整误差）
        assertTrue("produced=$produced", abs(produced - 16000) <= 2)
    }

    @Test
    fun resamplerIsIdentityAt16k() {
        val resampler = LinearResampler(16000)
        val input = ShortArray(1600) { it.toShort() }
        val output = resampler.process(input)
        assertEquals(1600, output.size)
        assertArrayEquals(input, output)
    }
}
