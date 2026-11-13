---
name: call-analysis
description: Pull, review, or analyze a Dogwalk call or playtest: transcripts, Orchestrator behavior, Progress Beats, Memory, pairing, whiteboard, invitations, Agent errors, deploy timing, "what happened on that call". Use before writing any ad hoc curl or Python against the Diagnostics API.
---

# Call analysis

Review Dogwalk calls from data, not from impressions. Read `DOMAIN.md` for terms (Theme, Intensity, Runtime Dramaturgy, Memory, Progress Beat, Bid, Paired Browser, Whiteboard, Invitation).

## Start with the CLI

```sh
uv run --script playtest.py --list          # recent calls
uv run --script playtest.py                 # latest call: transcript, orchestrator, boundary, stats, reminders
uv run --script playtest.py 2               # previous call, or a Call SID prefix
uv run --script playtest.py --only reminders   # or transcript | orchestrator | boundary | stats
uv run --script playtest.py --deploys       # Worker deployments interleaved with calls, PT
uv run --script playtest.py --full          # no truncation; --width N otherwise
uv run --script playtest.py --only timing --full  # detailed millisecond server timeline
```

Read the `reminders` block first: each line is context that was available on an earlier review but nobody reached for, and it names where to look. `boundary` hits are candidates: decide whether the word came from the caller's own request (fine) or from the Theme (a leak).

Masking is on by default (phone numbers become `+1XXX..NN`, Invite Code words `p***`). `--unmask` is for Adam only; never paste unmasked output into a report or commit.

## Raw API, when the CLI lacks something

Password: `security find-generic-password -a adam -s dogwalk-admin -w`; send header `user-agent: dogwalk-playtest/1` (Cloudflare blocks Python-urllib). Save pulls under `/tmp/dogwalk_<slug>/`.

