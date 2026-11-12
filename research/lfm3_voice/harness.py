# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.28,<1", "websockets>=14,<17"]
# ///
"""Synthetic, serialized LFM3 voice research. No production services or real tasks.

Run with uv run --script harness.py probe|tts|stt|replay. All model requests
are explicit CLI actions. Importing this module never contacts the runtime.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib.util
import io
import json
import math
import re
import struct
import time
import wave
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent
MODEL = "lfm3-audio-2b-q8"
TOOLS = [{"type": "function", "function": {"name": "delegate", "description":
          "Ask the backend to act on the host's received conversation. Acceptance is not completion.",
          "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}]


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def wav_info(data: bytes) -> dict:
    """Read PCM and IEEE-float WAV metadata without a third-party audio package."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF WAV")
    fmt = payload = None
    offset = 12
    while offset + 8 <= len(data):
        kind, size = struct.unpack_from("<4sI", data, offset)
        chunk = data[offset + 8:offset + 8 + size]
        if kind == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", chunk)
        elif kind == b"data":
            payload = chunk
        offset += 8 + size + size % 2
    if fmt is None or payload is None:
        raise ValueError("missing WAV fmt/data")
    encoding, channels, rate, byte_rate, align, bits = fmt
    return {"encoding": encoding, "channels": channels, "sample_rate": rate,
            "bits": bits, "duration_s": len(payload) / byte_rate, "payload": payload}


def audio_part(path: Path) -> dict:
    return {"type": "input_audio", "input_audio": {
        "data": base64.b64encode(path.read_bytes()).decode(), "format": "wav"}}


def response_message(body: dict) -> dict:
    try:
        return body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return {}


def text_content(message: dict) -> str:
    value = message.get("content") or ""
    if isinstance(value, str):
        return value
    return "".join(p.get("text", "") for p in value if isinstance(p, dict))


def parse_calls(message: dict) -> list[dict]:
    """Accept standard tool calls or Liquid XML, never repair invented arguments."""
    calls = []
    for item in message.get("tool_calls", []):
        function = item.get("function", {})
        args = function.get("arguments", None)
        try:
            args = json.loads(args) if isinstance(args, str) else args
        except (ValueError, TypeError):
            args = None
        calls.append({"id": item.get("id", f"call_{len(calls)}"),
                       "name": function.get("name"), "arguments": args,
                       "proposal_source": "api_tool_call", "dispatchable": isinstance(item.get("id"), str) and bool(item["id"].strip()),
                      "valid": function.get("name") == "delegate" and type(args) is dict and args == {}})
    if not calls:
        for block in re.findall(r"<tool_call>(.*?)</tool_call>", text_content(message), re.S):
            match = re.fullmatch(r"\s*<function=([\w]+)>\s*</function>\s*", block)
            # Liquid's argument-free function syntax has no parameter children.
            calls.append({"id": f"call_{len(calls)}", "name": match[1] if match else None,
                           "arguments": {} if match else None,
                           "proposal_source": "raw_xml_text", "dispatchable": False,
                          "valid": bool(match and match[1] == "delegate")})
    return calls


