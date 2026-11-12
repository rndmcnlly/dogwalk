# Source contract (checked 2026-11-12)

Primary task recipes:
https://docs.liquid.ai/lfm/key-concepts/audio-capabilities

- ASR: system `Perform ASR in English.`; input audio resampled to 16 kHz mono.
- Live ASR: `Perform live ASR.`, about 0.96-second encoder chunks, English only.
  After 30 seconds the reference restarts text context but retains encoder state.
- TTS: `Perform TTS in American English using a female voice.` (or male),
  assistant prefill `<|audio_start|>`; output waveform 24 kHz.
- Spoken chat: system `Respond with interleaved text and audio.`, prefill
  `<|mixed_start|>`; interleaving uses six text tokens and twelve speech frames.
  The guide says keep that system prompt exact and put other guidance in the user
  turn for this recipe.
- Tool-capable spoken chat has a distinct recipe: start system with
  `Respond with function calls, and interleaved text and audio.\n\n`, followed by
  instructions and a tool list. Model chooses calls or speech. Do not force an
  output-mode prefill which prevents that choice.
- Raw tool calls use `<tool_call><function=...>` blocks, not OpenAI JSON natively.
  The serving adapter parses calls; results return as `tool` messages. Thinking
  prefills are for text answers, not the tool-call recipe.
- Speech sampling: audio temperature 1, top-k 128, repetition penalty 1.1,
  repetition window 4096. These settings do not imply a text temperature of 1.

Those are model recipes, not a promise that every serving endpoint exposes all
controls. The runtime and probe reports record actual local API behavior.

Local serving implementation references:

- https://github.com/ggml-org/llama.cpp/pull/31269 (realtime conversation)
- https://github.com/ggml-org/llama.cpp/pull/31287 (realtime tools)
- https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md

Model:
https://huggingface.co/LiquidAI/LFM3-Audio-2B-GGUF

Independent reference:
`mlx-community/parakeet-tdt-0.6b-v3`, via `parakeet-mlx==0.7.0`.
Its timestamps describe recognized waveform positions, not model computation.
