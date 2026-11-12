# LFM3 Voice Frontend context strategies

Research only, 2026-11-12. No inference or services started. Target: LFM3-Audio-2B Q8_0, with compatible audio encoder/projector and output codec. Quantization alone does not establish capability or behavioral parity.

## Confirmed API surface

Sources consulted narrowly:

- https://docs.liquid.ai/lfm/key-concepts/audio-capabilities
- https://docs.liquid.ai/lfm/key-concepts/tool-use.md
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md (Realtime conversation section)
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-realtime.cpp (source blob `8ed3dd535f11229f8b1411332043936b0516e506` at inspection)
- Local `DOMAIN.md`: Voice Frontend lines 140–146, Session Orchestrator 148–158, Handoff 791–797, Alias 640–651.

Liquid documents audio plus text in a user turn, speech output, and spoken tool use. Spoken tool use specifically requires system prefix `Respond with function calls, and interleaved text and audio.\n\n`. This is distinct from plain spoken chat, whose exact system prompt must be retained and extra instructions placed in user turns. Builders use the tool-capable task prefix, offer exactly one empty-object `delegate()` function, and leave template tool insertion to the API. Do not append a `<think>` or forced mixed-output prefill before tool selection. Liquid's thinking prefill is documented for text-only output, not a private reasoning channel alongside speech. A supplied private brief is host context, not a model thinking mode.

Current llama.cpp `/v1/realtime` really supports conversation, not just live ASR:

- `session.update` sets initial instructions/tools before first audio/message. Later changes are rejected, not dynamically applied.
- Audio: `input_audio_buffer.append` base64 PCM16 mono; `commit` ends caller audio. Rate is negotiated in session metadata (8–192 kHz accepted input with server resampling). Output is PCM16 at reported output rate. Liquid's model-side defaults are 16 kHz input and 24 kHz output, not a license to hardcode the transport rate.
- `conversation.item.create` accepts only user messages containing `input_text`, or `function_call_output` after the last response. It does not accept arbitrary system/developer/assistant history injection or `input_audio` item parts. Audio uses the buffer events.
- `response.create` requires an open input turn or tool results. It overrides output modalities, token limit and tool choice, not instructions. It cannot simply wake to speak from no input.
- `response.cancel` stops generation. It does not cancel Session Orchestrator work or undo delivered audio. No assumed native asynchronous function-calling mode or editable historical context.
- Function results are formatted through the chat template. `on_tool_output` gates on tools, prior turns and closed user turn, but does not robustly check the supplied call ID against a pending call. The adapter must enforce correlation itself, not exploit this permissiveness.

Chat completions supports `input_audio` parts (base64 `data`, or llama.cpp `url`; format autodetected by llama.cpp). Builders return this request representation. A realtime runner maps each caller audio part to buffer append/commit and text to input_text. It must not send the builder fragment verbatim as a realtime event. Tools in realtime have Responses-style flat declarations: use `tool_declaration()['function']` plus `type: 'function'`, not the nested Chat shape. Output selection is an adapter concern; realtime `['text','audio']` is documented, while missing modalities allows model switching. Confirm tool-versus-speech behavior with the installed build.

## Three concrete arms

1. **`user_envelope`: fixed initial policy, append-only synthetic controls.** Caller audio remains a user audio item. Host-added instructions, private brief, commentary, progress and observed call/result facts arrive as `HOST_CONTROL {kind:...}` user text. Initial policy explicitly disambiguates provenance. Unsolicited progress is a host user-text turn followed by response.create. During pending backend work, caller corrections become new audio input and a new delegate proposal. No fake native tool history. Tradeoff: excellent fit to the realtime API, weakest role separation and growing context. Historical delegate observations are deliberately not encoded as fictitious assistant function calls.