class Artifacts:
    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        (path / ".gitignore").write_text("*\n")
        self.counter = 0
        self.run_id = str(time.time_ns())

    def save(self, name: str, value: Any):
        (self.path / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")

    def event(self, value: dict):
        with (self.path / "events.jsonl").open("a") as stream:
            stream.write(json.dumps({"run_id": self.run_id, **value}, ensure_ascii=False) + "\n")

    def prefix(self, label: str) -> str:
        self.counter += 1
        return f"{self.run_id}-{self.counter:04d}-{re.sub(r'[^a-zA-Z0-9_-]', '_', label)}"


class Runtime:
    def __init__(self, base_url: str, artifacts: Artifacts, *, timeout: float = 180,
                 fingerprint: str = "unverified", transport=None):
        self.base_url = base_url.rstrip("/")
        self.artifacts = artifacts
        self.timeout = timeout
        self.fingerprint = fingerprint
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)
        self.lock = asyncio.Lock()  # One GPU request at a time, including caller synthesis.

    async def close(self):
        await self.client.aclose()

    async def request(self, route: str, body: dict | None = None, *, label="request") -> dict:
        prefix = self.artifacts.prefix(label)
        self.artifacts.save(prefix + "-request.json", {"route": route, "body": body})
        start = time.perf_counter()
        result = {"status": "error", "route": route, "prefix": prefix}
        chunks = []
        async with self.lock:
            sent = time.perf_counter()
            try:
                async with self.client.stream("GET" if body is None else "POST", route, json=body) as response:
                    headers_at = time.perf_counter()
                    first = None
                    async for chunk in response.aiter_bytes():
                        if chunk and first is None:
                            first = time.perf_counter()
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    (self.artifacts.path / (prefix + "-response.bin")).write_bytes(raw)
                    try:
                        parsed = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        parsed = None
                    result.update(http_status=response.status_code, headers=dict(response.headers), body=parsed,
                                  raw_path=str(self.artifacts.path / (prefix + "-response.bin")),
                                  status="ok" if response.is_success else
                                  "unsupported" if response.status_code in (400, 404, 405, 422, 501) else "http_error",
                                  headers_s=headers_at - sent,
                                  first_body_s=None if first is None else first - sent)
            except httpx.TimeoutException as exc:
                result.update(status="timeout", error=str(exc))
            except httpx.HTTPError as exc:
                result.update(status="transport_error", error=str(exc))
            finally:
                raw_path = self.artifacts.path / (prefix + "-response.bin")
                raw_path.write_bytes(b"".join(chunks))
                result["raw_path"] = str(raw_path)
        result.update(elapsed_s=time.perf_counter() - sent, queue_s=sent - start)
        self.artifacts.save(prefix + "-result.json", result)
        return result

    async def chat(self, messages: list[dict], *, tools=False, native_audio=False, fragment=None) -> dict:
        body = {"model": MODEL, "messages": messages, "temperature": 0, "seed": 0,
                "max_tokens": 512, "stream": False}
        if tools:
            body["tools"] = TOOLS
        if fragment:
            body.update(fragment)
        if getattr(self, "text_recipe", False):
            body["messages"] = json.loads(json.dumps(body["messages"]))
            body["messages"][0]["content"] = body["messages"][0]["content"].replace(
                "Respond with function calls, and interleaved text and audio.\n\n",
                "Respond with text or function calls.\n\n", 1)
        if getattr(self, "text_only_output", False):
            # Explicit host adapter: HTTP chat does not decode interleaved codec
            # frames. Suppress the pinned model's mode switches, not tool calls.
            # Token IDs are independently checked by experiment.py at startup.
            body["logit_bias"] = {"125012": -100, "125015": -100}
        if native_audio:
            body.update(modalities=["text", "audio"], audio={"voice": "male", "format": "wav"})
        result = await self.request("/v1/chat/completions", body, label="native-chat" if native_audio else "chat")
        message = response_message(result.get("body") or {})
        result.update(message=message, predicted_text=text_content(message), calls=parse_calls(message),
                      output_transport="nonstreaming_chat_completions",
                      native_audio_status="requested_unverified" if native_audio else "not_requested_on_chat_route")
        audio = message.get("audio", {})
        if native_audio:
            result["audio_status"] = "unsupported_or_not_emitted"
            if isinstance(audio, dict) and audio.get("data"):
                try:
                    raw = base64.b64decode(audio["data"], validate=True)
                    info = wav_info(raw)
                    path = self.artifacts.path / (result["prefix"] + "-native.wav")
                    path.write_bytes(raw)
                    result.update(audio_status="native", audio_path=str(path), audio_duration_s=info["duration_s"])
                except (ValueError, struct.error) as exc:
                    result.update(audio_status="invalid_audio", audio_error=str(exc))
        self.artifacts.save(result["prefix"] + "-parsed.json", result)
        return result

    async def tts(self, text: str, voice="female") -> dict:
        if voice not in ("female", "male"):
            raise ValueError("voice must be female or male")
        key = digest({"text": text, "voice": voice, "fingerprint": self.fingerprint,
                      "lang": "American English", "seed": 0, "n_predict": 256, "route": "/tts"})
        cache = self.artifacts.path / "cache"
        cache.mkdir(exist_ok=True)
        path = cache / (key + ".wav")
        metadata = cache / (key + ".json")
        if self.fingerprint != "unverified" and path.exists() and metadata.exists():
            started = time.perf_counter()
            cached = json.loads(metadata.read_text())
            elapsed = time.perf_counter() - started
            return dict(cached, cache_hit=True, original_synthesis_elapsed_s=cached["elapsed_s"],
                        elapsed_s=elapsed, first_body_s=elapsed)
        result = await self.request("/tts", {"input": text, "voice": voice, "lang": "American English",
                                            "response_format": "wav", "seed": 0, "n_predict": 256,
                                            "stream": False}, label=f"tts-{voice}")
        if result["status"] == "ok":
            raw = Path(result["raw_path"]).read_bytes()
            try:
                info = wav_info(raw)
                path.write_bytes(raw)
                duration = info["duration_s"]
                result.update(audio_path=str(path), audio_duration_s=duration,
                              realtime_factor=result["elapsed_s"] / duration if duration else None,
                              cache_hit=False, text=text, voice=voice, fingerprint=self.fingerprint)
                metadata.write_text(json.dumps(result, indent=2) + "\n")
            except (ValueError, struct.error) as exc:
                result.update(status="invalid_audio", error=str(exc))
        return result

    async def stt(self, path: Path) -> dict:
        if getattr(self, "raw_asr", False):
            result = await self.chat([{"role": "system", "content": "Perform ASR in English."},
                                      {"role": "user", "content": [audio_part(path)]}])
            result.update(source_audio_path=str(path), recognizer_audio_path=str(path),
                          asr_input_mode="raw_wav", asr_preprocessing="none; historical serving-path diagnostic")
            return result
        from live_probe import wav_to_pcm16, save_wav
        pcm, source_info = wav_to_pcm16(path, target_rate=16000)
        directory = self.artifacts.path / "asr-inputs"
        directory.mkdir(exist_ok=True)
        normalized = directory / (hashlib.sha256(path.read_bytes()).hexdigest() + "-16k.wav")
        save_wav(normalized, pcm, 16000)
        result = await self.chat([{"role": "system", "content": "Perform ASR in English."},
                                  {"role": "user", "content": [audio_part(normalized)]}])
        result.update(source_audio_path=str(path), recognizer_audio_path=str(normalized),
                      asr_input_mode="normalized_16k",
                      recognizer_sample_rate=16000, source_audio_info=source_info,
                      asr_preprocessing="PCM16 mono, linear resampling to the official 16 kHz ASR input recipe")
        return result

    async def realtime_probe(self) -> dict:
        """Native turn-based WS text-to-audio probe, not evidence of full duplex."""
        import websockets
        prefix = self.artifacts.prefix("realtime")
        start = time.perf_counter()
        events, audio, first = [], [], None
        result = {"status": "unsupported", "mode": "native_realtime_turn_based", "prefix": prefix}
        async with self.lock:
            try:
                async with asyncio.timeout(self.timeout):
                    async with websockets.connect(self.base_url.replace("http", "ws", 1) + "/v1/realtime",
                                                  max_size=16 * 1024 * 1024) as ws:
                        async def send(event):
                            events.append({"direction": "send", "event": event})
                            await ws.send(json.dumps(event))
                        events.append({"direction": "receive", "event": json.loads(await ws.recv())})
                        await send({"type": "session.update", "session": {"type": "realtime",
                                    "instructions": "Respond with interleaved text and audio.",
                                    "output_modalities": ["text", "audio"], "max_output_tokens": 128}})
                        await send({"type": "conversation.item.create", "item": {"type": "message",
                                    "role": "user", "content": [{"type": "input_text", "text": "Say hello briefly."}]}})
                        await send({"type": "response.create"})
                        async for raw in ws:
                            event = json.loads(raw)
                            events.append({"direction": "receive", "event": event})
                            kind = event.get("type", "")
                            if kind in ("response.output_audio.delta", "response.audio.delta"):
                                first = first or time.perf_counter()
                                audio.append(base64.b64decode(event["delta"]))
                            if kind == "error":
                                result["error"] = event
                                break
                            if kind == "response.done":
                                result.update(status="ok" if audio else "unsupported_or_not_emitted",
                                              response=event, audio_status="native" if audio else "absent")
                                break
            except TimeoutError:
                result["status"] = "timeout"
            except Exception as exc:
                result.update(status="transport_error", error=f"{type(exc).__name__}: {exc}")
        if audio:
            path = self.artifacts.path / (prefix + ".pcm")
            path.write_bytes(b"".join(audio))
            result.update(audio_path=str(path), audio_encoding="see raw session output format; do not assume WAV")
        result.update(elapsed_s=time.perf_counter() - start,
                      first_audio_s=None if first is None else first - start)
        self.artifacts.save(prefix + "-events.json", events)
        self.artifacts.save(prefix + "-result.json", result)
        return result


