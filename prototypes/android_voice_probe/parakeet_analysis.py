#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "soundfile", "scipy"]
# ///
"""Independent timestamped ASR, with an intentionally small chime calibration.

uv run --script parakeet_analysis.py prepare
uv run --script parakeet_analysis.py transcribe [WAV ...] --out DIR
uv run --script parakeet_analysis.py report

With no WAVs, transcribe processes the calibration fixtures and original captures.
All model work holds .tools/media-gpu.lock. Cooperating TTS jobs must do likewise.
No reference text is passed to ASR. No speaker identification or sound classifier.
Waveform/spectral inspection uses the central audio-analysis-tool, not this helper.
"""
import argparse
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

import numpy as np
from scipy.signal import resample_poly
import soundfile as sf

ROOT = Path(__file__).resolve().parent
BASE = ROOT / '.tools/parakeet-calibration'
MODEL = 'mlx-community/parakeet-tdt-0.6b-v3'
PACKAGE = 'parakeet-mlx==0.5.2'
LEXICAL = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def metadata(path):
    info = sf.info(path)
    return dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                sample_rate=info.samplerate, channels=info.channels,
                frames=info.frames, duration_s=info.duration)


def word_spans(raw):
    """Intersect lexical character ranges with concatenated raw subword tokens.

    A punctuation-only token cannot extend a word endpoint. A token spanning
    multiple lexical words necessarily gives those words the same token bounds.
    """
    result = []
    for sentence_index, sentence in enumerate(raw.get('sentences', [])):
        tokens, cursor = [], 0
        for token in sentence.get('tokens', []):
            text = token['text']
            tokens.append((cursor, cursor + len(text), token))
            cursor += len(text)
        text = ''.join(t['text'] for _, _, t in tokens)
        for match in LEXICAL.finditer(text):
            contributing = [t for start, end, t in tokens
                            if start < match.end() and end > match.start()
                            and LEXICAL.search(t['text'])]
            if contributing:
                result.append(dict(word=match.group(), start=min(t['start'] for t in contributing),
                                   end=max(t['end'] for t in contributing), sentence=sentence_index))
    return result


