# /// script
# requires-python = ">=3.11"
# dependencies = ["grpcio==1.76.0", "grpcio-tools==1.76.0", "protobuf==6.33.0"]
# ///
"""Direct WAV <-> emulator gRPC audio, without host microphone/speaker routing.

Run emulator.py start --headless first (includes authenticated localhost gRPC).
  uv run --script audio_bridge.py status
  uv run --script audio_bridge.py inject input.wav
  uv run --script audio_bridge.py capture .tools/output.wav --seconds 8
  uv run --script audio_bridge.py exchange input.wav .tools/output.wav --seconds 12

Input must be uncompressed PCM WAV, mono/stereo, unsigned 8-bit or signed
little-endian 16-bit, 8–48 kHz. No implicit resampling or lossy conversion.
For other sources, convert explicitly, for example:
  ffmpeg -i source.mp3 -ar 16000 -ac 1 -c:a pcm_s16le .tools/input.wav
Capture is 48 kHz signed 16-bit stereo. It captures guest output, NOT injected
microphone audio unless the guest explicitly plays it back. Silence is reported
honestly; --require-signal fails when no nonzero samples were captured.
All operations have deadlines; input and capture are limited to 120 seconds.
Start guest recording before injecting. Emulator 37.2.13 can crash natively if
injectAudio runs without an open guest input voice. We check AudioFlinger first,
but cannot eliminate the upstream race if the guest closes it during the RPC.
"""

import argparse
from array import array
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / ".tools"
SDK = TOOLS / "sdk"
SERIAL = "emulator-5580"
MAX_SECONDS = 120


def read_wav(path):
    """Validate before reading potentially large files. Preserve native PCM."""
    with wave.open(str(path), "rb") as wav:
        channels, width, rate, frames = (wav.getnchannels(), wav.getsampwidth(),
                                         wav.getframerate(), wav.getnframes())
        if wav.getcomptype() != "NONE" or channels not in (1, 2) or width not in (1, 2):
            raise ValueError("Input must be uncompressed mono/stereo 8-bit or 16-bit PCM WAV")
        if not 8000 <= rate <= 48000:
            raise ValueError("WAV sample rate must be between 8000 and 48000 Hz")
        if not 0 < frames <= MAX_SECONDS * rate:
            raise ValueError("WAV must contain more than zero and at most 120 seconds")
        pcm = wav.readframes(frames)
        if len(pcm) != frames * channels * width:
            raise ValueError("Truncated WAV data")
    return pcm, channels, width, rate


def signal_stats(pcm, width=2):
    if width == 2:
        if len(pcm) % 2:
            raise ValueError("Partial 16-bit sample")
        values = array("h", pcm)
        if sys.byteorder != "little":
            values.byteswap()
        scale = 32768
    else:
        values = [value - 128 for value in pcm]
        scale = 128
    return {"samples": len(values),
            "nonzero_samples": sum(value != 0 for value in values),
            "peak": max((abs(value) for value in values), default=0) / scale,
            "rms": math.sqrt(sum(value * value for value in values) / len(values)) / scale
            if values else 0.0}


def protocol():
    """Compile the installed SDK's complete protocol, not guessed wire fields."""
    import grpc_tools
    from grpc_tools import protoc
    source = SDK / "emulator/lib/emulator_controller.proto"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    generated = TOOLS / "audio-grpc" / digest
    generated.mkdir(parents=True, exist_ok=True)
    if not (generated / "emulator_controller_pb2_grpc.py").exists():
        result = protoc.main(["grpc_tools.protoc", f"-I{source.parent}",
                              f"-I{Path(grpc_tools.__file__).parent / '_proto'}",
                              f"--python_out={generated}", f"--grpc_python_out={generated}",
                              str(source)])
        if result:
            raise RuntimeError("Could not compile installed emulator protocol")
    sys.path.insert(0, str(generated))
    return (importlib.import_module("emulator_controller_pb2"),
            importlib.import_module("emulator_controller_pb2_grpc"))


def microphone_active(dump):
    # Historical thread records are not evidence of a currently open microphone.
    current = dump.split("Historical Thread Log", 1)[0]
    for block in current.split("Input thread ")[1:]:
        if "Standby: no" in block and re.search(r"\d+ Tracks of which [1-9]\d* are active", block):
            return True
    return False


def wait_for_microphone(timeout):
    deadline = time.monotonic() + min(timeout, 5)
    while True:
        result = subprocess.run([str(SDK / "platform-tools/adb"), "-s", SERIAL,
                                 "shell", "dumpsys", "media.audio_flinger"],
                                check=True, capture_output=True, text=True,
                                timeout=max(0.1, deadline - time.monotonic()))
        if microphone_active(result.stdout):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("No active guest microphone: start recording before injection (avoids emulator native crash)")
        time.sleep(0.1)


def connection(pb_grpc):
    import grpc
    # Ask the selected emulator, rather than selecting an unrelated PID file.
    result = subprocess.run([str(SDK / "platform-tools/adb"), "-s", SERIAL,
                             "emu", "avd", "discoverypath"], check=True,
                            capture_output=True, text=True, timeout=10,
                            env={**os.environ, "HOME": str(TOOLS / "emulator-home")})
    paths = [Path(line.strip()) for line in result.stdout.splitlines()
             if line.strip().startswith("/")]
    if len(paths) != 1 or not paths[0].is_file():
        raise RuntimeError("No gRPC discovery file: restart with emulator.py start --headless")
    info = dict(line.split("=", 1) for line in paths[0].read_text().splitlines() if "=" in line)
    info = {key.strip(): value.strip() for key, value in info.items()}
    token = info.get("grpc.token")
    port = int(info.get("grpc.port", "0"))
    if not token or not 1 <= port <= 65535:
        raise RuntimeError("Authenticated gRPC unavailable: use emulator.py start --headless")
    # Never accept a discovery-provided remote host. Bearer travels only on loopback.
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        grpc.channel_ready_future(channel).result(timeout=5)
    except BaseException:
        channel.close()
        raise
    return channel, pb_grpc.EmulatorControllerStub(channel), (("authorization", "Bearer " + token),)