2. **`tool_envelope`: actual calls and actual immediate receipts.** Preserve observed native `delegate()` calls and return genuine host receipts as tool-role envelopes. The host operation is accepting a transcript Handoff for processing, not completing backend engineering work. An actual receipt may contain `{accepted:true, backend_pending:true, handoff_id:...}`. This decouples the short protocol tool lifecycle from the longer Handoff, which DOMAIN explicitly says has no completion event. This module never manufactures that receipt. Later brief/commentary/progress arrive as labelled user controls, not spurious extra results for an old call. Caller correction after receipt can delegate immediately while backend work continues. If no real receipt exists yet, builder rejects the protocol history: record an adapter limitation or use another arm. Tradeoff: learned tool-response framing could improve obedience, but additional results after unrelated conversation are not a supported realtime async channel.

3. **`reconstructed`: bounded suffix plus revised system snapshot.** Make fresh chat-completion requests. Pin trusted instruction deltas and the latest private brief/state in a new system prompt, retaining the exact Liquid task prefix. Keep a chronological suffix of caller audio, actual speech and public controls; summarize historical delegation only as observed JSON. Do not replay incomplete native calls as if they must execute again. Keep new caller corrections later than old commentary so reconstruction does not silently reverse priority. Runner supplies a current private brief snapshot including pending Handoff IDs, known Aliases, and any still-required context before old events are dropped. Tradeoff: clean policy/state and bounded audio history, but re-encoding/first-token cost and possible lost context. Not an in-place realtime session.update: use chat requests, or reconnect and replay only supported user items (assistant speech must then be host-labelled user context). Events bound history, not tokens; enforce token/audio duration limits separately. Instruction deltas themselves can grow: host periodically folds them into standing instructions and clears obsolete deltas.

All three are executable builders, not claims of effective model behavior. None can protect a brief with a security boundary inside model context. Do not put secrets there. Prompts prohibit recitation; measure leakage explicitly. Supplied commentary is an Orchestrator speech request, never caller intent. Assistant history must contain only actually audible words, not generated-but-cancelled output. No guessed Aliases, no invented execution and no structured task arguments replace the transcript Handoff.

**Authority boundary:** synthetic user envelopes have simulated authority only, not provider-level privilege. Their host origin is known to the application but represented only by text to the model. A caller speaking or typing a `HOST_CONTROL` mimic is an intentional adversarial test, not something this encoding authenticates or blocks. The tool arm's actual tool-role results have real role provenance, but its user-envelope updates retain the same spoofing risk. Reconstruction really promotes selected host-owned fields (instruction updates and latest private brief) into the provider system role; caller text is never promoted. That does not eliminate prompt injection or make private context confidential. Experiment labels deliberately distinguish simulated authority, real tool roles, and reconstructed system context.

`max_events` must be a positive integer in all arms, even the two append-only arms that do not truncate. Blank caller text without audio and blank text substitutions are rejected; nonblank whitespace is preserved verbatim. Empty host private briefs remain valid to clear previous context. Whitespace-formatted JSON `{ }` delegate arguments are accepted as an empty object, while other values are rejected.

## Runner interface and scheduling

`from research.lfm3_voice.strategies import STRATEGIES, build`

`request = build('user_envelope', history, instructions='Keep responses short.')`

Runner-aligned public signature: `build(strategy_name, history, event=None, *, instructions='', max_events=24, input_mode='native_audio')`. The optional current event is appended to a copied history. `normalize_events` exposes that operation independently. It accepts canonical events plus audible `{role:'assistant', content:'...'}` shorthand. Current caller audio may be an `audio` mapping or OpenAI `content` parts. Native audio excludes textual parts of audio-bearing events, and all caller scoring/reference metadata is stripped. Explicit `input_mode='oracle_text'` or `'lfm_stt'` substitutes the matching field on each audio event, removes audio, and raises if unavailable. These are separate input arms, not extra transcripts beside audio. `INPUT_MODES` and `STRATEGY_LABELS` expose clear experiment labels. Use `build`, not the lower-level named builders, for normalization and leakage protection.

