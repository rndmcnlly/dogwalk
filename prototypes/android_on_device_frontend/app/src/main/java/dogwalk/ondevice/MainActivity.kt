package dogwalk.ondevice

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.TextView
import kotlin.concurrent.thread

/**
 * Silent text chat, plus optional voice setup, model provisioning and diagnostics.
 * Explicit voice use runs in VoiceService and does not need this screen.
 */
class MainActivity : Activity() {
    private lateinit var log: TextView
    private lateinit var endpoint: EditText
    private lateinit var e2b: RadioButton
    private lateinit var e4b: RadioButton
    private lateinit var gpu: CheckBox
    private lateinit var chatColumn: LinearLayout
    private lateinit var chatScroll: ScrollView
    private lateinit var status: TextView
    private lateinit var compose: EditText
    private val rendered = HashSet<String>()
    private val prefs by lazy { getSharedPreferences("setup", MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val column = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 48, 32, 32) }
        fun button(label: String, action: () -> Unit) = column.addView(Button(this).apply { text = label; setOnClickListener { action() } })
        column.addView(TextView(this).apply {
            text = "On-device Voice Frontend (development). Recognition, the language model and speech run on this phone; " +
                "only text goes to the Session Orchestrator."
        })
        endpoint = EditText(this).apply { hint = "ws://127.0.0.1:8770/ws"; setText(prefs.getString("endpoint", "ws://127.0.0.1:8770/ws")) }
        column.addView(endpoint)
        val models = RadioGroup(this)
        e2b = RadioButton(this).apply { text = "${ModelSpec.E2B.label} (${ModelSpec.E2B.bytes / 1_000_000} MB)"; id = 1 }
        e4b = RadioButton(this).apply { text = "${ModelSpec.E4B.label} (${ModelSpec.E4B.bytes / 1_000_000} MB)"; id = 2 }
        models.addView(e2b); models.addView(e4b)
        choose(prefs.getString("model", "E2B")!!)
        column.addView(models)
        gpu = CheckBox(this).apply { text = "GPU backend (CPU if unchecked)"; isChecked = prefs.getBoolean("gpu", false) }
        column.addView(gpu)
        button("Grant microphone and notifications") {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        button("Download selected model (pinned, about 2.6 to 3.7 GB)") { provision(download = true) }
        button("Verify model file (after adb push)") { provision(download = false) }
        button("Run local model self-test (no audio)") { save(); modelSelfTest() }
        button("Enable voice explicitly (microphone and speaker)") {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                append("Grant microphone permission first. Text chat does not need it.")
            } else { save(); startForegroundService(serviceIntent().setAction(VoiceService.ACTION_VOICE)) }
        }
        button("Stop speaking") { startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_STOP_SPEAKING)) }
        button("Stop frontend") { stopService(Intent(this, VoiceService::class.java)) }
        log = TextView(this).apply { setTextIsSelectable(true); textSize = 12f }
        column.addView(log)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(16, 24, 16, 16) }
        root.setOnApplyWindowInsetsListener { view, insets ->
            val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars())
            view.setPadding(16 + bars.left, 24 + bars.top, 16 + bars.right, 16 + bars.bottom)
            insets
        }
        root.addView(TextView(this).apply { text = "Dogwalk: on-device text chat"; textSize = 20f })
        status = TextView(this).apply { text = VoiceService.status; setTextIsSelectable(true) }
        root.addView(status)
        root.addView(Button(this).apply {
            text = "Start / switch to silent text"
            setOnClickListener { save(); startForegroundService(serviceIntent().setAction(VoiceService.ACTION_TEXT)) }
        })
        chatColumn = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        chatScroll = ScrollView(this).apply { addView(chatColumn) }
        root.addView(chatScroll, LinearLayout.LayoutParams(-1, 0, 1f))
        val inputRow = LinearLayout(this)
        compose = EditText(this).apply {
            hint = "Message Dogwalk"; maxLines = 4
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            imeOptions = EditorInfo.IME_ACTION_SEND
            setOnEditorActionListener { _, action, _ -> if (action == EditorInfo.IME_ACTION_SEND) { sendText(); true } else false }
        }
        inputRow.addView(compose, LinearLayout.LayoutParams(0, -2, 1f))
        inputRow.addView(Button(this).apply { text = "Send"; setOnClickListener { sendText() } })
        root.addView(inputRow)
        val setup = ScrollView(this).apply { addView(column); visibility = View.GONE }
        root.addView(Button(this).apply { text = "Setup / voice / diagnostics"; setOnClickListener { setup.visibility = if (setup.visibility == View.GONE) View.VISIBLE else View.GONE } })
        root.addView(setup, LinearLayout.LayoutParams(-1, 260))
        setContentView(root)
        fun silenceClicks(view: View) {
            view.isSoundEffectsEnabled = false
            if (view is android.view.ViewGroup) for (i in 0 until view.childCount) silenceClicks(view.getChildAt(i))
        }
        silenceClicks(root)
        VoiceService.chat.forEach(::renderTurn)
        append("Model folder for adb push: ${getExternalFilesDir("models")}")
        synchronized(VoiceService.lines) { VoiceService.lines.forEach(::append) }
        VoiceService.listener_ = { line -> runOnUiThread { append(line) } }
        handleExtras(intent)
    }

    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); handleExtras(intent) }

    /** adb conveniences: `--es model E2B|E4B`, `--ez start true`, `--ez stop_speaking true`, `--ez selftest true`, debug-only `--es inject TEXT`. */
    private fun handleExtras(intent: Intent?) {
        if (intent == null) return
        intent.getStringExtra("endpoint")?.let { endpoint.setText(it) }
        intent.getStringExtra("model")?.let(::choose)
        if (intent.getBooleanExtra("selftest", false)) modelSelfTest()
        // Legacy start intents are deliberately silent. Voice requires the explicit UI action.
        if (intent.getBooleanExtra("start", false)) { save(); startForegroundService(serviceIntent().setAction(VoiceService.ACTION_TEXT)) }
        if (intent.getBooleanExtra("stop_speaking", false)) startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_STOP_SPEAKING))
        // Debug builds only: injected caller text, labelled by the service as not speech.
        val text = intent.getStringExtra("inject")
        if (text != null && BuildConfig.DEBUG) startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_INJECT).putExtra("text", text))
    }

    private fun choose(model: String) { if (model == ModelSpec.E4B.name) e4b.isChecked = true else e2b.isChecked = true }

    private fun selected() = if (e4b.isChecked) ModelSpec.E4B else ModelSpec.E2B

    private fun serviceIntent() = Intent(this, VoiceService::class.java)
        .putExtra("endpoint", endpoint.text.toString().trim()).putExtra("model", selected().name).putExtra("gpu", gpu.isChecked)

    private fun save() = prefs.edit().putString("endpoint", endpoint.text.toString().trim())
        .putString("model", selected().name).putBoolean("gpu", gpu.isChecked).apply()

    private fun provision(download: Boolean) {
        val spec = selected()
        val store = ModelStore(getExternalFilesDir("models")!!)
        thread {
            val report = { msg: String -> runOnUiThread { append(msg) } }
            val problem = runCatching { if (download) store.download(spec, report) else store.verify(spec, report) }
                .getOrElse { "${it.javaClass.simpleName}: ${it.message}" }
            report(problem?.let { "${spec.label}: $it" } ?: "${spec.label} verified: ${spec.provenance}")
        }
    }

    /**
     * Real local inference without microphone or speaker: the same prompts the service uses, so
     * a device lacking on-device recognition or an offline voice can still prove the model runs.
     */
    private fun modelSelfTest() {
        val spec = selected()
        val store = ModelStore(getExternalFilesDir("models")!!)
        val useGpu = gpu.isChecked
        thread {
            val report = { msg: String -> android.util.Log.i(VoiceService.TAG, "SELFTEST $msg"); runOnUiThread { append("SELFTEST $msg") } }
            if (!store.verified(spec)) store.verify(spec, report)?.let { return@thread report("not ready: $it") }
            val model = LocalModel(spec, store.file(spec), cacheDir, useGpu)
            try {
                var t = System.currentTimeMillis(); model.load(); report("loaded ${model.description} in ${System.currentTimeMillis() - t} ms")
                val core = FrontendCore()
                core.onBrief("Fir is rebuilding the project. Oak is idle.")
                val cases = mutableListOf<Job>()
                core.onCaller("hi, can you hear me okay"); cases += core.nextJob()!!
                fun run(job: Job): String { t = System.currentTimeMillis(); return model.generate(job.system, job.prompt).also { report("${job.javaClass.simpleName} ${System.currentTimeMillis() - t} ms raw=${it.trim().take(160)}") } }
                val decide = cases[0] as DecideJob
                val decision = core.completeDecide(decide, run(decide))
                report("decision: $decision")
                if (decision is Decision.Reply) core.textDisplayed(decision.utterance.id)
                for (text in listOf("Fir wants permission to delete the build folder before rebuilding. Should I allow it once, or deny it?",
                                    "Oak finished the test suite, and all forty tests passed.")) {
                    core.onSay(null, text)
                    val job = core.nextJob() as PhraseJob
                    val u = core.completePhrase(job, run(job))!!
                    report("${u.kind}${u.note?.let { " [$it]" } ?: ""} for ${u.requestId}: ${u.text}")
                    core.textDisplayed(u.id)
                }
            } catch (e: Throwable) {
                report("failed: ${e.javaClass.simpleName} ${e.message?.take(200)}")
            } finally { model.close() }
        }
    }

    private fun append(line: String) { log.append(line + "\n") }

    private fun sendText() {
        val text = compose.text.toString().trim()
        if (text.isEmpty()) return
        if (!VoiceService.status.startsWith("Text ready")) {
            status.text = "Start silent text and wait for readiness before sending. Your message is kept here."
            return
        }
        startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_SEND).putExtra("text", text))
        compose.setText("")
    }

    private fun renderTurn(turn: VoiceService.Companion.ChatTurn) {
        if (!rendered.add(turn.id)) return
        chatColumn.addView(TextView(this).apply {
            text = "${turn.role}\n${turn.text}"; textSize = 17f
            setPadding(16, 16, 16, 24); setTextIsSelectable(true)
            setBackgroundColor(if (turn.role == "You") 0xffe8eef4.toInt() else 0xfff3f3f3.toInt())
        })
        chatScroll.post { chatScroll.smoothScrollTo(0, chatColumn.height) }
    }

    override fun onResume() {
        super.onResume()
        VoiceService.chat.forEach(::renderTurn)
        status.text = VoiceService.status
        VoiceService.chatListener = ::renderTurn
        VoiceService.stateListener = { status.text = it }
        // Merely opening/restoring the app must silence an existing voice service as well.
        startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_RESTORE_TEXT))
        startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_REFRESH))
    }

    override fun onPause() {
        VoiceService.chatListener = null
        VoiceService.stateListener = null
        super.onPause()
    }

    override fun onDestroy() { VoiceService.listener_ = null; super.onDestroy() }
}
