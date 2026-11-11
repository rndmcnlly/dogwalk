package dogwalk.ondevice

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Messages from the Session Orchestrator, in the same shapes main's VoiceSession sends a Voice Frontend. */
sealed interface Inbound {
    data class Say(val delegationId: String?, val content: String) : Inbound
    data class Brief(val delegationId: String?, val content: String) : Inbound
    data class Guidance(val content: String) : Inbound
    data class Fixture(val description: String) : Inbound
    /** A capability this app does not have (input muting for recording, audio clips). */
    data class Unsupported(val type: String) : Inbound
}

/**
 * Transcript out, Handoffs out, context and speech requests in. Input/output provenance
 * distinguishes typed/displayed text from recognized/played speech. No session tools or briefs out.
 */
class OrchestratorLink(
    private val endpoint: String,
    private val onMessage: (Inbound) -> Unit,
    private val onClosed: (String) -> Unit,
    private val onOpen: () -> Unit = {},
) {
    private val client = OkHttpClient.Builder().pingInterval(20, TimeUnit.SECONDS).build()
    private var socket: WebSocket? = null
    private val clockStart = System.currentTimeMillis()
    @Volatile private var open = false

    /** The voice session's media clock, as the Worker's transcript cut expects. */
    fun nowMs() = System.currentTimeMillis() - clockStart

    fun connect() {
        socket = client.newWebSocket(Request.Builder().url(endpoint).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) { open = true; onOpen() }
            override fun onMessage(webSocket: WebSocket, text: String) { parse(text)?.let(onMessage) }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(1000, null) }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { finish("closed: $reason") }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) { finish("failed: ${t.javaClass.simpleName}") }
        })
    }

    private fun finish(reason: String) { if (open || socket != null) { open = false; socket = null; onClosed(reason) } }

    fun callerSaid(text: String, startMs: Long, endMs: Long, typed: Boolean = false) =
        send(transcript("input", text, startMs, endMs, if (typed) "text_typed" else "speech_recognized"))

    // Existing transcript envelopes and clock positions, not audio durations. No spoken_chars.
    // The app-local delivery field makes the provenance explicit to fixtures/future adapters.
    fun textShown(u: Utterance, atMs: Long) =
        send(transcript("output", u.text, atMs, atMs, "text_displayed"))

    /** Only words the caller actually heard: the full text, or the prefix before an interruption. */
    fun voiceHeard(text: String, startMs: Long, endMs: Long) =
        send(JSONObject().put("type", "session.output_transcript.delta").put("delta", text).put("start_ms", startMs).put("end_ms", endMs))

    /** A Handoff carries no task text: the Orchestrator reads the transcript up to offset_ms. */
    fun handoff(delegationId: String, offsetMs: Long) =
        send(JSONObject().put("type", "session.delegation.created").put("offset_ms", offsetMs)
            .put("delegation", JSONObject().put("id", delegationId).put("target", "client")))

    private fun send(json: JSONObject): Boolean = socket?.send(json.toString()) ?: false

    fun close() { socket?.close(1000, "frontend stopped"); socket = null; open = false; client.dispatcher.executorService.shutdown() }

    companion object {
        fun transcript(direction: String, text: String, startMs: Long, endMs: Long, delivery: String) =
            JSONObject().put("type", "session.${direction}_transcript.delta").put("delta", text)
                .put("start_ms", startMs).put("end_ms", endMs).put("delivery", delivery)

        fun parse(text: String): Inbound? {
            val m = runCatching { JSONObject(text) }.getOrNull() ?: return null
            val delegation = m.optString("delegation_id").takeIf { m.has("delegation_id") && !m.isNull("delegation_id") && it.isNotEmpty() }
            val content = m.optString("content")
            return when (val type = m.optString("type")) {
                "session.commentary.append" -> if (content.isBlank()) null else Inbound.Say(delegation, content)
                "session.thinking.append" -> if (content.isBlank()) null else Inbound.Brief(delegation, content)
                "session.instructions.append" -> if (content.isBlank()) null else Inbound.Guidance(content)
                "dev.fixture.ready" -> Inbound.Fixture(m.optString("description", "fixture"))
                "session.input_audio.mute", "session.input_audio.unmute", "session.output_audio.fixture.play" -> Inbound.Unsupported(type)
                else -> null
            }
        }
    }
}
