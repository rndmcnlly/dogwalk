package dogwalk.ondevice

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.UUID
import java.util.concurrent.Executors
import okhttp3.Call
import okhttp3.Callback
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

/** Messages from the Session Orchestrator, in the same shapes main's VoiceSession sends a Voice Frontend. */
sealed interface Inbound {
    data class Say(val delegationId: String?, val content: String, val sourceId: String? = null) : Inbound
    data class Brief(val delegationId: String?, val content: String) : Inbound
    data class Guidance(val content: String) : Inbound
    data class Fixture(val description: String) : Inbound
    /** A capability this app does not have (input muting for recording, audio clips). */
    data class Unsupported(val type: String) : Inbound
    data class Status(val summary: String) : Inbound
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
    private val real: Boolean = false,
    private val credential: String? = null,
    private val clientId: String = UUID.randomUUID().toString(),
    private val client: OkHttpClient = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false).pingInterval(20, TimeUnit.SECONDS).build(),
) {
    private val heartbeat = Executors.newSingleThreadScheduledExecutor()
    private val connectionId = UUID.randomUUID().toString()
    private var sequence = 0L
    private var attachment: String? = null
    private val pending = java.util.concurrent.ConcurrentHashMap<String, String>()
    private val sources = java.util.concurrent.ConcurrentHashMap<String, String>()
    private val received = mutableSetOf<String>()
    private var socket: WebSocket? = null
    private val clockStart = System.currentTimeMillis()
    @Volatile private var open = false

    /** The voice session's media clock, as the Worker's transcript cut expects. */
    fun nowMs() = System.currentTimeMillis() - clockStart

    fun connect() {
        val uri = java.net.URI(endpoint)
        require(uri.rawQuery == null && uri.rawUserInfo == null && uri.rawFragment == null)
        val request = Request.Builder().url(endpoint)
        if (real) {
            AppCredential.requireRealEndpoint(endpoint)
            require(credential != null && Regex("[A-Za-z0-9_-]{43,128}").matches(credential))
            request.header("Authorization", "Bearer $credential")
            heartbeat.schedule({ if (!open) { socket?.cancel(); finish("failed: frontend acceptance timed out") } }, 15, TimeUnit.SECONDS)
        }
        socket = client.newWebSocket(request.build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                if (!real) { open = true; onOpen() }
                else onMessage(Inbound.Status("Real frontend socket open · awaiting acceptance"))
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (!real) { parse(text)?.let(onMessage); return }
                try {
                    require(text.length <= 65536)
                    val m = JSONObject(text)
                    require(m.getInt("v") == 1)
                    when (m.getString("type")) {
                        "frontend.ready" -> {
                            require(attachment == null)
                            attachment = m.getString("attachment")
                            open = true; onOpen()
                            onMessage(Inbound.Status(if (m.getBoolean("agent_ready")) "Real frontend accepted · Agent connected" else "Real frontend accepted · Agent still connecting"))
                            heartbeat.scheduleWithFixedDelay({ renewLease() }, 0, 20, TimeUnit.SECONDS)
                        }
                        "frontend.accepted" -> {
                            require(m.getString("attachment") == attachment)
                            val id = m.getString("id")
                            pending.remove(id)?.let { onMessage(Inbound.Status("Server accepted $it ($id): not completion or authorization")) }
                        }
                        "frontend.event" -> {
                            require(open && m.getString("attachment") == attachment)
                            val id = m.getString("id")
                            if (!received.add(id)) return
                            val event = m.getJSONObject("event")
                            val inbound = parse(event.toString())
                            if (inbound is Inbound.Say) onMessage(inbound.copy(sourceId = id)) else inbound?.let(onMessage)
                        }
                        else -> error("unsupported protocol")
                    }
                } catch (_: Exception) { webSocket.close(1008, "invalid server protocol"); finish("failed: invalid server protocol") }
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(1000, null) }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { finish("closed: $reason") }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                finish(when (response?.code) {
                    401 -> "failed: app credential rejected"
                    404 -> "failed: app trial is disabled or endpoint is unavailable"
                    409 -> "failed: another frontend is attached, or the configured owner/assignment is unavailable"
                    503 -> "failed: existing sandbox unavailable; no replacement created"
                    else -> failureReason(t)
                })
            }
        })
    }

    private fun renewLease() {
        val generation = attachment ?: return
        if (!open) return
        val url = endpoint.replaceFirst("wss://", "https://").removeSuffix("/app/frontend") + "/app/heartbeat"
        val request = Request.Builder().url(url).header("Authorization", "Bearer $credential")
            .header("x-dogwalk-attachment", generation).post(ByteArray(0).toRequestBody()).build()
        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) { socket?.cancel(); finish("failed: authentication heartbeat unavailable") }
            override fun onResponse(call: Call, response: Response) { response.use { if (!it.isSuccessful) { socket?.close(1008, "authentication lease ended"); finish("failed: authentication lease ended") } } }
        })
    }

    @Synchronized private fun finish(reason: String) {
        heartbeat.shutdownNow()
        if (open || socket != null) {
            open = false; socket?.cancel(); socket = null
            val unknown = pending.size; pending.clear()
            onClosed("$reason · $unknown unacknowledged events: acceptance unknown; never automatically resent")
        }
        client.dispatcher.cancelAll()
        client.connectionPool.evictAll()
        client.dispatcher.executorService.shutdown()
    }

    fun bindRequest(localId: String, sourceId: String?) { if (sourceId != null) sources[localId] = sourceId }

    fun callerSaid(text: String, startMs: Long, endMs: Long, typed: Boolean = false) =
        send(transcript("input", text, startMs, endMs, if (typed) "text_typed" else "speech_recognized"))

    // Existing transcript envelopes and clock positions, not audio durations. No spoken_chars.
    // The app-local delivery field makes the provenance explicit to fixtures/future adapters.
    fun textShown(u: Utterance, atMs: Long) =
        send(receipt(transcript("output", u.text, atMs, atMs, "text_displayed"), u.requestId, true))

    /** Only words the caller actually heard: the full text, or the prefix before an interruption. */
    fun voiceHeard(text: String, startMs: Long, endMs: Long, requestId: String? = null, complete: Boolean = false) =
        send(receipt(transcript("output", text, startMs, endMs, "speech_played"), requestId, complete))

    private fun receipt(event: JSONObject, requestId: String?, complete: Boolean): JSONObject = event
        .put("request_id", requestId?.let { sources[it] } ?: JSONObject.NULL).put("complete", complete)

    /** A Handoff carries no task text: the Orchestrator reads the transcript up to offset_ms. */
    fun handoff(delegationId: String, offsetMs: Long) =
        send(JSONObject().put("type", "session.delegation.created").put("offset_ms", offsetMs)
            .put("delegation", JSONObject().put("id", delegationId).put("target", "client")))

    @Synchronized private fun send(json: JSONObject): Boolean {
        if (!open) return false
        if (!real) return socket?.send(json.toString()) ?: false
        val id = "$clientId-$connectionId-${++sequence}"
        val kind = json.getString("type")
        pending[id] = kind
        val sent = socket?.send(JSONObject().put("v", 1).put("attachment", attachment).put("id", id).put("event", json).toString()) ?: false
        onMessage(Inbound.Status(if (sent) "Pending server acceptance: $kind ($id)" else "Acceptance unknown: $kind ($id). Not resent."))
        if (!sent) finish("failed: outgoing event not queued; acceptance unknown")
        return sent
    }

    @Synchronized fun close() {
        heartbeat.shutdownNow(); socket?.close(1000, "frontend stopped"); socket = null; open = false
        client.dispatcher.cancelAll(); client.connectionPool.evictAll(); client.dispatcher.executorService.shutdown()
    }

    companion object {
        fun failureReason(error: Throwable): String =
            if (error is java.net.UnknownServiceException && error.message.orEmpty().contains("cleartext", ignoreCase = true))
                "failed: Android blocked cleartext traffic. Use wss:// or an approved debug fixture host."
            else "failed: ${error.javaClass.simpleName}"

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
                "frontend.agent_status" -> Inbound.Status("Agent ${m.optString("status")}")
                "session.input_audio.mute", "session.input_audio.unmute", "session.output_audio.fixture.play" -> Inbound.Unsupported(type)
                else -> null
            }
        }
    }
}
