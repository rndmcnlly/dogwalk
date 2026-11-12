# LFM3-Audio research runtime

## Isolation and hardware

Prepared 2026-11-12 in worktree `/Users/adam/.local/share/opencode/worktree/8607022b2dd2f80177bec186a7e16e7c4457b9b4/lfm3-voice-blanket`.
Apple M3 Max, 40 GPU cores, 64 GiB unified memory, Metal 4. Approximately 54 GiB disk space available before download. Existing `omlx-server` PID 45901 listens on 12348; it is not stopped or reconfigured. No existing llama-server listener was found. Other listening services remain untouched.

Existing Homebrew binary: `/opt/homebrew/Cellar/llama.cpp/0.9.0/bin/llama-server` (also `/opt/homebrew/bin/llama-server`). Version output: `0.9.0 (build 12110, commit cd959bae9)`, AppleClang 21.0.0.21000401, Darwin arm64. This exceeds Liquid's minimum 0.8.0/b11960. No installation, rebuild, or upgrade was needed. Old Jan copies b5509/b6929 were found and not used. The inspected Hugging Face cache contains Breeze-TTS, Parakeet, and SpeechBrain, not LFM3-Audio.

## Setup and exact invocation

All paths below are relative to this worktree unless absolute. `research/lfm3_voice/artifacts/.gitignore` ignores the entire runtime artifact directory, including itself. Downloads, manifests, upstream documentation snapshots, PID, and server logs stay there. No production code or existing service configuration is changed.

```sh
uv run --script research/lfm3_voice/runtime_setup.py download
uv run --script research/lfm3_voice/runtime_setup.py start
```

Official repository: `LiquidAI/LFM3-Audio-2B-GGUF`, pinned revision `39670cb5c42e9c4bfe046519ba7ffbe47f9552ad`.

- Backbone: `research/lfm3_voice/artifacts/LFM3-Audio-2B-Q8_0.gguf`.
- Audio encoder/codec projector: `research/lfm3_voice/artifacts/mmproj-LFM3-Audio-2B-Q8_0.gguf`.
- Download verifies SHA256 against the repository LFS object identifiers. Exact hashes and sizes go into `artifacts/model-manifest.json`.

Verified files:

- Backbone: 1,846,236,704 bytes; SHA256 `d4e36902ba8f0ee7b333596030a59d650f88ac76660b4d9ba5ff77c3c2258fc0`.
- Projector: 303,811,840 bytes; SHA256 `2b6648b1fd48af16aa0877b0592b144d15070783673e038a379fdbc51d40e365`.

The setup script starts this command with absolute model paths, detached from the launching shell, stdin disconnected, and stdout/stderr appended to `artifacts/server.log`:

```sh
/opt/homebrew/Cellar/llama.cpp/0.9.0/bin/llama-server \
  -m "$PWD/research/lfm3_voice/artifacts/LFM3-Audio-2B-Q8_0.gguf" \
  --mmproj "$PWD/research/lfm3_voice/artifacts/mmproj-LFM3-Audio-2B-Q8_0.gguf" \
  --host 127.0.0.1 --port 18391 -c 32768 -np 1 -ngl 99 \
  --alias lfm3-audio-2b-q8
```

The script refuses an occupied port; it never kills an existing process. `server.pid` and `server-command.json` record the dedicated launch. One slot and explicit 32K context bound memory use. Sampling defaults come from the GGUF. No API key is configured: this is a loopback-only research service.

Endpoints:

- Health: `http://127.0.0.1:18391/health`.
- Runtime metadata: `http://127.0.0.1:18391/props`.
- OpenAI-compatible API base: `http://127.0.0.1:18391/v1`.
- TTS: `POST http://127.0.0.1:18391/tts`.
- Conversation WebSocket: `ws://127.0.0.1:18391/v1/realtime`.
- Live transcription WebSocket: `ws://127.0.0.1:18391/v1/realtime?intent=transcription`.

## Voice controls and API boundaries

The following is based on Liquid documentation and llama.cpp source/docs at the installed commit, not on quality or speed benchmarks.

