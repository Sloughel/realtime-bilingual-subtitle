package com.sloughel.bilingualsubtitle.net

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * 协议常量与音频帧编码（对应《03-接口与通信协议设计》§3）。
 *
 * 帧结构：magic(1) + version(1) + seq(uint32 大端) + 3200 字节 PCM16。
 */
object Protocol {
    const val PROTOCOL_VERSION = 1
    const val SAMPLE_RATE = 16000
    const val CHUNK_MS = 100
    const val CHUNK_SAMPLES = 1600
    const val CHUNK_BYTES = 3200
    const val FRAME_HEADER_BYTES = 6
    const val FRAME_BYTES = 3206
    const val MAGIC: Byte = 0xAF.toByte()
    const val FRAME_VERSION: Byte = 1

    fun encodeAudioFrame(seq: Int, pcm: ByteArray): ByteArray {
        require(pcm.size == CHUNK_BYTES) { "每帧应为 $CHUNK_BYTES 字节，实际 ${pcm.size}" }
        val out = ByteArray(FRAME_BYTES)
        out[0] = MAGIC
        out[1] = FRAME_VERSION
        ByteBuffer.wrap(out, 2, 4).order(ByteOrder.BIG_ENDIAN).putInt(seq)
        System.arraycopy(pcm, 0, out, FRAME_HEADER_BYTES, CHUNK_BYTES)
        return out
    }
}
