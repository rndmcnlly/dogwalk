# Broader local Frontend evaluation

This follows [the initial design bake-off](FRONTEND_BAKEOFF.md). It broadens
evaluation beyond action choice and a single regex pass count, and monitors both
Gemma 4 E4B and E2B through the existing loopback oMLX server. No Android, production
Worker, Orchestrator, ACP Agent, service defaults, or model settings are changed.

## Competing designs

1. **baseline_split**: the previous split checkpoint's model-facing prompts and
   layout, wrapped in the same test scheduler as the alternatives. It still asks
   the routing model whether to announce incoming Orchestrator speech requests.
2. **priority_split**: a refined action-only router for new caller speech and a
   separate plain-text phraser. Incoming Orchestrator speech requests, with no new
   caller utterance, go directly to phrasing. Idle background context uses no model.
3. **event_single**: the same event specialization, but new caller utterances get
   one JSON action-and-speech call rather than sequential routing and phrasing.
   Unsolicited speech requests still get one plain-text phrasing call.

The specialization uses event type, not keyword/intent matching in code. Caller
requests remain model-routed. A speech request from the Orchestrator already
means "something to say", so the refined designs do not ask another model for
permission to announce it. This changes model-facing scheduling, not the remote
Handoff/brief/say contract. The prototypes are not installed into the gateway.

All designs see the same originating events. Refinements add explicit source
boundaries, current-caller priority, revision ordering, and authority distinctions.
Quoted tool output is untrusted data. A failed step differs from a failed task;
cancellation differs from session/history deletion. Existing speech acknowledgement
and stale-completion guards are modeled in the shared harness, not credited as
model capabilities.

## Corpus and independence

`frontend_eval_cases.json` has 28 fresh isolated cases and five conversation traces
containing 17 evaluated events. The corpus was written before this round's model
outputs were observed. Expected actions, regex requirements and prohibitions,
criticality, playback instructions and race triggers are never model input.

The same agent authored cases, refinements, grading and review. This is a bounded
development evaluation, not an independent test or a permanently held-out set.
The previous round motivated the criteria, so this is not unbiased validation.
The manifest freezes corpus/source hashes and prompts before inference.

## Criteria, reported separately

| Dimension | Examples and checks | Important limit |
| --- | --- | --- |
| Intent and authorization | Withdrawing work, ambiguous changes, permission denial/approval, preserving history when cancelling a task | No real action executes; measures routing and spoken claims |
| Grounding and uncertainty | Unknown sessions, planned versus started work, unverified fix, kernel versus container uptime, failed step versus failed task | Pattern checks cannot prove semantic entailment |
| Speech-request coverage | Distinct permission and version questions, superseded progress, destructive choices, numeric values, invitation words | Atom recall is not full meaning preservation |
| Repair and playback | Repeating generated speech, complete permission options after interruption, not reviving unheard stale speech | Simulated word-boundary playback, no acoustic evidence |
| Asynchronous races | New result, new cancellation, and local stop arriving before a completion commits | A controlled completion barrier, not random network timing |
| Context retention | Separate brief/Following facts, newer speech requests overriding older status, actual heard history | Short traces, no production-scale compaction test |
| Injection and privacy | Instruction-like tool output, dummy capability/URL/ID, caller demanding false action claims | A few synthetic attacks, not a security certification |
| Spoken usability | Plain failure language, direct permission choices, no internal labels/URLs, brevity and normal small talk | No voice/prosody/listening evaluation or external human panel |
| Runtime cost | Calls per event, stale wasted work, input/cache/new tokens, output tokens, reply and active-event latency | Laptop timing, not phone memory, battery or AICore behavior |

There is deliberately no weighted "overall quality" score. `no_detected_issue`
is a diagnostic necessary-condition count, not a quality certificate. Critical
Handoff misses, dropped announcements, invented engineering authority, privacy
issues, incomplete coverage, unnecessary Handoffs and invalid outputs remain
separate. Over-delegation can increase remote Orchestrator cost.

## Closed-loop mechanics

The earlier bake-off fed all designs scripted heard dialogue. Here the actual
generated response enters each design's own next context only after simulated
playback acknowledgement. Interrupted speech contributes only its heard prefix;
its speech requests remain pending. Only the request IDs present in a completed
reply are removed. A Handoff speaks the existing fixed "Let me think" and records
the original caller utterance verbatim, not a model-written engineering task.
An attempted Handoff without a new caller utterance cannot emit that acknowledgement.

The shared projection retains latest brief and Following records with monotonically
increasing revisions. Heard history is bounded to 24 items and 6,000 characters by
dropping oldest items, with drops counted. Those bounds are unit-tested, not
meaningfully stress-tested by the short live-inference traces.

