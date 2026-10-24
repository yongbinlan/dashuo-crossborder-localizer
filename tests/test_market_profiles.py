import copy
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
import test_localizer as fixtures

m=fixtures.m
ONLINE=('th-TH','fil-PH','id-ID')


class MarketProfileTests(unittest.TestCase):
    def test_unsupported_local_engines_fail_before_model_or_network(self):
        for target in ONLINE:
            for engine in ('qwen','chatterbox'):
                with self.subTest(target=target,engine=engine),self.assertRaises(m.Blocked):
                    m.tts_settings({'providers':{'tts':{'engine':engine,'model_dir':str(Path.cwd())}}},target)

    def test_online_permission_is_tts_only_and_absent_permission_blocks(self):
        for target in ONLINE:
            job={'providers':{'tts':{'engine':'edge'}},'online_tts_allowed':False}
            with self.subTest(target=target),self.assertRaises(m.Blocked):m.tts_settings(job,target)
            job['online_tts_allowed']=True
            cfg=m.tts_settings(job,target)
            self.assertTrue(cfg['cloud_allowed'])
            self.assertNotIn('cloud_allowed',job)
            self.assertNotIn('paid_calls_allowed',job)

    def test_korean_female_uses_korean_native_preset(self):
        cfg=m.voice_settings({}, {'source_voice_type':'female'}, 'ko-KR', {'engine':'qwen'}, Path.cwd())
        self.assertEqual(cfg['speaker'],'Sohee')

    def test_taiwan_translation_is_not_discarded_as_duplicate_chinese(self):
        source=[{'id':1,'start':0,'end':2,'text':'这个收纳盒'}]
        target=[{**source[0],'text':'這個收納盒'}]
        asset={'hard_subtitles':'keep','source_subtitle_region':[.1,.8,.8,.1]}
        hard,region=m.subtitle_layout(asset,'zh-TW')
        self.assertEqual(hard,'keep');self.assertIsNotNone(region)
        self.assertEqual(m.display_cues(target,source,'zh-TW','keep','bilingual'),target)
        self.assertEqual(m.display_cues(target,source,'zh-TW','none','bilingual')[0]['text'],'這個收納盒\n这个收纳盒')
        self.assertEqual(m.display_cues(target,source,'zh-SG','keep','bilingual'),[])

    def test_initialization_routes_added_targets_without_granting_cloud_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); video=root/'clip.mp4';video.write_bytes(b'init-only fixture')
            args=SimpleNamespace(input=str(video),output=str(root/'job'),targets='ko-KR,th-TH,fil-PH,id-ID',no_runtime=True)
            m.initialize(args); _,job=m.load_job(root/'job/job.json')
            self.assertFalse(job['online_tts_allowed']);self.assertFalse(job['cloud_allowed'])
            self.assertEqual(job['target_overrides']['ko-KR']['engine'],'qwen')
            for target in ONLINE:
                self.assertEqual(job['target_overrides'][target]['engine'],'edge' if target=='fil-PH' else 'bailian')
                with self.assertRaises(m.Blocked):m.tts_settings(job,target)

    def test_filipino_default_blessica_needs_only_scoped_online_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);video=root/'clip.mp4';video.write_bytes(b'init-only fixture')
            args=SimpleNamespace(input=str(video),output=str(root/'job'),targets='fil-PH,th-TH,id-ID',no_runtime=True,allow_online_tts=True)
            m.initialize(args);_,job=m.load_job(root/'job/job.json')
            cfg=m.tts_settings(job,'fil-PH')
            self.assertEqual(cfg['engine'],'edge')
            self.assertEqual(m.PROFILES['fil-PH']['edge_voice'],'fil-PH-BlessicaNeural')
            self.assertFalse(job['paid_tts_allowed']);self.assertFalse(job['paid_calls_allowed'])
            for target in ('th-TH','id-ID'):
                with self.assertRaises(m.Blocked):m.tts_settings(job,target)

    def test_explicit_initialization_permission_does_not_authorize_remote_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);video=root/'clip.mp4';video.write_bytes(b'init-only fixture')
            args=SimpleNamespace(input=str(video),output=str(root/'job'),targets='th-TH',no_runtime=True,allow_online_tts=True,allow_paid_tts=True)
            m.initialize(args);_,job=m.load_job(root/'job/job.json')
            self.assertTrue(job['online_tts_allowed']);self.assertFalse(job['cloud_allowed']);self.assertFalse(job['paid_calls_allowed'])
            self.assertTrue(m.tts_settings(job,'th-TH')['cloud_allowed'])
            job['providers']['translation']={'engine':'openai_compatible','base_url':'https://invalid.example/v1','model':'fixture'}
            with self.assertRaises(m.Blocked):m.translate_api(root/'job/job.json',job,{},'th-TH',[],root/'no-srt')

    def test_explicit_provided_runtime_is_not_replaced_by_default_online_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);video=root/'clip.mp4';video.write_bytes(b'init-only fixture')
            runtime=root/'runtime.json';m.save_json(runtime,{'providers':{'tts':{'engine':'provided','audio_dir':'chosen'}}})
            args=SimpleNamespace(input=str(video),output=str(root/'job'),targets='th-TH,ko-KR',runtime=str(runtime))
            m.initialize(args);_,job=m.load_job(root/'job/job.json')
            self.assertFalse(job.get('target_overrides'))
            self.assertEqual(m.tts_settings(job,'th-TH')['engine'],'provided')

    def test_profile_change_invalidates_old_variant_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            srt=Path(tmp)/'s.srt';srt.write_text('fixture')
            job={'mode':'subtitles_only','render':{}}
            asset={'id':'fixture','source_sha256':'source'}
            old=m.variant_fingerprint(Path(tmp)/'job.json',job,asset,'th-TH',srt,{}, {})
            saved=copy.deepcopy(m.PROFILES['th-TH'])
            try:
                m.PROFILES['th-TH']['subtitle_font']='changed fixture font'
                self.assertNotEqual(old,m.variant_fingerprint(Path(tmp)/'job.json',job,asset,'th-TH',srt,{},{}))
            finally:m.PROFILES['th-TH']=saved
