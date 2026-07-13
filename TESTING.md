# Testing Dogwalk

Dogwalk separates session-manager testing from Walker judgment and voice transport.
Most iteration should happen at the scripted ACP seam, without Realtime,
WebRTC, a browser, microphone, or speaker.

## Standard Cycle

Run the collaboration scenario:

```bash
uv run --script text_spike.py test collaboration
```

A passing run prints one summary line and exits zero:

```text
PASS collaboration (34.5s): 2 sessions, 3 turns, 2 files
```

A failed run exits nonzero and prints the reason, detailed ACP log, and preserved
temporary workspace. Inspect those artifacts only on failure. Add `--verbose` to
print JSON step events when debugging the runner itself.

The default workspace is a fresh directory under `$TMPDIR`. It is deleted after a
pass and preserved after a failure. Using an existing workspace requires both
flags, making accidental project mutation difficult:

```bash
uv run --script text_spike.py test collaboration \
  --workspace /path/to/workspace --allow-project-workspace
```

## What Collaboration Covers

`collaboration.test.yaml` currently verifies:

- Two Dogs receive distinct ACP sessions.
- A resting Dog retains its ACP session across another prompt.
- A Dog can be renamed and addressed through its new vocal alias.
- Two Dogs can hand work through shared files.
- Turn counts and expected files are structurally asserted.
- Write-enabled test Dogs are reported accurately.
- A sideband timer fires without a voice call.

The scenario runs write-capable Dogs only in its isolated test workspace. ACP
permission requests are accepted only when an explicit allow-once option exists.
Questions and unfamiliar permission shapes fail the test rather than guessing.

## Scenario Format

Scenarios are YAML files named `<name>.test.yaml` in the project root. The runner
validates the whole file before starting a Dog. Supported steps are `start`,
`wait`, `remember`, `continue`, `rename`, and `timer`; supported assertions are
`file_exists`, `same`, `different`, `turns`, and `contains`.

Session identifiers can be captured and compared without hardcoding them:

```yaml
steps:
  - start:
      dog: Scout
      task: Inspect the workspace.
  - wait: Scout
  - remember:
      original_session: Scout.session.id
  - continue:
      dog: Scout
      message: Inspect it again.
  - wait: Scout

assert:
  - same: [Scout.session.id, $original_session]
  - turns:
      Scout: 2
```

Always `wait` before observing a new Dog's session metadata. Preflight validation
rejects the common race of remembering `session.id` immediately after `start`.
The scenario timeout is one overall budget, not a fresh budget for every step.

## Test Layers

1. Scripted ACP scenarios test Dog lifecycle, concurrency, aliases, files,
   permissions, continuation, cancellation, and timers. Use these by default.
2. Text Walker orchestration tests will evaluate whether a weak Walker chooses and
   coordinates tools correctly from one overall natural-language task. This layer
   is not implemented yet and should reuse the same `AcpPack` seam.
3. WebRTC voice smoke tests cover transport-specific behavior such as barge-in,
   muting, audio cleanup, and earcons. Do not use audio tests to diagnose ACP state.

For a subagent, the preferred instruction is: run the relevant one-line scenario
command, inspect the reported artifacts only if it fails, make the smallest fix,
and rerun until the concise result passes.