class Backend:
    """Logical events, not wall-clock sleeps: work completes only on a later control."""
    def __init__(self):
        self.pending: dict[str, dict] = {}

    def accept(self, call: dict, transcript: list[dict], step: int, handoff_id=None) -> dict:
        receipt = {"status": "accepted", "receipt_id": f"receipt-{len(self.pending) + 1}",
                   "handoff_id": handoff_id, "backend_pending": True,
                   "host_received_step": step, "transcript_cut": len(transcript)}
        self.pending[receipt["receipt_id"]] = {"receipt": receipt, "transcript": json.loads(json.dumps(transcript))}
        return receipt


def load_strategies():
    spec = importlib.util.spec_from_file_location("lfm3_strategies", ROOT / "strategies.py")
    module = importlib.util.module_from_spec(spec)
    # Some dataclass-based strategy modules need their name registered during import.
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def narrowband(path: Path, artifacts: Artifacts) -> tuple[Path, dict]:
    """Documented approximation: one-pole 300–3400 Hz bandpass, 8k, G.711 mu-law."""
    raw = path.read_bytes()
    info = wav_info(raw)
    if info["channels"] != 1:
        raise ValueError("narrowband requires mono source")
    payload = info["payload"]
    if info["encoding"] == 3 and info["bits"] == 32:
        samples = list(struct.unpack(f"<{len(payload) // 4}f", payload))
    elif info["encoding"] == 1 and info["bits"] == 16:
        samples = [x / 32768 for x in struct.unpack(f"<{len(payload) // 2}h", payload)]
    else:
        raise ValueError("narrowband supports PCM16 or float32 WAV only")
    rate = info["sample_rate"]
    hi_a, low_a = math.exp(-2 * math.pi * 300 / rate), 1 - math.exp(-2 * math.pi * 3400 / rate)
    high = low = prior = 0.0
    filtered = []
    for sample in samples:
        high = hi_a * (high + sample - prior)
        prior = sample
        low += low_a * (high - low)
        filtered.append(low)
    values, clipped = [], 0
    for i in range(round(len(samples) * 8000 / rate)):
        position = i * rate / 8000
        left = min(int(position), len(filtered) - 1)
        frac = position - left
        sample = filtered[left] * (1 - frac) + filtered[min(left + 1, len(filtered) - 1)] * frac
        clipped += abs(sample) > 1
        pcm = round(max(-1, min(1, sample)) * 32767)
        sign = 0x80 if pcm < 0 else 0
        magnitude = min(abs(pcm), 32635) + 132
        exponent = max(0, min(7, magnitude.bit_length() - 8))
        mantissa = (magnitude >> (exponent + 3)) & 0xF
        encoded = ~(sign | exponent << 4 | mantissa) & 0xFF
        code = ~encoded & 0xFF
        decoded = (((code & 15) << 3) + 132) << ((code >> 4) & 7)
        decoded -= 132
        values.append(-decoded if code & 0x80 else decoded)
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(struct.pack(f"<{len(values)}h", *values))
    data = output.getvalue()
    target = artifacts.path / "cache" / (hashlib.sha256(raw).hexdigest() + "-pstn-v1.wav")
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(data)
    return target, {"condition": "pstn_8khz", "source_rate": rate, "delivered_rate": 8000,
                    "filter": "one-pole highpass 300Hz + lowpass 3400Hz; linear resample",
                    "codec": "G.711 mu-law encode/decode", "clipped_samples": clipped,
                    "duration_s": len(values) / 8000, "source_sha256": hashlib.sha256(raw).hexdigest(),
                    "delivered_sha256": hashlib.sha256(data).hexdigest()}


