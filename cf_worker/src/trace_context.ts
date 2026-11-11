// W3C Trace Context (Level 1, Recommendation) identities for live operations (issue #8, B1).
//
// Operations mint their context when they start and carry it explicitly, never read back from
// mutable call state at completion. No imports, so Node tests load this module directly.

export type TraceContext = { traceId: string; spanId: string };

function randomHex(bytes: number): string {
  // All-zero IDs are invalid; at 64 random bits this loop essentially never repeats.
  for (;;) {
    const id = crypto.getRandomValues(new Uint8Array(bytes));
    if (id.some((b) => b !== 0)) return Array.from(id, (b) => b.toString(16).padStart(2, "0")).join("");
  }
}

export const newTraceId = (): string => randomHex(16);
export const newSpanId = (): string => randomHex(8);

/** A new root context: a fresh trace with its first span. */
export const newRootContext = (): TraceContext => ({ traceId: newTraceId(), spanId: newSpanId() });

/** A new span in the parent's trace. */
export const childOf = (parent: TraceContext): TraceContext => ({ traceId: parent.traceId, spanId: newSpanId() });

const TRACE_ID = /^(?!0{32})[0-9a-f]{32}$/;
const SPAN_ID = /^(?!0{16})[0-9a-f]{16}$/;

export const isTraceContext = (value: { traceId?: unknown; spanId?: unknown } | null | undefined): value is TraceContext =>
  typeof value?.traceId === "string" && TRACE_ID.test(value.traceId)
  && typeof value?.spanId === "string" && SPAN_ID.test(value.spanId);

/** `traceparent` header value. Dogwalk records every intended observation, so the sampled flag is set. */
export function formatTraceparent(context: TraceContext): string {
  return `00-${context.traceId}-${context.spanId}-01`;
}

/**
 * Strict version-00 parsing. Anything else (unknown versions included) is rejected rather than
 * partially trusted; a caller then starts no context instead of continuing a malformed one.
 */
export function parseTraceparent(value: string | null | undefined): TraceContext | null {
  const match = /^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$/.exec(value ?? "");
  if (!match) return null;
  const context = { traceId: match[1], spanId: match[2] };
  return isTraceContext(context) ? context : null;
}

/** Journal detail fields for a span observation; the projection reads exactly these. */
export function spanFields(context: TraceContext | null, parent: TraceContext | null = null): Record<string, string> {
  if (!context) return {};
  return { trace_id: context.traceId, span_id: context.spanId,
    ...(parent && parent.traceId === context.traceId ? { parent_span_id: parent.spanId } : {}) };
}
