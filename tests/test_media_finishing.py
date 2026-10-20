from pathlib import Path
import unittest
from unittest.mock import patch
import test_localizer as fixtures

m=fixtures.m


class FinishingFlowTests(unittest.TestCase):
    setUp=fixtures.MediaFlowTests.setUp
    tearDown=fixtures.MediaFlowTests.tearDown

    def render_one(self):
        self.job['targets']=['en-SG']
        result=m.execute(self.path,self.job)['results'][0]
        self.assertEqual(result['status'],'verification_incomplete',result)
        return Path(result['receipt']).parent

    def test_audio_only_change_reuses_video_packets_and_measures_final_aac(self):
        first=self.render_one()
        before=m.finish.video_identity(first/'localized.mp4')
        self.job['render']['background_gain']=.3
        second=self.render_one()
        self.assertNotEqual(first,second)
        picture=m.read_json(second/'picture-preservation.json')
        self.assertEqual(picture['mode'],'stream_copy_verified')
        self.assertEqual(before,m.finish.video_identity(second/'localized.mp4'))
        levels=m.read_json(second/'loudness-receipt.json')
        actual=levels['final_encoded']['measurements']
        self.assertLess(abs(float(actual['input_i'])+16),1.)
        self.assertLessEqual(float(actual['input_tp']),-1.)
        self.assertTrue((second/'dub.wav').is_file())
        self.assertEqual(levels['voice']['samples'],72000)
        self.assertFalse(m.read_json(second/'receipt.json')['publication_authorized'])

    def test_changed_subtitle_encodes_from_original_instead_of_reusing_old_picture(self):
        first=self.render_one();old_sha=m.sha(first/'localized.mp4')
        path=m.request_path(self.path,self.job['assets'][0],'en-SG')
        translation=m.read_json(path);translation['items'][0]['text']='A different caption.';m.save_json(path,translation)
        second=self.render_one();picture=m.read_json(second/'picture-preservation.json')
        self.assertEqual(picture['mode'],'single_encode_from_original')
        self.assertEqual(Path(picture['source']),self.video)
        self.assertEqual(m.sha(first/'localized.mp4'),old_sha)

    def test_no_new_caption_copies_original_video_directly(self):
        self.job['targets']=['zh-SG']
        self.job['assets'][0].update({'hard_subtitles':'keep','source_subtitle_region':[.1,.8,.8,.1]})
        result=m.execute(self.path,self.job)['results'][0]
        self.assertEqual(result['status'],'verification_incomplete',result)
        folder=Path(result['receipt']).parent
        self.assertEqual(m.finish.video_identity(self.video),m.finish.video_identity(folder/'localized.mp4'))

    def test_invalid_loudness_target_blocks_before_creating_a_video(self):
        self.job['render']['loudness']={'delivery_tp':0}
        self.job['targets']=['en-SG']
        result=m.execute(self.path,self.job)
        self.assertEqual(result['counts'],{'blocked':1})
        self.assertFalse(list(self.jobdir.rglob('localized.mp4')))


class LoudnessGuardTests(unittest.TestCase):
    def test_sfx_tail_survives_full_duration_mixing(self):
        import tempfile,wave,array,math
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp);stems={}
            for name,hz in [('music',240),('sfx',900)]:
                path=work/(name+'.wav');fixtures.tone(path,1.,hz)
                stems[name]={'file':str(path),'rms_dbfs':-29.8,'peak_dbfs':-26.8}
            voice=work/'voice.wav';fixtures.tone(voice,1.,660)
            m.mix_bandit(work,{'stems':stems,'receipt':'fixture'},voice,1.,{'music_gain_db':0,'sfx_gain_db':0})
            pcm=work/'bed-inspection.wav'
            m.run(['ffmpeg','-v','error','-n','-i',work/'background.wav','-ac','1','-ar','24000','-c:a','pcm_s16le',pcm])
            with wave.open(str(pcm),'rb') as f:
                self.assertEqual(f.getnframes(),24000)
                f.setpos(23520);tail=array.array('h',f.readframes(480))
            self.assertGreater(math.sqrt(sum(v*v for v in tail)/len(tail)),300)

    def test_silence_is_not_boosted_into_a_successful_voice(self):
        import tempfile,wave
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'silence.wav'
            with wave.open(str(path),'wb') as f:
                f.setparams((1,2,24000,0,'NONE','not compressed'));f.writeframes(bytes(24000*2))
            with self.assertRaisesRegex(RuntimeError,'静音'):
                m.finish.voice(Path(tmp),path,{})

    def test_damaged_picture_candidate_is_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            parent=Path(tmp);folder=parent/'v1';folder.mkdir()
            m.save_json(folder/'receipt.json',{'technical_checks_passed':True})
            (folder/'localized.mp4').write_bytes(b'damaged media')
            m.save_json(folder/'picture-preservation.json',{'signature':'same','output_sha256':'wrong'})
            self.assertIsNone(m.finish.find_picture(parent,'same'))
