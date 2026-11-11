# Text Voice Frontend spike protocol

This is an experimental adapter for the existing Voice Frontend boundary, not a
new Session Orchestrator or session-management model. Audio stays on Android.
Only text and playback/control events cross a WebSocket to an operator-run
gateway. Production PSTN/SIP behavior is not changed by this prototype.

## Connection

- Endpoint: `ws://HOST:8768/ws` over the private tailnet, or
  `ws://10.0.2.2:8768/ws` from Android Emulator when the gateway listens locally.
- The app sends `Authorization: Bearer TOKEN` in the WebSocket handshake.
- The gateway stores its ephemeral access token in ignored `.tools/` or accepts
  one from the environment. It must not print token values, record authorization
  headers, embed inference credentials in the APK, or listen publicly by default.
- Optional app configuration link:
  `dogwalk-audio://connect?endpoint=URL_ENCODED_WEBSOCKET_URL&token=URL_ENCODED_TOKEN`.
  Opening a configuration link populates settings; the user still explicitly
  starts microphone capture. Treat these links as private capability material.
- Blank endpoint retains the original deterministic audio-probe behavior.
- No automatic remote speech-recognition or synthesis fallback.

## Android to gateway

```json
{"type":"user_transcript","turn_id":"unique-per-utterance","text":"What is the session doing?"}
{"type":"user_speech_started"}
{"type":"user_speech_ended","outcome":"no_transcript"}
{"type":"speech_started","speech_id":"server-issued-id"}
{"type":"speech_finished","speech_id":"server-issued-id","outcome":"completed","spoken_chars":null}
{"type":"session_stop"}
```

`outcome` is `completed`, `interrupted`, or `error`. `spoken_chars` may be null;
if provided, it is an approximate playback callback position, not an acoustic
measurement. Each server-issued speech ID gets at most one terminal event.
Local interruption stops playback immediately, without waiting for a network
round trip. It does not cancel an ACP Prompt Turn or erase a Managed Session.
`user_speech_ended` releases the conversational floor after a no-match, empty
result, or recognition error. It is not a new utterance or Handoff. An otherwise
idle recognition cycle must not trigger model inference; pending commentary or
an interrupted unanswered utterance can resume when the floor is released.

## Gateway to Android

```json
{"type":"ready","protocol":1,"session_id":"opaque-id","mode":"live","frontend_model":"provider/model"}
{"type":"speak","speech_id":"unique-per-speech-request","turn_id":"client-turn-id-or-null","text":"I will check with the session."}
{"type":"status","state":"thinking","message":"Waiting for the Session Orchestrator"}
{"type":"error","message":"Voice Frontend unavailable; try again"}
```

`mode` is `live` or `fixture`; fixture tests must announce themselves as such and
must not be reported as integration with the real Session Orchestrator. Status
and error events are diagnostic UI/log events, not permission to invent spoken
session facts. Replies can arrive asynchronously. Stale pending frontend speech
must be invalidated after user corrections; already-running downstream work is
not implicitly cancelled. The app must not start listening again after Stop just
because a delayed network message arrives.

`turn_id` is null for unsolicited speech and otherwise identifies the latest
user transcript this speech addresses. The app discards speech explicitly tied
to a superseded turn. Connection-generation guards reject callbacks from closed
connections independently of turn IDs.

## Model and Orchestrator boundary

The event-driven text model performs the Voice Frontend's existing role: persona,
repair, short speech, and Handoffs. It has no session-management tools. Handoffs
carry conversation text to the existing Orchestrator; `brief` remains unspoken
background context and `say` requests speech phrased by the Frontend. Existing
Following context is authoritative input, not permission to infer completion.
Handoff acknowledgements are constrained to a neutral thinking acknowledgement,
not arbitrary model-authored claims about session actions or future scheduling.
The Orchestrator still determines what actually happens and requests speech about
it through the existing boundary.

Idle audio/polling must not trigger language-model calls. Frontend usage records
contain model ID, request duration, input/output tokens, cache tokens when
reported, and provider-reported or explicitly estimated cost. Orchestrator/ACP
costs are excluded. Missing usage or cost remains unknown, never recorded as zero.

## Local spike acceptance and deployment boundary

The first implementation is built and tested locally. Do not attach it to the
deployed Orchestrator, alter production state, or deploy the guarded Worker route
without a separate operator decision. A fixture Orchestrator can exercise the
new frontend's behavior and costs, but is not evidence of downstream integration.
Worker-local tests must separately exercise the actual existing Orchestrator
implementation through the new transport seam.

Minimum evidence before a phone trial:

- Existing blank-endpoint probe still builds and has a deterministic regression.
- Real text inference handles a conversation/repair and an asynchronous speech
  request, with model usage/cost recorded outside Orchestrator accounting.
- Fake-inference tests cover stale speech, invalid payloads, stop/disconnect,
  and silence generating no model calls.
- App sends actual recognized words and plays a correlated asynchronous reply.
- Logs clearly distinguish fixture downstream, local Worker testing, and any
  later live attachment. Interrupted playback is not silently recorded as heard.
