package dogwalk.ondevice

import org.junit.Assert.*
import org.junit.Test

class AppTransportTest {
    @Test fun realTransportRequiresExactSecureNonCredentialUrl() {
        AppCredential.requireRealEndpoint("wss://dogwalk.tools/app/frontend")
        for (url in listOf("ws://127.0.0.1/app/frontend", "https://dogwalk.tools/app/frontend",
            "wss://dogwalk.tools/app/frontend?token=x", "wss://user:secret@dogwalk.tools/app/frontend",
            "wss://dogwalk.tools/app/frontend#secret", "wss://dogwalk.tools/fixture")) {
            assertTrue(url, runCatching { AppCredential.requireRealEndpoint(url) }.isFailure)
        }
    }

    @Test fun unsolicitedSayHasSeparateTransportSourceIdentity() {
        val say = OrchestratorLink.parse("""{"type":"session.commentary.append","delegation_id":null,"content":"Fir needs permission."}""") as Inbound.Say
        assertNull(say.delegationId)
        val bound = say.copy(sourceId = "server-source")
        assertEquals("server-source", bound.sourceId)
        assertNull(say.sourceId)
    }

    @Test fun detachmentDiscardsOldRequestsAndInvalidatesInferenceWithoutCancellingWork() {
        val core = FrontendCore()
        core.onSay(null, "Fir is working.")
        val oldEpoch = core.epoch
        assertEquals(1, core.pendingRequests().size)
        core.detach()
        assertTrue(core.epoch > oldEpoch)
        assertTrue(core.pendingRequests().isEmpty())
        assertNull(core.playing)
        assertNull(core.inFlight)
        assertEquals(InteractionMode.TEXT, core.mode)
    }

    @Test fun agentReadinessIsNotAChatUtterance() {
        val status = OrchestratorLink.parse("""{"type":"frontend.agent_status","status":"reconnecting"}""")
        assertTrue(status is Inbound.Status)
        assertEquals("Agent reconnecting", (status as Inbound.Status).summary)
    }
}
