package dogwalk.ondevice

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import okhttp3.*
import okhttp3.mockwebserver.*
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.net.InetAddress
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Emulator-only test APK: no microphone, TTS, model download, or production traffic. */
@RunWith(AndroidJUnit4::class)
class TransportRuntimeTest {
    private fun emulatorOnly() {
        check(android.os.Build.FINGERPRINT.contains("generic") || android.os.Build.MODEL.contains("sdk_gphone")) { "Explicit emulator required" }
    }

    @Test fun keystoreEncryptsAndBindsExactEndpoint() {
        emulatorOnly()
        // Isolated private test directory: never replace the installed app's credential.
        val context = object : android.content.ContextWrapper(InstrumentationRegistry.getInstrumentation().targetContext) {
            override fun getNoBackupFilesDir() = java.io.File(super.getNoBackupFilesDir(), "runtime-test-only").apply { mkdirs() }
        }
        val store = AppCredential(context)
        val endpoint = "wss://localhost:18443/app/frontend"
        val token = "runtime-test-not-a-live-credential-" + "x".repeat(24)
        store.clear()
        try {
            store.save(endpoint, token)
            assertEquals(token, AppCredential(context).load(endpoint))
            val ciphertext = java.io.File(context.noBackupFilesDir, "app-credential").readText()
            assertFalse(ciphertext.contains(token))
            for (wrong in listOf("wss://localhost:18444/app/frontend", "ws://localhost:18443/app/frontend", "$endpoint?token=wrong")) {
                assertTrue(runCatching { store.load(wrong) }.isFailure)
            }
            assertEquals(token, store.load(endpoint))
        } finally { store.clear() }
        assertTrue(runCatching { store.load(endpoint) }.isFailure)
    }

