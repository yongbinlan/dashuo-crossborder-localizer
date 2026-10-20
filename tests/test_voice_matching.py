from pathlib import Path
import tempfile
import unittest
import test_localizer as fixtures

m=fixtures.m


class VoiceMatchingTests(unittest.TestCase):
    def resolve(self,cfg,source='female',target='en-SG',job=None):
        return m.voice_settings(job or {},{'source_voice_type':source},target,cfg,Path.cwd())

    def test_female_source_selects_female_qwen_instead_of_male_profile_default(self):
        self.assertEqual(self.resolve({'engine':'qwen'})['speaker'],'Serena')
        self.assertEqual(self.resolve({'engine':'qwen'},target='ja-JP')['speaker'],'Ono_Anna')

    def test_female_source_blocks_explicit_male_voice(self):
        for cfg in ({'engine':'qwen','speaker':'Ryan'}, {'engine':'provided','voice_type':'male'}):
            with self.assertRaises(m.Blocked):self.resolve(cfg)

    def test_chatterbox_never_silently_uses_default_voice_for_female_source(self):
        with self.assertRaises(m.Blocked):self.resolve({'engine':'chatterbox'})

    def test_synthetic_reference_integrity_and_provenance_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'reference.wav';fixtures.tone(path)
            ref={'path':str(path),'sha256':m.sha(path),'kind':'synthetic_preset',
                 'voice_type':'female','provenance':'fixture synthetic preset generation receipt'}
            cfg={'engine':'chatterbox','synthetic_voice_reference':ref}
            self.assertEqual(self.resolve(cfg)['voice_type'],'female')
            path.write_bytes(path.read_bytes()+b'changed')
            with self.assertRaises(m.Blocked):self.resolve(cfg)
            ref['sha256']=m.sha(path);ref['kind']='human_reference'
            with self.assertRaises(m.Blocked):self.resolve(cfg)

    def test_unknown_new_job_requires_review_or_explicit_user_voice_choice(self):
        job={'require_voice_type_review':True}
        with self.assertRaises(m.Blocked):self.resolve({'engine':'qwen'},source='unknown',job=job)
        self.assertEqual(self.resolve({'engine':'qwen','voice_type':'female'},source='unknown',job=job)['speaker'],'Serena')

    def test_provided_requires_female_declaration_and_evidence(self):
        with self.assertRaises(m.Blocked):self.resolve({'engine':'provided','voice_type':'female'})
        cfg={'engine':'provided','voice_type':'female','voice_evidence':'verified synthetic generation receipt; listening pending'}
        self.assertEqual(self.resolve(cfg)['voice_type'],'female')

    def test_legacy_unknown_job_retains_existing_settings(self):
        cfg={'engine':'qwen','speaker':'Ryan'}
        self.assertEqual(self.resolve(cfg,source='unknown'),cfg)
