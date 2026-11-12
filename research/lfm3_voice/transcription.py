# /// script
# requires-python = ">=3.10"
# dependencies = ["parakeet-mlx==0.7.0"]
# ///
"""Independent ASR of rendered speech, with audio-relative timestamps.

Run only after the generation runtime releases the GPU:
    HF_HUB_OFFLINE=1 uv run --offline --no-project --script \
        research/lfm3_voice/transcription.py speech.wav -o speech.asr.json

Imports and --help do not import MLX or load a model. Word boundaries are
reconstructed from subword character spans, not forced onto the requested text.
These times describe the supplied waveform, not model thought/compute time.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import glob
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import re
import sys
import tempfile
from typing import Iterable


DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
SCHEMA_VERSION = 1
WORD = re.compile(r"\w+(?:['’]\w+)*", re.UNICODE)


def word_timestamps(sentences: list[dict]) -> list[dict]:
    """Intersect lexical spans with tokens; preserve contractions and raw tokens.

    A token covering multiple words supplies the same bounds to each: there is
    no justified within-token interpolation. Punctuation-only tokens supply no
    word endpoint. Hyphenated compounds are separate lexical words.
    """
    words = []
    for sentence_index, sentence in enumerate(sentences):
        pieces = []
        spans = []
        offset = 0
        for token in sentence["tokens"]:
            text = token["text"]
            pieces.append(text)
            spans.append((offset, offset + len(text), token))
            offset += len(text)
        for match in WORD.finditer("".join(pieces)):
            contributing = [
                token for start, end, token in spans
                if start < match.end() and end > match.start()
                and re.search(r"\w", token["text"])
            ]
            if contributing:
                words.append({
                    "text": match.group(),
                    "start": min(t["start"] for t in contributing),
                    "end": max(t["end"] for t in contributing),
                    "sentence_index": sentence_index,
                })
    return words


def validate_chunk_duration(chunk_duration: float) -> None:
    if not math.isfinite(chunk_duration) or chunk_duration <= 15:
        raise ValueError("chunk_duration must be finite and exceed the 15-second chunk overlap")


def load_recognizer(model: str):
    """The only model-loading entry point; never called during module import."""
    from parakeet_mlx import from_pretrained
    return from_pretrained(model)


def audio_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def recognition_metadata(path: Path, model: str, chunk_duration: float) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "audio_path": str(path),
        "audio_sha256": audio_sha256(path),
        "model": model,
        "parakeet_mlx_version": version("parakeet-mlx"),
        "chunk_duration": chunk_duration,
    }


def _transcribe_with(recognizer, path: Path, metadata: dict) -> dict:
    chunk_duration = metadata["chunk_duration"]
    result = asdict(recognizer.transcribe(str(path), chunk_duration=chunk_duration))
    result["words"] = word_timestamps(result["sentences"])
    result["metadata"] = {
        **metadata,
        "timestamp_unit": "seconds",
        "timestamp_origin": "input_audio_start",
        "word_alignment": "lexical_character_spans_intersecting_asr_tokens",
        "chunk_duration": chunk_duration,
        "caveats": [
            "Audio/playback time, not generation wall time or internal thought time.",
            "Independent ASR can miss or substitute speech and normalize numbers or casing.",
            "Sentence endpoints may include punctuation and pauses; word times are approximate.",
            "No forced alignment to a prompt; raw token confidence is not calibrated.",
        ],
    }
    return result


def transcribe(path: str | Path, *, model: str = DEFAULT_MODEL,
               chunk_duration: float = 300.0) -> dict:
    """Transcribe one file, explicitly loading one model on invocation.

    Set HF_HUB_OFFLINE=1 to prohibit downloads, or pass a local snapshot as model.
    Callers own GPU serialization. For multiple files use transcribe_batch.
    """
    validate_chunk_duration(chunk_duration)
    path = Path(path).resolve(strict=True)
    metadata = recognition_metadata(path, model, chunk_duration)
    return _transcribe_with(load_recognizer(model), path, metadata)


def output_path(audio: Path, output_dir: Path) -> Path:
    """Stable names distinguish equal stems in different directories."""
    identity = hashlib.sha256(str(audio.resolve()).encode()).hexdigest()[:16]
    return output_dir / f"{audio.stem}.{identity}.asr.json"


def save_json(path: Path, value: dict) -> None:
    """Replace atomically, preserving an older result if writing is interrupted."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def compatible_output(path: Path, metadata: dict) -> bool:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        return (isinstance(saved, dict)
                and isinstance(saved.get("text"), str)
                and isinstance(saved.get("sentences"), list)
                and isinstance(saved.get("words"), list)
                and isinstance(saved.get("metadata"), dict)
                and all(saved["metadata"].get(k) == v for k, v in metadata.items()))
    except (OSError, ValueError):
        return False


