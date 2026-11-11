# Per-request speech delivery experiment

This follows [the broader evaluation](FRONTEND_EVAL.md), whose central finding
was that a completed batch utterance could clear requests its words never covered.
The prototype is local and synthetic. It does not modify the deployed gateway,
Android app, Worker, Orchestrator, ACP Agents, or remote Handoff/brief/say contract.

## Three competing designs

- **batch_control**: the previous event-specialized split design, phrasing all
  pending requests together and binding the reply to the entire batch. This is
  the deliberately coarse acknowledgement control.
- **atomic_fifo**: one pending request becomes one phrasing job and one utterance.
  Completion acknowledges only that request. There is no supersession selection;
  this control may deliver obsolete progress or duplicate information.
- **atomic_planned**: a small structured model call examines a multi-request queue
  and proposes which requests a later request wholly replaces. Retained requests
  are phrased individually, with the same phraser as atomic FIFO.

The atomic phraser receives only its target request, that request's heard prefixes,
and current brief/Following context. It does not receive every other speech request
or unrelated caller history and cannot author request-ID bindings. The scheduler
owns that binding. Stable prompt prefixes are reused across jobs and cases.

A bounded follow-up adds **atomic_selected**: instead of returning a whole queue
plan, the model answers one question per older request, with a single constrained
`replacement` field: `none` or a later request ID. The scheduler assembles and
validates the full plan before applying it. This is a distinct tested design,
not a repair/retry of the whole-plan model's failed outputs.

Caller speech remains routed by the earlier priority-split design. A Handoff
forwards the original caller utterance and speaks only the deterministic
acknowledgement. A caller reply acknowledges no queued speech requests. The focused
corpus tests cancellation preemption, not the full range of caller conversation.

## What acknowledgement now means

Each source request has a local ID and a retained ledger record. Its prototype
delivery status is one of:

- `pending`: no complete utterance has acknowledged this request;
- `playback_complete`: its bound utterance completed simulated playback;
- `superseded`: a separate, explicit edge points to a later replacement request.

`playback_complete` is deliberately **not** named "fulfilled" or "meaning delivered".
Bad wording can still complete playback. The evaluator separately inspects whether
the utterance bound to that request contains its required facts and choices, asks
the actual question, and avoids meta-speech and obsolete statements.

An utterance receipt identifies its own request bindings and generation epoch.
Atomic receipts can bind exactly one request. A completed first request cannot
acknowledge a second one. Interrupted playback records only its heard prefix and
leaves the request pending. Unknown, duplicate, invalidated, or late callbacks
cannot clear newer requests. Local stop rejects new speech and stale completions.

These guarantees belong to deterministic bookkeeping, not to model capability.
Callbacks and word-boundary playback positions are simulated; the Android app
does not yet implement this prototype's per-request protocol.

## Supersession authority and failure policy

The current remote contract supplies free-text speech requests, not authoritative
supersedes IDs. Therefore local semantic supersession remains a hypothesis, not a
fact inferred from queue position. `atomic_planned` returns one decision for every
pending request: keep it, or identify a later pending replacement.

Application validation rejects missing/duplicate/unknown IDs, self references,
backward edges, malformed output and extra fields. The complete plan is validated
before changing any request. A failed plan leaves requests pending and halts this
delivery attempt; it does not silently fall back to FIFO, guess a plan, retry the
model, or use cloud inference. Forward-only edges prevent cycles.

Structural validity is not semantic safety. A model can produce a valid-looking
plan that drops a distinct question or Permission Request. The evaluation counts
those proposals separately against declared expected edges. A superseded request
is never marked heard, and its replacement remains pending until its own playback.
No plan can authorize a session action. The unresolved authority question is also
recorded in `DOMAIN.md`.

## Focused corpus

`frontend_request_cases.json` contains 20 cases, authored before this round's
outputs. Two reproduce earlier failures; others vary aliases, subjects, choice
types, explicit corrections, identical duplicates, and compound requests. Cases
include:

- two or three distinct requests that must all survive;
- same session but different questions/topics, including a pending destructive
  permission question followed by unrelated successful work;
