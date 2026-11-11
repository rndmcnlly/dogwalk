package dogwalk.ondevice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.speech.RecognitionListener
import android.speech.RecognitionSupport
import android.speech.RecognitionSupportCallback
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import java.util.concurrent.Executors

/**
 * The whole Voice Frontend on the phone: on-device recognition, a local LiteRT-LM model for
 * conversation and phrasing, and an offline TTS voice. Only text crosses the network, to the
 * Session Orchestrator. There is no cloud fallback for any of the three: a missing piece is
 * reported visibly in text mode, aloud only in explicitly enabled voice mode.
 *
 * Text uses a special-use foreground service, never a microphone service. Voice is opt-in.
 */
class VoiceService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private val modelThread = Executors.newSingleThreadExecutor()
    private val core = FrontendCore()
    private lateinit var audio: AudioManager
    private var wakeLock: PowerManager.WakeLock? = null
    private var tts: TextToSpeech? = null
    private var recognizer: SpeechRecognizer? = null
    private var model: LocalModel? = null
    private var link: OrchestratorLink? = null
    private var ready = false
    private var stopped = false
    private var listening = false
    private var noticeSpeaking = 0
    private var speechStartMs = 0L
    private var callerStartMs = 0L
    private var lastCallerEndMs = 0L
    private val spokenChars = HashMap<String, Int>()
    private var noticeSeq = 0
    private var ackPending = false
    private var initialized = false
    private var loading = false
    @Volatile private var voiceGeneration = 0
    private var voicePrepared = false
    private var connected = false
    private var connecting = false
    private val instanceId = System.nanoTime().toString()
    private var spec = ModelSpec.E2B
    private var endpoint = ""
    private var gpu = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_RESTORE_TEXT -> { if (initialized) textMode(); return START_NOT_STICKY }
            ACTION_TEXT -> {
                if (initialized) {
                    textMode()
                    if (!ready && !loading) {
                        endpoint = intent.getStringExtra("endpoint") ?: endpoint
                        if (model != null) connect(endpoint) else loadModel(spec, endpoint, gpu)
                    }
                    return START_NOT_STICKY
                }
            }
            ACTION_SEND -> {
                if (presentation.canSend) intent.getStringExtra("text")?.let { caller(it, typed = true) }
                return START_NOT_STICKY
            }
            ACTION_REFRESH -> { if (!core.voiceEnabled) core.playing?.let(::deliver); pump(); return START_NOT_STICKY }
            ACTION_VOICE -> { if (initialized) { voiceMode(); return START_NOT_STICKY } }
            ACTION_STOP_SPEAKING -> { localStop("button"); return START_NOT_STICKY }
            ACTION_INJECT -> {
                // Debug-build test hook: text that did not come from the microphone, labelled as such.
                if (BuildConfig.DEBUG && ready) intent.getStringExtra("text")?.let { log("INJECTED caller text (not speech)"); caller(it) }
                return START_NOT_STICKY
            }
        }
        if (initialized) { textMode(); return START_NOT_STICKY }
        initialized = true
        presentation = presentation.copy(running = true, problem = null)
        startTextForeground("Starting silently")
        audio = getSystemService(AudioManager::class.java)
        wakeLock = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "dogwalk:frontend").apply { acquire() }
        spec = ModelSpec.valueOf(intent?.getStringExtra("model") ?: "E2B")
        endpoint = intent?.getStringExtra("endpoint").orEmpty()
        gpu = intent?.getBooleanExtra("gpu", false) ?: false
        if (intent?.action == ACTION_VOICE) voiceMode() else loadModel(spec, endpoint, gpu)
        return START_NOT_STICKY
    }

    private fun textMode() {
        if (!core.voiceEnabled) { pump(); return }
        voiceGeneration++
        voicePrepared = false
        tts?.stop()
        core.playing?.let { u ->
            val chars = synchronized(spokenChars) { spokenChars.remove(u.id) }
            val interrupted = core.playbackFinished(u.id, false, chars)
            if (interrupted != null && interrupted.text.isNotEmpty()) link?.voiceHeard(interrupted.text, speechStartMs, link!!.nowMs())
        }
        // Disable first. Late TTS/ASR/readiness callbacks cannot reopen the audio path.
        core.switchMode(InteractionMode.TEXT)
        event(EventKind.CONTROL, "Switched to silent text", "Microphone and speaker off. No Prompt Turn cancelled. Opening or restoring the Activity does not resume voice.")
        presentation = presentation.copy(mode = InteractionMode.TEXT, microphone = false, speaker = false, activity = "Silent")
        model?.cancelCurrent()
        listening = false
        recognizer?.cancel(); recognizer?.destroy(); recognizer = null
        tts?.shutdown(); tts = null
        noticeSpeaking = 0
        ready = model != null && connected
        startTextForeground("Text mode: silent")
        update(if (ready) "Text ready" else "Text mode: preparing or disconnected")
        if (model == null && !loading) loadModel(spec, endpoint, gpu)
        pump()
    }

    private fun voiceMode() {
        if (core.voiceEnabled) return
        core.switchMode(InteractionMode.VOICE)
        presentation = presentation.copy(mode = InteractionMode.VOICE, activity = "Preparing voice", accepting = false)
        event(EventKind.CONTROL, "Voice explicitly enabled", "Checking installed offline speech and recognition. Only text crosses the network.")
        publish()
        voicePrepared = false
        model?.cancelCurrent()
        ready = false
        val generation = ++voiceGeneration
        startForeground(1, notification("Preparing voice"), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        tts = TextToSpeech(this) { status -> main.post {
            if (!core.voiceEnabled || generation != voiceGeneration || stopped) return@post
            if (status == TextToSpeech.SUCCESS) prepare(spec, endpoint, gpu, generation) else fatal("text to speech failed to start")
        } }
    }

    // --- Readiness: each local capability is checked explicitly, in order ---------------------

    private fun prepare(spec: ModelSpec, endpoint: String, gpu: Boolean, generation: Int) {
        if (!core.voiceEnabled || generation != voiceGeneration || stopped) return
        val voice = tts!!.voices.orEmpty()
            .filter { it.locale.language == "en" && !it.isNetworkConnectionRequired }
            .filter { TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED !in it.features.orEmpty() }
            .minByOrNull { if (it.locale.country == "US") 0 else 1 }
            ?: return fatal("no installed offline English voice; install one in text to speech settings")
        if (tts!!.setVoice(voice) != TextToSpeech.SUCCESS) return fatal("could not select the offline voice")
        log("TTS: ${tts!!.defaultEngine} voice ${voice.name} (offline)")
        tts!!.setOnUtteranceProgressListener(progress(generation))
        if (!SpeechRecognizer.isOnDeviceRecognitionAvailable(this)) return fatal("on-device speech recognition is unavailable")
        recognizer = SpeechRecognizer.createOnDeviceSpeechRecognizer(this)
        recognizer!!.checkRecognitionSupport(recognitionIntent(), mainExecutor, object : RecognitionSupportCallback {
            override fun onSupportResult(support: RecognitionSupport) {
                if (!core.voiceEnabled || generation != voiceGeneration || stopped) return
                val tag = "en-US"
                when {
                    tag in support.installedOnDeviceLanguages -> {
                        voicePrepared = true
                        log("ASR: on-device $tag installed")
                        if (model == null && !loading) loadModel(spec, endpoint, gpu)
                        else if (connected) { ready = true; update("Voice ready"); pump() }
                        else if (!loading && model != null) connect(endpoint)
                    }
                    tag in support.supportedOnDeviceLanguages || tag in support.pendingOnDeviceLanguages -> {
                        recognizer!!.triggerModelDownload(recognitionIntent())
                        fatal("on-device English recognition is downloading; start again when it finishes")
                    }
                    else -> fatal("on-device English recognition is not supported here")
                }
            }
            override fun onError(error: Int) { if (core.voiceEnabled && generation == voiceGeneration) fatal("recognition support check failed, error $error") }
        })
    }

    private fun loadModel(spec: ModelSpec, endpoint: String, gpu: Boolean) {
        if (loading || stopped) return
        loading = true
        engineOwned = true
        local(LocalState.VERIFYING)
        update("Loading on-device model (silent in text mode)")
        notice("Loading the on-device voice model.")
        val store = ModelStore(getExternalFilesDir("models")!!)
        modelThread.execute {
            val problem = if (store.verified(spec)) null else store.verify(spec) { msg -> main.post { log(msg) } }
            if (problem != null) { main.post { engineOwned = false; loading = false; local(if (store.file(spec).exists()) LocalState.ERROR else LocalState.MISSING); fatal("Model verification failed. Open Menu to verify or install the pinned file.") }; return@execute }
            val m = LocalModel(spec, store.file(spec), cacheDir, gpu)
            try {
                main.post { local(LocalState.LOADING) }
                val t0 = System.currentTimeMillis(); m.load(); val loadMs = System.currentTimeMillis() - t0
                // Readiness means a real local generation succeeded, not merely that a file exists.
                val t1 = System.currentTimeMillis()
                main.post { local(LocalState.WARMING) }
                val raw = m.generate(Prompts.DECIDE_SYSTEM, Prompts.decide(Prompts.context(emptyList(), null, emptyList(), emptyList()), "Hello, can you hear me?"))
                check(raw.isNotBlank()) { "Empty warmup output" }
                val warmMs = System.currentTimeMillis() - t1
                main.post {
                    loading = false
                    if (stopped) { m.close(); engineOwned = false; return@post }
                    model = m
                    local(LocalState.READY)
                    event(EventKind.DIAGNOSTIC, "Local model warmup complete", "${spec.label} · ${if (gpu) "GPU" else "CPU"}. Load ${loadMs} ms; real warmup ${warmMs} ms. LiteRT-LM 0.19.1.")
                    log("MODEL: ${m.description}; load ${loadMs} ms; warmup ${warmMs} ms -> ${raw.trim().take(80)}")
                    connect(endpoint)
                }
            } catch (e: Throwable) {
                m.close(); engineOwned = false; main.post { loading = false; if (!stopped) { local(LocalState.ERROR); fatal("Local model failed to run (${e.javaClass.simpleName}). Stop frontend, then check model and backend in Menu.") } }
            }
        }
    }

    private fun connect(endpoint: String) {
        if (endpoint.isBlank()) return fatal("no Session Orchestrator address is set")
        if (connected || connecting) return
        connecting = true
        transport(LinkState.CONNECTING, "Connecting to saved endpoint")
        update("Connecting to Session Orchestrator")
        try {
            link = OrchestratorLink(endpoint, { m -> main.post { if (!stopped) inbound(m) } }, { reason -> main.post { linkClosed(reason) } }, {
            main.post {
                if (!stopped) {
                    ready = !core.voiceEnabled || voicePrepared
                    connected = true
                    connecting = false
                    transport(LinkState.CONNECTED, "Connected to development link", "Socket open only. This prototype has no real coding or authentication adapter. No input automatically resent.")
                    update(if (core.voiceEnabled) "Voice ready" else "Text ready: silent")
                    notice(if (core.voiceEnabled) "Ready. Everything you say is recognized on this phone." else "Ready for typed messages. No microphone or speech output.")
                    pump()
                }
            }
            }).also { it.connect() }
        } catch (_: IllegalArgumentException) {
            connecting = false
            transport(LinkState.ERROR, "Invalid connection address", "Use ws:// or wss:// in Menu.")
            fatal("invalid Session Orchestrator address; use ws:// or wss://")
        }
    }

    // --- Inbound context and speech requests ---------------------------------------------------

    private fun inbound(m: Inbound) {
        when (m) {
            is Inbound.Say -> { val r = core.onSay(m.delegationId, m.content); event(EventKind.DIAGNOSTIC, "Orchestrator output queued", "One independent request awaits local phrasing and delivery.", requestId = r.id); log("SAY ${r.id} (${m.delegationId ?: "unsolicited"}): ${m.content}") }
            is Inbound.Brief -> { core.onBrief(m.content); event(EventKind.DIAGNOSTIC, "Background context updated", "Brief / Following context is not an assistant reply or display receipt.") }
            is Inbound.Guidance -> { core.onGuidance(m.content); event(EventKind.DIAGNOSTIC, "Background guidance updated", "Not conversation output or permission authorization.") }
            is Inbound.Fixture -> { log("FIXTURE ORCHESTRATOR: ${m.description}"); notice("Connected to a test fixture, not your real sessions.") }
            is Inbound.Unsupported -> event(EventKind.DIAGNOSTIC, "Unsupported capability", "The frontend cannot handle this capability; no action was performed.")
        }
        pump()
    }

    private fun linkClosed(reason: String) {
        if (stopped) return
        log("LINK $reason")
        connected = false
        connecting = false
        ready = false
        transport(LinkState.DISCONNECTED, "Connection lost · Retry when ready", "History stays in this running process. In-flight delivery is unknown. No automatic resend. Voice audio may still be active: Switch to text turns it off.")
        if (reason.startsWith("failed:")) presentation = presentation.copy(problem = reason)
        update("Disconnected")
    }

    // --- Caller input ---------------------------------------------------------------------------

    private fun caller(text: String, typed: Boolean = false) {
        if (!ready || text.isBlank()) { if (typed) update("Not ready: wait for model and connection, or stop and start text again"); return }
        if (!typed && !core.voiceEnabled) return
        if (typed) {
            core.abandonUndeliveredText()
            showChat("caller-${++chatSeq}", EventKind.USER, text, "Typed · accepted locally")
        }
        else event(EventKind.USER, text, provenance = "Recognized on device · accepted locally")
        val endMs = link!!.nowMs()
        lastCallerEndMs = endMs
        link!!.callerSaid(text, if (typed) endMs else callerStartMs.coerceAtMost(endMs), endMs, typed)
        if (core.inFlight != null) model?.cancelCurrent()
        when (val route = core.onCaller(text, typed)) {
            CallerRoute.LocalStop -> { log("CALLER (local stop): $text"); tts?.stop() }
            is CallerRoute.Handoff -> { log("CALLER -> HANDOFF ${route.delegationId} [${route.reason}]: $text"); handoff(route.delegationId) }
            CallerRoute.Model -> log("CALLER -> local model decides: $text")
        }
        pump()
    }

    private fun handoff(id: String) {
        link!!.handoff(id, lastCallerEndMs)
        if (core.playing != null) tts?.stop()  // the ack waits for the interrupted utterance's receipt
        ackPending = true
    }

    private fun localStop(source: String) {
        event(EventKind.CONTROL, "Speech stopped locally", "No Prompt Turn cancelled. Queued speech waits for new input.")
        presentation = presentation.copy(activity = "Locally paused")
        log("LOCAL STOP ($source): speech stops; no Prompt Turn is cancelled")
        core.stopLocal()
        ackPending = false
        model?.cancelCurrent()
        tts?.stop()
        publish()
    }

    // --- Scheduler: one model job at a time, then playback, then listening ----------------------

    private fun pump() {
        if (!ready || stopped) return
        if (ackPending) {
            val ack = core.acknowledge() ?: return  // still waiting on a playback receipt
            ackPending = false
            deliver(ack)
            if (core.mode == InteractionMode.TEXT && core.playing == null) pump()
            return
        }
        val job = core.nextJob()
        if (job != null) {
            presentation = presentation.copy(activity = "Thinking locally")
            publish()
            stopListening()
            val m = model ?: return
            modelThread.execute {
                val t0 = System.currentTimeMillis()
                val raw = runCatching { m.generate(job.system, job.prompt) }.getOrNull()
                val ms = System.currentTimeMillis() - t0
                main.post { completed(job, raw, ms) }
            }
            return
        }
        if (core.playing == null && core.inFlight == null && noticeSpeaking == 0) startListening()
    }

    private fun completed(job: Job, raw: String?, ms: Long) {
        event(EventKind.DIAGNOSTIC, if (raw == null) "Local inference failed" else "Local inference finished", "${job.javaClass.simpleName}: ${ms} ms. ${if (raw == null) "Safe fallback policy applies." else "Output checked by code-owned delivery policy."}", warning = raw == null)
        presentation = presentation.copy(activity = "Waiting")
        when (job) {
            is DecideJob -> when (val d = core.completeDecide(job, raw)) {
                null -> log("DECIDE discarded as stale (${ms} ms)")
                is Decision.Reply -> { log("DECIDE reply (${ms} ms): ${d.utterance.text}"); deliver(d.utterance) }
                is Decision.Handoff -> { log("DECIDE -> HANDOFF ${d.delegationId} [${d.reason}] (${ms} ms) raw=${raw?.trim()?.take(80)}"); handoff(d.delegationId) }
            }
            is PhraseJob -> when (val u = core.completePhrase(job, raw)) {
                null -> log("PHRASE ${job.requestId} discarded as stale (${ms} ms)")
                else -> { log("PHRASE ${job.requestId} -> ${u.id} ${u.kind}${u.note?.let { " [$it]" } ?: ""} (${ms} ms): ${u.text} | raw=${raw?.trim()?.take(120)}"); deliver(u) }
            }
        }
        pump()
    }

    // --- Playback receipts ----------------------------------------------------------------------

    private fun deliver(u: Utterance) {
        val receipt = core.deliver(u, display = {
            // A receipt exists only after the active UI has synchronously appended this turn.
            showChat("$instanceId-${it.id}", EventKind.ASSISTANT, it.text,
                (if (it.requestId == null) "Local reply" else "Orchestrator request · locally phrased") + " · displayed, not spoken", it.requestId)
        }, voice = { speak(it) })
        if (receipt != null) {
            link?.textShown(receipt.utterance, link!!.nowMs())
            log("TEXT_DISPLAYED ${u.id} request=${u.requestId ?: "none"} (not known read)")
            update("Text ready: silent")
        }
    }

    private fun speak(u: Utterance) = core.voiceOnly {
        stopListening()
        speechStartMs = link?.nowMs() ?: 0
        spokenChars.remove(u.id)
        if (tts!!.speak(u.text, TextToSpeech.QUEUE_FLUSH, Bundle(), u.id) == TextToSpeech.ERROR) {
            event(EventKind.DIAGNOSTIC, "Speech output failed", "No playback completion observed.", warning = true)
            finished(u.id, false, voiceGeneration)
        }
        if (duplexHeadset()) startListening()  // barge-in is only safe without a loudspeaker echo
    }

    private fun notice(text: String) {
        log(text)
        if (!core.voiceEnabled || stopped) return
        noticeSpeaking++
        stopListening()
        core.voiceOnly { tts?.speak(text, TextToSpeech.QUEUE_ADD, Bundle(), "notice-${++noticeSeq}") }
    }

    private fun progress(generation: Int) = object : UtteranceProgressListener() {
        override fun onStart(id: String) { main.post { if (core.voiceEnabled && generation == voiceGeneration) { presentation = presentation.copy(speaker = true, activity = "Speaking"); publish() } } }
        override fun onRangeStart(id: String, start: Int, end: Int, frame: Int) { if (generation == voiceGeneration) synchronized(spokenChars) { spokenChars[id] = end } }
        override fun onDone(id: String) = main.post { finished(id, true, generation) }.let {}
        @Deprecated("Deprecated in Java") override fun onError(id: String) = main.post {
            if (core.voiceEnabled && generation == voiceGeneration && !stopped) {
                event(EventKind.DIAGNOSTIC, "Offline speech playback failed", "No successful playback completion observed. Switch to text for silent interaction.", warning = true)
                finished(id, false, generation)
                presentation = presentation.copy(activity = "Speech playback failed"); publish()
            }
        }.let {}
        override fun onStop(id: String, interrupted: Boolean) = main.post { finished(id, false, generation) }.let {}
    }

    private fun finished(id: String, completed: Boolean, generation: Int) {
        if (!core.voiceEnabled || stopped || generation != voiceGeneration) return
        presentation = presentation.copy(speaker = false, activity = if (core.held) "Locally paused" else "Waiting")
        publish()
        if (id.startsWith("notice-")) { noticeSpeaking = (noticeSpeaking - 1).coerceAtLeast(0); pump(); return }
        val chars = synchronized(spokenChars) { spokenChars.remove(id) }
        val heard = core.playbackFinished(id, completed, chars) ?: return
        event(EventKind.ASSISTANT, heard.utterance.text, if (completed) "Playback completed callback, not proof of hearing or understanding." else "Playback interrupted. Played prefix is approximate, not acoustic evidence.", "${if (heard.utterance.requestId == null) "Local reply" else "Orchestrator request"} · playback ${if (completed) "completed" else "interrupted"}", heard.utterance.requestId)
        if (heard.text.isNotEmpty()) link?.voiceHeard(heard.text, speechStartMs, link!!.nowMs())
        log("PLAYBACK ${heard.utterance.id} ${if (completed) "completed" else "interrupted"}" +
            (heard.utterance.requestId?.let { " for $it" } ?: "") + (if (!completed) ", heard: \"${heard.text}\"" else ""))
        update("Waiting for recognition")
        pump()
    }

    // --- On-device recognition -----------------------------------------------------------------

    private fun recognitionIntent() = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
        .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
        .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
        .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)

    private fun startListening() {
        if (!core.voiceEnabled || !voicePrepared || listening || stopped || !ready || recognizer == null) return
        listening = true
        recognizer!!.setRecognitionListener(voiceListener(voiceGeneration))
        core.voiceOnly { recognizer!!.startListening(recognitionIntent()) }
    }

    private fun stopListening() {
        if (!listening || duplexHeadset()) return
        listening = false
        presentation = presentation.copy(microphone = false)
        publish()
        recognizer?.cancel()
    }

    private fun voiceListener(generation: Int) = object : RecognitionListener {
        private fun valid() = core.voiceEnabled && generation == voiceGeneration && !stopped
        override fun onReadyForSpeech(params: Bundle?) { if (valid()) { presentation = presentation.copy(microphone = true, activity = if (presentation.speaker) "Speaking" else "Listening"); publish() } }
        override fun onBeginningOfSpeech() { if (valid()) callerStartMs = link?.nowMs() ?: 0 }
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEndOfSpeech() { if (valid()) { presentation = presentation.copy(microphone = false, activity = "Recognizing"); publish() } }
        override fun onEvent(eventType: Int, params: Bundle?) {}
        override fun onPartialResults(partial: Bundle?) {
            if (!valid()) return
            val words = partial?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()
            if (words.isNotBlank() && core.playing != null) { log("BARGE-IN: caller spoke over playback"); tts?.stop() }
        }
        override fun onResults(results: Bundle?) {
            if (!valid()) return
            listening = false
            presentation = presentation.copy(microphone = false); publish()
            val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()
            if (text.isNotBlank()) caller(text) else pump()
        }
        override fun onError(error: Int) {
            if (!valid()) return
            listening = false
            presentation = presentation.copy(microphone = false, activity = "Recognition unavailable ($error)"); publish()
            // Silence and no-match restart listening without any model call.
            val delay = if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) 0L else 500L
            if (delay > 0) log("ASR error $error; retrying")
            main.postDelayed({ pump() }, delay)
        }
    }

    private fun duplexHeadset() = audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS).any {
        it.type in setOf(AudioDeviceInfo.TYPE_WIRED_HEADPHONES, AudioDeviceInfo.TYPE_WIRED_HEADSET,
            AudioDeviceInfo.TYPE_BLUETOOTH_A2DP, AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_USB_HEADSET)
    }

    // --- Lifecycle and diagnostics ---------------------------------------------------------------

    private fun fatal(reason: String) {
        presentation = presentation.copy(problem = reason, activity = if (core.voiceEnabled) "Voice unavailable" else "Silent")
        event(EventKind.READINESS, "Frontend unavailable", reason, warning = true)
        log("NOT READY: $reason")
        update("Not ready: $reason")
        ready = false
        publish()
        if (!stopped) core.voiceOnly { tts?.speak("I can't start: $reason.", TextToSpeech.QUEUE_ADD, Bundle(), "notice-fatal") }
    }

    private fun update(state: String) {
        publish()
        getSystemService(NotificationManager::class.java).notify(1, notification(state))
    }

    private fun notification(state: String): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("frontend-silent", "Frontend (silent)", NotificationManager.IMPORTANCE_LOW).apply {
            setSound(null, null); enableVibration(false)
        })
        return Notification.Builder(this, "frontend-silent").setContentTitle("Dogwalk on-device frontend").setContentText(state)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now).setOnlyAlertOnce(true).setOngoing(true).build()
    }

    private fun startTextForeground(state: String) {
        // API 33 has no special-use type, and permits an untyped foreground service.
        if (android.os.Build.VERSION.SDK_INT >= 34) {
            startForeground(1, notification(state), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(1, notification(state), ServiceInfo.FOREGROUND_SERVICE_TYPE_NONE)
        }
    }

    override fun onDestroy() {
        stopped = true
        voiceGeneration++
        ready = false
        core.switchMode(InteractionMode.TEXT)
        presentation = PresentationState(local = if (model != null) LocalState.INSTALLED else presentation.local)
        event(EventKind.CONTROL, "Frontend stopped", "Model files retained. Process-local history only. Stopping this frontend does not cancel a remote Prompt Turn.")
        publish()
        link?.close()
        recognizer?.destroy()
        tts?.shutdown()
        modelThread.execute { model?.close(); engineOwned = false }
        modelThread.shutdown()
        wakeLock?.takeIf { it.isHeld }?.release()
        log("STOPPED")
        super.onDestroy()
    }

    private fun log(line: String) {
        Log.i(TAG, line)
    }

    private fun publish() {
        presentation = presentation.copy(mode = core.mode, accepting = ready && !stopped, running = initialized && !stopped)
        stateListener?.invoke(presentation)
    }
    private fun local(state: LocalState) {
        if (presentation.local != state) event(EventKind.READINESS, "Local · ${state.label}", "Observed on this device. Installed files alone do not establish runtime readiness.")
        presentation = presentation.copy(local = state, problem = null); publish()
    }
    private fun transport(state: LinkState, summary: String, detail: String = "") {
        presentation = presentation.copy(link = state, problem = null)
        event(EventKind.CONNECTION, summary, detail, warning = state == LinkState.ERROR || state == LinkState.DISCONNECTED); publish()
    }

    companion object {
        const val TAG = "DogwalkFrontend"
        const val ACTION_STOP_SPEAKING = "dogwalk.ondevice.STOP_SPEAKING"
        const val ACTION_INJECT = "dogwalk.ondevice.INJECT"
        const val ACTION_TEXT = "dogwalk.ondevice.TEXT"
        const val ACTION_VOICE = "dogwalk.ondevice.VOICE"
        const val ACTION_SEND = "dogwalk.ondevice.SEND"
        const val ACTION_REFRESH = "dogwalk.ondevice.REFRESH"
        const val ACTION_RESTORE_TEXT = "dogwalk.ondevice.RESTORE_TEXT"
        val timeline = Timeline()
        var chatSeq = 0
        var presentation = PresentationState()
        @Volatile var engineOwned = false
        var stateListener: ((PresentationState) -> Unit)? = null
        var timelineListener: ((TimelineEvent) -> Unit)? = null
        var displayVisible: (() -> Boolean)? = null
        fun event(kind: EventKind, summary: String, detail: String = "", provenance: String = "", requestId: String? = null, warning: Boolean = false) {
            val item = timeline.append(kind, summary, detail, provenance, requestId, warning)
            timelineListener?.invoke(item)
        }
        private fun showChat(id: String, kind: EventKind, text: String, provenance: String, requestId: String? = null): Boolean {
            if (kind == EventKind.ASSISTANT && displayVisible?.invoke() != true) return false
            val turn = timeline.append(kind, text, provenance = provenance, requestId = requestId, id = id)
            timelineListener?.invoke(turn)
            return true
        }
    }
}