**Standalone TTS:** `/tts` accepts `input` (or `prompt`), `lang`, and `voice` (`female` or `male`; inspect `/props` for advertised names). Returns WAV, not JSON. `stream: true` streams audio bytes. `response_format: "pcm"` returns raw **float32 LE mono**, not PCM16. Output is 24 kHz. `seed` controls sampling; `n_predict` bounds audio frames (default 512). `top_k`, `top_p`, and `repeat_penalty` are documented. The standard OpenAI `/v1/audio/speech` route is not the documented interface. Generic `/tts` speaker-reference fields exist but do not establish that this particular model supports reliable voice cloning. No such claim is made here.

**Realtime conversation:** accepts streamed **PCM16 mono**, normally 16 kHz input and 24 kHz output; use the rates advertised in `session.created`. Audio prefills incrementally but a reply is turn-based: send `input_audio_buffer.commit`, then `response.create`. This is not an autonomous end-of-turn or full-duplex interruption policy. Client-side turn detection and playback interruption remain harness responsibilities. `response.cancel` is supported. Output audio deltas are base64 PCM16, unlike `/tts` raw PCM float32.

Set `session.output_modalities` to `["text", "audio"]` for interleaved spoken replies or `["text"]` for text-only. Session instructions and tool definitions must be configured **before the first audio or message**; later changes are rejected. Session type cannot change after that point either. Function calls and function-call results are supported, with the client responsible for execution and subsequent `response.create`.

**Important voice distinction:** `/tts` has explicit male/female selection. The installed realtime `session.update` handler has no voice selector and does not process `audio.output.voice`; do not assume OpenAI voice names or `/tts` voice fields carry over. Liquid's prescribed conversational system prompt is exactly `Respond with interleaved text and audio.` with additional directions in user content. Prompt-based changes to conversational voice are unverified research questions, not an exposed realtime control.

**ASR and other modes:** `/v1/chat/completions` accepts base64 `input_audio` content parts (WAV/MP3/FLAC); system prompt `Perform ASR in English.` selects transcription. This is not the same as appending realtime PCM. Live ASR is documented as English-only. The Transformers backend exposes thinking prefills, output-mode suppression and speech sampling controls which must not be assumed to map one-for-one onto llama.cpp JSON request fields. LFM3 tool syntax is XML at the model layer; llama.cpp parses it into API function-call events.

## Readiness

**Ready:** dedicated PID **98136**, `GET /health` returned `{"status":"ok"}`. `GET /props` confirms one slot, build `b12110-cd959bae9`, audio enabled, vision/video disabled, and `voices: ["female", "male"]`. `GET /v1/models` advertises `lfm3-audio-2b-q8`, Q8_0, 32768 runtime context, text/audio input and output.

A WebSocket connection returned `session.created` successfully, advertising 16000 Hz input, 24000 Hz output, and `turn_detection: null`. It was closed without sending audio or requesting a response. Evidence is saved in `artifacts/health.json`, `props.json`, `v1-models.json`, and `realtime-session-created.json`. No generation or benchmarks have been run by this setup task.

Startup warnings: llama.cpp labels audio input experimental and warns that no API key is set and CORS allows all origins. Loopback binding is not a browser-origin authorization boundary: do not treat this temporary research service as hardened or expose it publicly. Existing services were not modified.

## Sources

- https://docs.liquid.ai/lfm/key-concepts/audio-capabilities
- https://docs.liquid.ai/lfm/models/lfm3-audio-2b
- https://docs.liquid.ai/deployment/on-device/llama-cpp.md
- https://huggingface.co/LiquidAI/LFM3-Audio-2B-GGUF
- https://github.com/ggml-org/llama.cpp/blob/cd959bae9/tools/server/README.md (TTS and realtime sections)
- https://github.com/ggml-org/llama.cpp/blob/cd959bae9/tools/server/server-realtime.cpp (`on_session_update`)
- https://github.com/ggml-org/llama.cpp/blob/cd959bae9/tools/server/server-context.cpp (TTS handler)

Snapshots of those three llama.cpp files are saved under ignored `artifacts/upstream-*` for the harness authors.