- newer progress and explicit corrections/withdrawal of the same question;
- an older compound request with both obsolete progress and a live question;
- an instruction-like quoted log that tries to erase other requests;
- interruption of the first request, followed by an explicit resume;
- new results, a new distinct question, cancellation, or local stop arriving before
  the first phrasing completion commits.

The compound case is intentional: one Orchestrator append can itself contain
multiple speech acts. One-job-per-append is not guaranteed to be semantic atomization.

For the controlled races, the response is persisted, then the new event is ingested
before the awaiting delivery loop resumes. The epoch check discards stale wording.
If a planner fails before phrasing begins, that race is reported as not exercised,
not a passed guard. Explicit resume is a new test event, not an automatic retry.

## Evidence and metrics

The manifest freezes corpus/prompt/schema/source hashes before inference. Expected
coverage, question markers and supersession edges are never model input. Both
4-bit Gemma models run sequentially on laptop oMLX. Every inference completion is
journaled **before** ledger/evaluator bookkeeping, including raw output, input
messages, cache counters, latency, case/design/model and format failure.

The report separates request-level content recall, playback completions missing
required content, surface direct-question checks, incorrect/missed supersession,
obsolete/meta-speech, failed plans/phrasing, and interruption/preemption results.
None is a weighted overall acceptance score. Regex checks still need inspection:
negation, paraphrase and indirect questions can be misclassified.

First-text-ready and all-text-ready times sum model calls, including planner and
discarded work. They do not include real synthesis, audio playback or recognition.
Per-request jobs may improve coverage and first-response time while increasing
total inference, number of utterances, or end-to-end listening time. Memory,
battery and Pixel/AICore performance are not measured. Unknown cache counters
remain unknown. Local API billing is zero, not free compute.

With `OMLX_API_KEY` exported:

```sh
uv run --script frontend_requests.py --live-model --max-requests 400
uv run --with aiohttp python -m unittest test_frontend_requests.py
```

Each run creates an ignored `.tools/frontend-requests-*/` directory without
overwriting an existing take. The maximum request count applies across both models,
including warmups. There is no automatic deployment or backend-default change.

## Results

The first three-design run is retained at `.tools/frontend-requests-b915df2f/`
(184 requests). The follow-up selector run is
`.tools/frontend-requests-b796f2d6/` (96 requests). **280 inference requests total**,
including both models' warmups in each run, within the original 400-request budget.
Both runs use the same frozen 20-case corpus. The follow-up is a development
refinement after inspecting failures, not a fresh held-out validation.

| Model | Design | Required request content / 31 | Completed playback missing required content | Incorrect supersessions | Error cases / 20 |
| --- | --- | ---: | ---: | ---: | ---: |
| E4B | batch control | 5 | 22 | 0 | 3 |
| E4B | atomic FIFO | 30 | 1 | 0 | 0 |
| E4B | whole-plan atomic | 2 | 0 | 0 | 18 |
| E4B | per-request selector | 23 | 1 | 7 | 0 |
| E2B | batch control | 10 | 21 | 0 | 0 |
| E2B | atomic FIFO | 28 | 3 | 0 | 0 |
| E2B | whole-plan atomic | 0 | 0 | 0 | 19 |
| E2B | per-request selector | 17 | 1 | 14 | 0 |

Content counts are declared per-ID pattern checks, not semantic acceptance.
For example, "Should Fir have the caller choose staging or production?" includes
the required words but misdirects the choice. Some indirect questions also pass
the surface question-mark test. All 160 result records were inspected by this
session's agent; this is not an independent human review.

### Atomization helps within the small-model envelope

Atomic FIFO delivers both formerly competing questions, and handles unrelated
requests across sessions/topics far better than batching. The simpler target-only
phraser also reduces meta-speech. E4B covers 30/31 required requests and E2B 28/31;
their surface direct-question checks reach 16/17 each. Atomic playback completions
missing declared required content fall from 22 to 1 on E4B and 21 to 3 on E2B.
This is an improvement, not proof every completed utterance fulfills its meaning.

