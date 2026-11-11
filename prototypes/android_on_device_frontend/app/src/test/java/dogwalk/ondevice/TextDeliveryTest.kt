package dogwalk.ondevice

import org.junit.Assert.*
import org.junit.Test

class TextDeliveryTest {
    @Test fun textTranscriptUsesExistingEnvelopeWithExplicitProvenanceNotSpeechDuration() {
        val m = OrchestratorLink.transcript("output", "Hello", 120, 120, "text_displayed")
        assertEquals("session.output_transcript.delta", m.getString("type"))
        assertEquals("text_displayed", m.getString("delivery"))
        assertEquals(m.getLong("start_ms"), m.getLong("end_ms"))
        assertFalse(m.has("spoken_chars"))
        assertEquals("text_typed", OrchestratorLink.transcript("input", "hi", 100, 100, "text_typed").getString("delivery"))
    }

    private fun phrase(core: FrontendCore): Utterance {
        val job = core.nextJob() as PhraseJob
        return core.completePhrase(job, null)!!
    }

    @Test fun silentByDefaultAndNoAudioAdaptersOrCallbacksNeeded() {
        val core = FrontendCore()
        var microphoneStarts = 0
        var speechCalls = 0
        core.voiceOnly { microphoneStarts++ }
        core.voiceOnly { speechCalls++ } // readiness/error announcement adapter
        val request = core.onSay("d1", "Hello from Fir.")
        val u = phrase(core)
        val receipt = core.deliver(u, { true }, { speechCalls++ })!!
        assertEquals(u, receipt.utterance)
        assertEquals(0, microphoneStarts)
        assertEquals(0, speechCalls)
        assertEquals(RequestStatus.TEXT_DISPLAYED, request.status)
        assertEquals("", request.heardPrefix)
        assertNull(core.playbackFinished(u.id, true, u.text.length))
    }

    @Test fun eachQueuedRequestNeedsItsOwnDisplayAndCannotBeCollapsed() {
        val core = FrontendCore()
        val a = core.onSay("d1", "Oak finished forty tests.")
        val b = core.onSay("d1", "Fir asks: staging or production?")
        val u = phrase(core)
        assertNull(core.textDisplayed("unknown"))
        assertNull(core.textDisplayed(b.id))
        assertEquals(RequestStatus.PENDING, a.status)
        assertEquals(a.id, core.textDisplayed(u.id)!!.utterance.requestId)
        assertNull(core.textDisplayed(u.id))
        assertEquals(RequestStatus.PENDING, b.status)
        val second = phrase(core)
        assertEquals(b.id, second.requestId)
        assertEquals(b.id, core.textDisplayed(second.id)!!.utterance.requestId)
        assertTrue(core.pendingRequests().isEmpty())
    }

    @Test fun noActiveDisplayMeansNoReceiptAndSourceStaysPending() {
        val core = FrontendCore()
        val r = core.onSay(null, "Fir asks for permission. Allow once or deny?")
        val u = phrase(core)
        assertNull(core.deliver(u, { false }, { fail("audio") }))
        assertEquals(RequestStatus.PENDING, r.status)
        assertEquals(u, core.playing)
        assertNotNull(core.deliver(u, { true }, { fail("audio") }))
    }

    @Test fun displayedRequestsNeverReplayOnVoiceSwitch() {
        val core = FrontendCore()
        val r = core.onSay(null, "Oak finished.")
        core.textDisplayed(phrase(core).id)
        core.switchMode(InteractionMode.VOICE)
        assertEquals(RequestStatus.TEXT_DISPLAYED, r.status)
        assertNull(core.nextJob())
    }

    @Test fun modeSwitchInvalidatesOldPhrasingWithoutDroppingEitherRequest() {
        val core = FrontendCore()
        core.switchMode(InteractionMode.VOICE)
        val a = core.onSay(null, "Oak finished.")
        val b = core.onSay(null, "Fir has a question?")
        val old = core.nextJob() as PhraseJob
        core.switchMode(InteractionMode.TEXT)
        assertNull(core.completePhrase(old, "Oak finished."))
        assertEquals(listOf(a, b), core.pendingRequests())
        assertEquals(a.id, phrase(core).requestId)
    }

    @Test fun lateVoiceReceiptCannotCompleteAnUndeliveredTextRequest() {
        val core = FrontendCore()
        core.switchMode(InteractionMode.VOICE)
        val request = core.onSay(null, "Fir needs permission. Allow once or deny?")
        val old = phrase(core)
        core.switchMode(InteractionMode.TEXT)
        assertNull(core.playbackFinished(old.id, true, old.text.length))
        assertNull(core.textDisplayed(old.id))
        assertEquals(RequestStatus.PENDING, request.status)
        val current = phrase(core)
        assertNotEquals(old.id, current.id)
        assertNotNull(core.textDisplayed(current.id))
    }

    @Test fun latestTypedIntentWinsOverStaleDecisionAndPendingSaySurvives() {
        val core = FrontendCore()
        val request = core.onSay(null, "Oak finished.")
        core.onCaller("hello", typed = true)
        val old = core.nextJob() as DecideJob
        core.onCaller("how are you", typed = true)
        assertNull(core.completeDecide(old, "REPLY: Hello."))
        val next = core.nextJob() as DecideJob
        assertEquals("how are you", next.utterance)
        val reply = core.completeDecide(next, "REPLY: I am well.") as Decision.Reply
        core.textDisplayed(reply.utterance.id)
        assertEquals(RequestStatus.PENDING, request.status)
        assertEquals(request.id, phrase(core).requestId)
    }

    @Test fun typedCorrectionsInvalidateUndisplayedTextNotItsSource() {
        val core = FrontendCore()
        val r = core.onSay(null, "Fir is starting.")
        val old = phrase(core)
        core.abandonUndeliveredText()
        assertTrue(core.onCaller("cancel that", typed = true) is CallerRoute.Handoff)
        assertNull(core.textDisplayed(old.id))
        assertEquals(RequestStatus.PENDING, r.status)
        assertEquals(r.id, phrase(core).requestId)
    }

    @Test fun typedAndDisplayedHistoryIsNotLabelledHeardAndBriefsStayBackground() {
        val core = FrontendCore()
        core.onBrief("Secret background fact.")
        core.onBrief("Following Fir: compiling.")
        assertNull(core.nextJob())
        core.onCaller("hello", typed = true)
        val decide = core.nextJob() as DecideJob
        assertTrue(decide.prompt.contains("Caller (typed)"))
        val reply = core.completeDecide(decide, "REPLY: Hello.") as Decision.Reply
        core.textDisplayed(reply.utterance.id)
        core.onSay(null, "Oak finished.")
        val prompt = (core.nextJob() as PhraseJob).prompt
        assertTrue(prompt.contains("displayed text, not known read"))
        assertTrue(prompt.contains("know it, do not recite it"))
    }
}