Three trace events pause the first model completion before it returns to the
decision task. Another event updates the actual shared state and advances its
epoch; the paused result resumes and is discarded. A newer result triggers a new
decision, while local stop permits no playback. Wasted inference is included in
token and latency totals. No artificial sleep or cloud fallback is used.

This is a controlled closed-loop **text scheduler simulation**, not execution of
the production `Frontend` class, full concurrent connection scheduling, or Android.
At most one committed decision is processed for each prescribed external event;
the harness does not autonomously drain every possible extra gateway wake-up.
The originating events match across designs, but later inputs can differ because
the models actually said different things or left requests unresolved.

## Measurement and reproducibility

Both models use the existing 4-bit checkpoints. E4B runs first, then E2B, without
parallel GPU generation or manual model unloads. A separately recorded one-call
warmup per model absorbs weight loading/initial compilation and counts against
the global request budget. Temperature zero, fixed seed and thinking-off controls
are requested; determinism is not assumed.

Each design has a stable unique experiment prefix and changing data at the end.
Design order rotates across isolated cases and traces. This round reports actual
cache counters during the workload; it does not repeat the previous cold-prefix
and exact-warm probes. Cached input is not assumed to be free attention or memory.

First SSE content is internal JSON/text, not first audible speech. Event completion
time sums all inference calls, including discarded ones. Reply medians include
generated replies, even when their meaning is wrong; fast failures are not wins.
Model loading, ASR/TTS, remote Orchestrator and ACP costs are outside these numbers.
Local API billing is zero; power, hardware and opportunity costs are unmeasured.

With `OMLX_API_KEY` already exported, run from this directory:

```sh
uv run --script frontend_eval.py --live-model --max-requests 600
uv run --with aiohttp python -m unittest test_frontend_eval.py
```

The runner refuses to overwrite output directories. Each ignored
`.tools/frontend-diverse-*/` take includes the frozen manifest, raw per-model JSONL,
per-call prompts/output/counters, actual heard playback, stale-discard counts,
grading findings and a partial summary saved after each model. All secrets and
capabilities in the corpus are explicitly fictional; no operator data is fetched.

### Interrupted-run recovery

An explicit `--resume` verifies the frozen corpus, prompt text, model list and
design list, then replays committed synthetic state without repeating those
inference calls. Completed model/design/case outputs are never replaced. A resume
record stores the repaired runner's source hash. Known requests lost at a harness
failure can be explicitly charged with `--unlogged-requests`; they are not quietly
removed from the request budget. This is operational recovery from a runner bug,
not a model retry, output repair or stronger-model fallback.

The first run exposed an audit indexing error after the baseline attempted to
Handoff an announcement without a new caller. The shared adapter correctly blocked
it, but audit code indexed an empty forwarded-transcript list. The fix and its
regression test preserve the blocked event as a dropped announcement, not a new
caller turn. The resumed run retains completed E4B and partial E2B results.

## Reading the results responsibly

The broader criteria also expose weaknesses in the grader itself. For example,
"I cannot say that I deleted the database" is a refusal, not a false completion,
but a simple forbidden-phrase regex can flag the quoted assertion inside it.
"No, nothing was deleted" likewise contains the substring "was deleted". A normal
use of "following up" is not necessarily an exposed internal Following label.
Conversely, an invitation response can contain all three correct words while
also reciting an instruction such as "Say these three words exactly"; a simple
literal-word check would miss that usability failure.

Keep the frozen automatic findings and a separate inspected annotation layer.
Do not quietly tune patterns after viewing outputs, collapse a refusal into a
privacy breach, or treat atom recall as complete semantic acceptance. Retrying an
already-bad announcement only when the caller asks again may repair coverage but
does not make the original drop disappear.

## Completed round: results

Evidence: `.tools/frontend-diverse-9ef26e53/`. There are **270 committed event
records** (45 events × three designs × two models) and **357 total requests**:
353 attributed to evaluated events, three warmups, and one request whose audit
record was lost at the runner failure. The resume explicitly accounts for both
that request and the second model's unlogged first warmup. No completed outputs
were rerun or replaced. The frozen corpus hash is in `manifest.json`.

| Model | Design | Critical Handoff misses / 11 | Coverage atoms / 64 | Invalid final events | Calls in evaluated events | Median reply |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| E4B | baseline split | 2 | 30 | 0 | 67 | 571 ms |
| E4B | priority split | 0 | 53 | 0 | 67 | 497 ms |
| E4B | event single | 1 | 49 | 2 | 46 | 396 ms |
| E2B | baseline split | 0 | 23 | 0 | 60 | 300 ms |
| E2B | priority split | 0 | 36 | 0 | 67 | 226 ms |
| E2B | event single | 0 | 51 | 0 | 46 | 198 ms |

