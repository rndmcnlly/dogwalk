package as.adamsmith.dogwalk.audioprobe;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.media.*;
import android.net.Uri;
import android.os.*;
import android.speech.*;
import android.speech.tts.*;
import android.view.View;
import android.widget.*;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.*;

/** Local audio probe with an optional text/control Voice Frontend gateway. */
public class MainActivity extends Activity {
    private final Handler ui = new Handler(Looper.getMainLooper());
    private TextView status;
    private Switch duplex;
    private Spinner route;
    private EditText endpoint, token;
    private boolean resumed, pendingStart, requestingPermissions;
    private final Runnable refresh = new Runnable() {
        public void run() {
            status.setText(ProbeLog.snapshot());
            ui.postDelayed(this, 1000);
        }
    };

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        ProbeLog.init(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        root.setPadding(pad, pad, pad, pad);
        // Target 35 uses edge-to-edge layout: keep the probe controls outside system bars.
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            android.graphics.Insets bars = insets.getInsets(android.view.WindowInsets.Type.systemBars()
                | android.view.WindowInsets.Type.displayCutout());
            root.setPadding(pad + bars.left, pad + bars.top, pad + bars.right, pad + bars.bottom);
            return insets;
        });
        TextView heading = new TextView(this);
        heading.setText("Dogwalk: local audio probe");
        heading.setTextSize(22);
        root.addView(heading);
        // Keep the safety controls pinned while settings and diagnostics scroll.
        button(root, "Start local audio", v -> requestStart());
        button(root, "Stop", v -> { pendingStart = false; stopService(new Intent(this, AudioService.class)); });
        TextView note = new TextView(this);
        note.setText("Requests on-device recognition and an offline TTS voice. No cloud speech fallback. "
            + "System speech engines are separate apps; their network behavior is not enforced here. Start, lock the screen, and talk. "
            + "Blank endpoint: deterministic probe replies. Network mode sends text/control to the gateway, never audio. "
            + "Say ‘stop listening’ or ‘stop the probe’ to stop locally. "
            + "Transcripts are saved locally in the diagnostic log.");
        column.addView(note);
        SharedPreferences settings = getSharedPreferences("frontend", MODE_PRIVATE);
        endpoint = new EditText(this);
        endpoint.setHint("Gateway endpoint (blank = local probe)");
        endpoint.setContentDescription("Gateway endpoint");
        endpoint.setSingleLine(true);
        endpoint.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        endpoint.setSaveEnabled(false);
        endpoint.setText(settings.getString("endpoint", ""));
        column.addView(endpoint);
        token = new EditText(this);
        token.setHint("Gateway bearer token (private)");
        token.setContentDescription("Gateway bearer token");
        token.setSingleLine(true);
        token.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        token.setSaveEnabled(false);
        token.setText(settings.getString("token", ""));
        column.addView(token);
        button(column, "Save gateway settings", v -> {
            saveSettings();
            Toast.makeText(this, "Saved privately. Stop then Start to apply.", Toast.LENGTH_SHORT).show();
        });
        duplex = new Switch(this);
        duplex.setText("Listen during replies (headphones only; experimental)");
        column.addView(duplex);
        route = new Spinner(this);
        route.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item,
            new String[]{"Prefer headset", "Speaker", "System default"}));
        column.addView(route);
        TextView options = new TextView(this);
        options.setText("Route and interruption settings apply on the next Start. Stop before changing them.");
        column.addView(options);
        button(column, "Speak long test reply", v -> {
            if (AudioService.running && !AudioService.networkRunning) startService(new Intent(this, AudioService.class).setAction("speak"));
            else if (AudioService.networkRunning) Toast.makeText(this, "Test reply is local-probe only", Toast.LENGTH_SHORT).show();
            else Toast.makeText(this, "Start local audio first", Toast.LENGTH_SHORT).show();
        });
        button(column, "Export diagnostic log", v -> startActivityForResult(
            new Intent(Intent.ACTION_CREATE_DOCUMENT).setType("application/x-ndjson")
                .addCategory(Intent.CATEGORY_OPENABLE).putExtra(Intent.EXTRA_TITLE, "dogwalk-audio.jsonl"), 7));
        status = new TextView(this);
        status.setTextIsSelectable(true);
        status.setTypeface(android.graphics.Typeface.MONOSPACE);
        status.setTextSize(12);
        column.addView(status);
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.addView(column);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        applyConfigurationLink(getIntent());
    }

    private void saveSettings() {
        getSharedPreferences("frontend", MODE_PRIVATE).edit()
            .putString("endpoint", endpoint.getText().toString().trim())
            .putString("token", token.getText().toString().trim()).apply();
    }

    private void applyConfigurationLink(Intent intent) {
        Uri uri = intent == null ? null : intent.getData();
        if (uri == null || !"dogwalk-audio".equals(uri.getScheme()) || !"connect".equals(uri.getHost())) return;
        pendingStart = false; // Configuration is never permission to open the microphone.
        try {
            String address = uri.getQueryParameter("endpoint"), secret = uri.getQueryParameter("token");
            if (address != null) endpoint.setText(address);
            if (secret != null) token.setText(secret);
            saveSettings();
            Toast.makeText(this, "Gateway settings populated. Tap Start explicitly to connect.", Toast.LENGTH_LONG).show();
        } catch (RuntimeException ignored) {
            Toast.makeText(this, "Invalid configuration link", Toast.LENGTH_SHORT).show();
        }
        intent.setData(null); // Do not retain the private capability URI in activity state.
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        applyConfigurationLink(intent);
    }

    private void button(LinearLayout parent, String label, View.OnClickListener action) {
        Button b = new Button(this); b.setText(label); b.setOnClickListener(action); parent.addView(b);
    }

    private void requestStart() {
        if (requestingPermissions) return;
        pendingStart = true;
        ArrayList<String> permissions = new ArrayList<>();
        for (String permission : new String[]{Manifest.permission.RECORD_AUDIO, Manifest.permission.BLUETOOTH_CONNECT}) {
            if (checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) permissions.add(permission);
        }
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) permissions.add(Manifest.permission.POST_NOTIFICATIONS);
        if (!permissions.isEmpty()) {
            requestingPermissions = true;
            requestPermissions(permissions.toArray(new String[0]), 1);
        }
        else startProbe();
    }

    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        if (request == 1) {
            requestingPermissions = false;
            if (pendingStart) startProbe(); // Bluetooth and notifications are optional; microphone is not.
        }
    }

    private void startProbe() {
        if (!resumed || requestingPermissions || !pendingStart) return;
        pendingStart = false;
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "Microphone permission is required", Toast.LENGTH_LONG).show(); return;
        }
        try {
            saveSettings();
            startForegroundService(new Intent(this, AudioService.class).setAction("start")
                .putExtra("duplex", duplex.isChecked()).putExtra("route", route.getSelectedItemPosition())
                .putExtra("endpoint", endpoint.getText().toString().trim()).putExtra("token", token.getText().toString().trim()));
        } catch (RuntimeException e) {
            ProbeLog.event("start_rejected", e.toString());
            Toast.makeText(this, "Audio start rejected. Keep the app open and tap Start again.", Toast.LENGTH_LONG).show();
        }
    }

    @Override protected void onResume() {
        super.onResume(); resumed = true; ui.post(refresh); startProbe();
    }
    @Override protected void onPause() { resumed = false; ui.removeCallbacks(refresh); saveSettings(); super.onPause(); }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != 7 || result != RESULT_OK || data == null) return;
        Uri destination = data.getData();
        try (InputStream in = new FileInputStream(ProbeLog.file);
             OutputStream out = getContentResolver().openOutputStream(destination)) {
            if (out == null) throw new IOException("No output stream");
            byte[] buffer = new byte[8192];
            int count;
            while ((count = in.read(buffer)) != -1) out.write(buffer, 0, count);
            Toast.makeText(this, "Log exported: includes transcripts", Toast.LENGTH_LONG).show();
        } catch (Exception e) { Toast.makeText(this, e.toString(), Toast.LENGTH_LONG).show(); }
    }

    public static class AudioService extends Service {
        static volatile boolean running;
        static volatile boolean networkRunning;
        private final Handler main = new Handler(Looper.getMainLooper());
        private AudioManager audio;
        private AudioFocusRequest focus;
        private PowerManager.WakeLock wake;
        private SpeechRecognizer recognizer;
        private TextToSpeech tts;
        private boolean ready, listening, speaking, duplex, paused, stopping, configured;
        private boolean firstPartial, recognitionSpeech;
        private boolean screenRegistered, devicesRegistered, modeRegistered, routeRegistered, foreground;
        private boolean headsetArmed;
        private int protectedHeadsetId = -1, recognizerGeneration;
        private int routePreference, failures, oldMode;
        private long heardAt, replyAt, sessionAt;
        private String utterance;
        private FrontendClient frontend;
        private String speechId, pendingSpeechId, pendingSpeechText;
        private int sequence;
        private final AudioAttributes attributes = new AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build();
        private final Runnable listenAgain = this::listen;
        private Runnable recognitionTimeout;
        private final InitialRecognitionWatchdog initialRecognition = new InitialRecognitionWatchdog();
        private final Runnable startupTimeout = () -> fatal("Startup watchdog: no ready recognizer within 30 seconds");
        private final Runnable ttsTimeout = () -> {
            finishRemoteSpeech("error");
            fatal("TTS watchdog: no progress/completion within the bounded wait");
        };
        private final AudioManager.OnModeChangedListener modeChanged = mode -> {
            if (!stopping) ProbeLog.event("mode_changed", "requested=" + AudioManager.MODE_IN_COMMUNICATION + " actual=" + mode);
        };
        private final AudioManager.OnCommunicationDeviceChangedListener communicationChanged = device -> {
            if (stopping) return;
            ProbeLog.event("route_active", describe(device));
            checkHeadset(device);
            scheduleListen(300);
        };
        private final Runnable sample = new Runnable() {
            public void run() {
                if (stopping) return;
                PowerManager power = getSystemService(PowerManager.class);
                KeyguardManager lock = getSystemService(KeyguardManager.class);
                BatteryManager battery = getSystemService(BatteryManager.class);
                ProbeLog.event("sample", "interactive=" + power.isInteractive() + " locked=" + lock.isKeyguardLocked()
                    + " battery=" + battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
                    + "% current_uA=" + battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW)
                    + " thermal=" + power.getCurrentThermalStatus() + " listening=" + listening
                    + " speaking=" + speaking + " elapsed_s=" + ((SystemClock.elapsedRealtime() - sessionAt) / 1000)
                    + " " + routeSnapshot());
                headsetSafe();
                if (!stopping) main.postDelayed(this, 30000);
            }
        };
        private final BroadcastReceiver screen = new BroadcastReceiver() {
            @Override public void onReceive(Context c, Intent i) { ProbeLog.event("screen", i.getAction()); }
        };
        private final AudioDeviceCallback devices = new AudioDeviceCallback() {
            @Override public void onAudioDevicesAdded(AudioDeviceInfo[] added) { routesChanged(); }
            @Override public void onAudioDevicesRemoved(AudioDeviceInfo[] removed) {
                for (AudioDeviceInfo device : removed) {
                    if (device.getId() == protectedHeadsetId) { fatal("Headset disconnected; stopping without speaker fallback"); return; }
                }
                routesChanged();
            }
        };

        @Override public void onCreate() {
            super.onCreate();
            ProbeLog.init(this);
            try { initializeService(); }
            catch (RuntimeException e) {
                fatal("Audio startup rejected: " + e + ". Open the app and tap Start again.");
                Toast.makeText(this, "Audio startup failed. Open the app and tap Start again.", Toast.LENGTH_LONG).show();
            }
        }

        private void initializeService() {
            audio = getSystemService(AudioManager.class);
            oldMode = audio.getMode();
            NotificationManager manager = getSystemService(NotificationManager.class);
            manager.createNotificationChannel(new NotificationChannel("audio", "Audio probe", NotificationManager.IMPORTANCE_LOW));
            PendingIntent stop = PendingIntent.getService(this, 1,
                new Intent(this, AudioService.class).setAction("stop"), PendingIntent.FLAG_IMMUTABLE);
            PendingIntent open = PendingIntent.getActivity(this, 2, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
            Notification notification = new Notification.Builder(this, "audio")
                .setSmallIcon(android.R.drawable.ic_btn_speak_now).setContentTitle("Dogwalk audio probe")
                .setContentText("On-device speech requested. Stop releases audio.")
                .setContentIntent(open).setOngoing(true).addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stop).build();
            startForeground(1, notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE | ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
            foreground = true;
            running = true;
            sessionAt = SystemClock.elapsedRealtime();
            ProbeLog.event("start", Build.MANUFACTURER + " " + Build.MODEL + " Android " + Build.VERSION.RELEASE
                + " SDK=" + Build.VERSION.SDK_INT + " locale=" + Locale.US);
            wake = getSystemService(PowerManager.class).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "dogwalk:audio-probe");
            wake.acquire(2 * 60 * 60 * 1000L);
            // Hard stop matches the safety timeout on the wake lock, rather than silently degrading.
            main.postDelayed(this::stopSelf, 2 * 60 * 60 * 1000L);
            IntentFilter filter = new IntentFilter();
            filter.addAction(Intent.ACTION_SCREEN_ON); filter.addAction(Intent.ACTION_SCREEN_OFF);
            filter.addAction(Intent.ACTION_USER_PRESENT);
            registerReceiver(screen, filter);
            screenRegistered = true;
            audio.registerAudioDeviceCallback(devices, main);
            devicesRegistered = true;
            audio.addOnModeChangedListener(getMainExecutor(), modeChanged);
            modeRegistered = true;
            audio.addOnCommunicationDeviceChangedListener(getMainExecutor(), communicationChanged);
            routeRegistered = true;
            main.postDelayed(startupTimeout, 30000);
            focus = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN).setAudioAttributes(attributes)
                .setOnAudioFocusChangeListener(change -> {
                    ProbeLog.event("focus", Integer.toString(change));
                    if (stopping) return;
                    if (change == AudioManager.AUDIOFOCUS_GAIN) { paused = false; scheduleListen(300); }
                    else if (change == AudioManager.AUDIOFOCUS_LOSS) { stopSelf(); }
                    else { paused = true; cancelListening(); stopSpeech("focus loss"); }
                }, main).build();
            if (audio.requestAudioFocus(focus) != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) {
                fatal("Audio focus denied"); return;
            }
            if (!SpeechRecognizer.isOnDeviceRecognitionAvailable(this)) {
                fatal("No on-device recognition service. No cloud fallback is used."); return;
            }
            tts = new TextToSpeech(this, result -> main.post(() -> {
                try { initializeVoice(result); }
                catch (RuntimeException e) { fatal("TTS initialization failed: " + e); }
            }));
            main.post(sample);
        }

        @Override public int onStartCommand(Intent intent, int flags, int id) {
            if (intent == null || "stop".equals(intent.getAction())) { stopAudio(); return START_NOT_STICKY; }
            if (stopping) return START_NOT_STICKY;
            if ("speak".equals(intent.getAction())) { if (ready && !networkRunning) say(ProbePolicy.LONG_REPLY); }
            else if (!configured) {
                configured = true;
                duplex = intent.getBooleanExtra("duplex", false);
                routePreference = intent.getIntExtra("route", 0);
                String address = intent.getStringExtra("endpoint");
                networkRunning = address != null && !address.isBlank();
                if (networkRunning) {
                    String secret = intent.getStringExtra("token");
                    frontend = new FrontendClient(address, secret == null ? "" : secret, new FrontendClient.Listener() {
                        @Override public void speak(String id, String text) {
                            if (stopping) return;
                            if (paused) { frontend.playbackFinished(id, "interrupted"); return; }
                            if (!ready) {
                                dropPendingSpeech();
                                pendingSpeechId = id; pendingSpeechText = text;
                            } else say(text, id);
                        }
                        @Override public void status(String message) {
                            if (!stopping) ProbeLog.event("frontend", message);
                        }
                        @Override public void failed(String message) { fatal(message); }
                    });
                    ProbeLog.event("frontend", "Network text/control mode; audio remains local");
                }
                try { audio.setMode(AudioManager.MODE_IN_COMMUNICATION); }
                catch (RuntimeException e) { fatal("Mode request failed: " + e); return START_NOT_STICKY; }
                ProbeLog.event("mode_request", "requested=" + AudioManager.MODE_IN_COMMUNICATION + " actual=" + audio.getMode());
                if (duplex && routePreference == 1) { fatal("Duplex requires a headset, not the speaker route"); return START_NOT_STICKY; }
                routesChanged();
                ProbeLog.event("configuration", "duplex=" + duplex + " routePreference=" + routePreference);
                scheduleListen(300);
            } else ProbeLog.event("info", "Already running; Stop then Start to change settings.");
            return START_NOT_STICKY;
        }

        private void initializeVoice(int result) {
            if (stopping) return;
            if (result != TextToSpeech.SUCCESS) { fatal("TTS initialization failed"); return; }
            Set<Voice> voices = tts.getVoices();
            Voice selected = voices == null ? null : voices.stream()
                .filter(v -> v.getLocale().getLanguage().equals("en") && !v.isNetworkConnectionRequired())
                .filter(v -> v.getFeatures() != null && !v.getFeatures().contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED))
                .sorted(Comparator.<Voice>comparingInt(v -> v.getLocale().equals(Locale.US) ? 0 : 1)
                    .thenComparing(Comparator.comparingInt(Voice::getQuality).reversed())
                    .thenComparing(Voice::getName)).findFirst().orElse(null);
            if (selected == null) { fatal("No installed offline English voice. Install one in Android TTS settings."); return; }
            if (tts.setVoice(selected) != TextToSpeech.SUCCESS) { fatal("Could not select offline voice"); return; }
            tts.setAudioAttributes(attributes);
            tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                @Override public void onStart(String id) { main.post(() -> {
                    if (!id.equals(utterance)) return;
                    if (frontend != null && speechId != null) frontend.playbackStarted(speechId);
                    ProbeLog.event("tts_start", "id=" + id + " request_to_start_ms=" + (SystemClock.elapsedRealtime() - replyAt));
                    main.removeCallbacks(ttsTimeout);
                    main.postDelayed(ttsTimeout, 120000); // Absolute completion bound, not extended by range callbacks.
                }); }
                @Override public void onDone(String id) { main.post(() -> speechFinished(id, "done")); }
                @Override public void onError(String id) { main.post(() -> speechFinished(id, "error")); }
                @Override public void onStop(String id, boolean interrupted) { main.post(() -> speechFinished(id, "stopped")); }
                @Override public void onRangeStart(String id, int start, int end, int frame) {
                    main.post(() -> { if (id.equals(utterance)) ProbeLog.event("tts_range", "id=" + id + " chars=" + start + ".." + end); });
                }
            });
            ready = true;
            ProbeLog.event("offline_voice", selected.getName() + " engine=" + tts.getDefaultEngine());
            if (pendingSpeechId != null) {
                String id = pendingSpeechId, text = pendingSpeechText;
                pendingSpeechId = null; pendingSpeechText = null;
                say(text, id);
            }
            scheduleListen(300);
        }

        private void routesChanged() {
            if (stopping || !configured) return;
            try {
                List<AudioDeviceInfo> available = audio.getAvailableCommunicationDevices();
                ProbeLog.event("routes", available.stream().map(AudioService::describe).reduce((a,b) -> a + "; " + b).orElse("none"));
                AudioDeviceInfo chosen = null;
                if (routePreference == 0) chosen = available.stream().filter(AudioService::headset).findFirst().orElse(null);
                if (routePreference == 1) chosen = available.stream()
                    .filter(d -> d.getType() == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER).findFirst().orElse(null);
                if (protectedHeadsetId != -1 && available.stream().noneMatch(d -> d.getId() == protectedHeadsetId)) {
                    fatal("Headset unavailable; stopping without speaker fallback"); return;
                }
                if (duplex && chosen == null && available.stream().noneMatch(AudioService::headset)) {
                    fatal("Duplex requires a connected headset"); return;
                }
                if (chosen != null && headset(chosen)) protectedHeadsetId = chosen.getId();
                if (chosen != null) ProbeLog.event("route_request", describe(chosen) + " accepted=" + audio.setCommunicationDevice(chosen));
                else audio.clearCommunicationDevice();
                ProbeLog.event("route_snapshot", routeSnapshot());
                headsetSafe();
            } catch (RuntimeException e) { fatal("Route request failed: " + e); }
        }

        private String routeSnapshot() {
            try { return "mode=" + audio.getMode() + " communication_device=" + describe(audio.getCommunicationDevice()); }
            catch (RuntimeException e) { return "route_unreadable=" + e; }
        }
        private boolean checkHeadset(AudioDeviceInfo device) {
            if (!configured) return false;
            if (device != null && headset(device)) {
                if (duplex || routePreference != 1) {
                    headsetArmed = true;
                    protectedHeadsetId = device.getId();
                }
                return true;
            }
            if (headsetArmed) fatal("Active headset route lost; stopping without speaker fallback");
            return !duplex && !headsetArmed && protectedHeadsetId == -1;
        }
        private boolean headsetSafe() {
            if (stopping) return false;
            try { return checkHeadset(audio.getCommunicationDevice()); }
            catch (RuntimeException e) { fatal("Cannot verify headset route: " + e); return false; }
        }

        private static boolean headset(AudioDeviceInfo d) {
            return d.getType() == AudioDeviceInfo.TYPE_BLUETOOTH_SCO || d.getType() == AudioDeviceInfo.TYPE_BLE_HEADSET
                || d.getType() == AudioDeviceInfo.TYPE_WIRED_HEADSET || d.getType() == AudioDeviceInfo.TYPE_USB_HEADSET;
        }
        private static String describe(AudioDeviceInfo d) {
            return d == null ? "none" : "type=" + d.getType() + " id=" + d.getId() + " " + d.getProductName();
        }

        private void scheduleListen(long delay) {
            main.removeCallbacks(listenAgain);
            if (!stopping && !paused) main.postDelayed(listenAgain, delay);
        }
        private void listen() {
            if (stopping || paused || !configured || !ready || listening || (speaking && !duplex) || !headsetSafe()) return;
            if (initialRecognition.resumeForListen()) main.postDelayed(startupTimeout, 30000);
            Intent request = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
                .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
            // A fresh instance/listener per attempt makes cancellation and queued callbacks unambiguous.
            cancelListening();
            int generation = ++recognizerGeneration;
            listening = true;
            heardAt = 0;
            recognitionSpeech = false;
            firstPartial = true;
            ProbeLog.event("listen_request", "on-device recognizer generation=" + generation + " " + routeSnapshot());
            try {
                recognizer = SpeechRecognizer.createOnDeviceSpeechRecognizer(this);
                recognizer.setRecognitionListener(new RecognitionListener() {
                    private boolean current() { return generation == recognizerGeneration && listening && !stopping && !paused; }
                    @Override public void onReadyForSpeech(Bundle params) { if (current()) recognitionReady(); }
                    @Override public void onBeginningOfSpeech() { if (current()) speechBeginning(); }
                    @Override public void onEndOfSpeech() { if (current()) speechEnded(); }
                    @Override public void onPartialResults(Bundle b) { if (current()) partialResults(b); }
                    @Override public void onResults(Bundle b) { if (current()) recognitionResults(b); }
                    @Override public void onError(int e) { if (current()) recognitionError(e); }
                    @Override public void onRmsChanged(float rms) {} // Not a trustworthy VAD.
                    @Override public void onBufferReceived(byte[] buffer) {} // Never retain raw audio.
                    @Override public void onEvent(int type, Bundle params) {}
                });
                armRecognitionTimeout("ready", 15000, false);
                recognizer.startListening(request);
            }
            catch (RuntimeException e) { fatal("Recognizer start failed: " + e); }
        }
        private void cancelListening() {
            main.removeCallbacks(listenAgain);
            clearRecognitionTimeout();
            ++recognizerGeneration; // Invalidate before invoking engine cleanup.
            listening = false;
            SpeechRecognizer previous = recognizer;
            recognizer = null;
            if (previous != null) {
                cleanup("recognizer cancel", previous::cancel);
                cleanup("recognizer destroy", previous::destroy);
            }
        }

        private void clearRecognitionTimeout() {
            if (recognitionTimeout != null) main.removeCallbacks(recognitionTimeout);
            recognitionTimeout = null;
        }
        private void armRecognitionTimeout(String phase, long timeout, boolean retryIdle) {
            clearRecognitionTimeout();
            int generation = recognizerGeneration;
            recognitionTimeout = () -> {
                if (generation != recognizerGeneration || !listening || stopping) return;
                ProbeLog.event("recognition_watchdog", "phase=" + phase + " limit_ms=" + timeout);
                if (!retryIdle || ++failures >= 5) {
                    fatal("Recognition watchdog: " + phase + "; stopping rather than silently dropping speech");
                } else {
                    cancelListening();
                    scheduleListen(400);
                }
            };
            main.postDelayed(recognitionTimeout, timeout);
        }
        private void recognitionReady() {
            initialRecognition.recognitionReady();
            main.removeCallbacks(startupTimeout);
            ProbeLog.event("listening", "ready " + routeSnapshot());
            if (!recognitionSpeech) armRecognitionTimeout("idle without endpoint", 45000, true);
        }
        private void speechBeginning() {
            if (recognitionSpeech) return;
            recognitionSpeech = true;
            heardAt = SystemClock.elapsedRealtime();
            ProbeLog.event("speech_begin", "");
            if (frontend != null) { frontend.userSpeechStarted(); dropPendingSpeech(); }
            armRecognitionTimeout("speech exceeded two-minute probe limit", 120000, false);
            if (duplex && speaking) stopSpeech("barge-in detected");
        }
        private void speechEnded() {
            if (!recognitionSpeech) speechBeginning();
            ProbeLog.event("speech_end", "waiting for final transcript");
            armRecognitionTimeout("final result", 15000, false);
        }
        private void partialResults(Bundle bundle) {
            String text = text(bundle);
            if (!text.isEmpty() && !recognitionSpeech) {
                ProbeLog.event("speech_begin_missing", "Partial text arrived without a beginning-of-speech callback");
                speechBeginning();
            }
            if (!text.isEmpty() && firstPartial) {
                firstPartial = false;
                ProbeLog.event("first_partial", "from_speech_begin_ms=" + (heardAt == 0 ? -1 : SystemClock.elapsedRealtime() - heardAt) + " text=" + text);
            }
        }
        private void recognitionResults(Bundle bundle) {
            clearRecognitionTimeout();
            listening = false;
            if (stopping || paused) return;
            failures = 0;
            String transcript = text(bundle);
            ProbeLog.event("final", transcript);
            if (transcript.trim().isEmpty()) {
                if (frontend != null) frontend.recognitionIdle();
                scheduleListen(250); return;
            }
            ProbePolicy.Reply reply = ProbePolicy.reply(transcript);
            if (reply.stop()) { ProbeLog.event("voice_stop", transcript); stopAudio(); return; }
            if (frontend != null) {
                // Some recognizers omit speech/partial callbacks. A final still supersedes old speech.
                if (!recognitionSpeech) frontend.userSpeechStarted();
                dropPendingSpeech();
                if (duplex && speaking) stopSpeech("new user transcript");
                if (!frontend.transcript(transcript)) ProbeLog.event("frontend", "Transcript not sent: gateway not ready; no replay queued");
                scheduleListen(250);
                return;
            }
            say(reply.text());
        }
        private void recognitionError(int error) {
            clearRecognitionTimeout();
            listening = false;
            if (stopping || paused) return;
            ProbeLog.event("recognition_error", errorName(error));
            if (frontend != null) frontend.recognitionIdle();
            if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS
                    || error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED
                    || error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE) {
                fatal("Recognition unavailable: " + errorName(error) + ". Check downloaded English recognition language."); return;
            }
            if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) {
                scheduleListen(400); return;
            }
            if (++failures >= 5) { fatal("Five consecutive recognition failures; stopping instead of spinning."); return; }
            scheduleListen(Math.min(10000, 500L << failures));
        }
        private static String text(Bundle b) {
            if (b == null) return "";
            ArrayList<String> values = b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
            return values == null || values.isEmpty() ? "" : values.get(0);
        }
        private static String errorName(int e) {
            return switch (e) {
                case 1 -> "NETWORK_TIMEOUT"; case 2 -> "NETWORK"; case 3 -> "AUDIO";
                case 4 -> "SERVER"; case 5 -> "CLIENT"; case 6 -> "SPEECH_TIMEOUT";
                case 7 -> "NO_MATCH"; case 8 -> "RECOGNIZER_BUSY"; case 9 -> "INSUFFICIENT_PERMISSIONS";
                case 10 -> "TOO_MANY_REQUESTS"; case 11 -> "SERVER_DISCONNECTED";
                case 12 -> "LANGUAGE_NOT_SUPPORTED"; case 13 -> "LANGUAGE_UNAVAILABLE";
                default -> "ERROR_" + e;
            };
        }

        private void say(String text) {
            say(text, null);
        }
        private void say(String text, String remoteSpeechId) {
            if (!ready || stopping || paused || !headsetSafe()) {
                if (frontend != null) frontend.playbackFinished(remoteSpeechId, "interrupted");
                return;
            }
            stopSpeech("replacement reply");
            speechId = remoteSpeechId;
            if (!duplex) {
                // An initial gateway reply can intentionally defer the first recognition attempt.
                // Keep the separate TTS watchdog active, then give recognition a fresh startup bound.
                if (initialRecognition.suspendForSpeech()) main.removeCallbacks(startupTimeout);
                cancelListening();
            }
            replyAt = SystemClock.elapsedRealtime();
            utterance = "reply-" + (++sequence);
            speaking = true;
            ProbeLog.event("tts_request", "id=" + utterance + " " + routeSnapshot() + " text=" + text);
            main.postDelayed(ttsTimeout, 15000);
            try {
                if (tts.speak(text, TextToSpeech.QUEUE_FLUSH, new Bundle(), utterance) == TextToSpeech.ERROR) {
                    finishRemoteSpeech("error");
                    fatal("TTS rejected speech request"); return;
                }
            } catch (RuntimeException e) { finishRemoteSpeech("error"); fatal("TTS speech request failed"); return; }
            if (duplex) scheduleListen(200);
        }
        private void stopSpeech(String reason) {
            main.removeCallbacks(ttsTimeout);
            if (!speaking) return;
            ProbeLog.event("tts_cancel", "id=" + utterance + " reason=" + reason);
            finishRemoteSpeech("interrupted"); // Emit before invalidating the correlated local utterance.
            utterance = null; speaking = false;
            if (tts != null) cleanup("TTS stop", () -> tts.stop());
        }
        private void speechFinished(String id, String result) {
            if (!id.equals(utterance) || stopping) return;
            main.removeCallbacks(ttsTimeout);
            ProbeLog.event("tts_" + result, id);
            finishRemoteSpeech("done".equals(result) ? "completed" : "error".equals(result) ? "error" : "interrupted");
            speaking = false; utterance = null;
            scheduleListen(300);
        }
        private void fatal(String reason) {
            if (stopping) return;
            ProbeLog.event("fatal", reason);
            stopping = true;
            cancelListening();
            stopSpeech(reason); // Stop audio now, not on the next lifecycle dispatch.
            stopSelf();
        }

        private void finishRemoteSpeech(String outcome) {
            if (frontend != null) frontend.playbackFinished(speechId, outcome);
            speechId = null;
        }
        private void dropPendingSpeech() {
            if (frontend != null) frontend.playbackFinished(pendingSpeechId, "interrupted");
            pendingSpeechId = null; pendingSpeechText = null;
        }
        private void stopAudio() {
            stopping = true;
            cancelListening();
            stopSpeech("Stop");
            dropPendingSpeech();
            if (frontend != null) frontend.close();
            stopSelf();
        }

        private void cleanup(String operation, Runnable action) {
            try { action.run(); }
            catch (RuntimeException e) { ProbeLog.event("cleanup_error", operation + ": " + e); }
        }

        @Override public void onDestroy() {
            stopping = true; running = false; networkRunning = false;
            main.removeCallbacksAndMessages(null);
            cancelListening();
            stopSpeech("service stopped");
            dropPendingSpeech();
            if (frontend != null) { frontend.close(); frontend = null; }
            if (tts != null) { cleanup("TTS stop", () -> tts.stop()); cleanup("TTS shutdown", () -> tts.shutdown()); }
            if (devicesRegistered) cleanup("devices", () -> audio.unregisterAudioDeviceCallback(devices));
            if (modeRegistered) cleanup("mode listener", () -> audio.removeOnModeChangedListener(modeChanged));
            if (routeRegistered) cleanup("route listener", () -> audio.removeOnCommunicationDeviceChangedListener(communicationChanged));
            if (screenRegistered) cleanup("screen receiver", () -> unregisterReceiver(screen));
            if (configured) {
                cleanup("route clear", () -> audio.clearCommunicationDevice());
                cleanup("mode restore", () -> audio.setMode(oldMode));
            }
            if (focus != null) cleanup("focus", () -> audio.abandonAudioFocusRequest(focus));
            if (wake != null && wake.isHeld()) cleanup("wake lock", () -> wake.release());
            if (foreground) stopForeground(STOP_FOREGROUND_REMOVE);
            ProbeLog.event("stop", "Audio resources released");
            super.onDestroy();
        }
        @Override public IBinder onBind(Intent intent) { return null; }
    }

    /** Bounded UI tail, capped private log, monotonic timestamps for latency analysis. */
    static class ProbeLog {
        static File file;
        private static final ArrayDeque<String> tail = new ArrayDeque<>();
        static synchronized void init(Context c) {
            if (file == null) file = new File(c.getNoBackupFilesDir(), "audio-probe.jsonl");
        }
        static synchronized void event(String type, String detail) {
            try {
                JSONObject row = new JSONObject().put("wall_ms", System.currentTimeMillis())
                    .put("elapsed_ms", SystemClock.elapsedRealtime()).put("event", type).put("detail", detail);
                // A long walk must not grow storage without bound. Rotation is intentionally simple.
                if (file.length() > 8 * 1024 * 1024) {
                    try (FileOutputStream truncated = new FileOutputStream(file)) { truncated.write(new byte[0]); }
                }
                try (FileOutputStream out = new FileOutputStream(file, true)) {
                    out.write((row + "\n").getBytes(StandardCharsets.UTF_8));
                }
            } catch (Exception e) { android.util.Log.e("DogwalkProbe", "Log write failed", e); }
            String time = new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date());
            tail.addLast(time + " " + type + " " + detail);
            while (tail.size() > 55) tail.removeFirst();
        }
        static synchronized String snapshot() { return String.join("\n\n", tail); }
    }
}

/** Deterministic stand-in for conversational text: deliberately no session-management authority. */
final class ProbePolicy {
    static final String LONG_REPLY = "This is a deliberately long local speech test. "
        + "If you are wearing headphones and enabled listening during replies, you can interrupt me now. "
        + "Otherwise I will finish speaking before listening again. "
        + "The app requests on-device recognition and an offline speech voice from the system engines. "
        + "No coding session is connected. Lock the screen, walk around, and check whether you can still hear me. "
        + "This reply continues for a few more seconds so you have time to test interruption and audio routing.";
    record Reply(String text, boolean stop) {}
    static Reply reply(String transcript) {
        String command = transcript.toLowerCase(Locale.US).replaceAll("[^a-z ]", "").trim().replaceAll(" +", " ");
        return switch (command) {
            case "stop listening", "stop the probe" -> new Reply("", true);
            case "long reply", "long test reply" -> new Reply(LONG_REPLY, false);
            case "probe status" -> new Reply("The local audio probe is running. There is no model or remote backend connected.", false);
            default -> new Reply("I heard: " + transcript, false);
        };
    }
}
