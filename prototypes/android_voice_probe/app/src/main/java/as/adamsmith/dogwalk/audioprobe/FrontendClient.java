package as.adamsmith.dogwalk.audioprobe;

import android.os.Handler;
import android.os.Looper;
import org.json.JSONException;
import org.json.JSONObject;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import okhttp3.*;

/** Text/control only. All state and callbacks are confined to the main looper. */
final class FrontendClient implements AutoCloseable {
    interface Listener {
        void speak(String speechId, String text);
        void status(String message);
        void failed(String message);
    }

    private final Handler main = new Handler(Looper.getMainLooper());
    private final Listener listener;
    private final OkHttpClient http = new OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS).readTimeout(0, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false).followRedirects(false).followSslRedirects(false).build();
    private final FrontendState state = new FrontendState();
    private WebSocket socket;
    private boolean active = true, ready;
    private int generation;

    FrontendClient(String endpoint, String token, Listener listener) {
        this.listener = listener;
        int connection = ++generation;
        try {
            if (!(endpoint.startsWith("ws://") || endpoint.startsWith("wss://")) || token.isBlank())
                throw new IllegalArgumentException();
            Request request = new Request.Builder().url(endpoint).header("Authorization", "Bearer " + token).build();
            // Do not allow credentials in endpoint URLs, redirects, or diagnostic exceptions.
            if (!request.url().username().isEmpty() || !request.url().password().isEmpty()
                    || request.url().query() != null || request.url().fragment() != null)
                throw new IllegalArgumentException();
            socket = http.newWebSocket(request, new WebSocketListener() {
                private void dispatch(Runnable action) {
                    main.post(() -> { if (active && connection == generation) action.run(); });
                }
                @Override public void onMessage(WebSocket ws, String text) { dispatch(() -> receive(text)); }
                @Override public void onFailure(WebSocket ws, Throwable error, Response response) {
                    dispatch(() -> fail("Gateway connection failed. Stop and Start to reconnect."));
                }
                @Override public void onClosing(WebSocket ws, int code, String reason) {
                    ws.close(code, null);
                    dispatch(() -> fail("Gateway disconnected. Stop and Start to reconnect."));
                }
                @Override public void onClosed(WebSocket ws, int code, String reason) {
                    dispatch(() -> fail("Gateway disconnected. Stop and Start to reconnect."));
                }
            });
            main.postDelayed(() -> {
                if (active && connection == generation && !ready) fail("Gateway ready timeout.");
            }, 15000);
        } catch (RuntimeException error) {
            main.post(() -> { if (active) fail("Invalid gateway configuration. Check endpoint and token."); });
        }
    }

    private void receive(String text) {
        try {
            JSONObject event = new JSONObject(text);
            switch (event.getString("type")) {
                case "ready" -> {
                    String mode = event.getString("mode");
                    if (event.getInt("protocol") != 1 || !(mode.equals("live") || mode.equals("fixture"))) {
                        fail("Unsupported gateway protocol."); return;
                    }
                    ready = true;
                    listener.status(mode.equals("fixture") ? "Gateway ready: FIXTURE (not Orchestrator integration)" : "Gateway ready: live");
                }
                case "speak" -> {
                    if (!ready) { fail("Gateway speech arrived before ready."); return; }
                    String id = event.getString("speech_id");
                    String speech = event.getString("text");
                    if (!state.receive(id)) return;
                    String turn = event.isNull("turn_id") ? null : event.getString("turn_id");
                    // Null means unsolicited, not an alias for the last turn. Never speak
                    // unsolicited text while the user is speaking or completing a correction.
                    if (!state.maySpeak(turn) || speech.isBlank()) {
                        playbackFinished(id, "interrupted"); return;
                    }
                    listener.speak(id, speech);
                }
                case "status" -> listener.status("Gateway status update");
                case "error" -> listener.status("Gateway reports Voice Frontend unavailable; try again");
                default -> listener.status("Unknown gateway event ignored");
            }
        } catch (JSONException | RuntimeException error) {
            // Never log server payloads or exception messages: either can echo credentials.
            fail("Malformed gateway event.");
        }
    }

    void userSpeechStarted() {
        if (!active) return;
        state.userSpeechStarted();
        send(object("user_speech_started"));
    }

    boolean transcript(String text) {
        String turn = UUID.randomUUID().toString();
        state.transcript(turn);
        if (!active || !ready) return false; // No buffering or transcript replay.
        return send(put(put(object("user_transcript"), "turn_id", turn), "text", text));
    }

    void recognitionIdle() {
        state.recognitionIdle();
        // Release the gateway's conversational floor even when recognition yields no text.
        // send() guards closed connections; idle cycles never enqueue transcripts or replay.
        send(put(object("user_speech_ended"), "outcome", "no_transcript"));
    }

    void playbackStarted(String id) {
        if (active && state.start(id)) send(put(object("speech_started"), "speech_id", id));
    }

    void playbackFinished(String id, String outcome) {
        if (!active || !state.finish(id)) return;
        send(put(put(put(object("speech_finished"), "speech_id", id), "outcome", outcome), "spoken_chars", JSONObject.NULL));
    }

    private static JSONObject object(String type) { return put(new JSONObject(), "type", type); }
    private static JSONObject put(JSONObject object, String name, Object value) {
        try { return object.put(name, value); }
        catch (JSONException error) { throw new IllegalArgumentException("Invalid protocol field"); }
    }
    private boolean send(JSONObject event) {
        return active && socket != null && socket.send(event.toString());
    }
    private void fail(String message) {
        if (!active) return;
        close();
        listener.failed(message);
    }
    @Override public void close() {
        if (!active) return;
        send(object("session_stop"));
        active = false;
        ++generation;
        main.removeCallbacksAndMessages(null);
        if (socket != null) {
            WebSocket previous = socket;
            previous.close(1000, "Frontend stopped");
            main.postDelayed(previous::cancel, 1000); // Bound close-handshake resource retention.
            socket = null;
        }
        http.dispatcher().executorService().shutdown();
        http.connectionPool().evictAll();
    }
}
