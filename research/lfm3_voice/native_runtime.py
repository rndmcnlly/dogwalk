"""Research adapter: real realtime generation, staged male TTS via harness.Runtime.

No speech-mode logit suppression. Persistent arms send only newly appended
events. Reconstruction uses a fresh connection, replays caller audio, and labels
actual historical assistant speech as host history because realtime cannot
insert assistant-role items. That role emulation is explicitly recorded.
"""
import asyncio
import base64
import json
from pathlib import Path
import time

from harness import Runtime, parse_calls
from live_probe import wav_to_pcm16, save_wav, event_metadata


class NativeRuntime(Runtime):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ws = None
        self.previous = []
        self.system = None
        self.reconstruct = False
        self.last_text = ""
        self.call_ids = {}
        self.events = []
        self.sent_audio_seconds = 0.0
        self.generated_tokens = 0

    async def reset_context(self):
        if self.ws is not None:
            await self.ws.close()
        self.ws, self.previous, self.system = None, [], None
        self.last_text, self.call_ids = "", {}
        self.sent_audio_seconds, self.generated_tokens = 0.0, 0

    async def close(self):
        await self.reset_context()
        await super().close()

    async def request(self, route, body=None, *, label="request"):
        if self.ws is not None and route in {"/tts", "/v1/chat/completions", "/completion"}:
            raise RuntimeError("One-slot server: prefreeze caller TTS/ASR and close realtime before staged TTS")
        return await super().request(route, body, label=label)

    async def send(self, event):
        self.events.append({"direction": "send", "at": time.perf_counter(), "event": event_metadata(event)})
        await self.ws.send(json.dumps(event))

    async def receive(self):
        event = json.loads(await self.ws.recv())
        self.events.append({"direction": "receive", "at": time.perf_counter(), "event": event_metadata(event)})
        if event.get("type") == "error":
            raise ValueError(json.dumps(event))
        return event

    async def until(self, kind):
        while True:
            event = await self.receive()
            if event.get("type") == kind:
                return event

    async def user_text(self, text):
        await self.send({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": text}]}})
        await self.until("conversation.item.done")

    async def open_session(self, system, tools):
        import websockets
        self.ws = await websockets.connect(self.base_url.replace("http", "ws", 1) + "/v1/realtime",
                                           max_size=32 * 1024 * 1024, close_timeout=2)
        await self.until("session.created")
        await self.send({"type": "session.update", "session": {
            "instructions": system, "tools": [dict(tool["function"], type="function") for tool in tools],
            "tool_choice": "auto", "max_output_tokens": 512}})
        configured = await self.until("session.updated")
        self.system = system
        return configured

    async def input_message(self, message, *, replay=False):
        role = message["role"]
        if role == "assistant":
            if message.get("tool_calls"):
                # This was emitted by this connection, not inserted history.
                if replay:
                    raise ValueError("Native tool calls cannot be forged into a fresh realtime session")
                return
            text = message.get("content") or ""
            if not replay and text == self.last_text:
                return
            await self.user_text("HOST_HISTORY " + json.dumps({"kind": "assistant", "text": text,
                                  "historical_not_a_new_caller_request": True}))
            return
        if role == "tool":
            native_id = next((actual for actual in self.call_ids if message["tool_call_id"].endswith(actual)), None)
            if native_id is None:
                raise ValueError("Tool receipt has no observed call on this connection")
            await self.send({"type": "conversation.item.create", "item": {"type": "function_call_output",
                             "call_id": native_id, "output": message["content"]}})
            await self.until("conversation.item.done")
            del self.call_ids[native_id]
            return
        if role != "user":
            raise ValueError(f"Unsupported realtime replay role {role}")
        content = message.get("content", "")
        if isinstance(content, str):
            await self.user_text(content)
            return
        for part in content:
            if part["type"] == "text":
                await self.user_text(part["text"])
            elif part["type"] == "input_audio":
                audio = part["input_audio"]
                if "data" not in audio:
                    raise ValueError("Native adapter requires local base64 WAV, not remote URLs")
                temporary = self.artifacts.path / "input-current.wav"
                temporary.write_bytes(base64.b64decode(audio["data"]))
                pcm, _ = wav_to_pcm16(temporary)
                temporary.unlink()
                self.sent_audio_seconds += len(pcm) / 32000
                if replay and audio["data"] not in getattr(self, "current_audio_data", []):
                    await self.user_text("HOST_HISTORY: the following is historical caller audio, not a new work request.")
                for offset in range(0, len(pcm), 6400):
                    await self.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[offset:offset+6400]).decode()})
                await self.send({"type": "input_audio_buffer.commit"})
                await self.until("input_audio_buffer.committed")
            else:
                raise ValueError(f"Unsupported part {part['type']}")

    async def chat(self, messages, *, tools=False, native_audio=False, fragment=None):
        # ASR remains the official separate HTTP task, never realtime generation.
        if messages[0].get("content") == "Perform ASR in English.":
            return await super().chat(messages, tools=tools, fragment=fragment)
        prefix = self.artifacts.prefix("native-decision")
        self.artifacts.save(prefix + "-request.json", fragment or {"messages": messages})
        self.events = []
        start = time.perf_counter()
        result = {"status": "error", "prefix": prefix, "message": {}, "calls": [], "predicted_text": "",
                  "output_transport": "native_realtime_auto_then_optional_staged_tts",
                  "sampling": "server defaults; realtime has no request seed/temperature controls"}
        async with self.lock:
          try:
            async with asyncio.timeout(self.timeout):
                system = messages[0]["content"]
                append_only = messages[:len(self.previous)] == self.previous
                new_connection = self.reconstruct or not append_only or self.system != system or self.ws is None
                if new_connection:
                    await self.reset_context()
                    result["configured_session"] = await self.open_session(system, (fragment or {}).get("tools", []))
                new_messages = messages[1:] if new_connection else messages[len(self.previous):]
                if not new_messages:
                    await self.user_text('HOST_CONTROL {"kind":"observation_only","speech_requested":false}')
                    result["host_emulated_response_trigger"] = "explicit silent observation boundary"
                for index, message in enumerate(new_messages):
                    await self.input_message(message, replay=new_connection and index < len(new_messages) - 1)
                self.previous = json.loads(json.dumps(messages))
                result["input_ready_s"] = time.perf_counter() - start
                response_start = time.perf_counter()
                await self.send({"type": "response.create"})
                pcm, text = bytearray(), []
                while True:
                    event = await self.receive()
                    kind = event.get("type")
                    if kind == "response.output_text.delta":
                        result.setdefault("first_text_s", time.perf_counter() - start)
                        text.append(event["delta"])
                    elif kind == "response.output_audio.delta":
                        result.setdefault("first_audio_s", time.perf_counter() - start)
                        pcm.extend(base64.b64decode(event["delta"]))
                    elif kind == "response.done":
                        response = event["response"]
                        result["native_response"] = response
                        break
                output = response.get("output", [])
                self.generated_tokens += response.get("usage", {}).get("output_tokens", 0)
                calls = [{"id": item.get("call_id"), "function": {"name": item.get("name"),
                         "arguments": item.get("arguments")}} for item in output if item.get("type") == "function_call"]
                message = {"role": "assistant", "content": "".join(text), "tool_calls": calls}
                result.update(status="ok" if response.get("status") == "completed" else "incomplete",
                              message=message, predicted_text=message["content"], calls=parse_calls(message),
                              body={"usage": response.get("usage", {})}, response_generation_s=time.perf_counter()-response_start)
                result["native_context_observations"] = {
                    "caller_audio_seconds_sent_on_connection": self.sent_audio_seconds,
                    "generated_tokens_on_connection": self.generated_tokens,
                    "builder_messages": len(messages),
                    "native_prompt_tokens": None,
                    "limitation": "Realtime does not report retained prompt/audio token occupancy; output tokens include speech generation."}
                self.last_text = message["content"]
                self.call_ids.update({call["id"]: call for call in result["calls"] if call.get("id")})
                if pcm:
                    audio_path = self.artifacts.path / (prefix + "-native.wav")
                    save_wav(audio_path, pcm)
                    result.update(native_audio_path=str(audio_path), native_audio_duration_s=len(pcm)/48000)
          except Exception as exc:
            result.update(status="adapter_error", error=f"{type(exc).__name__}: {exc}")
            await self.reset_context()
        result["elapsed_s"] = time.perf_counter() - start
        self.artifacts.save(prefix + "-events.json", self.events)
        self.artifacts.save(prefix + "-parsed.json", result)
        return result