def manifest_paths(manifest: Path) -> list[Path]:
    """Read a JSON list, or {"files": [...]}, relative to the manifest directory.

    Entries are path strings or objects with audio/audio_path/wav_path/wav/path.
    Other fields (especially requested TTS text) are deliberately never used.
    """
    manifest = manifest.resolve(strict=True)
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    if isinstance(entries, dict):
        entries = entries.get("files")
    if not isinstance(entries, list):
        raise ValueError("manifest must be a JSON list or an object with a files list")
    paths = []
    for index, entry in enumerate(entries):
        if isinstance(entry, dict):
            candidates = [entry[k] for k in ("audio", "audio_path", "wav_path", "wav", "path")
                          if k in entry]
            if len(candidates) != 1:
                raise ValueError(f"manifest entry {index} needs exactly one audio path field")
            entry = candidates[0]
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"manifest entry {index} needs a nonempty path string")
        path = Path(entry).expanduser()
        paths.append((manifest.parent / path).resolve())
    return paths


def transcribe_batch(paths: Iterable[str | Path], output_dir: str | Path, *,
                     model: str = DEFAULT_MODEL, chunk_duration: float = 300.0) -> dict:
    """Serial, incremental ASR with one lazy model load, resume, and error summary.

    Only one waveform is decoded at a time. Compatibility checks stream file
    hashes and never load waveform arrays. A failed model load is not retried
    for every file. summary.json is checkpointed after every input.
    """
    validate_chunk_duration(chunk_duration)
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    recognizer = None
    load_error = None
    seen = set()
    summary = {"completed": 0, "skipped": 0, "errors": 0, "duplicates": 0, "files": []}
    for source in paths:
        path = Path(source).expanduser().resolve()
        if path in seen:
            summary["duplicates"] += 1
            continue
        seen.add(path)
        destination = output_path(path, output_dir)
        record = {"audio_path": str(path), "output": str(destination)}
        try:
            metadata = recognition_metadata(path, model, chunk_duration)
            if compatible_output(destination, metadata):
                record["status"] = "skipped"
            else:
                if load_error is not None:
                    raise RuntimeError(load_error)
                if recognizer is None:
                    try:
                        recognizer = load_recognizer(model)
                    except Exception as exc:
                        load_error = f"recognizer load failed: {type(exc).__name__}: {exc}"
                        raise RuntimeError(load_error) from exc
                result = _transcribe_with(recognizer, path, metadata)
                save_json(destination, result)
                record["status"] = "completed"
            summary[record["status"]] += 1
        except Exception as exc:
            record.update(status="error", error=f"{type(exc).__name__}: {exc}")
            summary["errors"] += 1
        summary["files"].append(record)
        save_json(output_dir / "summary.json", summary)
        print(f"{record['status']}: {path}", file=sys.stderr)
    save_json(output_dir / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, nargs="?")
    batch = parser.add_mutually_exclusive_group()
    batch.add_argument("--batch", type=Path, help="JSON manifest; relative paths use its directory")
    batch.add_argument("--glob", dest="pattern", help="Quoted WAV glob, relative to cwd; supports **")
    parser.add_argument("--output-dir", type=Path, help="Required for batch; writes results and summary.json")
    parser.add_argument("-o", "--output", type=Path, help="Default: JSON to stdout")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="HF ID or local snapshot directory")
    parser.add_argument("--chunk-duration", type=float, default=300.0)
    args = parser.parse_args()
    is_batch = args.batch is not None or args.pattern is not None
    if is_batch:
        if args.audio or args.output or args.output_dir is None:
            parser.error("batch requires --output-dir and cannot use positional audio or --output")
        try:
            paths = manifest_paths(args.batch) if args.batch else [
                Path(p) for p in sorted(glob.glob(args.pattern, recursive=True)) if Path(p).is_file()
            ]
            if not paths:
                parser.error("batch selected no audio files")
            summary = transcribe_batch(paths, args.output_dir, model=args.model,
                                       chunk_duration=args.chunk_duration)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        raise SystemExit(1 if summary["errors"] else 0)
    if args.audio is None or args.output_dir is not None:
        parser.error("provide an audio file, or --batch/--glob with --output-dir")
    result = transcribe(args.audio, model=args.model, chunk_duration=args.chunk_duration)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        save_json(args.output, result)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
