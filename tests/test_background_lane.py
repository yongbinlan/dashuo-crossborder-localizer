from pathlib import Path
import json,math,tempfile,unittest,wave,array
from unittest.mock import patch
import test_localizer as fixtures
m=fixtures.m


class ThreeStemLaneTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.asset={'video':str(self.base/'source.wav'),'source_sha256':'fixture-identity'}
        self.job={'providers':{'separation':{'engine':'bandit','device':'cpu'}},'render':{'background_mode':'bandit'}}
        self.calls=0

    def tearDown(self):self.temp.cleanup()

    def worker_fixture(self,command,**kwargs):
        self.calls+=1
        request=m.read_json(command[-1]);folder=Path(request['output']);folder.mkdir()
        stems={}
        for name,hz in [('speech',1500),('music',240),('sfx',900)]:
            path=folder/(name+'.wav');fixtures.tone(path,1.,hz)
            stems[name]={'file':str(path),'sha256':m.sha(path),'rms_dbfs':-29.8,'peak_dbfs':-26.8}
        m.save_json(folder/'receipt.json',{'signature':request['signature'],'source_sha256':request['source_sha256'],'stems':stems})

    def work(self,name):
        path=self.base/name;path.mkdir();return path

    def test_same_source_separates_once_across_market_variants_and_invalidates_changed_stem(self):
        with patch.object(m,'separation_signature',return_value='fixture-signature'),patch.object(m,'run',side_effect=self.worker_fixture):
            first=m.bandit_background(self.asset,self.job,self.base,self.work('malay'))
            second=m.bandit_background(self.asset,self.job,self.base,self.work('english'))
            self.assertEqual(self.calls,1);self.assertEqual(first,second)
            self.assertTrue(m.read_json(self.base/'english/separation-reference.json')['reused'])
            prior=Path(first['stems']['music']['file']);prior.write_bytes(prior.read_bytes()+b'corruption')
            repaired=m.bandit_background(self.asset,self.job,self.base,self.work('retry'))
            self.assertEqual(self.calls,2);self.assertNotEqual(first['receipt'],repaired['receipt']);self.assertTrue(prior.exists())

    def test_failed_separator_not_retried_for_each_language(self):
        with patch.object(m,'separation_signature',return_value='fixture-signature'),patch.object(m,'run',side_effect=m.Blocked('fixture failure')) as worker:
            for language in ('malay','english'):
                with self.assertRaises(m.Blocked):m.bandit_background(self.asset,self.job,self.base,self.work(language))
            self.assertEqual(worker.call_count,1)

    def test_missing_model_blocks_without_download_or_silent_demucs(self):
        with self.assertRaises(m.Blocked):m.separation_signature({'engine':'bandit','repo':str(self.base),'checkpoint':str(self.base/'missing.ckpt')})

    def test_actual_mix_contains_music_effects_new_voice_but_excludes_original_dialogue_marker(self):
        folder=self.work('mix');stems={}
        for name,hz in [('speech',1500),('music',240),('sfx',900)]:
            p=self.base/(name+'.wav');fixtures.tone(p,1.,hz)
            stems[name]={'file':str(p),'sha256':m.sha(p),'rms_dbfs':-29.8,'peak_dbfs':-26.8}
        voice=self.base/'female-fixture-tone.wav';fixtures.tone(voice,1.,660)
        mixed=m.mix_bandit(folder,{'stems':stems,'receipt':'fixture-receipt'},voice,1.,{'music_gain_db':0,'sfx_gain_db':0})
        pcm=folder/'inspect.wav';m.run(['ffmpeg','-v','error','-n','-i',mixed,'-ac','1','-ar','24000','-c:a','pcm_s16le',pcm])
        with wave.open(str(pcm),'rb') as f:values=array.array('h',f.readframes(f.getnframes()))
        def magnitude(hz):
            re=sum(v*math.cos(2*math.pi*hz*i/24000) for i,v in enumerate(values))
            im=sum(v*math.sin(2*math.pi*hz*i/24000) for i,v in enumerate(values))
            return math.hypot(re,im)/len(values)
        for hz in (240,660,900):self.assertGreater(magnitude(hz),100)
        self.assertLess(magnitude(1500),5)
        self.assertLess(max(abs(v) for v in values),32767)
        receipt=m.read_json(folder/'mix-receipt.json')
        self.assertEqual(set(receipt['inputs']),{'music','sfx'})
        self.assertFalse(receipt['original_dialogue_in_mix'])

    def test_packaged_female_reference_resolves_and_is_integrity_bound(self):
        cfg={'engine':'chatterbox','voice_type':'female','synthetic_voice_reference':{'preset':'serena-female-v1'}}
        resolved=m.voice_settings({},self.asset,'ms-MY',cfg,self.base)
        self.assertEqual(m.sha(resolved['synthetic_voice_reference']['path']),resolved['synthetic_voice_reference']['sha256'])