def host_event(event: dict, state: dict, backend: Backend) -> dict:
    """Translate fixtures only, never expectations, into canonical trusted controls."""
    kind = event["kind"]
    handoff = event.get("handoff")
    if kind == "brief":
        state["background"] = event["text"]
    elif kind in ("backend_pending", "backend_result"):
        state.setdefault("handoffs", {})[handoff] = {
            "kind": kind, "text": event["text"], "handoff_id": handoff,
            **({"revision": event["revision"]} if "revision" in event else {})}
        if kind == "backend_result":
            for pending in backend.pending.values():
                if pending["receipt"]["handoff_id"] == handoff:
                    pending["backend_observation_received"] = True
    if kind in ("brief", "backend_pending", "backend_result"):
        return {"kind": "private_brief", "text": state.get("background", ""),
                "data": {"host_state": state, "pending_handoffs": [p["receipt"] for p in backend.pending.values()
                                                                           if not p.get("backend_observation_received")]},
                **({"handoff_id": handoff} if handoff else {})}
    return {"kind": kind, "text": event["text"],
            **({"handoff_id": handoff} if handoff else {})}


def speech_script(text: str) -> tuple[str, str | None]:
    """Only strip recognized call syntax. Do not turn thinking/private output into TTS."""
    if "<think>" in text or "<|" in text:
        return "", "unsafe_markup_or_thinking"
    if "<tool_call>" in text and "</tool_call>" not in text:
        return "", "incomplete_tool_markup"
    return re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.S).strip(), None


