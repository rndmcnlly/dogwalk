# Research reference: do not merge this branch

Tracking proposal: [Dogwalk #9](https://github.com/rndmcnlly/dogwalk/issues/9).

This branch collects exploratory Android audio plumbing, a guarded alternate
Frontend transport, model comparisons, failed designs, and request-delivery
experiments. It is a **reference archive for review and selective extraction**,
not a proposed production implementation. Open a fresh implementation branch from
current main for work that will eventually land. Do not merge, auto-merge, or
deploy this entire branch. Its base is `66e32fb`; main has advanced since then.

## Access and economics

The app path requires an installed app and a recent-enough smartphone. That asks
more of callers' equipment while reducing operators' dependence on continuous
GPT-Live per-minute costs. Local inference still consumes hardware, battery and
storage, and remote Orchestrator/ACP work still costs money. Ordinary telephone
access remains a parallel minimum-equipment path. Eventual Paired Browser access
inside/from the app should be optional, not a visual prerequisite for control.

## Reading order

1. [FRONTEND_REQUESTS.md](FRONTEND_REQUESTS.md): strongest current result and
   clearest failure. Individual jobs/acknowledgements improve coverage; small-model
   semantic supersession incorrectly retires live questions.
2. [FRONTEND_EVAL.md](FRONTEND_EVAL.md): broader E4B/E2B criteria, actual generated
   heard-history simulations, controlled races, and grader limitations.
3. [FRONTEND_BAKEOFF.md](FRONTEND_BAKEOFF.md): cache-aware design comparisons and
   why raw prompt length is not the right optimization target.
4. [FRONTEND_PROTOCOL.md](FRONTEND_PROTOCOL.md): existing app/gateway boundary and
   authority limits. Later queue experiments are not integrated into that runtime.
5. [README.md](README.md): physical/emulator setup, deployment history, scripts,
   provider setup and recorded validation. This is an operator-specific runbook,
   not a portable release installation guide.
6. [research_results.json](research_results.json): curated numeric summaries and
   selected synthetic outputs, exported without private call payloads or media.

## Candidates for extraction

- Local Android ASR/TTS, locked-screen/headset experiments and bounded interruption
  handling, subject to fresh device testing.
- Explicit local inference provider selection, thinking-off controls, output
  validation, no cloud fallback, and separate frontend/downstream accounting.
- Stable prompt prefixes, clearly distinguished current caller input and heard
  history, and separate brief/Following context.
- One request per phrasing job and playback receipt. Completion is not proof of
  semantic fulfillment. Preserve pending requests through partial playback.

Do **not** adopt small-model semantic request retirement, batch acknowledgement
as meaning coverage, or the operator-admin attachment as production app identity.
No model is demonstrated to provide full canonical/persona/capability parity.

## Evidence boundaries

The successful real Android → gateway → Orchestrator → ACP task used a cloud text
Frontend. Gemma E2B/E4B comparisons here use laptop oMLX, not an on-phone runtime.
The later per-request queues, playback callbacks and races are synthetic text
experiments, not real Android callback integration. Regex/manual-agent inspection
is not independent human acceptance or a statistically calibrated benchmark.

Raw `.tools/` runs, generated Breeze audio, private live-call evidence, local
credentials, provider payloads, APKs, model weights, SDKs/JDK/Gradle and dependency
symlinks are excluded. The curated JSON preserves only explicitly selected
synthetic-research summaries/examples. A clone is not a private corpus backup.
Live service state must be checked separately before any operational change;
publishing this archive does not deploy, stop, wake or refresh anything.

## Reproducible offline checks

From the repository root:

```sh
uv run --with aiohttp --with numpy --with scipy --with soundfile \
  python -m unittest discover -s prototypes/android_voice_probe -p 'test_*.py'
uv run --script sandbox_acp_gateway.py test
```

From `cf_worker/` with its dependencies installed:

```sh
uv run --script text_frontend_test.py
uv run --script voice_session_test.py
uv run --script smoke_test.py
npm run typecheck
```

Real inference, emulator/device audio, and live attachments are explicitly opt-in
and require local setup. Do not run the live harness or `deploy.py` merely to review
the PR. Use the fixture paths first.

## Useful review targets

Look for privacy/security boundaries, lifecycle races and resource leaks; test
validity and claims that exceed their evidence; incomplete permission wording;
whether small-model jobs are genuinely bounded; cache versus attention/memory
trade-offs; and the smallest coherent pieces worth reimplementing on current main.
Review findings inform successor PRs, not a drive to merge this archive.

## Publication-time validation

- All 98 discoverable Python prototype tests pass with the optional audio-analysis
  dependencies included (see command above).
- Sandbox ACP gateway: 13 checks pass.
- Development text frontend and deterministic startup-race checks pass.
- Worker smoke: 31 core checks plus operator/provider timing checks pass.
- Worker TypeScript typecheck passes.
- Full legacy `voice_session_test.py` currently fails at its Whiteboard `chart.png`
  fetch assertion (line 1330): expected HTTP 200, got HTTP 400. This has not been
  diagnosed or hidden by changing/skipping the assertion. Earlier historical passes
  in the runbook do not supersede this fresh result.
- Secret scan of publication candidates was inspected: flagged values are runtime
  expressions, generated local test credentials, illustrative URLs, and explicit
  fixture strings, not real credential literals. No binary artifacts or dependency
  symlinks are publication candidates.

This snapshot is not a green production candidate, even apart from its deliberate
research-only scope. The next Android implementation should start on current main
and selectively reuse the prototype, rather than inherit the entire diff.
