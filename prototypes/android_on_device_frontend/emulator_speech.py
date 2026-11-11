# /// script
# requires-python = ">=3.11"
# dependencies = ["grpcio==1.76.0", "grpcio-tools==1.76.0", "protobuf==6.33.0"]
# ///
"""Speak synthetic caller lines into the Android Emulator's microphone.

The phrase is synthesized on this Mac with `say` (synthetic speech, never a private
recording) and streamed in real time through the emulator's authenticated gRPC
`injectAudio`, so the app's on-device recognizer hears it like microphone input.
Requires an emulator started with `-grpc 8554 -grpc-use-token` and HOME pointing at
the toolchain's emulator-home (where the discovery file and token live).

uv run --script emulator_speech.py "what are the sessions doing"
"""

import importlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import wave

TOOLS = Path(os.environ["DOGWALK_ANDROID_TOOLS"])
SDK, SERIAL = TOOLS / "sdk", os.environ.get("ANDROID_SERIAL", "emulator-5580")
ADB = str(SDK / "platform-tools/adb")
GENERATED = Path(__file__).resolve().parent / ".tools" / "emulator-grpc"


def protocol():
    from grpc_tools import protoc
    import grpc_tools
    source = SDK / "emulator/lib/emulator_controller.proto"
    if not (GENERATED / "emulator_controller_pb2_grpc.py").exists():
        GENERATED.mkdir(parents=True, exist_ok=True)
        if protoc.main(["protoc", f"-I{source.parent}", f"-I{Path(grpc_tools.__file__).parent / '_proto'}",
                        f"--python_out={GENERATED}", f"--grpc_python_out={GENERATED}", str(source)]):
            raise SystemExit("could not compile the installed emulator protocol")
    sys.path.insert(0, str(GENERATED))
    return importlib.import_module("emulator_controller_pb2"), importlib.import_module("emulator_controller_pb2_grpc")


def wait_for_microphone(timeout=20.0):
    # Emulator injectAudio can crash natively without an open guest input stream.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        dump = subprocess.run([ADB, "-s", SERIAL, "shell", "dumpsys", "media.audio_flinger"],
                              capture_output=True, text=True, check=True).stdout.split("Historical Thread Log", 1)[0]
        for block in dump.split("Input thread ")[1:]:
            if "Standby: no" in block and re.search(r"\d+ Tracks of which [1-9]\d* are active", block):
                return
        time.sleep(0.2)
    raise SystemExit("no active guest microphone: is the app listening?")


def stub(pb_grpc):
    import grpc
    env = {**os.environ, "HOME": str(TOOLS / "emulator-home")}
    out = subprocess.run([ADB, "-s", SERIAL, "emu", "avd", "discoverypath"], capture_output=True, text=True, env=env, check=True).stdout
    path = next(Path(line.strip()) for line in out.splitlines() if line.strip().startswith("/"))
    info = {k.strip(): v.strip() for k, v in (line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)}
    channel = grpc.insecure_channel(f"127.0.0.1:{int(info['grpc.port'])}")  # loopback only
    grpc.channel_ready_future(channel).result(timeout=5)
    return pb_grpc.EmulatorControllerStub(channel), (("authorization", "Bearer " + info["grpc.token"]),)


def main():
    phrase = " ".join(sys.argv[1:]) or "hello"
    pb, pb_grpc = protocol()
    controller, metadata = stub(pb_grpc)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "caller.wav"
        subprocess.run(["say", "-o", str(wav), "--data-format=LEI16@16000", phrase], check=True)
        with wave.open(str(wav)) as w:
            rate, pcm = w.getframerate(), w.readframes(w.getnframes())
    pcm = b"\0" * (rate // 2 * 2) + pcm + b"\0" * (rate * 2)  # lead-in and trailing silence for endpointing
    fmt = pb.AudioFormat(samplingRate=rate, channels=pb.AudioFormat.Mono, format=pb.AudioFormat.AUD_FMT_S16,
                         mode=pb.AudioFormat.MODE_REAL_TIME)
    chunk = rate // 50 * 2

    def packets():
        start = time.monotonic()
        for offset in range(0, len(pcm), chunk):
            wait = start + offset / (rate * 2) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            yield pb.AudioPacket(format=fmt, audio=pcm[offset:offset + chunk])

    wait_for_microphone()
    controller.injectAudio(packets(), metadata=metadata, timeout=60)
    print(f"injected {len(pcm) / (rate * 2):.1f} s of synthetic speech: {phrase!r}")


if __name__ == "__main__":
    main()
