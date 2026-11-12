# Android trial checkpoint: 2026-11-11

## Start here

`prototype/android-real-orchestrator` is the retained implementation branch:
native Android frontend, approved HTML design and translator brief in `design/`,
authenticated app transport, local hosting adapter and reproducible test helpers.
This is a research prototype, not an Android service offer. Do not merge or
deploy it wholesale while unrelated mainline work is arriving.

Curated earlier research remains on `prototype/android-text-frontend-live`
(reference PR #10, intentionally DO NOT MERGE). Read its
`prototypes/android_voice_probe/RESEARCH_REFERENCE.md`. Original unreconciled
drafts are separately retained on `prototype/android-voice-probe`; prefer the
curated reference over those drafts.

## What actually worked

- Android-local Gemma E2B inference, optional on-device recognition/offline TTS,
  safely silent text startup, and explicit voice activation.
- Native IME-safe composer, separate connection/local readiness, one chronological
  conversation/diagnostic timeline, and hidden setup controls.
- Actual emulator Keystore and HTTPS/WSS client tests. Shipping TLS rejected the
  self-signed certificate; test trust lived only in the separate test APK.
- A real local Worker/Session Orchestrator connected to real OpenCode ACP, using
  a private Agent database and disposable workspace. The automated proof created
  Fir, surfaced a real Permission Request, accepted an offered once-only choice
  through a Handoff, wrote/read a marker, observed normal `end_turn`, and retrieved
  retained status after reconnect without another Prompt Turn.
- Adam installed v5 on his phone, connected through tailnet TLS and passed the
  permission gate. Host inspection verified `permission-marker.txt` contained
  exactly `Hello from my phone\n`: a phone-to-real-Agent file write, not a fixture.
- The suggested subsequent physical-phone reconnect test was not confirmed before
  shutdown. Automated reconnect evidence is separate from that missing observation.

Last completed verification: 33 JVM tests, three emulator instrumentation tests,
silent E2B UI smoke, Worker typecheck, app/local-adapter integration, normal smoke,
Voice Session and Gateway suites. Last Android lint report: 0 errors, 17 warnings.
These are not evidence of production hosting or locked-screen phone voice.

## Scope and remaining limits

No Cloudflare deployment, remote D1 change, Daytona action, production sandbox
attachment or main-branch edit was made for this on-device/local trial. Earlier
research includes separately authorized experiments, documented in its own record.

The local workspace is **not an OS security sandbox**. Agent permissions deny
shell, network tools, subagents and external paths. Only two marker files may be
edited, with explicit permission. It is not a general workspace configuration.

One private bearer credential is bound to a synthetic local identity. No passkey,
enrollment website or device registry was built. Acceptance is not completion or
authorization. Lost acknowledgments leave acceptance unknown; reconnect never
automatically replays input. Dedupe is live-attachment-only. Android history and
pending delivery are not durable mailboxes.

Physical-device voice/lock-screen behavior, small-model phrasing fidelity, ASR
truncation and interrupted-delivery behavior need further work. No cloud Frontend
fallback is permitted by this experiment.

## Shutdown and private local recovery

The local supervisor, Worker, Gateway and private ACP Agent were stopped. Trial
Tailscale Serve routes on 8443 and 8444 were removed. The phone's saved endpoint
will no longer connect. Its installed app, settings and weights were not changed.
Next time, start a fresh local trial and provision its new local token.

Private, non-Git recovery assets are retained on Adam's laptop at:

```text
/Users/adam/Desktop/dogwalk/.git/private-archives/android-2026-11-11/
```

This contains relocated ignored assets from retired worktrees: toolchain, weights,
keys, builds and private evidence. `manifest.json` records old/new paths.
`local-trial-state/` retains stopped local state and private credentials; its
runtime JSON contains old absolute paths, so do not resume its supervisor blindly.
`dogwalk-local-v5.apk` is the preserved verified APK. These assets are not pushed.

The retained worktree's ignored `.tools/` links reuse the archived JDK/SDK/Gradle.
Its default debug key is the phone-compatible key; the unrelated newly generated
key was archived, not discarded. `uv run --script build.py` should work from this
prototype directory on this laptop. Fresh clones need a toolchain and the private
update-compatible key. Never uninstall to bypass a signing mismatch.

Version 5 APK SHA-256 before cleanup:

```text
d514714646405aede988cdc563464f84166543d33a9b07a67ce5dba8a37c6cb0
```

Expected signing certificate SHA-256:

```text
770c6f0b86fa8f1de880a8f60a3f4c937733335f17e3ba8667b97bc331959b92
```

## Next session

1. Read this checkpoint, README and `DOMAIN.md`. Inspect incoming mainline changes
   before deciding how to integrate or rebase.
2. Use `local_trial.py start` with the explicit private provider environment. Keep
   D1/DO state local and production untouched.
3. Expose only the app Worker port through tailnet HTTPS, never ACP or Funnel.
   Transfer the newly generated app token privately into Android settings.
4. Test phone reconnect/continuity, then voice. Expand beyond the two-marker
   workspace only after deliberately agreeing on its authority.
