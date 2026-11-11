package dogwalk.ondevice

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.view.inputmethod.EditorInfo
import android.widget.*
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread

/** Native conversation surface. Raw debug logs are deliberately not a UI data source. */
class MainActivity : Activity() {
    private val ink = Color.rgb(24, 39, 55)
    private val blue = Color.rgb(36, 91, 137)
    private val pale = Color.rgb(248, 250, 252)
    private val prefs by lazy { getSharedPreferences("setup", MODE_PRIVATE) }
    private lateinit var column: LinearLayout
    private lateinit var scroll: ScrollView
    private lateinit var compose: EditText
    private lateinit var health: TextView
    private lateinit var mode: TextView
    private lateinit var activity: TextView
    private lateinit var hint: TextView
    private lateinit var connect: Button
    private lateinit var enableVoice: Button
    private lateinit var send: Button
    private lateinit var latest: Button
    private lateinit var composer: LinearLayout
    private lateinit var voicePanel: LinearLayout
    private lateinit var menu: Button
    private var resumed = false
    private var modal = false
    private var follow = true
    private var inspecting = false
    private var unread = 0
    private var sheetFeedback: TextView? = null
    private val rendered = HashSet<String>()
    private val expanded = HashSet<String>()
    private val time = SimpleDateFormat("HH:mm:ss", Locale.getDefault())
    private fun dp(n: Int) = (n * resources.displayMetrics.density).toInt()
    private fun text(value: String, size: Float = 14f) = TextView(this).apply { text = value; textSize = size; setTextColor(ink) }
    private fun stack() = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
    private fun background(color: Int, radius: Int = 12) = GradientDrawable().apply { setColor(color); cornerRadius = dp(radius).toFloat() }
    private fun button(label: String, primary: Boolean = false, action: () -> Unit) = Button(this).apply {
        text = label; textSize = 14f; isAllCaps = false; minHeight = dp(48); minimumHeight = dp(48)
        setPadding(dp(12), dp(6), dp(12), dp(6)); setTextColor(if (primary) Color.WHITE else blue)
        background = background(if (primary) blue else Color.WHITE)
        isSoundEffectsEnabled = false; setOnClickListener { action() }
    }
    private fun row() = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL; setPadding(dp(12), dp(4), dp(12), dp(4)) }
    private fun silenceClicks(view: View) {
        view.isSoundEffectsEnabled = false
        if (view is android.view.ViewGroup) for (i in 0 until view.childCount) silenceClicks(view.getChildAt(i))
    }
    private fun service(action: String) = Intent(this, VoiceService::class.java).setAction(action)
    private fun startText() { if (!toolBusy) startForegroundService(config(VoiceService.ACTION_TEXT)) }
    private fun config(action: String) = service(action).putExtra("endpoint", prefs.getString("endpoint", "ws://127.0.0.1:8770/ws"))
        .putExtra("model", prefs.getString("model", "E2B")).putExtra("gpu", prefs.getBoolean("gpu", false))
    private fun selected() = ModelSpec.valueOf(prefs.getString("model", "E2B")!!)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.setDecorFitsSystemWindows(false)
        val root = stack().apply { setBackgroundColor(pale) }
        // Constrain the upper surface as a whole: even very short windows cannot push the
        // composer below the IME. Upper content may clip in extreme landscape/font settings.
        val content = stack()
        root.addView(content, LinearLayout.LayoutParams(-1, 0, 1f))
        // Edge-to-edge is owned here: IME and navigation overlap, never add their heights.
        root.setOnApplyWindowInsetsListener { v, insets ->
            val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
            val ime = insets.getInsets(WindowInsets.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, ime.bottom))
            WindowInsets.CONSUMED
        }
        val header = row()
        val title = stack().apply { addView(text("Dogwalk", 20f)); addView(text("Development · scripted fixture", 12f).apply { setTextColor(0xff665132.toInt()) }) }
        header.addView(title, LinearLayout.LayoutParams(0, -2, 1f))
        menu = button("Menu") { settings() }; header.addView(menu); content.addView(header)
        val readiness = row().apply { setBackgroundColor(0xffedf2f6.toInt()) }
        health = text("").apply { accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE }
        readiness.addView(health, LinearLayout.LayoutParams(0, -2, 1f))
        connect = button("Start text", true) { if (VoiceService.presentation.local == LocalState.MISSING) settings() else startText() }
        readiness.addView(connect); content.addView(readiness)
        val modeRow = row()
        mode = text("", 12f); modeRow.addView(mode, LinearLayout.LayoutParams(0, -2, 1f))
        enableVoice = button("Enable voice") { if (VoiceService.presentation.mode == InteractionMode.VOICE) startText() else confirmVoice() }
        modeRow.addView(enableVoice); content.addView(modeRow)
        column = stack().apply { setPadding(dp(12), dp(8), dp(12), dp(8)) }
        scroll = ScrollView(this).apply {
            contentDescription = "Conversation and useful diagnostics"; isFillViewport = true; addView(column)
            setOnScrollChangeListener { _, _, _, _, _ ->
                follow = column.height - scrollY - height < dp(64)
                if (follow) { unread = 0; latest.visibility = View.GONE }
            }
            addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> if (follow) post { scrollTo(0, column.height) } }
        }
        content.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        latest = button("Latest") { bottom() }.apply { visibility = View.GONE; accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE }
        content.addView(latest)
        composer = stack().apply { setPadding(dp(12), dp(8), dp(12), dp(8)); setBackgroundColor(Color.WHITE) }
        val input = LinearLayout(this).apply { gravity = Gravity.BOTTOM }
        compose = object : EditText(this) {
            override fun onCreateInputConnection(outAttrs: EditorInfo): android.view.inputmethod.InputConnection? =
                super.onCreateInputConnection(outAttrs).also { outAttrs.imeOptions = outAttrs.imeOptions and EditorInfo.IME_FLAG_NO_ENTER_ACTION.inv() }
        }.apply {
            id = View.generateViewId(); hint = "Message Dogwalk"; contentDescription = "Message Dogwalk"
            textSize = 16f; setTextColor(ink); setHintTextColor(0xff526273.toInt()); minHeight = dp(48)
            maxLines = 4; inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE or android.text.InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            imeOptions = EditorInfo.IME_ACTION_SEND; setHorizontallyScrolling(false)
            background = background(pale); setPadding(dp(12), dp(10), dp(12), dp(10))
            setText(savedInstanceState?.getString("draft") ?: retainedDraft)
            setOnEditorActionListener { _, action, _ -> if (action == EditorInfo.IME_ACTION_SEND) { sendText(); true } else false }
        }
        input.addView(compose, LinearLayout.LayoutParams(0, -2, 1f).apply { marginEnd = dp(8) })
        send = button("Send", true) { sendText() }; input.addView(send); composer.addView(input)
        hint = text("", 12f).apply { setPadding(0, dp(4), 0, 0) }; composer.addView(hint); root.addView(composer)
        voicePanel = stack().apply { setPadding(dp(12), dp(8), dp(12), dp(8)); setBackgroundColor(0xffedf2f6.toInt()) }
        activity = text(""); voicePanel.addView(activity)
        val actions = row(); actions.addView(button("Stop speaking") { startService(service(VoiceService.ACTION_STOP_SPEAKING)) })
        actions.addView(button("Switch to text", true) { startText() }); voicePanel.addView(actions); root.addView(voicePanel)
        setContentView(root)
        silenceClicks(root)
        root.post {
            window.insetsController?.setSystemBarsAppearance(
                android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or android.view.WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS,
                android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or android.view.WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS)
        }
        scanInstalled()
        VoiceService.timeline.snapshot().forEach(::render)
        savedInstanceState?.let { saved -> scroll.post { follow = saved.getBoolean("follow", true); scroll.scrollTo(0, saved.getInt("scroll")) } }
        if (VoiceService.timeline.snapshot().isEmpty()) column.addView(stack().apply {
            tag = "welcome"; setPadding(dp(8), dp(16), dp(8), dp(16))
            if (VoiceService.presentation.local == LocalState.MISSING) {
                addView(text("Install a local model to begin", 20f))
                addView(text("Gemma E2B needs about 2.6 GB. Text uses no microphone or speech output. You can also verify a file already on this device."))
                addView(button("Set up model", true) { settings() })
            } else addView(text("Your conversation stays here while this process runs. Start text to prepare the local model and connect. No real coding runs through this fixture."))
        })
        handleExtras(intent)
    }

    private fun scanInstalled() {
        if (VoiceService.presentation.running || toolBusy) return
        val file = ModelStore(getExternalFilesDir("models")!!).file(selected())
        VoiceService.presentation = VoiceService.presentation.copy(local = if (file.exists()) LocalState.INSTALLED else LocalState.MISSING)
        update(VoiceService.presentation)
    }
    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); handleExtras(intent) }
    private fun handleExtras(intent: Intent?) {
        if (intent == null) return
        val edit = prefs.edit()
        intent.getStringExtra("endpoint")?.let { edit.putString("endpoint", it) }
        intent.getStringExtra("model")?.takeIf { it in ModelSpec.entries.map { s -> s.name } }?.let { edit.putString("model", it) }
        edit.apply()
        if (intent.getBooleanExtra("start", false)) startText()
        if (intent.getBooleanExtra("selftest", false)) selfTest()
        if (intent.getBooleanExtra("stop_speaking", false)) startService(service(VoiceService.ACTION_STOP_SPEAKING))
        intent.getStringExtra("inject")?.let { if (BuildConfig.DEBUG) startService(service(VoiceService.ACTION_INJECT).putExtra("text", it)) }
    }
    private fun update(s: PresentationState) {
        health.text = "Link   ${s.link.label}\nLocal  ${s.local.label}"
        connect.visibility = if (s.link == LinkState.CONNECTED && s.local == LocalState.READY) View.GONE else View.VISIBLE
        connect.text = when { s.local == LocalState.MISSING -> "Set up model"; s.local in listOf(LocalState.VERIFYING, LocalState.LOADING, LocalState.WARMING) -> "Preparing…"; s.link == LinkState.CONNECTING -> "Connecting…"; s.running -> "Retry"; else -> "Start text" }
        connect.isEnabled = !toolBusy && s.link != LinkState.CONNECTING && s.local !in listOf(LocalState.VERIFYING, LocalState.LOADING, LocalState.WARMING)
        val voice = s.mode == InteractionMode.VOICE
        mode.text = if (voice) "Voice · explicitly enabled" else "Text · microphone and speaker off"
        enableVoice.text = if (voice) "Switch to text" else "Enable voice"
        enableVoice.isEnabled = voice || s.canSend
        composer.visibility = if (voice) View.GONE else View.VISIBLE; voicePanel.visibility = if (voice) View.VISIBLE else View.GONE
        activity.text = if (s.link != LinkState.CONNECTED) "Voice unavailable · link disconnected\nAudio may still be active. Switch to text turns audio off." else "${s.activity}\nMicrophone ${if (s.microphone) "listening (callback confirmed)" else "not confirmed listening"} · speaker ${if (s.speaker) "playing (callback confirmed)" else "not confirmed playing"}"
        send.isEnabled = s.canSend
        send.alpha = if (s.canSend) 1f else .5f
        hint.text = when { s.problem != null -> s.problem; s.canSend -> "Silent text. Fixture only, no real coding."; s.local == LocalState.MISSING -> "Set up a local model to send. Your draft stays here."; !s.running -> "Start text to load the model. Your draft stays here."; s.local != LocalState.READY -> "Local model preparing. Your draft stays here."; else -> "Connect to send. Your draft stays here." }
    }
    private fun sendText() {
        val value = compose.text.toString().trim()
        if (value.isEmpty() || !VoiceService.presentation.canSend) return
        startService(service(VoiceService.ACTION_SEND).putExtra("text", value))
        compose.setText(""); retainedDraft = ""; bottom()
    }
    private fun bottom() { follow = true; inspecting = false; unread = 0; latest.visibility = View.GONE; scroll.post { scroll.scrollTo(0, column.height) } }
    private fun render(e: TimelineEvent) {
        if (!rendered.add(e.id)) return
        column.findViewWithTag<View>("welcome")?.let { column.removeView(it) }
        val own = e.kind == EventKind.USER
        val conversation = own || e.kind == EventKind.ASSISTANT
        val item = stack().apply { tag = e.id; setPadding(dp(12), dp(if (conversation) 10 else 4), dp(12), dp(if (conversation) 10 else 4)); background = background(if (own) 0xffe8eef4.toInt() else if (conversation) 0xfff0f2f4.toInt() else if (e.warning) 0xfffff2e4.toInt() else 0xffedf2f6.toInt()) }
        if (conversation) {
            item.addView(text(if (own) "You" else "Dogwalk", 13f).apply { setTypeface(null, android.graphics.Typeface.BOLD) })
            item.addView(text(e.summary, 16f).apply { setTextIsSelectable(true); setOnLongClickListener { follow = false; inspecting = true; false } })
            item.addView(text("${e.provenance} · ${time.format(Date(e.observedAt))}", 11f))
            if (e.detail.isNotEmpty()) item.addView(text(e.detail, 12f))
        } else {
            val stamp = "${if (e.warning) "Warning" else "Info"} · ${e.kind.name.lowercase()} · ${time.format(Date(e.observedAt))} observed"
            fun label(open: Boolean) = "${if (open) "−" else "+"} ${e.summary}\n$stamp"
            val detail = text(e.detail, 13f).apply { visibility = if (expanded.contains(e.id)) View.VISIBLE else View.GONE }
            item.addView(button(label(expanded.contains(e.id))) {
                val open = detail.visibility != View.VISIBLE
                follow = false
                inspecting = open
                if (open) expanded.add(e.id) else expanded.remove(e.id)
                detail.visibility = if (open) View.VISIBLE else View.GONE
                (item.getChildAt(0) as Button).text = label(open)
                item.getChildAt(0).stateDescription = if (open) "Expanded" else "Collapsed"
            }.apply { stateDescription = if (expanded.contains(e.id)) "Expanded" else "Collapsed"; textSize = 13f; setPadding(0, dp(4), 0, dp(4)); gravity = Gravity.START or Gravity.CENTER_VERTICAL; background = background(Color.TRANSPARENT); contentDescription = "${e.summary}. $stamp. Expand or collapse diagnostic details" })
            item.addView(detail)
        }
        column.addView(item, LinearLayout.LayoutParams(-1, -2).apply { bottomMargin = dp(8); if (own) marginStart = dp(22) else if (conversation) marginEnd = dp(22) })
        // Match bounded source retention without reflowing the visible anchor unnecessarily.
        while (column.childCount > 300) { val first = column.getChildAt(0); rendered.remove(first.tag as? String); val h = first.height + dp(8); column.removeViewAt(0); if (!follow) scroll.scrollBy(0, -h) }
        if ((follow && !inspecting) || own) bottom() else { unread++; latest.text = "$unread new · Latest"; latest.visibility = View.VISIBLE }
    }
    private fun modalClosed() { modal = false; sheetFeedback = null; menu.requestFocus(); refreshDisplay() }
    private fun refreshDisplay() { if (resumed && !modal && hasWindowFocus()) startService(service(VoiceService.ACTION_REFRESH)) }
    private fun confirmVoice() {
        modal = true
        AlertDialog.Builder(this).setTitle("Enable voice?")
            .setMessage("Enable microphone and offline speech output. Only text goes to the development fixture. Opening or restoring this screen switches back to silent text. Stop speaking never cancels Agent work.")
            .setNegativeButton("Cancel", null).setPositiveButton("Enable microphone and speaker") { _, _ ->
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 7)
                else startForegroundService(config(VoiceService.ACTION_VOICE))
            }.create().apply { setOnDismissListener { modalClosed() }; show(); silenceClicks(window!!.decorView) }
    }
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 7 && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) confirmVoice()
        else if (requestCode == 7) VoiceService.event(EventKind.READINESS, "Microphone permission denied", "Text still works without microphone permission.", warning = true)
    }
    private fun settings() {
        if (modal) return
        modal = true
        val body = stack().apply { setPadding(dp(20), dp(8), dp(20), dp(12)) }
        body.addView(text("Development · choose isolated fixture or Adam-only real Orchestrator. Real mode requires an operator-provisioned app credential, never admin/provider keys."))
        body.addView(text("Connection", 18f))
        val endpoint = EditText(this).apply { hint = "Saved endpoint"; contentDescription = "Saved endpoint"; setText(prefs.getString("endpoint", "ws://127.0.0.1:8770/ws")); inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI }
        body.addView(endpoint)
        val real = CheckBox(this).apply { text = "Real Orchestrator · authenticated WSS"; isChecked = prefs.getBoolean("app_real", false); minHeight = dp(48) }; body.addView(real)
        val credential = EditText(this).apply {
            hint = "Replace app credential (blank keeps saved)"; contentDescription = "Replace app credential"
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD
            imeOptions = android.view.inputmethod.EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING or android.view.inputmethod.EditorInfo.IME_FLAG_NO_EXTRACT_UI
            importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
            isSaveEnabled = false
        }; body.addView(credential)
        body.addView(button("Clear saved app credential") { AppCredential(this).clear(); credential.text.clear(); sheetFeedback?.text = "Credential cleared locally. Stop frontend to detach; server rotation revokes other copies." })
        body.addView(text("Local model", 18f))
        val models = RadioGroup(this)
        ModelSpec.entries.forEach { spec -> models.addView(RadioButton(this).apply { id = View.generateViewId(); tag = spec.name; text = "${spec.label} · ${String.format(Locale.getDefault(), "%.1f", spec.bytes / 1_000_000_000.0)} GB"; minHeight = dp(48); isChecked = selected() == spec }) }
        body.addView(models)
        val gpu = CheckBox(this).apply { text = "GPU · experimental (unchecked: CPU)"; isChecked = prefs.getBoolean("gpu", false); minHeight = dp(48) }; body.addView(gpu)
        body.addView(text("Applies on next start. Stop frontend before changing model/backend. Installed is not ready: startup verifies, loads and performs a real warmup."))
        fun save(): Boolean {
            val address = endpoint.text.toString().trim()
            val secret = credential.text.toString().trim()
            try {
                val uri = java.net.URI(address)
                require(uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null)
                if (real.isChecked) AppCredential.requireRealEndpoint(address)
                if (secret.isNotEmpty()) { require(real.isChecked); AppCredential(this).save(address, secret) }
                credential.text.clear()
                val name = models.findViewById<RadioButton>(models.checkedRadioButtonId).tag as String
                prefs.edit().putString("endpoint", address).putString("model", name).putBoolean("gpu", gpu.isChecked).putBoolean("app_real", real.isChecked).apply()
                return true
            } catch (_: Exception) { credential.text.clear(); sheetFeedback?.text = "Not saved: real mode needs wss://host/app/frontend and a valid app credential. No URLs with credentials."; return false }
        }
        body.addView(button("Save for next start", true) { if (save()) { sheetFeedback?.text = "Saved for next start. Active model/backend unchanged."; scanInstalled() } })
        body.addView(button("Download pinned model") { if (!VoiceService.presentation.running) { save(); provision(true) } else sheetFeedback?.text = "Stop frontend first." })
        body.addView(button("Verify existing model file") { if (!VoiceService.presentation.running) { save(); provision(false) } else sheetFeedback?.text = "Stop frontend first." })
        body.addView(text("Pinned HTTPS download, about 2.6–3.7 GB. Keep this process running. No cancel button: closing Menu does not cancel; interrupted downloads resume on next attempt. Verify also accepts an adb-pushed file.", 12f))
        body.addView(button("Local self-test · no audio") { save(); selfTest() })
        body.addView(button("Stop frontend") { stopService(service(VoiceService.ACTION_TEXT)); sheetFeedback?.text = "Stopping frontend. Model files and settings retained." })
        sheetFeedback = text(if (toolBusy) "Local setup/test is running." else "No download starts until requested.").apply { accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE }; body.addView(sheetFeedback)
        AlertDialog.Builder(this).setTitle("Setup and tools").setView(ScrollView(this).apply { addView(body) }).setPositiveButton("Done", null)
            .create().apply { setOnDismissListener { modalClosed() }; show(); window!!.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE); silenceClicks(window!!.decorView) }
    }
    private fun provision(download: Boolean) {
        if (toolBusy || VoiceService.presentation.running) { sheetFeedback?.text = "Stop frontend and wait for other tools first."; return }
        toolBusy = true; val spec = selected(); val store = ModelStore(getExternalFilesDir("models")!!)
        update(VoiceService.presentation)
        thread {
            val progress: (String) -> Unit = { value -> runOnUiThread { sheetFeedback?.text = value } }
            val error = runCatching { if (download) store.download(spec, progress) else store.verify(spec, progress) }.getOrElse { "Setup failed (${it.javaClass.simpleName}). Check network and free storage, then retry." }
            runOnUiThread {
                toolBusy = false
                val result = error ?: "${spec.label} verified. Start text to load and warm the model."
                sheetFeedback?.text = result
                VoiceService.event(EventKind.READINESS, if (error == null) "Model file verified" else "Model setup failed", result, warning = error != null)
                scanInstalled()
            }
        }
    }
    private fun selfTest() {
        if (VoiceService.presentation.running || VoiceService.engineOwned || toolBusy) { sheetFeedback?.text = "Stop frontend and wait for engine release before self-test: never allocate a second engine."; return }
        toolBusy = true; val spec = selected(); val gpu = prefs.getBoolean("gpu", false)
        sheetFeedback?.text = "Running local self-test, no audio."; update(VoiceService.presentation)
        thread {
            val store = ModelStore(getExternalFilesDir("models")!!)
            val result = runCatching {
                check(store.verified(spec)) { "Verify the model file first" }
                val m = LocalModel(spec, store.file(spec), cacheDir, gpu)
                try { val t = System.currentTimeMillis(); m.load(); val raw = m.generate(Prompts.DECIDE_SYSTEM, Prompts.decide(Prompts.context(emptyList(), null, emptyList(), emptyList()), "Hello")); check(raw.isNotBlank()); "Load and real generation succeeded in ${System.currentTimeMillis() - t} ms. No audio." } finally { m.close() }
            }
            runOnUiThread { toolBusy = false; val summary = result.getOrNull() ?: "Self-test failed (${result.exceptionOrNull()!!.javaClass.simpleName}). Verify the selected file and backend."; sheetFeedback?.text = summary; VoiceService.event(EventKind.DIAGNOSTIC, "Local self-test ${if (result.isSuccess) "passed" else "failed"}", summary, warning = result.isFailure); scanInstalled() }
        }
    }
    override fun onResume() {
        super.onResume(); resumed = true
        VoiceService.timeline.snapshot().forEach(::render)
        VoiceService.timelineListener = ::render; VoiceService.stateListener = ::update
        VoiceService.displayVisible = { DisplayPolicy.visible(resumed, modal, hasWindowFocus()) }
        update(VoiceService.presentation)
        startService(service(VoiceService.ACTION_RESTORE_TEXT)); refreshDisplay()
    }
    override fun onWindowFocusChanged(hasFocus: Boolean) { super.onWindowFocusChanged(hasFocus); if (hasFocus && ::compose.isInitialized) refreshDisplay() }
    override fun onSaveInstanceState(outState: Bundle) { outState.putString("draft", compose.text.toString()); outState.putInt("scroll", scroll.scrollY); outState.putBoolean("follow", follow); super.onSaveInstanceState(outState) }
    override fun onPause() { retainedDraft = compose.text.toString(); resumed = false; VoiceService.displayVisible = null; VoiceService.timelineListener = null; VoiceService.stateListener = null; super.onPause() }
    companion object { private var retainedDraft = ""; @Volatile private var toolBusy = false }
}
