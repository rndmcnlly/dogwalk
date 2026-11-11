package dogwalk.ondevice

import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Content
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.Conversation
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.SamplerConfig
import com.google.ai.edge.litertlm.ThinkingConfig
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * Public, ungated (Apache-2.0) LiteRT-LM builds of Gemma 4, pinned to an exact Hugging Face
 * revision and SHA-256. Laptop MLX checkpoints are a different format and are not used here.
 */
enum class ModelSpec(val label: String, val repo: String, val revision: String, val file: String,
                     val sha256: String, val bytes: Long) {
    E2B("Gemma 4 E2B", "litert-community/gemma-4-E2B-it-litert-lm", "5b472bf479ec0e944b01c214b46fec082b3e8c01",
        "gemma-4-E2B-it.litertlm", "181938105e0eefd105961417e8da75903eacda102c4fce9ce90f50b97139a63c", 2_588_147_712),
    E4B("Gemma 4 E4B", "litert-community/gemma-4-E4B-it-litert-lm", "e75b1386809cda8e0fda1ef93a39f75b70d400b9",
        "gemma-4-E4B-it.litertlm", "0b2a8980ce155fd97673d8e820b4d29d9c7d99b8fa6806f425d969b145bd52e0", 3_659_530_240);

    val url get() = "https://huggingface.co/$repo/resolve/$revision/$file"
    val provenance get() = "$repo@${revision.take(12)}/$file sha256:${sha256.take(12)}"
}

/** The model file's state on this device. Verification hashes once, then trusts a marker. */
class ModelStore(private val dir: File) {
    fun file(spec: ModelSpec) = File(dir, spec.file)
    private fun marker(spec: ModelSpec) = File(dir, spec.file + ".verified")

    fun verified(spec: ModelSpec): Boolean {
        val f = file(spec)
        return f.length() == spec.bytes && marker(spec).takeIf { it.exists() }?.readText() == "${spec.sha256} ${f.lastModified()}"
    }

    /** Hash an adb-pushed or downloaded file. Returns null on success, else the reason. */
    fun verify(spec: ModelSpec, progress: (String) -> Unit): String? {
        val f = file(spec)
        if (!f.exists()) return "model file missing"
        if (f.length() != spec.bytes) return "model file is ${f.length()} bytes, expected ${spec.bytes}"
        progress("Checking ${spec.label} integrity")
        val digest = MessageDigest.getInstance("SHA-256")
        f.inputStream().buffered(1 shl 20).use { input ->
            val buffer = ByteArray(1 shl 20)
            while (true) { val n = input.read(buffer); if (n < 0) break; digest.update(buffer, 0, n) }
        }
        val actual = digest.digest().joinToString("") { "%02x".format(it) }
        if (actual != spec.sha256) return "model SHA-256 mismatch"
        marker(spec).writeText("${spec.sha256} ${f.lastModified()}")
        return null
    }

    /** Pinned HTTPS download for setup only; inference itself never touches the network. */
    fun download(spec: ModelSpec, progress: (String) -> Unit): String? {
        dir.mkdirs()
        val part = File(dir, spec.file + ".part")
        if (dir.usableSpace < (spec.bytes - part.length()).coerceAtLeast(0) + 64_000_000) return "Insufficient storage. Free space for the selected model plus 64 MB, then retry."
        val connection = URL(spec.url).openConnection() as HttpURLConnection
        connection.connectTimeout = 30_000
        connection.readTimeout = 30_000
        if (part.exists()) connection.setRequestProperty("Range", "bytes=${part.length()}-")
        connection.connect()
        val append = connection.responseCode == 206
        if (connection.responseCode !in listOf(200, 206)) return "download failed: HTTP ${connection.responseCode}"
        var done = if (append) part.length() else 0L
        var lastReport = -1L
        connection.inputStream.use { input ->
            java.io.FileOutputStream(part, append).use { out ->
                val buffer = ByteArray(1 shl 20)
                while (true) {
                    val n = input.read(buffer); if (n < 0) break
                    out.write(buffer, 0, n); done += n
                    val pct = done * 100 / spec.bytes
                    if (pct / 10 != lastReport) { lastReport = pct / 10; progress("Downloading ${spec.label}: $pct percent") }
                }
            }
        }
        if (!part.renameTo(file(spec))) return "could not move downloaded model into place"
        return verify(spec, progress)
    }
}

/** One LiteRT-LM engine; jobs run one at a time on the caller's (background) thread. */
class LocalModel(private val spec: ModelSpec, private val modelFile: File, private val cacheDir: File, private val gpu: Boolean) {
    private var engine: Engine? = null
    @Volatile private var current: Conversation? = null
    val description get() = "${spec.provenance} via LiteRT-LM 0.19.1 on ${if (gpu) "GPU" else "CPU"}"

    fun load() {
        val config = EngineConfig(modelPath = modelFile.absolutePath,
            backend = if (gpu) Backend.GPU() else Backend.CPU(), cacheDir = cacheDir.absolutePath)
        engine = Engine(config).also { it.initialize() }
    }

    /** Fresh conversation per job: no hidden cross-job memory, identical system prefix. */
    fun generate(system: String, prompt: String): String {
        val e = engine ?: error("model not loaded")
        val config = ConversationConfig(
            systemInstruction = Contents.of(system),
            samplerConfig = SamplerConfig(topK = 1, topP = 1.0, temperature = 1.0, seed = 0),
            maxOutputToken = 96,
            thinkingConfig = ThinkingConfig(enableThinking = false),
        )
        return e.createConversation(config).use { conversation ->
            current = conversation
            try {
                conversation.sendMessage(prompt).contents.contents.filterIsInstance<Content.Text>().joinToString("") { it.text }
            } finally { current = null }
        }
    }

    /** Ask the runtime to abandon a stale generation early; the epoch guard discards it anyway. */
    fun cancelCurrent() { runCatching { current?.cancelProcess() } }

    fun close() { runCatching { engine?.close() }; engine = null }
}
