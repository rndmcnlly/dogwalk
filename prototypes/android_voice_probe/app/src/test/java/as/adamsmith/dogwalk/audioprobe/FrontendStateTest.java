package as.adamsmith.dogwalk.audioprobe;

import org.junit.Test;
import static org.junit.Assert.*;

public class FrontendStateTest {
    @Test public void correctionInvalidatesOldTurnBeforeFinal() {
        FrontendState state = new FrontendState();
        state.transcript("first");
        assertTrue(state.maySpeak("first"));
        state.userSpeechStarted();
        assertFalse(state.maySpeak("first"));
        assertFalse(state.maySpeak(null));
        state.transcript("second");
        assertFalse(state.maySpeak("first"));
        assertTrue(state.maySpeak("second"));
        assertTrue(state.maySpeak(null));
    }
    @Test public void noMatchDoesNotReviveSupersededTurn() {
        FrontendState state = new FrontendState();
        state.transcript("first");
        state.userSpeechStarted();
        state.recognitionIdle();
        assertFalse(state.maySpeak("first"));
        assertTrue(state.maySpeak(null));
    }
    @Test public void unsolicitedSpeechIsSafeBeforeAnyTranscript() {
        FrontendState state = new FrontendState();
        assertTrue(state.maySpeak(null));
        assertFalse(state.maySpeak("unknown"));
    }
    @Test public void playbackHasOneStartAndOneTerminalEvent() {
        FrontendState state = new FrontendState();
        assertTrue(state.receive("speech"));
        assertFalse(state.receive("speech"));
        assertTrue(state.start("speech"));
        assertFalse(state.start("speech"));
        assertTrue(state.finish("speech"));
        assertFalse(state.finish("speech"));
        assertFalse(state.start("speech"));
        assertFalse(state.finish(null));
    }
    @Test public void interruptionBeforePlaybackSuppressesLateStart() {
        FrontendState state = new FrontendState();
        assertTrue(state.receive("pending"));
        assertTrue(state.finish("pending"));
        assertFalse(state.start("pending"));
    }
}
