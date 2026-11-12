package as.adamsmith.dogwalk.audioprobe;

import java.util.HashSet;
import java.util.Set;

/** Pure protocol state: connection lifetime is guarded separately by FrontendClient. */
final class FrontendState {
    private final Set<String> received = new HashSet<>(), started = new HashSet<>(), finished = new HashSet<>();
    private String latestTurn;
    private boolean userSpeaking;

    void userSpeechStarted() { userSpeaking = true; latestTurn = null; }
    void transcript(String turn) { userSpeaking = false; latestTurn = turn; }
    void recognitionIdle() { userSpeaking = false; }
    boolean receive(String id) { return !id.isBlank() && received.add(id); }
    boolean maySpeak(String turn) { return !userSpeaking && (turn == null || turn.equals(latestTurn)); }
    boolean start(String id) { return !finished.contains(id) && started.add(id); }
    boolean finish(String id) { return id != null && finished.add(id); }
}
