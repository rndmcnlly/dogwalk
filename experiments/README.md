# Speaker verification research handoff

This branch is a reference archive and runnable proof of concept, **not a proposed
production merge or an authentication system**. Main may advance independently.
Start here when resuming from a session rooted in the main checkout. Read the
reports directly from this branch on GitHub, or use `git show
origin/speaker-verification:experiments/README.md` without switching branches.
Experiments remain in the separate `dogwalk-speaker-verification` worktree.

## Reading order

1. `speaker_verification/MODEL_RESEARCH.md`: released contemporary models, evidence,
   source links and licenses, rather than assuming newer architecture means better.
2. `speaker_verification/COMPARISON.md`: ECAPA, CAM++ and ReDimNet2 synthetic tests.
3. `phone_voice_collection/README.md`: actual collection transport, security and
   rollback. Its direct-stream section supersedes the historical Record proposal.
4. `speaker_verification/REAL_PHONE_RESULTS.md`: 18 real telephone clips, three
   audio modes, genuine-only consistency and CPU measurements.
5. `speaker_worker/RESULTS.md`: deployed float32 WASM equivalence, memory concerns
   and the corrected personal-account deployment's unresolved availability.

## Product proposal, not implemented behavior

Preserve ordinary-telephone access: no smartphone app, screen, remembered password
or companion device required. An unfamiliar handset should remain usable. Caller
ID is a number assertion, not identification of a person or physical handset;
STIR/SHAKEN strengthens number provenance, not speaker identity.

Passive text-independent speaker verification can collect evidence during ordinary
conversation. Continuous authentication and a time-limited trust window for a
familiar calling number were discussed, but neither policy was selected. A daily
number cache would inherit number spoofing/shared-phone risks and must not become
an unexamined bypass. A spoken, nonsecret user ID could select one enrolled
reference for one-to-one verification, rather than identifying a speaker among all
users. It is an identifier, not a credential; multi-user support is future scope.

For an explicit check, use a fresh short grammatical sentence, such as “The purple
suitcase belongs upstairs.” The caller repeats it immediately, without remembering
anything for later. Familiar syntax and concrete words reduce cognitive load;
avoid digit strings, tongue twisters, disturbing commands and references the caller
must understand. The Sneakers inspiration is optional theme flavor, not a rule.
Use neutral terms in core code, following `DOMAIN.md`.

Phrase correctness (ASR) and speaker similarity are separate signals. A randomized,
single-attempt phrase raises the bar for fixed recordings and clip-splicing, but
does not establish liveness or defeat modern voice cloning. Sequential challenges
on an unfamiliar number were discussed, not validated as a defense. A short public
utterance, including an identifier, may supply cloning material. Do not claim a
voice check is a strong password replacement or let an inference score directly
authorize powerful coding agents.

## Proposed stateless internal API

Use a Cloudflare Service Binding / RPC, with no public routes or workers.dev
endpoint in an eventual implementation. This is a design proposal: the current
isolated experiment exposes authenticated HTTP, not this RPC interface.

```ts
type Audio = {
  audio: ArrayBuffer; // bounded mono signed 16-bit little-endian PCM
  format: "pcm_s16le";
  sampleRate: 8000 | 16000;
};
type Reference = { embedding: number[]; embeddingSpace: string };

embed(input: Audio): Promise<Reference & { audioSeconds: number }>;
verify(input: Audio & { reference: Reference }): Promise<{
  cosineSimilarity: number;
  embeddingSpace: string;
  audioSeconds: number;
}>;
```

`embeddingSpace` must identify weights, preprocessing and normalization; reject
incompatible references. Keep loaded-model caching, but no per-user state. The
main Worker owns reference storage, user selection, thresholds, recovery, consent,
retention and authorization. Return explicit invalid-input, insufficient-audio,
version and inference errors, not a fake low score. Bound audio duration and
concurrency. Challenge ASR and spoof detection are separate components. Binding
access does not remove Worker memory or CPU limits.

## What the evidence supports

- CPU inference is practical: real short clips took roughly 15–16 ms for ECAPA and
  CAM++ PyTorch, 67 ms for B3 on an M3 Max, excluding startup and collection.
- CAM++ PyTorch is a provisional CPU-service candidate; its released ONNX artifact
  did not match the tested PyTorch checkpoint and remains unresolved.
- B3 is a plausible Worker candidate because of its small weights, not because we
  proved better authentication. Deployed waveform-to-embedding WASM matched local
  PyTorch with maximum normalized error about 1.41e-6 and score error 1.32e-6.
- Worker WASM linear memory reached 111.875 MiB, not total isolate peak memory.
  Warm/cold client round trips were about 0.884/2.792 seconds. Capacity is unproven.
- The real corpus has only Adam, one call per audio mode, and 1.56–2.38 seconds of
  energy-active audio per response. No impostor, fairness, threshold, spoofing or
  operational reliability claim follows. No listening/ASR audit was performed.

## Operational and privacy handoff (2026-11-10)

Normal Twilio incoming configuration was restored and read-back verified after
collection. Production Cloudflare code was not changed. Do not re-arm collection
as part of reading or testing these reports.

The initially successful isolated Worker was accidentally deployed using BayLeaf's
Wrangler login, then deleted. The personal-account experimental Worker remains
documented in `speaker_worker/RESULTS.md`; its HTTPS probe failed before inference,
and that account's Free CPU budget remains a separate concern. No subscription
upgrade was made. Account-pinned tooling and an explicit cleanup command are
provided there. Nothing in production depends on the experimental service.

Real WAVs, embeddings, trial-level results, call metadata and routing snapshots
remain outside Git at `~/Library/Application Support/Dogwalk/phone_voice_collection/`.
The local retention cleanup is explicit, not scheduled. Generated synthetic audio,
downloaded weights, ONNX exports and bundles are ignored, so cloning this branch
alone does not reconstruct the corpus. Follow each experiment's setup instructions;
some collection helpers intentionally depend on Adam's local environment.

Next evaluation needs longer speech, independent calls/days, consented impostors,
held-out calibration and replay/cloning tests. Next Worker work needs explicit
account/plan verification, safe memory headroom and internal binding deployment.
Those are proposals, not authorization to deploy or enable biometric access.
