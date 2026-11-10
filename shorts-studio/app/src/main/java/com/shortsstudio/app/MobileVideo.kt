package com.shortsstudio.app

import android.content.Context
import android.graphics.*
import android.media.*
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.*
import kotlin.random.Random

/** All frames and sound are encoded directly on Android. No server or FFmpeg required. */
object MobileVideo {
    const val WIDTH = 432
    const val HEIGHT = 768
    private const val FPS = 24
    data class Sample(val data: ByteArray, val presentationUs: Long, val flags: Int)
    data class Stream(val format: MediaFormat, val samples: List<Sample>)
    data class Audio(val pcm: ByteArray, val sampleRate: Int, val channels: Int) {
        val durationSeconds: Double get() = pcm.size.toDouble() / (sampleRate * channels * 2)
    }

    suspend fun make(context: Context, topic: String, script: String, headings: List<String>,
                     voiceName: String, style: String, seconds: Int, plan: MotionPlan? = null): File = withContext(Dispatchers.Default) {
        val dir = File(context.filesDir, "videos").apply { mkdirs() }
        val wav = File(context.cacheDir, "speech-${System.currentTimeMillis()}.wav")
        try {
            speak(context, script, voiceName, wav, plan?.language)
            val audio = parseWav(wav)
            val actualSeconds = max(seconds.toDouble(), audio.durationSeconds + 0.4).coerceAtMost(95.0)
            val videoStream = video(headings, topic, style, actualSeconds, plan)
            val audioStream = aac(audio, actualSeconds)
            val file = File(dir, "short-${System.currentTimeMillis()}.mp4")
            combine(file, videoStream, audioStream)
            file
        } finally { wav.delete() }
    }

