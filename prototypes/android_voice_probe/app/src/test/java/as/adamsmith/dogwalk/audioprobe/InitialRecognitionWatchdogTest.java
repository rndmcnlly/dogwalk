package as.adamsmith.dogwalk.audioprobe;

import org.junit.Test;
import static org.junit.Assert.*;

public class InitialRecognitionWatchdogTest {
    @Test public void longInitialSpeechSuspendsUntilFirstActualListenRequest() {
        InitialRecognitionWatchdog watchdog = new InitialRecognitionWatchdog();
        assertFalse(watchdog.resumeForListen()); // Initial startup timer is already armed.
        assertTrue(watchdog.suspendForSpeech()); // Remove it before intentional playback.
        assertTrue(watchdog.suspendForSpeech()); // A replacement reply remains suspended.
        assertTrue(watchdog.resumeForListen()); // Rearm only when listening can actually start.
        assertFalse(watchdog.resumeForListen()); // Retries must not keep extending the bound.
    }

    @Test public void repeatedInitialRepliesEachSuspendAndRearm() {
        InitialRecognitionWatchdog watchdog = new InitialRecognitionWatchdog();
        assertTrue(watchdog.suspendForSpeech());
        assertTrue(watchdog.resumeForListen());
        assertTrue(watchdog.suspendForSpeech());
        assertTrue(watchdog.resumeForListen());
    }

    @Test public void firstRecognitionReadyPermanentlyDisarmsStartupWatchdog() {
        InitialRecognitionWatchdog watchdog = new InitialRecognitionWatchdog();
        watchdog.suspendForSpeech();
        watchdog.recognitionReady();
        assertFalse(watchdog.resumeForListen());
        assertFalse(watchdog.suspendForSpeech());
        assertFalse(watchdog.resumeForListen());
    }
}