    @Test fun actualClientUsesTlsReadyAcknowledgmentsHeartbeatAndBoundReceipts() {
        emulatorOnly()
        // This private test CA lives only in the instrumentation APK and this client instance.
        // Shipping defaults and network-security resources remain untouched.
        val cert = HeldCertificate.Builder().commonName("localhost").addSubjectAlternativeName("localhost").addSubjectAlternativeName("127.0.0.1").build()
        val serverTls = HandshakeCertificates.Builder().heldCertificate(cert).build()
        val testTls = HandshakeCertificates.Builder().addTrustedCertificate(cert.certificate).build()
        val client = OkHttpClient.Builder().sslSocketFactory(testTls.sslSocketFactory(), testTls.trustManager)
            .followRedirects(false).followSslRedirects(false).build()
        val server = MockWebServer()
        server.useHttps(serverTls.sslSocketFactory(), false)
        val sockets = LinkedBlockingQueue<WebSocket>()
        val frames = LinkedBlockingQueue<JSONObject>()
        server.dispatcher = object : okhttp3.mockwebserver.Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                "/app/frontend" -> MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                    override fun onOpen(webSocket: WebSocket, response: Response) { sockets.add(webSocket) }
                    override fun onMessage(webSocket: WebSocket, text: String) { frames.add(JSONObject(text)) }
                })
                "/app/heartbeat" -> MockResponse().setResponseCode(204)
                else -> MockResponse().setResponseCode(404)
            }
        }
        server.start(InetAddress.getByName("127.0.0.1"), 0)
        val events = LinkedBlockingQueue<Inbound>()
        val closed = LinkedBlockingQueue<String>()
        val opens = AtomicInteger()
        val token = "x".repeat(43) // public test value, not an operator credential
        val endpoint = server.url("/app/frontend").toString().replaceFirst("https:", "wss:")
        val untrustedFailure = LinkedBlockingQueue<String>()
        val shippingDefaults = OrchestratorLink(endpoint, {}, { untrustedFailure.add(it) }, {}, true, token)
        try {
            shippingDefaults.connect()
            assertTrue(untrustedFailure.poll(10, TimeUnit.SECONDS)!!.contains("SSLHandshakeException"))
            assertNull(sockets.poll())
        } finally { shippingDefaults.close() }
        val link = OrchestratorLink(endpoint, { events.add(it) }, { closed.add(it) }, { opens.incrementAndGet() }, true, token, "test-client", client)
        fun status(part: String) {
            val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
            while (System.nanoTime() < deadline) {
                val event = events.poll(1, TimeUnit.SECONDS)
                if (event is Inbound.Status && event.summary.contains(part)) return
            }
            fail("Expected sanitized status: $part")
        }
        try {
            link.connect()
            val remote = sockets.poll(10, TimeUnit.SECONDS) ?: error("TLS WebSocket did not open: ${closed.poll()}")
            val upgrade = server.takeRequest(5, TimeUnit.SECONDS)!!
            assertEquals("Bearer $token", upgrade.getHeader("Authorization"))
            assertEquals("/app/frontend", upgrade.path)
            status("awaiting acceptance")
            assertEquals(0, opens.get())
            assertNull(server.takeRequest(300, TimeUnit.MILLISECONDS))
            remote.send("""{"v":1,"type":"frontend.ready","attachment":"runtime-generation","agent_ready":false,"lease_ms":60000}""")
            status("Agent still connecting")
            assertEquals(1, opens.get())
            val heartbeat = server.takeRequest(5, TimeUnit.SECONDS)!!
            assertEquals("/app/heartbeat", heartbeat.path)
            assertEquals("POST", heartbeat.method)
            assertEquals("Bearer $token", heartbeat.getHeader("Authorization"))
            assertEquals("runtime-generation", heartbeat.getHeader("X-Dogwalk-Attachment"))
            link.callerSaid("Check Fir", 1, 1, true)
            val input = frames.poll(5, TimeUnit.SECONDS)!!
            assertEquals("runtime-generation", input.getString("attachment"))
            assertEquals("text_typed", input.getJSONObject("event").getString("delivery"))
            remote.send(JSONObject().put("v", 1).put("type", "frontend.accepted").put("attachment", "runtime-generation").put("id", input.getString("id")).toString())
            status("Server accepted")
            remote.send("""{"v":1,"type":"frontend.event","attachment":"runtime-generation","id":"source-one","event":{"type":"session.commentary.append","delegation_id":null,"content":"Fir needs permission."}}""")
            val say = events.poll(5, TimeUnit.SECONDS) as Inbound.Say
            assertNull(say.delegationId); assertEquals("source-one", say.sourceId)
            link.bindRequest("local-request", say.sourceId)
            link.textShown(Utterance("u1", say.content, "local-request", UtteranceKind.VERBATIM), 2)
            val receipt = frames.poll(5, TimeUnit.SECONDS)!!.getJSONObject("event")
            assertEquals("source-one", receipt.getString("request_id"))
            assertEquals("text_displayed", receipt.getString("delivery"))
            assertTrue(receipt.getBoolean("complete"))
            assertFalse(receipt.has("spoken_chars"))
            // Receipt deliberately remains unacknowledged: loss is unknown, never replayed.
            remote.close(1000, "test disconnect")
            val loss = closed.poll(5, TimeUnit.SECONDS)!!
            assertTrue(loss.contains("1 unacknowledged")); assertTrue(loss.contains("never automatically resent"))
            assertFalse(loss.contains(token))
        } finally { link.close(); server.shutdown() }
    }

    @Test fun fixtureTransportNeverSendsSavedCredentialAndRemainsUsable() {
        emulatorOnly()
        val server = MockWebServer()
        val frames = LinkedBlockingQueue<JSONObject>()
        val events = LinkedBlockingQueue<Inbound>()
        val ready = LinkedBlockingQueue<Boolean>()
        server.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) { webSocket.send("""{"type":"dev.fixture.ready","description":"isolated runtime fixture"}""") }
            override fun onMessage(webSocket: WebSocket, text: String) { frames.add(JSONObject(text)) }
        }))
        server.start(InetAddress.getByName("127.0.0.1"), 0)
        val link = OrchestratorLink(server.url("/ws").toString().replaceFirst("http:", "ws:"), { events.add(it) }, {}, { ready.add(true) }, false, "x".repeat(43))
        try {
            link.connect(); assertEquals(true, ready.poll(5, TimeUnit.SECONDS))
            assertNull(server.takeRequest(5, TimeUnit.SECONDS)!!.getHeader("Authorization"))
            assertTrue(events.poll(5, TimeUnit.SECONDS) is Inbound.Fixture)
            link.callerSaid("Fixture status", 1, 1, true)
            val input = frames.poll(5, TimeUnit.SECONDS)!!
            assertEquals("session.input_transcript.delta", input.getString("type"))
            assertEquals("text_typed", input.getString("delivery")); assertFalse(input.has("attachment"))
        } finally { link.close(); server.shutdown() }
    }
}