Remaining atomic phrasing failures include omitting the withdrawal fact versus
"nothing was deleted", or omitting "nothing was deleted" while announcing
withdrawal. E2B sometimes says only "Would you allow once or deny?" without the
session or proposed action. That is inadequate grounding for a permission choice.

Interruption of the first request no longer clears the second. Explicit resume
repeats the first request and then delivers the next. The callback, request-ID and
epoch guarantees are enforced by deterministic code and tested separately from
model language quality. These remain simulated acknowledgements, not Android
audio/callback validation.

### Supersession does not fit the same envelope yet

Atomic FIFO intentionally performs no supersession. It repeats duplicates and
can speak obsolete build/test status, outdated version choices, or a permission
question that an authoritative newer request explicitly withdrew. Four E4B and
three E2B cases trigger the corpus's forbidden-speech patterns. Inspection also
finds instruction echoing that can evade a literal stale-question pattern.
**FIFO is not safe to deploy unchanged.**

The whole-plan model mostly produces Markdown-fenced JSON, or omits required
request entries. Validation halts delivery and preserves pending requests. Its
zero incorrect *applied* supersessions therefore reflects fail-closed behavior,
not sound selection. Inspection of rejected proposals also shows attempts to
retire live questions. One stop-race case per model never reaches phrasing because
planning fails first: it is reported as not exercised, not a successful stop test.

The smaller selector removes the formatting barrier: neither model has an error
case. But now it **incorrectly retires seven E4B and fourteen E2B requests**. These
include different sessions' progress, unrelated questions, permission versus
invitation requests, and an older compound request with a still-live question.
E2B often treats any newer request as a replacement, including a chained drop of
two distinct requests in the three-request case. Structural ID validation cannot
catch this semantic error. No actual permissions or session actions are executed,
but callers can lose the chance to answer a permission question.

The E4B and E2B per-request selectors identify all six declared legitimate supersessions, eliminating
the flagged stale speech at the unacceptable cost of dropping unrelated material.
Thus selecting only for freshness would reward the wrong design.

### Cache and latency trade-offs

E4B FIFO makes 43 evaluated calls versus 24 for batching, but processes **3,717 new
input tokens versus 4,192**: its smaller, stable per-job prompt offsets additional
calls. E2B is similar (3,721 versus 4,196). Median first text becomes ready at
299 ms versus 332 ms on E4B, and 149 ms versus 157 ms on E2B. These are single
laptop runs, not significant or phone-performance claims.

Total model work rises: median per-case inference time is about 583 ms FIFO versus
452 ms batch on E4B, and 297 versus 146 ms on E2B. The successful selector costs
6,009 new input tokens on E4B and 5,719 on E2B, greater than FIFO, and delays the
first text. It is not merely a semantic risk; it adds preprocessing overhead.

The original summaries call total attempt time `median_all_text_ready_ms`; that
field includes failed/stopped attempts and must **not** be read as successful
delivery latency. The runner now labels it `median_attempt_ms` and reports
`median_completed_drain_ms` separately. First-ready medians omit cases that never
queue text, so their eligibility differs. A completed drain still means playback
bookkeeping completed, not that every fact was semantically correct. Inference
time also excludes serial TTS listening time and any opportunity to overlap
generation with playback.

## Conclusion and next decision

Keep **one speech request, one phrasing job, one playback acknowledgement** as the
promising infrastructure change. Do not grant the tiny Frontend authority to
semantically discard pending requests on the evidence here. Callback safety and
semantic supersession are different problems.

The next architectural question is how a stronger, authoritative component can
identify obsolete speech without requiring the Frontend to rediscover that fact.
Possibilities include explicit Orchestrator replacement hints or trusted metadata
for replaceable progress versus durable questions. Neither is implemented, and
either needs a deliberate contract/authority decision. Exact duplicate handling
alone would not solve corrections, withdrawals or compound requests.

Before deployment, also implement eyes-free recovery for a stalled phrasing/plan
attempt. A preserved ledger and an operator error log do not satisfy the caller's
accessibility promise. No production changes, Android run, or cloud fallback were
performed in this step.
