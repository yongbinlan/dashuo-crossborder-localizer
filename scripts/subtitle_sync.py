"""Bind independent ASS timelines to the speech actually used. Not a listening test."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re


class SyncError(ValueError):
    pass


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def plain(text, ass=False):
    if ass:
        text = re.sub(r'\{[^}]*\}', '', text).replace(r'\N', ' ').replace(r'\n', ' ')
    else:
        text = text.translate(str.maketrans({'\\': '＼', '{': '｛', '}': '｝'}))
    return re.sub(r'\s+', '', text)


def parse_ass(path):
    def seconds(value):
        h, m, s = value.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    cues = []
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        if not line.startswith('Dialogue:'):
            continue
        fields = line.split(':', 1)[1].lstrip().split(',', 9)
        if len(fields) != 10:
            raise SyncError('Invalid ASS Dialogue row')
        cues.append({'start': seconds(fields[1]), 'end': seconds(fields[2]),
                     'text': fields[9], 'id': len(cues) + 1})
    return cues


def same(actual, expected):
    return len(actual) == len(expected) and all(
        abs(a['start'] - b['start']) <= .011 and abs(a['end'] - b['end']) <= .011
        and plain(a['text'], ass=True) == plain(b['text'])
        for a, b in zip(actual, expected))


def evidence_identity(path):
    path = Path(path).resolve()
    evidence = read(path)
    sources = evidence.get('sources', [])
    if not sources:
        raise SyncError('Missing alignment source artifacts')
    identities = []
    for source in sources:
        source_path = Path(source['path'])
        if not source_path.is_absolute():
            source_path = path.parent / source_path
        if not source_path.is_file() or sha(source_path) != source.get('sha256'):
            raise SyncError('Stale or missing alignment source artifact')
        identities.append([str(source_path.resolve()), source['sha256']])
    return {'evidence_sha256': sha(path), 'sources': identities}


def check(ass_path, planned, spoken, audio_path, total, evidence_path=None):
    """Reject stale/unsupported independent timelines before video encoding.

    Default one-caption-per-clip assembly is structurally checked, not word-aligned.
    Changed ASS events require a separately recorded, current-audio-bound timeline.
    All successes still require actual review; no listening boolean is manufactured.
    """
    actual = parse_ass(ass_path)
    previous_end = 0.0
    if not actual or not math.isfinite(total) or total <= 0:
        raise SyncError('Missing captions or invalid duration')
    for cue in actual:
        a, b = cue['start'], cue['end']
        if not (math.isfinite(a) and math.isfinite(b) and 0 <= a < b <= total + .02):
            raise SyncError('Caption outside source duration')
        if a < previous_end - .011:
            raise SyncError('Overlapping or unordered captions')
        previous_end = b
    independent = not same(actual, planned)
    result = {'schema_version': 1, 'ass_sha256': sha(ass_path),
              'audio_sha256': sha(audio_path), 'caption_count': len(actual),
              'independent_timeline': independent, 'structural_checks_passed': True,
              'sync_status': 'verification_incomplete', 'audio_full_playback': False,
              'limitations': ['Structural/anchor validation does not establish audible word boundaries or listening approval.']}
    if not evidence_path:
        if independent:
            raise SyncError('Independent subtitle timeline needs current-audio alignment evidence; paragraph duration is insufficient')
        result['timing_basis'] = 'assembled_clip_slots_only'
        return result
    path = Path(evidence_path).resolve()
    evidence = read(path)
    if evidence.get('schema_version') != 1 or evidence.get('audio_sha256') != result['audio_sha256']:
        raise SyncError('Alignment evidence refers to different audio')
    if not evidence.get('reviewer') or evidence.get('method') not in {'asr_waveform', 'listened', 'provided_aligned'}:
        raise SyncError('Missing reviewer/alignment method')
    anchors = evidence.get('captions', [])
    if not same(actual, anchors):
        raise SyncError('Actual ASS differs from the aligned timeline')
    if ''.join(plain(c.get('target_text', c['text'])) for c in anchors) != ''.join(plain(c['text']) for c in spoken):
        raise SyncError('Aligned captions omit, reorder or change spoken text')
    evidence_identity(path)
    offsets = []
    for i, (cue, anchor) in enumerate(zip(actual, anchors)):
        start, end = anchor.get('speech_start'), anchor.get('speech_end')
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            raise SyncError('Missing speech start/end anchor')
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= total + .02):
            raise SyncError('Invalid speech anchor')
        delta = cue['start'] - start
        # Diagnostic defaults, not a claim of 200ms perceptual accuracy.
        if abs(delta) > .20 + .011:
            raise SyncError(f'Caption {i + 1} start differs from speech anchor by {delta:.3f}s')
        if cue['end'] < end - .12 - .011:
            raise SyncError(f'Caption {i + 1} disappears before its speech ends')
        if i + 1 < len(anchors):
            next_start = anchors[i + 1].get('speech_start')
            if not isinstance(next_start, (int, float)) or next_start < end - .02:
                raise SyncError('Overlapping/unordered speech anchors')
            if cue['end'] > next_start + .10 + .011:
                raise SyncError(f'Caption {i + 1} remains after the next phrase starts')
        offsets.append(delta)
    result.update({'timing_basis': evidence['method'], 'evidence_sha256': sha(path),
                   'start_offsets_seconds': offsets, 'anchor_checks_passed': True,
                   'max_abs_start_offset_seconds': max(abs(x) for x in offsets)})
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ass', required=True)
    p.add_argument('--planned', required=True, help='JSON array of intended displayed cues')
    p.add_argument('--spoken', required=True, help='JSON array of target narration cues')
    p.add_argument('--audio', required=True)
    p.add_argument('--duration', required=True, type=float)
    p.add_argument('--evidence')
    p.add_argument('--output', required=True)
    args = p.parse_args()
    output = Path(args.output)
    try:
        result = check(args.ass, read(args.planned), read(args.spoken), args.audio, args.duration, args.evidence)
    except (SyncError, KeyError, ValueError, OSError) as error:
        result = {'structural_checks_passed': False, 'sync_status': 'blocked', 'reason': str(error)}
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        raise SystemExit(str(error))
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
