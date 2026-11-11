package dogwalk.ondevice

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.os.Bundle
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
 * One-time setup (permissions, model provisioning, Orchestrator address) and a diagnostic log.
 * Normal voice use runs in VoiceService and does not need this screen.
 */
class MainActivity : Activity() {
    private lateinit var log: TextView
    private lateinit var endpoint: EditText
    private lateinit var e4b: RadioButton
    private lateinit var gpu: CheckBox
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
        val e2b = RadioButton(this).apply { text = "${ModelSpec.E2B.label} (${ModelSpec.E2B.bytes / 1_000_000} MB)"; id = 1 }
        e4b = RadioButton(this).apply { text = "${ModelSpec.E4B.label} (${ModelSpec.E4B.bytes / 1_000_000} MB)"; id = 2 }
        models.addView(e2b); models.addView(e4b)
        models.check(if (prefs.getString("model", "E2B") == "E4B") 2 else 1)
        column.addView(models)
        gpu = CheckBox(this).apply { text = "GPU backend (CPU if unchecked)"; isChecked = prefs.getBoolean("gpu", false) }
        column.addView(gpu)
        button("Grant microphone and notifications") {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        button("Download selected model (pinned, about 2.6 to 3.7 GB)") { provision(download = true) }
        button("Verify model file (after adb push)") { provision(download = false) }
        button("Run local model self-test (no audio)") { save(); modelSelfTest() }
        button("Start voice") { save(); startForegroundService(serviceIntent()) }
        button("Stop speaking") { startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_STOP_SPEAKING)) }
        button("Stop voice") { stopService(Intent(this, VoiceService::class.java)) }
        log = TextView(this).apply { setTextIsSelectable(true); textSize = 12f }
        column.addView(log)
        setContentView(ScrollView(this).apply { addView(column) })
        append("Model folder for adb push: ${getExternalFilesDir("models")}")
        synchronized(VoiceService.lines) { VoiceService.lines.forEach(::append) }
        VoiceService.listener_ = { line -> runOnUiThread { append(line) } }
        handleExtras(intent)
    }

    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); handleExtras(intent) }

    /** adb conveniences: `--ez start true`, `--ez stop_speaking true`, `--ez selftest true`, debug-only `--es inject TEXT`. */
    private fun handleExtras(intent: Intent?) {
        if (intent == null) return
        intent.getStringExtra("endpoint")?.let { endpoint.setText(it) }
        if (intent.getBooleanExtra("selftest", false)) modelSelfTest()
        if (intent.getBooleanExtra("start", false)) { save(); startForegroundService(serviceIntent()) }
        if (intent.getBooleanExtra("stop_speaking", false)) startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_STOP_SPEAKING))
        // Debug builds only: injected caller text, labelled by the service as not speech.
        val text = intent.getStringExtra("inject")
        if (text != null && BuildConfig.DEBUG) startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_INJECT).putExtra("text", text))
    }

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
                if (decision is Decision.Reply) core.playbackFinished(decision.utterance.id, true, null)
                for (text in listOf("Fir wants permission to delete the build folder before rebuilding. Should I allow it once, or deny it?",
                                    "Oak finished the test suite, and all forty tests passed.")) {
                    core.onSay(null, text)
                    val job = core.nextJob() as PhraseJob
                    val u = core.completePhrase(job, run(job))!!
                    report("${u.kind}${u.note?.let { " [$it]" } ?: ""} for ${u.requestId}: ${u.text}")
                    core.playbackFinished(u.id, true, null)
                }
            } catch (e: Throwable) {
                report("failed: ${e.javaClass.simpleName} ${e.message?.take(200)}")
            } finally { model.close() }
        }
    }

    private fun append(line: String) { log.append(line + "\n") }

    override fun onDestroy() { VoiceService.listener_ = null; super.onDestroy() }
}
