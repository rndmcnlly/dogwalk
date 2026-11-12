#!/usr/bin/env python3
"""Render synthetic human fixtures, then independently recognize the actual WAVs.

Run from any directory:
    python3 prototypes/android_voice_probe/generate_voice_fixtures.py

Uses /Users/adam/Desktop/voice-doodle/.venv/bin/python for Breeze (MLX),
then `uvx parakeet-mlx` for recognition, in separate sequential processes.
Both batches hold .tools/media-gpu.lock with exclusive fcntl.flock, including
model loading. No concurrent GPU work. Never modify or install into voice-doodle.
Use --take take-002 --seed-offset 100 for an explicit new take, preserving failures.
To retry one phrase while retaining verified performances, add
--reuse-take take-003 --only long_reply (with a new --take name).
The manifest is published only after all five transcripts match ignoring case
and punctuation. Original renders, requests and ASR remain under ignored .tools.
For an isolated single phrase, use --fixture-id uptime --text 'Your question?'
--output-dir .tools/uptime-fixtures. This leaves the default manifest untouched.

Breeze code is Apache-2.0, but weights AND self-hosted outputs are restricted
to research/noncommercial use. Check upstream terms before production use:
https://github.com/breezeblue-ai/breeze-tts
The selected anchor is synthetic, not a recording of Adam or another user.
Identity continuity and acting quality remain listening judgments, not ASR claims.
"""

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
SOURCE = Path('/Users/adam/Desktop/voice-doodle')
PYTHON = SOURCE / '.venv/bin/python'
MODEL = 'mlx-community/Breeze-TTS-2-mlx-4bit'
ASR_MODEL = 'mlx-community/parakeet-tdt-0.6b-v3'
ASR_PACKAGE = 'parakeet-mlx==0.7.0'
LICENSE = 'Breeze weights and self-hosted outputs: research/noncommercial only; code: Apache-2.0.'
TEXTS = {
    'status': 'Probe status.',
    'echo': 'Please remember the blue umbrella.',
    'long_reply': 'Long reply.',
    'stop': 'Stop listening.',
    'intro': 'In this test, I am the simulated human user, voiced by Breeze. The other voice is the Android system.',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def words(text):
    return re.findall(r"[\w]+(?:['’][\w]+)*", text.lower())


def render(args, out):
    import importlib.metadata
    import mlx.core as mx
    from mlx_audio.tts import load
    import numpy as np
    from scipy.signal import resample_poly
    import soundfile as sf

    cast = json.loads((SOURCE / 'casting/cast.json').read_text())
    selected = cast['characters']['cone']
    original = SOURCE / selected['reference_audio']
    if digest(original) != selected['reference_sha256']:
        raise ValueError('Original approved anchor hash changed; refusing to recast')
    if args.text is not None:
        approved_path = ROOT / '.tools/voice-fixtures/manifest.json'
        approved = json.loads(approved_path.read_text())
        if (approved['voice']['reference_sha256'] != digest(original)
                or approved['voice']['reference_text'] != cast['reference_transcript']
                or approved['voice']['casting_record_sha256'] != digest(SOURCE / 'casting/cast.json')):
            raise ValueError('Custom fixture anchor differs from published approved provenance')
    reference = out / 'reference-cone-c.wav'
    shutil.copyfile(original, reference)
    voice = dict(
        name='Cone C: The mischievous scout', reference_path=str(reference),
        original_reference_path=str(original), reference_sha256=digest(reference),
        reference_text=cast['reference_transcript'], license=LICENSE,
        selection_authority={
            'identity': 'Adam selected Cone C on 2026-09-08, casting/cast.json and CASTING-NOTES.md',
            'role': 'Agent selected this previously approved synthetic identity as the simulated human stand-in; Adam did not choose this role.',
        },
        casting_record_path=str(SOURCE / 'casting/cast.json'),
        casting_record_sha256=digest(SOURCE / 'casting/cast.json'),
    )
    if args.text is not None:
        voice['selection_authority'] = approved['voice']['selection_authority']
        voice['approved_manifest_path'] = str(approved_path)
        voice['approved_manifest_sha256'] = digest(approved_path)
    model = load(MODEL)
    fixtures = []
    texts = {args.fixture_id: args.text} if args.text is not None else TEXTS
    for index, (identity, text) in enumerate(texts.items()):
        if args.only and identity not in args.only:
            previous = ROOT / '.tools/voice-fixtures' / args.reuse_take
            prior = json.loads((previous / 'render.json').read_text())
            if prior['voice']['reference_sha256'] != voice['reference_sha256']:
                raise ValueError('Cannot reuse takes from another anchor')
            retained = next(f for f in prior['fixtures'] if f['id'] == identity)
            if retained['text'] != text or digest(Path(retained['wav'])) != retained['sha256']:
                raise ValueError('Retained take text or audio changed')
            save(out / f'{identity}.generation.json', retained)
            fixtures.append(retained)
            continue
        direction = "Speak clearly and deliberately at a moderate conversational pace. Preserve the reference speaker's natural voice. Say the requested words exactly, without additions."
        if identity == 'long_reply':
            direction += " This is a two-word label. Stress the second syllable of reply, pronounced ruh-PLY. Join its initial R smoothly to its vowel."
        request = dict(text=text, ref_audio=str(reference),
                       ref_text=voice['reference_text'], cfg_scale=4,
                       instruct=direction,
                       seed=1700 + index + args.seed_offset,
                       max_tokens=500, voice='S0')
        save(out / f'{identity}.request.json', request)
        print(f'Rendering {identity}', flush=True)
        start = time.perf_counter()
        results = list(model.generate(**request))
        audio = np.concatenate([np.asarray(r.audio, dtype=np.float32).reshape(-1) for r in results])
        if not len(audio) or not np.isfinite(audio).all():
            raise ValueError(f'{identity}: empty or nonfinite waveform')
        sf.write(out / f'{identity}.raw.wav', audio, model.sample_rate, subtype='FLOAT')
        # Keep 100 ms before/160 ms after audible content; -50 dBFS threshold.
        active = np.flatnonzero(np.abs(audio) > 0.0031623)
        if not len(active):
            raise ValueError(f'{identity}: no above-threshold signal')
        first = max(0, int(active[0]) - round(.1 * model.sample_rate))
        last = min(len(audio), int(active[-1]) + round(.16 * model.sample_rate) + 1)
        trimmed = audio[first:last] * .8
        import math
        divisor = math.gcd(16000, model.sample_rate)
        converted = resample_poly(trimmed, 16000 // divisor, model.sample_rate // divisor)
        if np.abs(converted).max() >= 1:
            raise ValueError(f'{identity}: resampled output would clip')
        wav = out / f'{identity}.wav'
        sf.write(wav, converted, 16000, subtype='PCM_16')
        metadata = dict(
            id=identity, text=text, wav=str(wav), sha256=digest(wav),
            review_only=identity == 'intro', duration_seconds=len(converted) / 16000,
            sample_rate=16000, channels=1, subtype='PCM_16',
            generation=dict(request=request, wall_seconds=time.perf_counter() - start,
                            raw_wav=str(out / f'{identity}.raw.wav'),
                            raw_sha256=digest(out / f'{identity}.raw.wav'),
                            raw_sample_rate=model.sample_rate, raw_peak=float(np.abs(audio).max()),
                            possible_source_saturation=bool(np.abs(audio).max() >= 1),
                            token_count=sum(r.token_count for r in results),
                            possibly_truncated=any(r.token_count >= 500 for r in results),
                            trim_start_sample=first, trim_end_sample=last,
                            silence_threshold_dbfs=-50, leading_margin_seconds=.1,
                            trailing_margin_seconds=.16, gain=.8,
                            resampler='scipy.signal.resample_poly'),
        )
        save(out / f'{identity}.generation.json', metadata)
        fixtures.append(metadata)
        mx.clear_cache()
    save(out / 'render.json', dict(
        schema_version=1, engine='Breeze TTS 2 on MLX', model=MODEL, voice=voice,
        license=LICENSE, fixtures=fixtures,
        versions={name: importlib.metadata.version(name) for name in ('mlx', 'mlx-audio', 'scipy', 'soundfile')},
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--take', default='take-001')
    parser.add_argument('--seed-offset', type=int, default=0)
    parser.add_argument('--only', nargs='+', choices=TEXTS)
    parser.add_argument('--reuse-take')
    parser.add_argument('--fixture-id', help='ID for an isolated custom phrase')
    parser.add_argument('--text', help='Exact custom phrase, independently checked by ASR')
    parser.add_argument('--output-dir', type=Path, help='Custom fixture base, relative to this script directory')
    parser.add_argument('--render-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', args.take):
        parser.error('--take must be a simple directory name')
    if args.only and not args.reuse_take:
        parser.error('--only requires --reuse-take to supply the other fixtures')
    if args.reuse_take and not re.fullmatch(r'[a-zA-Z0-9_-]+', args.reuse_take):
        parser.error('--reuse-take must be a simple directory name')
    custom = args.text is not None
    if any((args.fixture_id is not None, custom, args.output_dir is not None)):
        if not (args.fixture_id and custom and args.output_dir):
            parser.error('--fixture-id, --text and --output-dir must be supplied together')
        if not args.text.strip() or not re.fullmatch(r'[a-zA-Z0-9_-]+', args.fixture_id):
            parser.error('Custom phrase must be nonempty and fixture ID must be a simple name')
        if args.only or args.reuse_take:
            parser.error('Custom phrases cannot be combined with default-fixture reuse')
    base = (ROOT / args.output_dir).resolve() if custom else ROOT / '.tools/voice-fixtures'
    if custom and (base == (ROOT / '.tools/voice-fixtures').resolve()
                   or (ROOT / '.tools/voice-fixtures').resolve() in base.parents):
        parser.error('Custom output must not overwrite the default fixtures')
    out = base / args.take
    if args.render_worker:
        render(args, out)
        return
    out.mkdir(parents=True, exist_ok=False)
    # All cooperating media helpers share this advisory GPU lock. Hold it across
    # both subprocesses, including model loading; never overlap MLX model batches.
    with (ROOT / '.tools/media-gpu.lock').open('a') as gpu_lock:
        fcntl.flock(gpu_lock, fcntl.LOCK_EX)
        worker = [str(PYTHON), str(Path(__file__).resolve()), '--render-worker',
                  '--take', args.take, '--seed-offset', str(args.seed_offset)]
        if custom:
            worker += ['--fixture-id', args.fixture_id, '--text', args.text,
                       '--output-dir', str(base)]
        if args.only:
            worker += ['--reuse-take', args.reuse_take, '--only', *args.only]
        subprocess.run(worker, check=True)
        # Breeze has exited, freeing its model before ASR loads. Only audio paths
        # enter ASR: never provide requested text or vocabulary hints.
        rendered = json.loads((out / 'render.json').read_text())
        subprocess.run(['uvx', '--from', ASR_PACKAGE, 'parakeet-mlx', '--output-dir', str(out / 'asr'),
                        '--output-format', 'json', '--model', ASR_MODEL,
                        *[f['wav'] for f in rendered['fixtures']]], check=True)
    manifest = json.loads((out / 'render.json').read_text())
    failures = []
    for fixture in manifest['fixtures']:
        wav = Path(fixture['wav'])
        if digest(wav) != fixture['sha256']:
            raise ValueError('Audio changed before ASR comparison')
        asr_path = out / 'asr' / f"{fixture['id']}.json"
        asr = json.loads(asr_path.read_text())
        heard = asr['text'].strip()
        matches = words(heard) == words(fixture['text'])
        fixture['asr'] = dict(model=ASR_MODEL, package=ASR_PACKAGE, json=str(asr_path),
                              sha256=digest(asr_path), audio_sha256=digest(wav),
                              transcript=heard, words_match=matches,
                              comparison='case/punctuation-insensitive lexical equality; not calibrated accuracy')
        if not matches or fixture['generation']['possibly_truncated']:
            failures.append(fixture['id'])
        print(f"{fixture['id']}: {fixture['duration_seconds']:.3f}s, match={matches}, heard={heard!r}")
    manifest['qc'] = dict(passed=not failures, failed_fixtures=failures,
                           human_listening_review='Not yet performed on these fixtures')
    save(out / 'manifest.json', manifest)
    if failures:
        raise SystemExit(f'Failed take preserved at {out}; not publishing: {failures}')
    temporary = base / 'manifest.pending.json'
    save(temporary, manifest)
    temporary.replace(base / 'manifest.json')
    print(f"Published {base / 'manifest.json'}")


if __name__ == '__main__':
    main()
