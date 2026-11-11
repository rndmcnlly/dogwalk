package dogwalk.ondevice

import org.junit.Assert.*
import org.junit.Test

class PresentationTest {
    @Test fun connectedDoesNotMeanReady() {
        val s = PresentationState(link = LinkState.CONNECTED, local = LocalState.LOADING, accepting = true)
        assertFalse(s.canSend)
        assertTrue(s.copy(local = LocalState.READY).canSend)
        assertFalse(s.copy(local = LocalState.READY, mode = InteractionMode.VOICE).canSend)
        assertFalse(s.copy(local = LocalState.READY, accepting = false).canSend)
        assertFalse(s.copy(local = LocalState.INSTALLED).canSend)
    }
    @Test fun sourceSequenceAndObservedClockAreNotProviderTime() {
        val timeline = Timeline(clock = { 1234L })
        timeline.append(EventKind.READINESS, "Warming")
        timeline.append(EventKind.USER, "hello", provenance = "Typed")
        timeline.append(EventKind.DIAGNOSTIC, "Background updated")
        timeline.append(EventKind.ASSISTANT, "first", requestId = "r1")
        timeline.append(EventKind.ASSISTANT, "second", requestId = "r2")
        val items = timeline.snapshot()
        assertEquals(listOf(1L, 2L, 3L, 4L, 5L), items.map { it.sequence })
        assertTrue(items.all { it.observedAt == 1234L })
        assertEquals(listOf("r1", "r2"), items.filter { it.kind == EventKind.ASSISTANT }.map { it.requestId })
    }
    @Test fun boundedRetentionAndStableOutputIdentity() {
        val timeline = Timeline(limit = 2)
        timeline.append(EventKind.DIAGNOSTIC, "old")
        timeline.append(EventKind.USER, "new")
        val a = timeline.append(EventKind.ASSISTANT, "reply", id = "u1")
        val b = timeline.append(EventKind.ASSISTANT, "reply", id = "u1")
        assertEquals(a, b)
        assertEquals(2, timeline.snapshot().size)
        assertEquals(listOf("new", "reply"), timeline.snapshot().map { it.summary })
    }
    @Test fun modalAndHiddenWindowNeverAcknowledgeDisplay() {
        assertTrue(DisplayPolicy.visible(true, false, true))
        assertFalse(DisplayPolicy.visible(true, true, true))
        assertFalse(DisplayPolicy.visible(false, false, true))
        assertFalse(DisplayPolicy.visible(true, false, false))
    }
    @Test fun failureDoesNotClaimAudioStopped() {
        val voice = PresentationState(mode = InteractionMode.VOICE, microphone = true, speaker = true)
        val drop = voice.copy(link = LinkState.DISCONNECTED, accepting = false)
        assertTrue(drop.microphone); assertTrue(drop.speaker); assertFalse(drop.canSend)
    }
}
