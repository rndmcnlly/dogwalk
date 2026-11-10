# Third-party provenance

ReDimNet2 B3 VoxCeleb2 large-margin weights:
https://github.com/PalabraAI/redimnet2/releases/download/v1.0.0/b3-vox2-lm.pt

Source revision: `c5bbe0b76e37df698c403f8844e41304ceab6307`.
Checkpoint SHA256: `02d14efdc184573873b5c3f62bea847fcdb6919fbba19a8da7af7c1ba36b76a2`.
Publisher repository labels the release MIT. Training-data rights and biometric
deployment obligations have not been independently audited. This is a research
experiment, not authorization to deploy identity decisions.

Citation: Ivan Yakovlev and Anton Okhotnikov, *ReDimNet2: Scaling Speaker
Verification via Time-Pooled Dimension Reshaping*, Interspeech 2026,
https://arxiv.org/abs/2603.11841.

## ReDimNet2 MIT license

Copyright (c) 2026 Palabra.ai

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Some upstream layer source carries the same MIT terms with
`Copyright (c) 2024 ID R&D, Inc.`. Retain that attribution when redistributing
those sources or derived binary exports.

ONNX Runtime 1.24.1: Microsoft, MIT, notices included with the npm package.
The build-time precompiled WASM injection technique was described by CosteGieF:
https://github.com/microsoft/onnxruntime/discussions/27400 and
https://github.com/cloudflare/workers-sdk/discussions/12616.
Those discussions are implementation reports, not official platform guarantees.

The local fixtures originate only from the existing synthetic Breeze corpus:
`../speaker_verification/generation.json`. That experiment records the Breeze
model/output research and noncommercial restriction. No fixtures are bundled or
served publicly, and no real caller recordings, templates or embeddings are used.
