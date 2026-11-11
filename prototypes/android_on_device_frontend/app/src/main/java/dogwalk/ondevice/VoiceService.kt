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
 * reported (aloud when synthesis works) and the service stays not-ready.
 *
 * Runs as a microphone foreground service so it keeps working with the screen locked.
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

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP_SPEAKING -> { localStop("button"); return START_NOT_STICKY }
            ACTION_INJECT -> {
                // Debug-build test hook: text that did not come from the microphone, labelled as such.
                if (BuildConfig.DEBUG && ready) intent.getStringExtra("text")?.let { log("INJECTED caller text (not speech)"); caller(it) }
                return START_NOT_STICKY
            }
        }
        if (tts != null) return START_NOT_STICKY
        startForeground(1, notification("Starting"), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        audio = getSystemService(AudioManager::class.java)
        wakeLock = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "dogwalk:frontend").apply { acquire() }
        val spec = ModelSpec.valueOf(intent?.getStringExtra("model") ?: "E2B")
        val endpoint = intent?.getStringExtra("endpoint").orEmpty()
        val gpu = intent?.getBooleanExtra("gpu", false) ?: false
        tts = TextToSpeech(this) { status -> main.post { if (status == TextToSpeech.SUCCESS) prepare(spec, endpoint, gpu) else fatal("text to speech failed to start") } }
        return START_NOT_STICKY
    }

    // --- Readiness: each local capability is checked explicitly, in order ---------------------

    private fun prepare(spec: ModelSpec, endpoint: String, gpu: Boolean) {
        val voice = tts!!.voices.orEmpty()
            .filter { it.locale.language == "en" && !it.isNetworkConnectionRequired }
            .filter { TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED !in it.features.orEmpty() }
            .minByOrNull { if (it.locale.country == "US") 0 else 1 }
            ?: return fatal("no installed offline English voice; install one in text to speech settings")
        if (tts!!.setVoice(voice) != TextToSpeech.SUCCESS) return fatal("could not select the offline voice")
        log("TTS: ${tts!!.defaultEngine} voice ${voice.name} (offline)")
        tts!!.setOnUtteranceProgressListener(progress)
        if (!SpeechRecognizer.isOnDeviceRecognitionAvailable(this)) return fatal("on-device speech recognition is unavailable")
        recognizer = SpeechRecognizer.createOnDeviceSpeechRecognizer(this)
        recognizer!!.checkRecognitionSupport(recognitionIntent(), mainExecutor, object : RecognitionSupportCallback {
            override fun onSupportResult(support: RecognitionSupport) {
                val tag = "en-US"
                when {
                    tag in support.installedOnDeviceLanguages -> { log("ASR: on-device $tag installed"); loadModel(spec, endpoint, gpu) }
                    tag in support.supportedOnDeviceLanguages || tag in support.pendingOnDeviceLanguages -> {
                        recognizer!!.triggerModelDownload(recognitionIntent())
                        fatal("on-device English recognition is downloading; start again when it finishes")
                    }
                    else -> fatal("on-device English recognition is not supported here")
                }
            }
            override fun onError(error: Int) = fatal("recognition support check failed, error $error")
        })
    }

    private fun loadModel(spec: ModelSpec, endpoint: String, gpu: Boolean) {
        notice("Loading the on-device voice model.")
        val store = ModelStore(getExternalFilesDir("models")!!)
        modelThread.execute {
            val problem = if (store.verified(spec)) null else store.verify(spec) { msg -> main.post { log(msg) } }
            if (problem != null) { main.post { fatal("${spec.label}: $problem") }; return@execute }
            val m = LocalModel(spec, store.file(spec), cacheDir, gpu)
            try {
                val t0 = System.currentTimeMillis(); m.load(); val loadMs = System.currentTimeMillis() - t0
                // Readiness means a real local generation succeeded, not merely that a file exists.
                val t1 = System.currentTimeMillis()
                val raw = m.generate(Prompts.DECIDE_SYSTEM, Prompts.decide(Prompts.context(emptyList(), null, emptyList(), emptyList()), "Hello, can you hear me?"))
                val warmMs = System.currentTimeMillis() - t1
                main.post {
                    model = m
                    log("MODEL: ${m.description}; load ${loadMs} ms; warmup ${warmMs} ms -> ${raw.trim().take(80)}")
                    connect(endpoint)
                }
            } catch (e: Throwable) {
                m.close(); main.post { fatal("${spec.label} failed to run: ${e.javaClass.simpleName} ${e.message?.take(120)}") }
            }
        }
    }

    private fun connect(endpoint: String) {
        if (endpoint.isBlank()) return fatal("no Session Orchestrator address is set")
        link = OrchestratorLink(endpoint, { m -> main.post { inbound(m) } }, { reason -> main.post { linkClosed(reason) } }).also { it.connect() }
        ready = true
        update("Listening")
        notice("Ready. Everything you say is recognized on this phone.")
    }

    // --- Inbound context and speech requests ---------------------------------------------------

    private fun inbound(m: Inbound) {
        when (m) {
            is Inbound.Say -> { val r = core.onSay(m.delegationId, m.content); log("SAY ${r.id} (${m.delegationId ?: "unsolicited"}): ${m.content}") }
            is Inbound.Brief -> { core.onBrief(m.content); log("BRIEF: ${m.content}") }
            is Inbound.Guidance -> { core.onGuidance(m.content); log("GUIDANCE: ${m.content}") }
            is Inbound.Fixture -> { log("FIXTURE ORCHESTRATOR: ${m.description}"); notice("Connected to a test fixture, not your real sessions.") }
            is Inbound.Unsupported -> log("UNSUPPORTED by this app: ${m.type}")
        }
        pump()
    }

    private fun linkClosed(reason: String) {
        if (stopped) return
        log("LINK $reason")
        ready = false
        notice("I lost the connection to your sessions. Their work keeps going.")
        update("Disconnected")
    }

    // --- Caller input ---------------------------------------------------------------------------

    private fun caller(text: String) {
        if (!ready || text.isBlank()) return
        val endMs = link!!.nowMs()
        lastCallerEndMs = endMs
        link!!.callerSaid(text, callerStartMs.coerceAtMost(endMs), endMs)
        if (core.inFlight != null) model?.cancelCurrent()
        when (val route = core.onCaller(text)) {
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
        log("LOCAL STOP ($source): speech stops; no Prompt Turn is cancelled")
        core.stopLocal()
        ackPending = false
        model?.cancelCurrent()
        tts?.stop()
    }

    // --- Scheduler: one model job at a time, then playback, then listening ----------------------

    private fun pump() {
        if (!ready || stopped) return
        if (ackPending) {
            val ack = core.acknowledge() ?: return  // still waiting on a playback receipt
            ackPending = false
            return speak(ack)
        }
        val job = core.nextJob()
        if (job != null) {
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
        when (job) {
            is DecideJob -> when (val d = core.completeDecide(job, raw)) {
                null -> log("DECIDE discarded as stale (${ms} ms)")
                is Decision.Reply -> { log("DECIDE reply (${ms} ms): ${d.utterance.text}"); speak(d.utterance) }
                is Decision.Handoff -> { log("DECIDE -> HANDOFF ${d.delegationId} [${d.reason}] (${ms} ms) raw=${raw?.trim()?.take(80)}"); handoff(d.delegationId) }
            }
            is PhraseJob -> when (val u = core.completePhrase(job, raw)) {
                null -> log("PHRASE ${job.requestId} discarded as stale (${ms} ms)")
                else -> { log("PHRASE ${job.requestId} -> ${u.id} ${u.kind}${u.note?.let { " [$it]" } ?: ""} (${ms} ms): ${u.text} | raw=${raw?.trim()?.take(120)}"); speak(u) }
            }
        }
        pump()
    }

    // --- Playback receipts ----------------------------------------------------------------------

    private fun speak(u: Utterance) {
        stopListening()
        speechStartMs = link?.nowMs() ?: 0
        spokenChars.remove(u.id)
        tts!!.speak(u.text, TextToSpeech.QUEUE_FLUSH, Bundle(), u.id)
        update("Speaking")
        if (duplexHeadset()) startListening()  // barge-in is only safe without a loudspeaker echo
    }

    private fun notice(text: String) {
        noticeSpeaking++
        stopListening()
        tts?.speak(text, TextToSpeech.QUEUE_ADD, Bundle(), "notice-${++noticeSeq}")
    }

    private val progress = object : UtteranceProgressListener() {
        override fun onStart(id: String) {}
        override fun onRangeStart(id: String, start: Int, end: Int, frame: Int) { synchronized(spokenChars) { spokenChars[id] = end } }
        override fun onDone(id: String) = main.post { finished(id, true) }.let {}
        @Deprecated("Deprecated in Java") override fun onError(id: String) = main.post { finished(id, false) }.let {}
        override fun onStop(id: String, interrupted: Boolean) = main.post { finished(id, false) }.let {}
    }

    private fun finished(id: String, completed: Boolean) {
        if (id.startsWith("notice-")) { noticeSpeaking = (noticeSpeaking - 1).coerceAtLeast(0); pump(); return }
        val chars = synchronized(spokenChars) { spokenChars.remove(id) }
        val heard = core.playbackFinished(id, completed, chars) ?: return
        if (heard.text.isNotEmpty()) link?.voiceHeard(heard.text, speechStartMs, link!!.nowMs())
        log("PLAYBACK ${heard.utterance.id} ${if (completed) "completed" else "interrupted"}" +
            (heard.utterance.requestId?.let { " for $it" } ?: "") + (if (!completed) ", heard: \"${heard.text}\"" else ""))
        update("Listening")
        pump()
    }

    // --- On-device recognition -----------------------------------------------------------------

    private fun recognitionIntent() = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
        .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
        .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
        .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)

    private fun startListening() {
        if (listening || stopped || !ready) return
        listening = true
        recognizer!!.setRecognitionListener(listener)
        recognizer!!.startListening(recognitionIntent())
    }

    private fun stopListening() {
        if (!listening || duplexHeadset()) return
        listening = false
        recognizer?.cancel()
    }

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {}
        override fun onBeginningOfSpeech() { callerStartMs = link?.nowMs() ?: 0 }
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEndOfSpeech() {}
        override fun onEvent(eventType: Int, params: Bundle?) {}
        override fun onPartialResults(partial: Bundle?) {
            val words = partial?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()
            if (words.isNotBlank() && core.playing != null) { log("BARGE-IN: caller spoke over playback"); tts?.stop() }
        }
        override fun onResults(results: Bundle?) {
            listening = false
            val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()
            if (text.isNotBlank()) caller(text) else pump()
        }
        override fun onError(error: Int) {
            listening = false
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
        log("NOT READY: $reason")
        update("Not ready: $reason")
        ready = false
        tts?.speak("I can't start: $reason.", TextToSpeech.QUEUE_ADD, Bundle(), "notice-fatal")
    }

    private fun update(state: String) =
        getSystemService(NotificationManager::class.java).notify(1, notification(state))

    private fun notification(state: String): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("voice", "Voice Frontend", NotificationManager.IMPORTANCE_LOW))
        return Notification.Builder(this, "voice").setContentTitle("Dogwalk on-device voice").setContentText(state)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now).setOngoing(true).build()
    }

    override fun onDestroy() {
        stopped = true
        ready = false
        link?.close()
        recognizer?.destroy()
        tts?.shutdown()
        modelThread.execute { model?.close() }
        modelThread.shutdown()
        wakeLock?.takeIf { it.isHeld }?.release()
        log("STOPPED")
        super.onDestroy()
    }

    private fun log(line: String) {
        Log.i(TAG, line)
        main.post { synchronized(lines) { lines.addLast(line); while (lines.size > 200) lines.removeFirst() }; listener_?.invoke(line) }
    }

    companion object {
        const val TAG = "DogwalkFrontend"
        const val ACTION_STOP_SPEAKING = "dogwalk.ondevice.STOP_SPEAKING"
        const val ACTION_INJECT = "dogwalk.ondevice.INJECT"
        val lines = ArrayDeque<String>()
        @Volatile var listener_: ((String) -> Unit)? = null
    }
}