async def replay(runtime: Runtime, scenarios: list[dict], strategy: str, *, limit=None,
                 input_mode="native_audio", repeat=1, max_events=24) -> list[dict]:
    module = load_strategies()
    if strategy not in module.STRATEGIES or input_mode not in module.INPUT_MODES:
        raise ValueError("unknown strategy or input mode")
    reports = []
    for repetition in range(repeat):
      for scenario in scenarios[:limit]:
        if hasattr(runtime, "reset_context"):
            await runtime.reset_context()
        backend, history, transcript, state = Backend(), [], [], {}
        report = {"scenario_id": scenario["id"], "strategy": strategy, "input_mode": input_mode,
                  "repeat": repetition, "audio_condition": scenario.get("audio_condition", "clean"), "steps": []}
        for index, point in enumerate(scenario["steps"]):
            runtime.current_audio_data = []
            step = {"index": index, "step_id": point["id"], "events": point["events"],
                    "expected": point["expect"], "call_receipts": [], "caller_audio": []}
            started = time.perf_counter()
            mediated_stt_s = 0.0
            failure = None
            for event in point["events"]:
                if event["kind"] == "caller":
                    prepared_key = digest({"text": event["text"], "condition": scenario.get("audio_condition", "clean")})
                    prepared_caller = getattr(runtime, "prepared_callers", {}).get(prepared_key)
                    audio = prepared_caller["synthesis"] if prepared_caller else await runtime.tts(event["text"], "female")
                    record = {"synthesis": audio, "fixture_text": event["text"]}
                    step["caller_audio"].append(record)
                    if audio["status"] != "ok":
                        failure = "caller_audio_unavailable"
                        break
                    path = Path(audio["audio_path"])
                    if prepared_caller:
                        path = Path(prepared_caller["delivered_audio_path"])
                        if "preprocessing" in prepared_caller:
                            record["preprocessing"] = prepared_caller["preprocessing"]
                    elif scenario.get("audio_condition") == "pstn_8khz":
                        path, record["preprocessing"] = narrowband(path, runtime.artifacts)
                    asr = prepared_caller["stt"] if prepared_caller else await runtime.stt(path)
                    record.update(delivered_audio_path=str(path), stt=asr)
                    # Native and oracle arms run ASR for host capture/measurement only.
                    if input_mode == "lfm_stt":
                        mediated_stt_s += asr["elapsed_s"]
                    if asr["status"] != "ok" or not asr["predicted_text"]:
                        failure = "caller_transcription_unavailable"
                        break
                    history.append({"kind": "caller", "audio": audio_part(path)["input_audio"],
                                     "oracle_text": event["text"], "lfm_stt": asr["predicted_text"]})
                    runtime.current_audio_data.append(history[-1]["audio"]["data"])
                    transcript.append({"role": "caller", "text": asr["predicted_text"], "step": point["id"],
                                       "audio_path": str(path), "source": "lfm_stt_host_capture"})
                else:
                    # Freeze snapshots so subsequent host events cannot mutate earlier context.
                    canonical = host_event(event, state, backend)
                    history.append(json.loads(json.dumps(canonical)))
            step["input_preparation_s"] = time.perf_counter() - started
            if failure:
                step["status"] = failure
                step["routing_pass"] = False
                report["steps"].append(step)
                runtime.artifacts.event({"type": "step", "scenario": scenario["id"], **step})
                break
            if backend.pending and (not history or history[-1]["kind"] != "private_brief"):
                # Pin current host state before bounded reconstruction can drop receipts.
                snapshot = host_event({"kind": "brief", "text": state.get("background", "")}, state, backend)
                history.append(json.loads(json.dumps(snapshot)))
            response_started = time.perf_counter()
            fragment = module.build(strategy, history, input_mode=input_mode, max_events=max_events,
                                    instructions="When speaking, be brief. If delegating, give a short holding acknowledgment when possible. Private updates and instruction changes alone are not requests to speak." + ("\n" + runtime.policy_extra if getattr(runtime, "policy_extra", "") else ""))
            reply = await runtime.chat(fragment["messages"], fragment=fragment)
            step.update(response=reply, model_visible_request_sha256=digest(fragment),
                        context_usage=(reply.get("body") or {}).get("usage"),
                        decision_s=time.perf_counter() - response_started + mediated_stt_s)
            calls = reply["calls"]
            step["routing_pass"] = (reply["status"] == "ok" and all(c["valid"] and c.get("dispatchable", True) for c in calls)
                                    and len(calls) == (1 if point["expect"]["delegate"] else 0))
            # Host receives all observed calls before synthesis. No generated task is used.
            for number, call in enumerate(calls):
                call["host_received_after_response_start_s"] = time.perf_counter() - response_started
                if not call["valid"] or not call.get("dispatchable", True):
                    runtime.artifacts.event({"type": "invalid_tool_call", "call": call, "step": point["id"]})
                    continue
                unique_id = f"r{repetition}-{scenario['id']}-{point['id']}-{number}-{call['id']}"
                accepted = backend.accept(call, transcript, index, point["id"])
                history.extend([{"kind": "delegate_call", "call_id": unique_id, "arguments": {}},
                                {"kind": "delegate_result", "call_id": unique_id, "data": accepted}])
                receipt = {"call": call, "output": accepted, "host_transcript": json.loads(json.dumps(transcript))}
                step["call_receipts"].append(receipt)
                runtime.artifacts.event({"type": "tool_output", "scenario": scenario["id"], **receipt})
            spoken, suppressed = speech_script(reply["predicted_text"])
            step.update(raw_predicted_text=reply["predicted_text"], synthesis_script=spoken,
                        host_suppression=suppressed, semantic_verdict="unresolved_requires_audio_and_semantic_review")
            step["silence_text_pass"] = not bool(reply["predicted_text"].strip()) if point["expect"]["silence"] else None
            step["holding_ack_missing"] = bool(point["expect"]["delegate"] and not spoken)
            if spoken:
                deferred = getattr(runtime, "defer_synthesis", False)
                synthesis = {"status": "deferred", "text": spoken} if deferred else await runtime.tts(spoken, "male")
                step.update(assistant_synthesis=synthesis, speech_mode="host_emulated_text_then_same_model_tts",
                            speech_delivery="artifact_only_no_realtime_playout", independently_recognized_wav_text=None)
                if synthesis["status"] == "ok" or (deferred and reply.get("native_audio_path")):
                    history.append({"kind": "assistant", "text": spoken})
                    transcript.append({"role": "assistant", "text": spoken, "step": point["id"],
                                       "source": "synthesis_script_not_independently_recognized"})
                    if not deferred:
                        step["first_audio_artifact_s"] = step["decision_s"] + synthesis.get("first_body_s", synthesis["elapsed_s"])
                        step["complete_audio_artifact_s"] = step["decision_s"] + synthesis["elapsed_s"]
            step.update(status=reply["status"], elapsed_s=time.perf_counter() - started)
            runtime.artifacts.event({"type": "step", "scenario": scenario["id"], "strategy": strategy,
                                     "input_mode": input_mode, "repeat": repetition, **step})
            report["steps"].append(step)
            if reply["status"] != "ok":
                break
        # Keep the predeclared denominator even when an adapter failure prevents
        # later turns. These are not observations or successful silent turns.
        for index in range(len(report["steps"]), len(scenario["steps"])):
            point = scenario["steps"][index]
            skipped = {"index": index, "step_id": point["id"], "events": point["events"],
                       "expected": point["expect"], "status": "not_run_after_prior_failure",
                       "routing_pass": False, "semantic_verdict": "unsupported_not_run",
                       "caller_audio": [], "call_receipts": []}
            report["steps"].append(skipped)
            runtime.artifacts.event({"type": "step", "scenario": scenario["id"], **skipped})
        reports.append(report)
    return reports