def prepare():
    fixtures = BASE / 'fixtures'
    fixtures.mkdir(parents=True, exist_ok=True)
    manifest_path = BASE / 'composition.json'
    if manifest_path.exists():
        raise SystemExit('Calibration already exists; preserve its evidence or choose a new location in code.')
    rate = 16000
    source = ROOT / '.tools/audio-bridge-smoke/awake.wav'
    audio, original_rate = sf.read(source, always_2d=True)
    # Inspected central-tool log spectrogram: speech ends ~6.5 s; the isolated
    # stationary ringing/transients occupy ~9.75–10.45 s, with silence around it.
    start, end = 9.5, 10.6
    isolated = audio[round(start * original_rate):round(end * original_rate)].mean(axis=1)
    divisor = math.gcd(original_rate, rate)
    chime = resample_poly(isolated, rate // divisor, original_rate // divisor)
    speech_source = ROOT / '.tools/probe-status.wav'
    speech, speech_rate = sf.read(speech_source, always_2d=True)
    assert speech_rate == rate
    speech = speech.mean(axis=1)
    t = np.arange(round(.3 * rate)) / rate
    tone = .1 * (np.sin(2 * np.pi * 880 * t) + .5 * np.sin(2 * np.pi * 1320 * t))
    tone *= np.sin(np.pi * np.arange(len(t)) / (len(t) - 1)) ** 2
    sources = dict(android_chime=chime, speech=speech, synthetic_tone=tone)
    specs = {
        'android-chime-only': [(0.5, 'android_chime')],
        'synthetic-tone-only': [(0.5, 'synthetic_tone')],
        'speech-only': [(0.5, 'speech')],
        'alternating': [(0.5, 'android_chime'), (2.0, 'speech'),
                        (5.5, 'android_chime'), (7.0, 'speech')],
    }
    cases = []
    for name, placements in specs.items():
        duration = max(at + len(sources[kind]) / rate for at, kind in placements) + .5
        output = np.zeros(math.ceil(duration * rate))
        spans = []
        for at, kind in placements:
            signal = sources[kind]
            frame = round(at * rate)
            output[frame:frame + len(signal)] += signal
            spans.append(dict(kind=kind, start=frame / rate, end=(frame + len(signal)) / rate))
        path = fixtures / f'{name}.wav'
        sf.write(path, output, rate, subtype='PCM_16')
        cases.append(dict(name=name, audio=metadata(path), placements=spans))
    save(manifest_path, dict(cases=cases, sources={
        'android_chime': dict(audio=metadata(source), crop_start_s=start, crop_end_s=end,
                              attribution='Isolated Android earcon candidate: stationary ringing after TTS, visually separated from speech; not a general chime classifier.',
                              spectral_evidence=str(BASE / 'waveform-awake/timeline.png')),
        'speech': dict(audio=metadata(speech_source), provenance='macOS say fixture, not a person identity'),
        'synthetic_tone': dict(frequencies_hz=[880, 1320], duration_s=.3, envelope='sine-squared'),
    }))
    print(manifest_path)


def transcribe(files, out):
    if not files:
        manifest = json.loads((BASE / 'composition.json').read_text())
        files = [Path(case['audio']['path']) for case in manifest['cases']]
        files += [ROOT / '.tools/audio-bridge-smoke' / f'{name}.wav'
                  for name in ['awake', 'screen-off']]
        files += [ROOT / '.tools/probe-status.wav']
    out.mkdir(parents=True, exist_ok=True)
    if len({p.stem for p in files}) != len(files):
        raise SystemExit('Input basenames must be unique.')
    if any((out / f'{p.stem}.json').exists() for p in files):
        raise SystemExit('Refusing to overwrite raw ASR; use a new --out directory.')
    command = ['uvx', '--from', PACKAGE, 'parakeet-mlx', '--model', MODEL,
               '--output-dir', str(out), '--output-format', 'json',
               '--chunk-duration', '300', '--silence-gap', '0.8',
               '--max-words', '40', '--max-duration', '25', *map(str, files)]
    save(out / 'inputs.json', dict(model=MODEL, package=PACKAGE, command=command,
                                 inputs=[metadata(p) for p in files], reference_text_supplied=False))
    with (ROOT / '.tools/media-gpu.lock').open('a') as lock:
        print('Waiting for exclusive media GPU lock', flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
        print('Acquired media GPU lock', flush=True)
        subprocess.run(command, check=True)
    for path in files:
        raw = json.loads((out / f'{path.stem}.json').read_text())
        save(out / f'{path.stem}.words.json', dict(audio=metadata(path), words=word_spans(raw)))


def report():
    composition = json.loads((BASE / 'composition.json').read_text())
    cases = []
    baseline = json.loads((BASE / 'asr/speech-only.words.json').read_text())['words']
    for case in composition['cases']:
        raw_path = BASE / 'asr' / f"{case['name']}.json"
        raw = json.loads(raw_path.read_text())
        words = word_spans(raw)
        comparisons = []
        for placement in case['placements']:
            inside = [w for w in words if w['start'] < placement['end'] and w['end'] > placement['start']]
            comparison = dict(**placement, overlapping_words=inside)
            if placement['kind'] == 'speech' and inside:
                comparison.update(first_word_minus_clip_start_s=inside[0]['start'] - placement['start'],
                                  last_word_minus_clip_end_s=inside[-1]['end'] - placement['end'])
                if [w['word'].lower() for w in inside] == [w['word'].lower() for w in baseline]:
                    shift = placement['start'] - .5
                    comparison['relative_to_speech_only_errors_s'] = [
                        dict(word=w['word'], start=w['start'] - b['start'] - shift,
                             end=w['end'] - b['end'] - shift) for w, b in zip(inside, baseline)]
            comparisons.append(comparison)
        punctuation = [dict(text=s['text'], start=s['start'], end=s['end'])
                       for s in raw.get('sentences', []) if not LEXICAL.search(s['text'])]
        cases.append(dict(**case, raw_asr=str(raw_path), text=raw.get('text'),
                          raw_keys=list(raw), words=words, comparisons=comparisons,
                          punctuation_only_sentence_spans=punctuation))
    captures = []
    for name in ['awake', 'screen-off', 'probe-status']:
        raw_path = BASE / 'asr' / f'{name}.json'
        raw = json.loads(raw_path.read_text())
        derived = json.loads((BASE / 'asr' / f'{name}.words.json').read_text())
        captures.append(dict(name=name, audio=derived['audio'], text=raw['text'],
                             words=derived['words'], raw_asr=str(raw_path)))
    errors = [abs(e[boundary]) for case in cases if case['name'] == 'alternating'
              for comparison in case['comparisons']
              for e in comparison.get('relative_to_speech_only_errors_s', [])
              for boundary in ['start', 'end']]
    summary = dict(
        chime_only_text=cases[0]['text'], tone_only_text=cases[1]['text'],
        speech_only_text=cases[2]['text'], alternating_text=cases[3]['text'],
        alternating_max_absolute_word_boundary_shift_vs_speech_only_s=max(errors, default=None),
        alternating_mean_absolute_word_boundary_shift_vs_speech_only_s=float(np.mean(errors)) if errors else None,
        isolated_signal_lexical_word_counts={c['name']: len(c['words']) for c in cases[:2]},
        alternating_words_overlapping_chime=[w for comparison in cases[3]['comparisons']
                                            if comparison['kind'] == 'android_chime'
                                            for w in comparison['overlapping_words']],
        observation='Inspect raw ASR for any explicit event labels; lexical output or its absence alone does not demonstrate sound detection or establish silence.')
    save(BASE / 'report.json', dict(model=MODEL, package=PACKAGE, summary=summary,
        cases=cases, captures=captures,
        interpretation='ASR is lexical recognition, not silence, vocal-presence or general sound detection. Empty output means no recognizable words recovered. Composition boundaries are exact clip placements, not manually labeled phonetic boundaries. Relative timing errors compare identical speech audio against speech-only ASR, not independent ground-truth word labels. No speaker identity inferred.'))
    print(BASE / 'report.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('prepare')
    run = sub.add_parser('transcribe')
    run.add_argument('files', nargs='*', type=Path)
    run.add_argument('--out', type=Path, default=BASE / 'asr')
    sub.add_parser('report')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    elif args.action == 'transcribe':
        transcribe(args.files, args.out)
    else:
        report()


if __name__ == '__main__':
    main()
