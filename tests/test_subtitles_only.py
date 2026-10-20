"""Outcome tests: original audio integrity, skipped engines and lane isolation."""
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import test_localizer as fixtures

m=fixtures.m


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'requires FFmpeg')
class SubtitlesOnlyTests(unittest.TestCase):
    def setUp(self):
        fixtures.MediaFlowTests.setUp(self)
        self.job['mode']='subtitles_only'
        self.job['providers'].pop('tts')
        self.job['assets'][0]['background']=None

    def tearDown(self):
        fixtures.MediaFlowTests.tearDown(self)

    def test_malay_subtitles_without_tts_or_background_preserve_audio(self):
        self.job['targets']=['ms-MY']
        with patch.object(m,'tts_settings',side_effect=AssertionError('must skip TTS config')), \
             patch.object(m,'voice_settings',side_effect=AssertionError('must skip voice matching')), \
             patch.object(m,'background',side_effect=AssertionError('must skip separation')), \
             patch.object(m,'synthesize',side_effect=AssertionError('must skip TTS')), \
             patch.object(m,'assemble_audio',side_effect=AssertionError('must skip speech fitting')):
            result=m.execute(self.path,self.job)
        self.assertEqual(result['counts'],{'verification_incomplete':1},result)
        directory=Path(result['results'][0]['receipt']).parent
        receipt=m.read_json(directory/'receipt.json')
        self.assertEqual(m.audio_identity(self.video),m.audio_identity(directory/'localized.mp4'))
        self.assertFalse((directory/'dub.wav').exists())
        self.assertFalse((directory/'speech').exists())
        self.assertFalse(receipt['evidence']['tts_used'])
        self.assertFalse(receipt['evidence']['separation_used'])
        translated=m.parse_srt(directory/'translated.srt')
        self.assertEqual((translated[0]['start'],translated[0]['end']),(.5,2.5))
        self.assertEqual(receipt['mode'],'subtitles_only')

    def test_six_profiles_resume_despite_irrelevant_voice_config_changes(self):
        first=m.execute(self.path,self.job)
        self.assertEqual(first['counts'],{'verification_incomplete':6},first)
        self.job['providers']['tts']={'engine':'missing-engine','model_dir':'missing-model'}
        self.job['providers']['separation']={'repo':'missing-repo'}
        self.job['assets'][0]['background']='does-not-exist.wav'
        self.job['render']['max_speedup']=0
        resumed=m.execute(self.path,self.job)
        self.assertEqual(resumed['counts'],{'reused_checked_bytes':6},resumed)

    def test_all_original_audio_tracks_are_retained(self):
        self.job['targets']=['en-SG']
        video=self.root/'双音轨.mp4'
        m.run(['ffmpeg','-v','error','-n','-i',self.video,'-f','lavfi','-i','sine=frequency=900:duration=3',
               '-map','0:v:0','-map','0:a:0','-map','1:a:0','-c:v','copy','-c:a','aac',video])
        self.job['assets'][0]['video']=str(video)
        self.job['assets'][0]['source_sha256']=m.sha(video)
        result=m.execute(self.path,self.job)
        self.assertEqual(result['counts'],{'verification_incomplete':1},result)
        receipt=m.read_json(result['results'][0]['receipt'])
        self.assertEqual(receipt['evidence']['original_audio_streams'],2)

    def test_silent_source_stays_silent_without_adding_audio(self):
        self.job['targets']=['ja-JP']
        video=self.root/'无音轨.mp4'
        m.run(['ffmpeg','-v','error','-n','-i',self.video,'-map','0:v:0','-c:v','copy','-an',video])
        self.job['assets'][0]['video']=str(video)
        self.job['assets'][0]['source_sha256']=m.sha(video)
        result=m.execute(self.path,self.job)
        self.assertEqual(result['counts'],{'verification_incomplete':1},result)
        directory=Path(result['results'][0]['receipt']).parent
        self.assertEqual(m.audio_identity(directory/'localized.mp4'),[])

    def test_audio_replacement_is_detected_even_with_same_duration(self):
        changed=self.root/'音轨被改.mp4'
        m.run(['ffmpeg','-v','error','-n','-i',self.video,'-af','volume=0.7',
               '-c:v','copy','-c:a','aac',changed])
        with self.assertRaises(m.Blocked):
            m.verify_original_audio(self.video,changed)

    def test_switching_from_dub_creates_new_version_and_keeps_old(self):
        self.job['targets']=['en-SG']
        self.job['mode']='dub'
        voice=self.root/'人工配音'
        self.job['providers']['tts']={'engine':'provided','audio_dir':str(voice)}
        self.job['assets'][0]['background']=str(self.root/'background.wav')
        first=m.execute(self.path,self.job)['results'][0]
        original=Path(first['receipt']).parent/'localized.mp4'
        old_sha=m.sha(original)
        self.job['mode']='subtitles_only'
        self.job['providers'].pop('tts')
        self.job['assets'][0]['background']=None
        second=m.execute(self.path,self.job)['results'][0]
        self.assertNotEqual(first['receipt'],second['receipt'])
        self.assertEqual(m.sha(original),old_sha)
        self.assertEqual(m.audio_identity(self.video),m.audio_identity(Path(second['receipt']).parent/'localized.mp4'))

    def test_subtitle_review_requires_original_audio_check_not_voice_removal(self):
        self.job['targets']=['en-SG']
        item=m.execute(self.path,self.job)['results'][0]
        directory=Path(item['receipt']).parent
        evidence=self.root/'review.json'
        receipt=m.read_json(directory/'receipt.json')
        m.save_json(evidence,{'visual':True,'translation_semantics':True,'subtitle_sync':True,
                             'original_audio_preserved':True,'audio_full_playback':False,
                             'reviewer':'synthetic-review-test-fixture','notes':'Not an actual human review',
                             'video_sha256':receipt['artifact_sha256']['localized.mp4']})
        class Args:pass
        args=Args();args.variant=str(directory);args.evidence=str(evidence)
        with self.assertRaises(m.Blocked):m.record_review(args)
        self.assertEqual(m.read_json(directory/'receipt.json')['status'],'verification_incomplete')

    def test_unknown_mode_never_falls_back_to_dub(self):
        self.job['mode']='typo-subtitles'
        m.save_json(self.path,self.job)
        with self.assertRaises(m.Blocked):m.load_job(self.path)

    def test_provided_source_srt_needs_no_asr_environment(self):
        self.job['providers'].pop('asr')
        records=m.transcribe(self.path,self.job)
        self.assertEqual(records[0]['status'],'existing_srt_checked')

    def test_legacy_job_without_mode_keeps_dub_requirements(self):
        self.job.pop('mode')
        self.job['render']['background_mode']='required'  # Actual pre-v1.5 default.
        self.job['targets']=['en-SG']
        self.job['providers']['tts']={'engine':'provided','audio_dir':str(self.root/'人工配音')}
        result=m.execute(self.path,self.job)
        self.assertEqual(result['counts'],{'blocked':1},result)
        self.assertIn('缺背景声',result['results'][0]['reason'])


if __name__=='__main__':unittest.main(verbosity=2)