def summarize(reports: list[dict]) -> dict:
    points = [step for report in reports for step in report["steps"]]
    times = sorted(step["decision_s"] for step in points if "decision_s" in step)
    not_run = sum(p.get("status") == "not_run_after_prior_failure" for p in points)
    return {"scenarios_observed": len(reports), "points_recorded": len(points),
            "points_observed": len(points) - not_run, "points_not_run": not_run,
            "routing_pass": sum(bool(p.get("routing_pass")) for p in points),
            "holding_ack_missing": sum(bool(p.get("holding_ack_missing")) for p in points),
            "semantic_verdict": "unresolved_requires_audio_and_semantic_review",
            "timing_clock": "complete response boundary (HTTP result or realtime response.done); not first token or audible playout",
            "decision_s": {"first_observed_not_proven_cold": next((p["decision_s"] for p in points if "decision_s" in p), None),
                           "p50": times[len(times)//2], "p95": times[math.ceil(len(times)*.95)-1],
                           "max": times[-1]} if times else None,
            "targets": {"plan_primary_p95_s": 2, "scenario_aspirational_audible_p95_s": 1.5},
            "reports": reports}


async def main(args):
    artifacts = Artifacts(Path(args.artifacts).resolve())
    manifest = ROOT / "artifacts" / "model-manifest.json"
    props = ROOT / "artifacts" / "props.json"
    fingerprint = args.fingerprint or (digest({"manifest": json.loads(manifest.read_text()),
        "runtime_props": json.loads(props.read_text())}) if manifest.exists() and props.exists() else "unverified")
    artifacts.save("provenance.json", {"base_url": args.base_url, "fingerprint": fingerprint,
                                      "sampling": {"temperature": 0, "seed": 0, "max_tokens": 512},
                                      "tts_sampling": {"seed": 0, "n_predict": 256, "other": "pinned runtime defaults"},
                                      "input_mode": args.input_mode, "strategy": args.strategy,
                                      "corpus_sha256": hashlib.sha256(Path(args.scenarios).read_bytes()).hexdigest()
                                          if args.command == "replay" else None,
                                      "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    runtime = Runtime(args.base_url, artifacts, timeout=args.timeout, fingerprint=fingerprint)
    runtime.text_only_output = args.text_only_output
    try:
        if args.command == "probe":
            result = {"health": await runtime.request("/health"), "props": await runtime.request("/props"),
                      "models": await runtime.request("/v1/models")}
            if result["health"]["status"] == "ok":
                result["native_chat"] = await runtime.chat([
                    {"role": "system", "content": "Respond with interleaved text and audio."},
                    {"role": "user", "content": "Say hello briefly."}], native_audio=True)
                result["female_tts"] = await runtime.tts("Hello, can you hear me?", "female")
                result["male_tts"] = await runtime.tts("Yes, I can hear you.", "male")
                result["native_realtime"] = await runtime.realtime_probe()
        elif args.command == "tts":
            result = await runtime.tts(args.text, args.voice)
        elif args.command == "stt":
            result = await runtime.stt(Path(args.wav))
        else:
            corpus = json.loads(Path(args.scenarios).read_text())
            scenarios = corpus if isinstance(corpus, list) else corpus["scenarios"]
            if args.scenario:
                selected = set(args.scenario)
                missing = selected - {s["id"] for s in scenarios}
                if missing:
                    raise ValueError(f"unknown scenarios: {sorted(missing)}")
                scenarios = [s for s in scenarios if s["id"] in selected]
            if args.repeat < 1 or (args.limit is not None and args.limit < 1):
                raise ValueError("repeat and limit must be positive")
            result = summarize(await replay(runtime, scenarios, args.strategy, limit=args.limit,
                                            input_mode=args.input_mode, repeat=args.repeat, max_events=args.max_events))
        summary_path = f"summary-{artifacts.run_id}.json"
        artifacts.save(summary_path, result)
        artifacts.save("summary.json", result)
        print(json.dumps({"summary": str(artifacts.path / summary_path),
                          "status": result.get("status", "recorded") if isinstance(result, dict) else "recorded"}))
    finally:
        await runtime.close()


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--base-url", default="http://127.0.0.1:18391")
    cli.add_argument("--artifacts", default=str(ROOT / "artifacts" / "harness"))
    cli.add_argument("--fingerprint", help="Pinned model+runtime fingerprint; required for safe cache reuse")
    cli.add_argument("--timeout", type=float, default=180)
    cli.add_argument("--limit", type=int)
    cli.add_argument("--strategy", default="user_envelope")
    cli.add_argument("--input-mode", choices=("native_audio", "oracle_text", "lfm_stt"), default="native_audio")
    cli.add_argument("--scenario", action="append", help="Scenario ID, repeat this option to select several")
    cli.add_argument("--repeat", type=int, default=1)
    cli.add_argument("--max-events", type=int, default=24)
    cli.add_argument("--text-only-output", action="store_true",
                     help="Host-emulated HTTP text output: suppress audio/mixed mode tokens; not native speech")
    sub = cli.add_subparsers(dest="command", required=True)
    sub.add_parser("probe")
    tts = sub.add_parser("tts")
    tts.add_argument("text")
    tts.add_argument("--voice", choices=("male", "female"), default="female")
    stt = sub.add_parser("stt")
    stt.add_argument("wav")
    replay_cli = sub.add_parser("replay")
    replay_cli.add_argument("--scenarios", default=str(ROOT / "scenarios.json"))
    return cli


if __name__ == "__main__":
    asyncio.run(main(parser().parse_args()))