    private suspend fun speak(context: Context, text: String, voiceName: String, file: File, language: String? = null) {
        val initialized = CompletableDeferred<TextToSpeech>()
        lateinit var tts: TextToSpeech
        tts = TextToSpeech(context.applicationContext) { status ->
            if (status == TextToSpeech.SUCCESS) initialized.complete(tts)
            else initialized.completeExceptionally(IllegalStateException("Install or enable a Text-to-Speech engine in Android Settings"))
        }
        try {
            val service = initialized.await()
            if(language!=null) {
                val available=service.setLanguage(java.util.Locale.forLanguageTag(language))
                check(available>=0) { "Install an Android TTS voice for $language, then try again" }
            }
            val selected = service.voices?.firstOrNull { it.name == voiceName }
            if (selected != null) service.voice = selected
            val result = CompletableDeferred<Unit>()
            service.setOnUtteranceProgressListener(object: UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {}
                override fun onDone(utteranceId: String?) { result.complete(Unit) }
                override fun onError(utteranceId: String?) { result.completeExceptionally(IllegalStateException("TTS synthesis failed")) }
            })
            val code = service.synthesizeToFile(text.take(3600), Bundle(), file, "shorts-${System.nanoTime()}")
            if (code != TextToSpeech.SUCCESS) throw IllegalStateException("Text-to-speech could not start")
            result.await()
        } finally { tts.stop(); tts.shutdown() }
    }

    private fun parseWav(file: File): Audio {
        val bytes = file.readBytes()
        require(bytes.size >= 44 && String(bytes, 0, 4) == "RIFF") { "TTS engine did not produce a WAV file" }
        val b = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        var at = 12
        var channels = 1
        var sampleRate = 22050
        var bitDepth = 16
        var format = 1
        var payload = byteArrayOf()
        while (at + 8 <= bytes.size) {
            val name = String(bytes, at, 4)
            val len = b.getInt(at + 4)
            if (len < 0 || at + 8L + len > bytes.size) break
            if (name == "fmt " && len >= 16) {
                format = b.getShort(at + 8).toInt()
                channels = b.getShort(at + 10).toInt()
                sampleRate = b.getInt(at + 12)
                bitDepth = b.getShort(at + 22).toInt()
            }
            if (name == "data") { payload = bytes.copyOfRange(at + 8, at + 8 + len); break }
            at += 8 + len + len % 2
        }
        require(format == 1 && bitDepth == 16 && channels in 1..2 && sampleRate in 8000..48000 && payload.isNotEmpty()) {
            "This TTS voice uses an unsupported WAV format; select a different installed voice"
        }
        return Audio(payload, sampleRate, channels)
    }

    private fun chooseEncoder(mime: String): MediaCodec = MediaCodec.createEncoderByType(mime)

    private fun aac(audio: Audio, maxDuration: Double): Stream {
        val fmt = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC, audio.sampleRate, audio.channels).apply {
            setInteger(MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC)
            setInteger(MediaFormat.KEY_BIT_RATE, 96000)
            setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 16384)
        }
        val codec = chooseEncoder(MediaFormat.MIMETYPE_AUDIO_AAC)
        codec.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        val samples = mutableListOf<Sample>()
        var outputFormat: MediaFormat? = null
        val meta = MediaCodec.BufferInfo()
        try {
            codec.start()
            val bytesPerFrame = audio.channels * 2
            val available = min(audio.pcm.size, ((maxDuration * audio.sampleRate).toInt()) * bytesPerFrame)
            var offset = 0
            var eosSent = false
            var finished = false
            while (!finished) {
                if (!eosSent) {
                    val idx = codec.dequeueInputBuffer(10000)
                    if (idx >= 0) {
                        val buffer = codec.getInputBuffer(idx)!!
                        buffer.clear()
                        val size = min(available - offset, buffer.capacity() / bytesPerFrame * bytesPerFrame)
                        if (size > 0) buffer.put(audio.pcm, offset, size)
                        val pts = (offset.toLong() / bytesPerFrame) * 1000000L / audio.sampleRate
                        val flags = if (offset + size >= available) MediaCodec.BUFFER_FLAG_END_OF_STREAM else 0
                        codec.queueInputBuffer(idx, 0, size, pts, flags)
                        offset += size
                        eosSent = flags != 0
                    }
                }
                var result = codec.dequeueOutputBuffer(meta, 10000)
                while (result >= 0) {
                    val bytes = codec.getOutputBuffer(result)!!
                    if (meta.size > 0 && meta.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG == 0) {
                        bytes.position(meta.offset); bytes.limit(meta.offset + meta.size)
                        val data = ByteArray(meta.size); bytes.get(data)
                        samples.add(Sample(data, meta.presentationTimeUs, meta.flags))
                    }
                    if (meta.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) finished = true
                    codec.releaseOutputBuffer(result, false)
                    result = codec.dequeueOutputBuffer(meta, 0)
                }
                if (result == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) outputFormat = codec.outputFormat
            }
        } finally { codec.stop(); codec.release() }
        return Stream(outputFormat ?: error("AAC encoder has no output format"), samples)
    }

    private fun video(headings: List<String>, topic: String, style: String, duration: Double, plan: MotionPlan?): Stream {
        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, WIDTH, HEIGHT).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible)
            setInteger(MediaFormat.KEY_BIT_RATE, 1200000)
            setInteger(MediaFormat.KEY_FRAME_RATE, FPS)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2)
        }
        val codec = chooseEncoder(MediaFormat.MIMETYPE_VIDEO_AVC)
        codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        val samples = mutableListOf<Sample>()
        val info = MediaCodec.BufferInfo()
        var outputFormat: MediaFormat? = null
        val count = max(1, (duration * FPS).toInt())
        val sceneNames = headings.filter { it.isNotBlank() }.ifEmpty { listOf(topic) }
        val bitmap = Bitmap.createBitmap(WIDTH, HEIGHT, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        val pixels = IntArray(WIDTH * HEIGHT)
        try {
            codec.start()
            for (frame in 0 until count) {
                val index = codec.dequeueInputBuffer(500000)
                if (index < 0) error("Video encoder input timed out")
                if(plan!=null) MotionDrawing.draw(canvas,plan,frame.toFloat()/max(1,count-1))
                else drawFrame(canvas, sceneNames, topic, style, frame, count)
                bitmap.getPixels(pixels, 0, WIDTH, 0, 0, WIDTH, HEIGHT)
                val yuv = codec.getInputImage(index) ?: error("Video encoder does not support flexible YUV420 input")
                fillYuv(yuv, pixels)
                yuv.close()
                codec.queueInputBuffer(index, 0, WIDTH * HEIGHT * 3 / 2,
                    frame.toLong() * 1000000L / FPS, 0)
                drain(codec, info, samples) { outputFormat = it }
            }
            val idx = codec.dequeueInputBuffer(500000)
            if (idx < 0) error("Video encoder could not finish recording")
            codec.queueInputBuffer(idx, 0, 0, count.toLong() * 1000000L / FPS, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
            var ended = false
            var attempts = 0
            while (!ended && attempts++ < 200) {
                ended = drain(codec, info, samples, 100000) { outputFormat = it }
            }
            if (!ended) error("Video encoder did not finalize")
        } finally {
            bitmap.recycle()
            codec.stop()
            codec.release()
        }
        return Stream(outputFormat ?: error("Video encoder did not return format"), samples)
    }

    private fun drain(codec: MediaCodec, meta: MediaCodec.BufferInfo, target: MutableList<Sample>,
                      timeout: Long = 0, onFormat: (MediaFormat)->Unit): Boolean {
        var eos = false
        var result = codec.dequeueOutputBuffer(meta, timeout)
        while (result >= 0 || result == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
            if (result == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) onFormat(codec.outputFormat)
            else {
                if (meta.size > 0 && meta.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG == 0) {
                    val bb = codec.getOutputBuffer(result)!!
                    bb.position(meta.offset); bb.limit(meta.offset + meta.size)
                    val data = ByteArray(meta.size); bb.get(data)
                    target.add(Sample(data, meta.presentationTimeUs, meta.flags))
                }
                if (meta.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) eos = true
                codec.releaseOutputBuffer(result, false)
            }
            result = codec.dequeueOutputBuffer(meta, 0)
        }
        return eos
    }

    private fun fillYuv(image: android.media.Image, px: IntArray) {
        val planes = image.planes
        val y = planes[0].buffer
        val u = planes[1].buffer
        val v = planes[2].buffer
        for (row in 0 until HEIGHT) {
            for (col in 0 until WIDTH) {
                val rgb = px[row*WIDTH + col]
                val r = (rgb shr 16) and 255
                val g = (rgb shr 8) and 255
                val b = rgb and 255
                val yy = (16 + (66*r + 129*g + 25*b + 128) / 256).coerceIn(0,255)
                y.put(row*planes[0].rowStride + col*planes[0].pixelStride, yy.toByte())
                if (row % 2 == 0 && col % 2 == 0) {
                    val uu = (128 + (-38*r - 74*g + 112*b + 128) / 256).coerceIn(0,255)
                    val vv = (128 + (112*r - 94*g - 18*b + 128) / 256).coerceIn(0,255)
                    u.put(row/2*planes[1].rowStride + col/2*planes[1].pixelStride, uu.toByte())
                    v.put(row/2*planes[2].rowStride + col/2*planes[2].pixelStride, vv.toByte())
                }
            }
        }
    }

    private fun drawFrame(canvas: Canvas, scenes: List<String>, topic: String, style: String, frame: Int, count: Int) {
        val t = frame.toFloat()/count
        val scene = min(scenes.lastIndex, (t * scenes.size).toInt())
        val inside = (t*scenes.size) - scene
        val palette = when(style) {
            "Minimal" -> intArrayOf(Color.rgb(16,20,29), Color.rgb(39,55,68), Color.rgb(240,218,179))
            "Cyber" -> intArrayOf(Color.rgb(3,22,30), Color.rgb(36,13,51), Color.rgb(69,232,241))
            else -> intArrayOf(Color.rgb(18,11,43), Color.rgb(26,37,72), Color.rgb(156,121,255))
        }
        val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        paint.shader = LinearGradient(0f,0f, WIDTH.toFloat(), HEIGHT.toFloat(), palette[0], palette[1], Shader.TileMode.CLAMP)
        canvas.drawRect(0f,0f, WIDTH.toFloat(), HEIGHT.toFloat(), paint)
        paint.shader = null
        paint.style = Paint.Style.STROKE
        for (i in 0..5) {
            val radius = 70f + i*70 + 12f*sin(t*13+i)
            val cx = WIDTH*0.58f + 18f*sin(t*18+i*1.3f)
            val cy = HEIGHT*0.50f + 30f*cos(t*8+i)
            paint.color = palette[2]
            paint.alpha = max(7,25-i*3)
            paint.strokeWidth = 1.8f
            canvas.drawCircle(cx, cy, radius, paint)
        }
        paint.style = Paint.Style.FILL
        paint.alpha = 255
        paint.color = palette[2]
        paint.textSize = 17f
        paint.typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
        canvas.drawText("SHORTS  /  STUDIO", 28f, 55f, paint)
        canvas.drawRect(28f, 85f, WIDTH-28f, 87f, paint)
        paint.textSize = 22f
        canvas.drawText("%02d  /  %02d".format(scene+1, scenes.size), 28f, 165f, paint)
        val smooth = (sin(inside * Math.PI).toFloat() * 10f)
        canvas.save()
        canvas.translate(0f, smooth)
        paint.textSize = if (scenes[scene].length > 55) 30f else 39f
        paint.color = Color.WHITE
        val lines = splitLines(paint, scenes[scene], WIDTH-58f)
        val lineH = paint.textSize*1.22f
        val baseY = HEIGHT*0.43f - lines.size*lineH/2
        lines.forEachIndexed { idx, line -> canvas.drawText(line, 29f, baseY+idx*lineH,paint) }
        canvas.restore()
        paint.color = palette[2]
        canvas.drawRoundRect(28f,HEIGHT-115f,WIDTH-28f,HEIGHT-110f,3f,3f,paint.apply { alpha=40 })
        paint.alpha=255
        canvas.drawRoundRect(28f,HEIGHT-115f,28f+(WIDTH-56f)*t,HEIGHT-110f,3f,3f,paint)
        paint.textSize = 15f
        paint.color = Color.rgb(190,195,215)
        canvas.drawText(topic.take(36), 28f,HEIGHT-63f,paint)
        canvas.drawText("MOTION  •  VOICE  •  IDEAS",28f,HEIGHT-25f,paint.apply { alpha=145 })
    }

    private fun splitLines(paint: Paint, input: String, width: Float): List<String> {
        val words = input.replace("\\n"," ").split(Regex("\\s+"))
        val list = mutableListOf<String>()
        var line = ""
        for (word in words) {
            val test = if (line.isEmpty()) word else "$line $word"
            if (paint.measureText(test) > width && line.isNotEmpty()) { list += line; line=word }
            else line=test
        }
        if (line.isNotBlank()) list+=line
        return list.take(6)
    }

    private fun combine(file: File, video: Stream, audio: Stream) {
        val muxer = MediaMuxer(file.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        try {
            val v = muxer.addTrack(video.format)
            val a = muxer.addTrack(audio.format)
            muxer.setOrientationHint(0)
            muxer.start()
            val info = MediaCodec.BufferInfo()
            for ((track, stream) in listOf(v to video, a to audio)) {
                for (s in stream.samples) {
                    info.set(0,s.data.size,s.presentationUs,s.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME)
                    muxer.writeSampleData(track, ByteBuffer.wrap(s.data), info)
                }
            }
            muxer.stop()
        } finally { muxer.release() }
    }

    fun localStoryboard(topic: String, narration: String): List<String> {
        val source = (if(narration.isBlank()) topic else narration).trim()
        val segments = source.split(Regex("(?<=[.!?؟])\\s+|\\n+")).map { it.trim() }.filter { it.isNotBlank() }
        if (segments.size > 1) return segments.take(8)
        val words = source.split(Regex("\\s+"))
        return if (words.size < 8) listOf(topic, "A fresh perspective", "The key idea", "Think about it", topic)
               else words.chunked(max(5,ceil(words.size/5.0).toInt())).map { it.joinToString(" ") }.take(8)
    }

    fun fallbackNarration(topic: String): String =
        "Today's topic is $topic. This is a quick introduction. Consider the main ideas and explore what makes this topic interesting. " +
        "If you want a detailed, accurate explanation, add your own script or connect Claude Code. " +
        "Thanks for watching."
}

