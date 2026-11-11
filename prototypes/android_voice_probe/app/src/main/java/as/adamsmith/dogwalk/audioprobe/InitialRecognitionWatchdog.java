package as.adamsmith.dogwalk.audioprobe;

/** Timer decisions only: intentional half-duplex playback is not recognition startup time. */
final class InitialRecognitionWatchdog {
    private boolean ready, suspended;

    boolean suspendForSpeech() {
        if (ready) return false;
        suspended = true;
        return true;
    }

    boolean resumeForListen() {
        if (ready || !suspended) return false;
        suspended = false;
        return true;
    }

    void recognitionReady() { ready = true; suspended = false; }
}
