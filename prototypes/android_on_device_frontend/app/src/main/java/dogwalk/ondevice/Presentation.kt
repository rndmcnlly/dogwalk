package dogwalk.ondevice

/** Phone-observed presentation, not ACP or provider state. */
enum class LinkState(val label: String) { DISCONNECTED("Disconnected"), CONNECTING("Connecting…"), CONNECTED("Connected"), ERROR("Connection error") }
enum class LocalState(val label: String) { UNKNOWN("Not checked"), MISSING("Model missing"), INSTALLED("Installed · not loaded"), VERIFYING("Verifying…"), LOADING("Loading…"), WARMING("Warming up…"), READY("Model ready"), ERROR("Model error") }
data class PresentationState(
    val link: LinkState = LinkState.DISCONNECTED,
    val local: LocalState = LocalState.UNKNOWN,
    val mode: InteractionMode = InteractionMode.TEXT,
    val accepting: Boolean = false,
    val running: Boolean = false,
    val activity: String = "Silent",
    val microphone: Boolean = false,
    val speaker: Boolean = false,
    val problem: String? = null,
) {
    val canSend get() = mode == InteractionMode.TEXT && local == LocalState.READY && link == LinkState.CONNECTED && accepting
}
enum class EventKind { USER, ASSISTANT, CONNECTION, READINESS, CONTROL, DIAGNOSTIC }
data class TimelineEvent(val id: String, val sequence: Long, val observedAt: Long,
    val kind: EventKind, val summary: String, val detail: String = "", val provenance: String = "",
    val requestId: String? = null, val warning: Boolean = false)

/** Append at the source, never merge old logs into fabricated chronology. Main-thread owned. */
class Timeline(private val limit: Int = 300, private val clock: () -> Long = System::currentTimeMillis) {
    private var sequence = 0L
    private val items = ArrayDeque<TimelineEvent>()
    fun snapshot() = items.toList()
    fun append(kind: EventKind, summary: String, detail: String = "", provenance: String = "", requestId: String? = null,
               warning: Boolean = false, id: String? = null): TimelineEvent {
        if (id != null) items.firstOrNull { it.id == id }?.let { return it }
        val n = ++sequence
        return TimelineEvent(id ?: "event-$n", n, clock(), kind, summary, detail.take(1200), provenance, requestId, warning).also {
            items.addLast(it); while (items.size > limit) items.removeFirst()
        }
    }
}

object DisplayPolicy {
    fun visible(resumed: Boolean, modal: Boolean, windowFocused: Boolean) = resumed && !modal && windowFocused
}