def inject(stub, pb, metadata, source, timeout):
    pcm, channels, width, rate = read_wav(source)
    fmt = pb.AudioFormat(samplingRate=rate,
                         channels=pb.AudioFormat.Mono if channels == 1 else pb.AudioFormat.Stereo,
                         format=pb.AudioFormat.AUD_FMT_U8 if width == 1 else pb.AudioFormat.AUD_FMT_S16,
                         mode=pb.AudioFormat.MODE_REAL_TIME)
    stride = channels * width
    chunk_bytes = (rate // 50) * stride  # 20ms, well below 300ms queue size

    def packets():
        start = time.monotonic()
        for offset in range(0, len(pcm), chunk_bytes):
            wait = start + offset / (rate * stride) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            yield pb.AudioPacket(format=fmt, audio=pcm[offset:offset + chunk_bytes])

    stub.injectAudio(packets(), metadata=metadata, timeout=timeout)
    return {"seconds": len(pcm) / (rate * stride), "rate": rate,
            "channels": channels, "bits": width * 8, **signal_stats(pcm, width)}


def capture(stub, pb, metadata, target, seconds, timeout, ready=None):
    import grpc
    rate, channels, width = 48000, 2, 2
    fmt = pb.AudioFormat(samplingRate=rate, channels=pb.AudioFormat.Stereo,
                         format=pb.AudioFormat.AUD_FMT_S16)
    stream = stub.streamAudio(fmt, metadata=metadata, timeout=min(seconds, timeout))
    chunks = []
    limit = int(seconds * rate) * channels * width
    total = 0
    if ready is not None:
        # RPC has been started. Do not wait for headers: an idle guest may not
        # send them until producing audio, which would deadlock an exchange.
        ready.set()
    try:
        for packet in stream:
            if packet.format != fmt:
                raise ValueError("Emulator returned an unexpected capture PCM format")
            if len(packet.audio) % (channels * width):
                raise ValueError("Emulator returned a partial PCM frame")
            chunk = packet.audio[:limit - total]
            chunks.append(chunk)
            total += len(chunk)
            if total >= limit:
                break
    except grpc.RpcError as error:
        if error.code() != grpc.StatusCode.DEADLINE_EXCEEDED:
            raise
        # Inactive guest audio is allowed to block forever; deadline bounds it.
    finally:
        stream.cancel()
    pcm = b"".join(chunks)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as wav:
        wav.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    return {"path": str(target), "seconds": len(pcm) / (rate * channels * width),
            "requested_seconds": seconds, **signal_stats(pcm)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["status", "inject", "capture", "exchange"])
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--timeout", type=float, default=125, help="RPC deadline in seconds, max 130")
    parser.add_argument("--require-signal", action="store_true")
    args = parser.parse_args()
    count = {"status": 0, "inject": 1, "capture": 1, "exchange": 2}[args.action]
    if len(args.paths) != count:
        parser.error(f"{args.action} requires {count} path(s)")
    if not 0 < args.seconds <= MAX_SECONDS or not 0 < args.timeout <= 130:
        parser.error("--seconds must be in (0, 120], --timeout in (0, 130]")
    if args.action in {"inject", "exchange"}:
        read_wav(args.paths[0])  # Fail before connecting or starting capture.
    pb, pb_grpc = protocol()
    if args.action in {"inject", "exchange"}:
        # Do subprocess work before creating gRPC's background threads.
        wait_for_microphone(args.timeout)
    channel, stub, metadata = connection(pb_grpc)
    try:
        if args.action == "status":
            from google.protobuf.empty_pb2 import Empty
            state = stub.getMicrophoneState(Empty(), metadata=metadata, timeout=5)
            result = {"authenticated_grpc": True, "host_microphone_enabled": state.realAudioEnabled}
        elif args.action == "inject":
            result = {"injected": inject(stub, pb, metadata, args.paths[0], args.timeout)}
        elif args.action == "capture":
            result = {"captured": capture(stub, pb, metadata, args.paths[0], args.seconds, args.timeout)}
        else:
            import threading
            ready = threading.Event()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(capture, stub, pb, metadata, args.paths[1],
                                         args.seconds, args.timeout, ready)
                if not ready.wait(min(5, args.timeout)):
                    raise RuntimeError("Capture did not become ready within five seconds")
                time.sleep(0.1)  # Give server streaming a small lead before input.
                result = {"injected": inject(stub, pb, metadata, args.paths[0], args.timeout),
                          "captured": future.result()}
        print(json.dumps(result, indent=2))
        if args.require_signal and "captured" in result and not result["captured"]["nonzero_samples"]:
            raise SystemExit("Captured WAV contains no nonzero signal")
    finally:
        channel.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Avoid dumping discovery credentials or gRPC debug metadata.
        import grpc
        if isinstance(error, grpc.RpcError):
            raise SystemExit(f"Audio RPC failed: {error.code().name}") from None
        raise SystemExit(f"{type(error).__name__}: {error}") from None