- `https://dogwalk.tools/admin/api/calls?verbose=1&limit=N`: `calls[].activity[]` rows `{ts, source, event, detail}` with `detail` a JSON string. Families: `voice.live.*` (including `voice.live.usage`, the Live model's audio seconds and context `usage_ratio`, and `voice.live.command.rejected`, a say, brief, or other sideband command the provider refused), `voice.opening`, `voice.theme`, `voice.fixture.*`, `voice.earcons`, `voice.delegation.received`, `voice.continuation.forwarded` (caller speech after a Handoff that never got its own), `orchestrator.model` (usage, cached tokens), `orchestrator.say`, `orchestrator.tool` (name, ok), `session.beat` (text, narrate, earcon), `session.beat.coalesced`, `session.narrated`, `session.followed`, `agent.turn.*` (including `agent.turn.steered`, `steer_delivered`, `observed` for a turn another client began, and `caught_up` for one that ended while Dogwalk was not listening), `agent.session.*`, `agent.permission.*` (`resolved_elsewhere` when another client answered), `agent.connected`, `agent.disconnected`, `pairing.*`, `whiteboard.*`, `takeover.*`, `orchestrator.context.trimmed|cut`, `call.completed`.
- `https://dogwalk.tools/admin/api/conversations?verbose=1` (one item): `orchestrator_context` (Responses items: user messages with `[handoff ...]` and `[harness event] kind: json` lines, `function_call`, `function_call_output` joined by `call_id`), `call_marks` (one per voice session; a call spans its marks), `call_transcripts` (one per voice session, verbatim), `current_transcript`, `memory`, `memory_versions`, `context_archives`, `context_tokens`.
- A call's `call_ended` harness event arrives with the next `call_started`, so it sits at the start of the next call's slice.

## Extension policy

Keep `playtest.py` small; this skill carries the checklist as prose.

1. Prose first: a new kind of check starts here, with the raw API pointer.
2. Crystallize into the CLI after repeated use (you wrote the same extraction twice) or after a stumble, shaped so that stumble cannot recur.
3. When a review later finds that the needed context was in the data all along, add a Reminder in `reminder_lines`, with a one-line comment naming the incident so the reason survives.
4. A feature that adds a domain concept updates this checklist in the same change, as it updates `DOMAIN.md`.

## Checklist (what to look for, and why)

- **Arc and outcomes:** what the caller wanted, what got done. Verify claims against tool outputs and `turn_stopped` agent messages; "done" spoken is not done.
- **Frontend vs Orchestrator split:** handoffs that should have happened; handoffs with no `say` (stats shows `none`); the Frontend claiming results before a turn began; aliases the Frontend invented instead of naming given ones. Several caller utterances merged into one transcript entry suggest the Frontend never responded between them.
- **Latency:** handoff to first `say` (stats), model p50 and max; long silences in the transcript.
- **Precise latency:** `--only timing --full` opts into `voice.timing` rows (`/admin/api/calls?verbose=1&timing=1`). Sort by `detail.ts_ms`, not database insertion order; timing writes are batched. Use delegation/request/tool-call/RPC IDs and `live_session_id` for joins. `live.received.queue_ms` is receipt-to-handler delay; handoff preparation no longer waits for transcript (GPT-Live sends it before the delegation), and `voice.continuation.forwarded` marks caller speech forwarded after a handoff that never got its own; model spans separate headers from body completion; `live.held` versus `live.sent` distinguishes speech queueing from transport send. Provider media offsets are a separate clock. Transcript output and `say` acceptance are not telephone playout. Old records have second-only precision; resets can lose the pending timing batch.
- **Live operator view:** `/admin/timing` is operator-authenticated and independent of browser pairing. Its incremental API `/admin/api/timing?call_sid=...&after=0` uses insertion IDs as cursors (500 rows/page, drain `has_more`), but observations must be drawn in timestamp order. It covers all callers. Agent tool IDs/status/kind and Prompt Turn Alias/Agent Session ID support Agent lanes; missing nested-session relationships are unknown, not evidence that no subagent ran.
- **External event provenance:** `provider.twilio.*` observations distinguish `occurred_at_ms` from `received_at_ms`, with `event_time_source`, `clock_id`, and `timestamp_precision_ms`. Use occurrence time for diagram placement when known; null means receipt-only evidence. Twilio's standard dates are second-resolution, and receipt minus occurrence includes clock skew. SIP child progress callbacks link to the PSTN root via `ParentCallSid`. Call resource metadata arrives asynchronously; request/response Call Events are backfilled by a durable cron job after sixteen minutes. Preserve insertion cursors when pulling late events; historical observations do not change operational state. No paid Insights is enabled.
- **Content alongside timing:** the operator page's `Show content` is independent of raw metadata. `/admin/api/timing/content?call_sid=...` recovers retained transcripts and scoped Orchestrator history; future `voice.timing` observations may contain bounded `content_text`/`content_kind`. Preserve the distinction between exact observation correlations, media-relative saved transcripts, and unknown timing. These are private operator records: never commit content pulls or screenshots with private conversation text. Paired Browser credentials do not authorize this endpoint.
- **OpenTelemetry view:** `uv run --script otlp_send.py CA... --signals traces` sends one call's metadata-only export to a local OTLP receiver (drop `--signals` for an `otelcol-contrib` that also takes logs). For a waterfall, run a stock Jaeger v2 binary (OTLP on `localhost:4318`, UI on `localhost:16686`, traces only); `--save DIR` keeps the bodies, which come from production and stay out of Git. Never send them to a hosted service. Read `dogwalk.context`: `recorded_at_start` means the Worker minted the IDs when the operation began (calls after 2026-11-11), `derived` means the export reconstructed them from older rows. Model spans are `chat {model}` with `gen_ai.*` and the provider's `x-request-id`; a `gen_ai.provider.name` appears only for OpenAI hosts. A Prompt Turn is its own trace linked to the call that started it; the starting call's export completes a turn that ended under a later call. Missing spans are evidence, not bugs: a start whose log names a span that was never exported had no observed end. Rows before the native Agent API (2026-11-17 migration) keep `rpc_id` and `stop_reason` and project as logs only. `x-dogwalk-truncated` on the traces response means the row or cross-call lookup bounds were hit. The Agent Gateway's trace-context log lines live in the Sandbox, and the trace stops there: OpenCode does not continue it.
- **Beats:** sparse, the Agent's own remarks quoted in its tense, no raw tool names or shell commands (`running a command (git ...)` is a defect), no recitation of every step. Check `session.beat.coalesced` (bursts within 1.5 s make one beat) and that narrated beats waited for a pause.
- **Themes and register:** `voice.theme` and the voice sessions it started; Intensity moves with the caller (light by default, plain when frustrated); the clarity floor (the facts must survive the metaphor); Runtime Dramaturgy fits the moment.
- **Agent boundary:** no Theme words or phone numbers in Agent prompts or steering (`boundary`).
- **Keepalive and endings:** close reasons; an end near 900 s or `voice end not observed` (Reminders); transcripts saved per voice session.
- **Sandbox reaping:** a call whose `since_last_call.sandbox_reaped` is true should open with the cleared-sandbox clause instead of the old welcome, exactly once per reap, and the Orchestrator should not offer the Memory's old sessions or files as still there. Reaping itself happens outside calls: `sandbox.reap.candidate`, `sandbox.reap.started`, `sandbox.reaped`, `sandbox.reap.failed`, and `sandbox.reap.stuck` live only in `audit_log`, and unconfirmed deletions in `sandbox_deletions` (README, Sandbox Reaping).
- **Identity Check (shadow):** `identity.started`, `identity.round`, `identity.escape`, `identity.who`, `identity.result` (source `access`). Read `identity.result.enforcement` (what enforcing would have done) against what happened: in shadow mode every Home path connects, so a `require_roaming` or `undecided` followed by a normal call is correct, while a Home caller hung up by the check is a defect. A `roaming` path always ends the call; check that `voice_calls.user_id` stayed the Home User. Check that the call's `call_started` carries `identity` and that the Orchestrator did not recite it or a level unasked. Enrollment rounds log no cosine; `identity.result` says `enrolled` with the answer count, or `enrollment_failed`. Per-round voice evidence (`cosine`, `speech_seconds`, `cold`, `wait_ms`, `speculative`) belongs to the `voice-research` skill.
- **Memory:** whether `compact_context` ran, what the Memory kept, what is missing; `context_tokens` against the 150k backstop; `orchestrator.context.trimmed` means the backstop cut without the Orchestrator's say.
- **Pairing:** `pairing.started` to `claimed` to `accepted` (or `unclaimed` with decode failures); `browser_connected` events.
- **Whiteboard and images:** `draw_on_whiteboard` program components and `Image(path, ...)` paths (Workspace-relative), `whiteboard_error` and whether the Orchestrator redrew honestly, `whiteboard.answered`.
- **Invitations:** at most 3 active; a recipient bound only after a confirmed readback; flag any code left active (Reminders); keep numbers and words masked.
- **Agent errors and websearch:** `agent.turn.failed` (its `message`, and `agent_termination` when the service died), `agent.disconnected`; distinguish a failed fetch (403) from broken search; check whether beats went stale after a failure.
- **Cost and consent:** for paid generation, the caller agreed before the spend, and the actual spend was reported.
- **Cache misses:** `cached_tokens` 0 with large input away from a voice session start means the prompt prefix changed.

## Lessons learned

- Check deploy times against call times before blaming a deploy (`--deploys`; the overlap is a Reminder). `deploy.py` refuses to stop the Sandbox mid-call, so a call can run old code after a deploy.
- A paired browser tab can be stale after a deploy: an "unknown component" whiteboard error is the usual sign.
- A subagent's hypothesis is a hypothesis: verify it against data or docs (one claimed GPT-Live delegations block speech; they do not).
- Merged caller transcript entries suggest the Frontend never responded.
- GPT-Live fixes the voice at session start, so a Theme change needs a new voice session (a close and reattach is expected, not a drop).
- The Durable Object is evicted after 15 min with only outbound WebSockets; alarms fixed it, and a regression shows as endings near 900 s.

## Report format

Write `/tmp/dogwalk_<slug>/report.md`:

1. **Summary:** three sentences.
2. **What worked.**
3. **What fell short:** per item: symptom, evidence (short quote or event with time), likely cause with file and function, fix.
4. **Feature acceptance:** status of each feature the call exercised.
5. **Open questions.**

Keep quotes short; mask numbers and codes.

## Privacy

Transcripts are Adam's conversations. Keep pulls and reports in `/tmp`, never in the repo, and never commit them. Never print the admin password or Cloudflare tokens.