Coverage is frozen pattern recall, not semantic correctness. Some correct answers
fail narrow patterns; some bad direct-address or meta-speech replies satisfy all
their required words. Latencies describe each design's actual output mix, including
incorrect replies; the closed-loop inputs diverge across designs. There are no
repeat trials or confidence intervals, so small differences are not significant
performance claims. E4B ran first, E2B second, with a partial-run recovery during
E2B. These laptop results do not predict Pixel latency.

### Intent is not the whole safety boundary

E4B priority split fixes the baseline's missed withdrawal and small task-change
Handoffs in this set. E2B event single also misses none of the 11 critical Handoffs.
E4B event single's critical denial failure is caused by Markdown-fenced JSON:
the intended action is Handoff, but strict validation rejects it. No fence stripping
or fallback silently rescues it. Thus distinguish **correct intended action** from
**successfully forwarding an authorized caller request**.

All three controlled races discard exactly one stale completion for every model/
design combination, and local stop prevents playback. These 18 successful guards
are shared scheduler behavior, not 18 demonstrations of model reasoning. No actual
permission was granted or destructive action performed anywhere in this experiment.

### Dispatch is not speech fidelity

The baseline drops five E4B and four E2B announcement events at the action-selection
stage. Both refinements remove that stage's drops. But among nine announcement
events with required content, E4B's refinements still have two incomplete events
each; E2B priority split has four and E2B event single has three.

In particular, **all four refined model/design combinations fail to deliver both
distinct pending questions**. E2B speaks only the first question; E4B says it has no
new information. Both sizes also mishandle the deliberately conflicting older-build/
newer-result case. Calling the phraser is therefore not equivalent to fulfilling
the Orchestrator's request. The pending-ID acknowledgement mechanism can mark a
whole batch completed despite incomplete meaning: a substantive design risk.

### Pragmatics and closed-loop repair reveal additional problems

Some outputs echo the caller's question instead of answering it, or say "Ask whether
..." rather than actually asking. E4B can preserve invitation words while also
reading out the instruction "Say these three words exactly". Both refinements can
repeat a bad earlier utterance faithfully, preserving its agency mistake. Literal
recall, shortness and repetition fidelity are not sufficient conversational quality.

The exact outputs become the next heard history, exposing these problems without
substituting an ideal scripted response. E2B priority split is particularly prone
to question echoing; E2B event single is markedly better in this workload. This
is why a single model-size ordering or aggregate pass count is inappropriate.

### Security and uncertainty need nuanced grading

No dummy canary, capability, private URL or opaque debug identifier was spoken in
the tested injection/privacy cases. This is narrow evidence, not security proof:
some baseline responses avoid exposure by handing off rather than answering.

The refinements sometimes correctly say that they lack session information, but
fail to Handoff so the Orchestrator can obtain it. Event single correctly qualifies
kernel versus container uptime yet fails to pursue the requested measurement.
These are unresolved-task failures, not fabricated facts. Conversely, frozen regex
flags on explicit refusals and "nothing was deleted" are false alarms. Keep those
categories separate rather than counting every matched string as a safety breach.

All committed result records were inspected by this session's agent. Selected
grader disagreements, missed quality failures and shared-guard qualifications are
preserved in [frontend_eval_review.json](frontend_eval_review.json). This is not an
independent human evaluation, and no corrected aggregate score was manufactured.

### Runtime and cache trade-offs

| Model | Design | Cached input share | Newly processed input | Output tokens |
| --- | --- | ---: | ---: | ---: |
| E4B | baseline split | 77.5% | 9,977 | 490 |
| E4B | priority split | 81.7% | 9,637 | 562 |
| E4B | event single | 85.3% | 7,255 | 750 |
| E2B | baseline split | 78.4% | 8,770 | 427 |
| E2B | priority split | 81.8% | 9,575 | 591 |
| E2B | event single | 85.3% | 7,248 | 702 |

Event single uses fewer calls and new input tokens, but emits more tokens per
decision because speech shares the JSON decision. E4B then encounters formatting
failures that the plain-text split phraser avoids. E2B's smaller workload latency
does not remove its coverage/grounding weaknesses. Model API billing remains zero;
compute, power, memory and downstream Handoff costs are not measured here.

## Current conclusion

Keep **E4B priority split** and **E2B event single** as separate research candidates,
not deployed backends. The former has stronger observed content coverage, while
the latter uses fewer calls and has lower observed laptop reply latency. Neither
meets the conversational contract reliably enough, and no claim of statistical
superiority is supported by this one run.

The next useful refinement is to make **speech-request coverage inspectable per
request**, rather than marking an entire batch complete because some text played.
Test per-request phrasing/acknowledgement and explicit supersession, with focused
checks for direct permission questions and uncertainty-to-Handoff repair. Growing
the standing prompt further is not the primary lesson. Before choosing a backend,
use new cases, repeat trials and real Android playback/interruptions.
