package dogwalk.ondevice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FrontendTest {
    @Test fun workPermissionCancelAndEndAreHandoffsBeforeAnyModel() {
        for (text in listOf("cancel that", "yes, allow it", "what is the session doing", "start a build", "goodbye", "deny")) {
            val core = FrontendCore()
            assertTrue(text, core.onCaller(text) is CallerRoute.Handoff)
            assertNull(text, core.nextJob())
        }
    }

    @Test fun smallTalkGoesToTheModelAndOnlyACleanReplyStaysLocal() {
        val core = FrontendCore()
        assertEquals(CallerRoute.Model, core.onCaller("hi there, how are you"))
        val job = core.nextJob() as DecideJob
        assertTrue(job.prompt.contains("Current caller input: \"hi there, how are you\""))
        val reply = core.completeDecide(job, "REPLY: I'm well, thanks for asking.") as Decision.Reply
        assertEquals(UtteranceKind.REPLY, reply.utterance.kind)
    }

    @Test fun malformedOrClaimingRepliesBecomeHandoffs() {
        for (raw in listOf("Sure thing", "", null, "REPLY: I've started that for you.", "HANDOFF")) {
            val core = FrontendCore()
            core.onCaller("hmm, okay then")
            val job = core.nextJob() as DecideJob
            assertTrue("$raw", core.completeDecide(job, raw) is Decision.Handoff)
        }
    }

    @Test fun oneRequestOneJobOneReceipt() {
        val core = FrontendCore()
        val a = core.onSay("d1", "Fir needs permission to delete the build folder. Allow once or deny?")
        val b = core.onSay(null, "Oak finished the tests.")
        val first = core.nextJob() as PhraseJob
        assertEquals(a.id, first.requestId)
        assertTrue(first.prompt.contains(a.text) && !first.prompt.contains(b.text))
        val u = core.completePhrase(first, "Fir wants to delete the build folder. Allow once, or deny?")!!
        assertNull("no second job while playing", core.nextJob())
        assertTrue(core.playbackFinished(u.id, true, null)!!.complete)
        assertNull("duplicate receipt", core.playbackFinished(u.id, true, null))
        assertEquals(RequestStatus.PLAYBACK_COMPLETE, a.status)
        assertEquals(RequestStatus.PENDING, b.status)
        assertEquals(b.id, (core.nextJob() as PhraseJob).requestId)
    }

    @Test fun interruptionKeepsTheRequestPendingWithOnlyTheHeardPrefix() {
        val core = FrontendCore()
        val r = core.onSay(null, "x")
        val u = core.completePhrase(core.nextJob() as PhraseJob, "Fir is asking to delete the build folder")!!
        val heard = core.playbackFinished(u.id, false, 15)!!
        assertEquals("Fir is asking", heard.text)
        assertEquals(RequestStatus.PENDING, r.status)
        assertTrue((core.nextJob() as PhraseJob).prompt.contains("heard only the start"))
    }

    @Test fun newCallerInputMakesInFlightPhrasingStaleAndTakesPriority() {
        val core = FrontendCore()
        val r = core.onSay(null, "Oak finished.")
        val job = core.nextJob() as PhraseJob
        core.onCaller("sorry, what was that")
        assertNull(core.completePhrase(job, "Oak is done."))
        assertEquals(RequestStatus.PENDING, r.status)
        assertTrue(core.nextJob() is DecideJob)
    }

    @Test fun localStopHoldsSpeechWithoutHandoffOrRetirement() {
        val core = FrontendCore()
        val r = core.onSay(null, "Oak finished.")
        core.completePhrase(core.nextJob() as PhraseJob, "Oak is done.")
        assertEquals(CallerRoute.LocalStop, core.onCaller("stop talking"))
        core.playbackFinished(core.playing!!.id, false, 0)
        assertNull("held until the caller speaks again", core.nextJob())
        assertEquals(RequestStatus.PENDING, r.status)
        assertTrue(core.onCaller("okay go on") is CallerRoute.Model)
    }

    @Test fun bareStopWithNothingSpeakingIsAHandoffBecauseItMayMeanWork() {
        assertTrue(FrontendCore().onCaller("stop") is CallerRoute.Handoff)
    }

    @Test fun unusablePhrasingFallsBackToTheOrchestratorsOwnWords() {
        val core = FrontendCore()
        core.onSay(null, "Oak finished the tests.")
        val u = core.completePhrase(core.nextJob() as PhraseJob, "see https://example.com")!!
        assertEquals(UtteranceKind.VERBATIM, u.kind)
        assertEquals("Oak finished the tests.", u.text)
    }

    @Test fun briefAndFollowingAreRetainedSeparately() {
        val core = FrontendCore()
        core.onBrief("Fir is refactoring the parser.")
        core.onBrief("Following Fir: running the tests.")
        core.onSay(null, "Tell them hello.")
        val prompt = (core.nextJob() as PhraseJob).prompt
        assertTrue(prompt.contains("Background from your thinking (know it, do not recite it):\n- \"Fir is refactoring the parser.\""))
        assertTrue(prompt.contains("Followed session, what it is doing now: \"Following Fir: running the tests.\""))
    }

    @Test fun inboundParsingMatchesTheWorkerShapes() {
        assertEquals(Inbound.Say("d1", "hi"), OrchestratorLink.parse("""{"type":"session.commentary.append","delegation_id":"d1","content":"hi"}"""))
        assertEquals(Inbound.Brief(null, "ctx"), OrchestratorLink.parse("""{"type":"session.thinking.append","delegation_id":null,"content":"ctx"}"""))
        assertEquals(Inbound.Unsupported("session.input_audio.mute"), OrchestratorLink.parse("""{"type":"session.input_audio.mute"}"""))
        assertNull(OrchestratorLink.parse("not json"))
    }

    @Test fun phrasingThatDropsTheQuestionOrNamesFallsBackVerbatim() {
        val ask = "Fir wants permission to delete the build folder. Should I allow it once, or deny it?"
        for (bad in listOf("REPLY: Hello.", "Hello.", "Someone wants to delete a folder. Allow once or deny?", "Fir wants to delete the build folder.",
            // Observed from on-device E2B: keeps the name but turns "deny" into "stop it".
            "Fir needs permission to delete the build folder before rebuilding. Should I let it go through once, or stop it?")) {
            val core = FrontendCore()
            core.onSay(null, ask)
            val u = core.completePhrase(core.nextJob() as PhraseJob, bad)!!
            assertEquals(bad, UtteranceKind.VERBATIM, u.kind)
            assertEquals(ask, u.text)
        }
        val core = FrontendCore()
        core.onSay(null, "Oak finished the test suite, and all forty tests passed.")
        assertEquals("dropped number forty", Policy.coverageProblem("Oak finished, forty passed.", "Oak finished and everything passed."))
        val ok = core.completePhrase(core.nextJob() as PhraseJob, "Oak's test suite is done: all forty tests passed.")!!
        assertEquals(UtteranceKind.PHRASED, ok.kind)
    }
}