Return is `{messages, tools, tool_choice}`; runner adds model and generation settings. See module docstring for canonical events. Inputs are never mutated. A caller event can have both text and audio, but avoid giving an answer transcript alongside audio unless testing that particular condition. Delegate events need real call IDs; results need matching prior calls. Host controls may carry optional handoff_id/revision provenance. No alias resolution or backend cancellation occurs in this module.

For unsolicited speech the host schedules an inference turn on a commentary/progress event, without inventing a human utterance. Private-brief-only updates should normally wait for the next turn: do not trigger needless speech. The host serializes Voice Frontend generations, queues controls during generation or cancels and rebuilds, tracks actual playout, and discards cancelled/stale generated speech. It continues capturing caller audio while the backend works. A new delegate() sends the recent caller/Voice Agent transcript, not private brief or synthetic controls represented as caller words. The Orchestrator judges whether a correction supersedes earlier work; frontend receipt alone does not authorize cancelling unrelated work.

## Installed-runtime integration corrections

The ordinary HTTP completion path is not an interleaved speech decoder. A raw
diagnostic returned mixed-start, six text tokens, and EOS (`stop_type: eos`), not
a token-budget cutoff. Suppressing both audio/mixed mode tokens made all 40
opportunities delegate in each of three input conditions for one arm. That
diagnostic was stopped, not counted as a meaningful model bakeoff. Changing the
recipe to text helped text input but did not repair audio input. The experiment
therefore uses actual realtime generation through `native_runtime.py`.

A one-slot server retains its slot while a realtime connection is open.
`experiment.py` freezes caller TTS/ASR first, then renders staged male TTS after
closing native connections. Native and staged output WAVs remain separate. No
male selector is claimed for native conversation. Staged synthesis scheduling is
offline, not measured online playback latency. STT-mediated decision totals add
the independently measured frozen ASR duration, not contemporaneous online ASR.

Persistent arms append only new events and real receipts. In the user-envelope
arm receipt observations use user controls; the tool-envelope arm uses actual
native call IDs and tool-result roles. Native emitted calls remain in server
history in both arms: that history is not editable. Reconstruction reconnects,
pins host instruction/brief state, replays old caller audio and labels historical
speech as user `HOST_HISTORY`, since assistant-role insertion is unsupported.
This is role emulation, not equivalent native history. An observation-only
boundary supplies the required input when a private update was folded into the
system snapshot. It is never caller speech.

## Focused revision

`policies.py` preserves v1 and adds v2 with two general changes, motivated by a
blinded preliminary eight-point review:

1. Explicit event-origin/routing distinctions: observations, speak-now commentary,
   actual work requests, local recall and social speech.
2. Minimal relevant disclosure and requested/accepted/started/completed distinctions,
   avoiding unsolicited private facts and invented causal links.

No fixture answers, Aliases, colors or grading predicates enter either policy.
Generation budgets, tools and strategies remain matched. Silence failures are
reported before application suppression.

## Evaluation: capability versus behavior

Run capability probes first against the **installed build and exact Q8_0 artifacts**: audio input decoding; simultaneous text/audio input; intelligible speech output with codec; recognized empty-argument tool proposals; native result continuation; synthetic user update; rejected late instructions; cancellation followed by a new turn; fresh-request reconstruction. Record HTTP/WebSocket errors, dropped/ignored fields, template failure, missing codec/projector, and unsupported format as **capability/adapter failures**, not instruction-following failures. A parser rejecting valid raw XML is an adapter failure; malformed function output by the model is behavioral. Never credit raw tool text as execution.

Only after transport/template probes succeed, score **behavior**: delegate for work rather than guessing; exactly zero args; supplied Alias only; no brief/thinking leakage into text or speech; concise paraphrase of commentary; no treating commentary as caller authorization; unsolicited progress delivered once; correction delegated while earlier backend work is pending; completion claimed only from authoritative supplied facts. Preserve raw text and independently inspect audible output: correct text plus wrong speech is not a pass. Record host scheduling errors and stale delivery separately from model output. Pending-backend concurrency here is an application scheduler property, not a new llama.cpp primitive.
