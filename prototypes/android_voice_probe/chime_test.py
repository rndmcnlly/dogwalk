# /// script
# dependencies = ["numpy", "scipy", "soundfile"]
# ///
"""CPU-only known-chime experiment. Run: uv run --script chime_test.py

Composition/resampling and evaluation only. All spectral extraction, matching and
plots are performed by the canonical audio-analysis-tool CLI. No ASR dependency.
Fresh output directory required: --out .tools/chime-matching-take-002.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
from scipy.signal import resample_poly
import soundfile as sf

ROOT = Path(__file__).resolve().parent
TOOL = Path.home() / '.config/opencode/skills/audio-analysis-tool/audio_analysis.py'
RATE = 16000
SETTINGS = ['--nfft', '512', '--hop', '128', '--bands', '64', '--basis', 'log']
THRESHOLD = .85  # Frozen before examining held-out capture or negative controls.


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path, start=0, end=None):
    audio, rate = sf.read(path, always_2d=True)
    audio = audio[round(start*rate):None if end is None else round(end*rate)].mean(axis=1)
    divisor = math.gcd(rate, RATE)
    return resample_poly(audio, RATE//divisor, rate//divisor)


def instrument(*args):
    then = time.perf_counter()
    process = subprocess.run(['uv', 'run', '--script', str(TOOL), *map(str, args)],
                             capture_output=True, text=True, check=True)
    return json.loads(process.stdout), time.perf_counter()-then


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / '.tools/chime-matching')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'fixtures').mkdir()
    overall = time.perf_counter()
    awake = ROOT / '.tools/audio-bridge-smoke/awake.wav'
    off = ROOT / '.tools/audio-bridge-smoke/screen-off.wav'
    fixture_manifest = ROOT / '.tools/voice-fixtures/manifest.json'
    manifest = json.loads(fixture_manifest.read_text())
    chime = read(awake, 9.5, 10.6)
    speech = read(awake, 1.8, 7.2)
    cases = []

    def case(name, audio, category, events=(), provenance=None):
        path = out / 'fixtures' / f'{name}.wav'
        sf.write(path, audio, RATE, subtype='PCM_16')
        cases.append(dict(name=name, path=str(path), sha256=digest(path),
                          duration_s=len(audio)/RATE, category=category,
                          expected_clip_starts_s=list(events), provenance=provenance))

    template = out / 'template.wav'
    sf.write(template, chime, RATE, subtype='PCM_16')
    case('source-awake', read(awake), 'source-self-match', [9.5], dict(path=str(awake), sha256=digest(awake)))
    for level in [-18, 0, 6]:
        audio = np.zeros(3 * RATE)
        audio[RATE:RATE+len(chime)] = chime * 10**(level/20)
        case(f'level-{level:+d}', audio, 'source-derived-controlled', [1.0], dict(gain_db=level))
    for level in [0, -12]:
        audio = np.tile(speech, 2)[:10*RATE].copy()
        if len(audio) < 10*RATE:
            audio = np.pad(audio, (0, 10*RATE-len(audio)))
        starts = [1.0, 4.0, 7.0]
        for at in starts:
            frame = round(at*RATE)
            audio[frame:frame+len(chime)] += chime * 10**(level/20)
        case(f'mixture-{level:+d}', audio, 'source-derived-controlled-mixture', starts,
             dict(chime_gain_db=level, background='Android TTS at captured level'))
    case('android-tts', speech, 'negative')
    case('mac-say', read(ROOT / '.tools/probe-status.wav'), 'negative')
    breeze = np.concatenate([np.r_[read(Path(f['wav'])), np.zeros(RATE//2)] for f in manifest['fixtures']])
    case('breeze', breeze, 'negative', provenance=dict(manifest=str(fixture_manifest), sha256=digest(fixture_manifest)))
    case('silence', np.zeros(3*RATE), 'negative')
    case('unrelated-tone', read(ROOT / '.tools/parakeet-calibration/fixtures/synthetic-tone-only.wav'), 'negative')
    rng = np.random.default_rng(417)
    case('noise', rng.normal(0, .015, 3*RATE), 'negative')
    # Independent recording; do not optimize threshold after inspecting it.
    case('heldout-screen-off', read(off), 'heldout-real', provenance=dict(path=str(off), sha256=digest(off)))
    save(out / 'design.json', dict(template=dict(path=str(template), sha256=digest(template),
         source_path=str(awake), source_sha256=digest(awake), crop_start_s=9.5, crop_end_s=10.6),
         settings=SETTINGS, threshold=THRESHOLD,
         threshold_rationale='Preselected conservative image cosine .85; envelope cosine >=.8, onset contrast >=3, peak >=-65 dB. No threshold search on heldout or negative set.',
         tool=str(TOOL), tool_sha256=digest(TOOL), cases=cases))
    template_result, template_wall = instrument('analyze', template, '--out', out / 'template-analysis', *SETTINGS)
    results = []
    for item in cases:
        analysis_dir = out / f"analysis-{item['name']}"
        extraction, extraction_wall = instrument('analyze', item['path'], '--out', analysis_dir,
            *SETTINGS, '--format', 'report' if item['category'] == 'heldout-real' else 'data')
        matched, match_wall = instrument('match', out / 'template-analysis/analysis.npz',
            analysis_dir / 'analysis.npz', '--threshold', THRESHOLD, '--min-db', '-65', '--separation', '.7')
        save(out / f"match-{item['name']}.json", matched)
        evaluation = dict()
        if item['category'] not in ['heldout-real', 'negative']:
            unmatched = list(item['expected_clip_starts_s'])
            errors, false_hits = [], []
            for hit in matched['hits']:
                nearest = min(unmatched, key=lambda t: abs(t-hit['start_s'])) if unmatched else None
                if nearest is not None and abs(nearest-hit['start_s']) <= .15:
                    errors.append(hit['start_s']-nearest)
                    unmatched.remove(nearest)
                else:
                    false_hits.append(hit)
            evaluation = dict(true_positives=len(errors), false_positives=len(false_hits),
                              false_negatives=len(unmatched), clip_start_errors_s=errors)
        elif item['category'] == 'negative':
            evaluation = dict(false_positives=len(matched['hits']))
        results.append(dict(**item, hits=matched['hits'], max_score=matched['max_score'],
                            evaluation=evaluation, analysis_seconds=extraction['analysis_seconds'],
                            match_seconds=matched['match_seconds'],
                            cli_wall_seconds=extraction_wall+match_wall))
        print(item['name'], 'hits', len(matched['hits']), 'max', round(matched['max_score'], 4), flush=True)
    controlled = [r for r in results if r['category'].startswith('source-derived')]
    tp = sum(r['evaluation']['true_positives'] for r in controlled)
    fp = sum(r['evaluation']['false_positives'] for r in controlled)
    fn = sum(r['evaluation']['false_negatives'] for r in controlled)
    save(out / 'report.json', dict(results=results, threshold=THRESHOLD,
        controlled_totals=dict(tp=tp, fp=fp, fn=fn, precision=tp/(tp+fp) if tp+fp else None,
                               recall=tp/(tp+fn) if tp+fn else None),
        negative_false_hits=sum(r['evaluation']['false_positives'] for r in results if r['category']=='negative'),
        negative_duration_s=sum(r['duration_s'] for r in results if r['category']=='negative'),
        spectral_compute_seconds=template_result['analysis_seconds']+sum(r['analysis_seconds']+r['match_seconds'] for r in results),
        total_wall_seconds=time.perf_counter()-overall,
        note='Source self-match and derived variants are not independent validation. Heldout recording requires visual event confirmation. Scores are not probabilities. No ASR used; no claim of all-chime detection or event cause.'))
    print(out / 'report.json')


if __name__ == '__main__':
    main()
