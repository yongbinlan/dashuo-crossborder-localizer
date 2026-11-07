import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sync', ROOT/'scripts/subtitle_sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SubtitleSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.audio = self.root/'speech.wav'
        self.audio.write_bytes(b'fixed-current-speech-fixture')
        self.source = self.root/'words.json'
        self.source.write_text('{"words":[]}', encoding='utf-8')
        self.ass = self.root/'captions.ass'
        self.evidence = self.root/'alignment.json'
        self.planned = [{'start': 0, 'end': 3, 'text': 'Hello world.'}]
        self.cues = [{'start': .06, 'end': 1.2, 'text': 'Hello', 'speech_start': .10, 'speech_end': 1.0},
                     {'start': 1.46, 'end': 3.0, 'text': 'world.', 'speech_start': 1.50, 'speech_end': 2.8}]
        self.proof = {'schema_version': 1, 'audio_sha256': sync.sha(self.audio),
                      'reviewer': 'unit test, not listening', 'method': 'asr_waveform',
                      'sources': [{'path': 'words.json', 'sha256': sync.sha(self.source)}], 'captions': self.cues}
        self.write()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self):
        def stamp(t):
            return f'0:00:{t:05.2f}'
        self.ass.write_text('\n'.join(f"Dialogue: 0,{stamp(c['start'])},{stamp(c['end'])},Default,,0,0,0,,{{\\an2\\pos(80,250)}}{c['text']}" for c in self.cues), encoding='utf-8')
        self.evidence.write_text(json.dumps(self.proof), encoding='utf-8')

    def check(self):
        return sync.check(self.ass, self.planned, self.planned, self.audio, 3, self.evidence)

    def test_current_audio_and_complete_split_captions_pass_without_listening_claim(self):
        result = self.check()
        self.assertTrue(result['anchor_checks_passed'])
        self.assertFalse(result['audio_full_playback'])
        self.assertEqual(result['sync_status'], 'verification_incomplete')

    def test_independent_caption_override_without_evidence_is_blocked(self):
        with self.assertRaisesRegex(sync.SyncError, 'needs current-audio'):
            sync.check(self.ass, self.planned, self.planned, self.audio, 3)

    def test_changed_audio_invalidates_previous_alignment(self):
        self.audio.write_bytes(b'replacement-voice')
        with self.assertRaisesRegex(sync.SyncError, 'different audio'):
            self.check()

    def test_source_change_invalidates_alignment_and_cache_identity(self):
        self.source.write_text('{"words":["changed"]}', encoding='utf-8')
        with self.assertRaisesRegex(sync.SyncError, 'Stale'):
            self.check()
        with self.assertRaises(sync.SyncError):
            sync.evidence_identity(self.evidence)

    def test_changed_evidence_changes_cache_identity(self):
        before = sync.evidence_identity(self.evidence)
        self.proof['reviewer'] = 'new provenance'
        self.write()
        self.assertNotEqual(before, sync.evidence_identity(self.evidence))

    def test_unrecorded_ass_edit_is_blocked(self):
        self.ass.write_text(self.ass.read_text().replace('world.', 'world!'), encoding='utf-8')
        with self.assertRaisesRegex(sync.SyncError, 'Actual ASS differs'):
            self.check()

    def test_matching_ass_and_proof_cannot_omit_spoken_words(self):
        self.cues[1]['text'] = 'w.'
        self.write()
        with self.assertRaisesRegex(sync.SyncError, 'omit, reorder or change'):
            self.check()

    def test_late_start_and_premature_end_are_blocked(self):
        self.cues[1]['start'] = 2.0
        self.write()
        with self.assertRaisesRegex(sync.SyncError, 'start differs'):
            self.check()
        self.cues[1]['start'] = 1.46
        self.cues[1]['end'] = 2.3
        self.write()
        with self.assertRaisesRegex(sync.SyncError, 'before its speech ends'):
            self.check()

    def test_previous_caption_cannot_linger_into_next_spoken_phrase(self):
        self.cues[0]['end'] = 1.65
        self.cues[1]['start'] = 1.66
        self.write()
        with self.assertRaisesRegex(sync.SyncError, 'remains after'):
            self.check()

    def test_unordered_or_overlapping_ass_is_blocked(self):
        self.cues[0]['end'] = 1.9
        self.write()
        with self.assertRaisesRegex(sync.SyncError, 'Overlapping'):
            self.check()

    def test_plain_clip_slots_are_only_structural_checks(self):
        self.cues[:] = copy.deepcopy(self.planned)
        self.write()
        result = sync.check(self.ass, self.planned, self.planned, self.audio, 3)
        self.assertEqual(result['timing_basis'], 'assembled_clip_slots_only')
        self.assertNotIn('anchor_checks_passed', result)
        self.assertFalse(result['audio_full_playback'])


if __name__ == '__main__':
    unittest.main()
