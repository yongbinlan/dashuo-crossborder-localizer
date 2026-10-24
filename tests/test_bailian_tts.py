import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave
import test_localizer as fixtures

m=fixtures.m
spec=importlib.util.spec_from_file_location('bailian_test',m.ROOT/'scripts/bailian_tts.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)


class BailianTests(unittest.TestCase):
    def cfg(self):return {'engine':'bailian','cloud_allowed':True,'paid_tts_allowed':True}

    def audio(self):
        stream=io.BytesIO()
        with wave.open(stream,'wb') as out:
            out.setparams((1,2,24000,0,'NONE','not compressed'))
            out.writeframes(b'\x80\x10'*6000)
        return stream.getvalue()

    def test_paid_permission_is_separate_from_text_transfer_and_translation(self):
        job={'providers':{'tts':{'engine':'bailian'}},'online_tts_allowed':True}
        with self.assertRaises(m.Blocked):m.tts_settings(job,'th-TH')
        job['paid_tts_allowed']=True
        self.assertTrue(m.tts_settings(job,'th-TH')['paid_tts_allowed'])
        self.assertNotIn('paid_calls_allowed',job)
        job['online_tts_allowed']=False
        with self.assertRaises(m.Blocked):m.tts_settings(job,'th-TH')

    def test_target_voice_and_gender_cannot_silently_change(self):
        for changed in ({'model':'sambert-zhigui-v1'},{'voice_type':'male'},{'voice':'unknown'}):
            with self.assertRaises(b.BailianError):
                b.settings({**self.cfg(),**changed},m.PROFILES['th-TH'])
        with self.assertRaises(b.BailianError):b.settings(self.cfg(),m.PROFILES['ja-JP'])

    def test_no_duplicate_paid_call_across_render_versions(self):
        calls=[]
        def transport(text,cfg,key):
            calls.append(text)
            return self.audio(),{'request_id':'fixture-id','usage':{'characters':len(text)}}
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DASHSCOPE_API_KEY':'fixture-secret'}):
            root=Path(tmp);cues=[{'id':1,'text':'sample'}]
            for version in ('v1','v2'):
                b.generate(cues,self.cfg(),m.PROFILES['th-TH'],root/version/'speech',root/'cache',transport)
            self.assertEqual(len(calls),1)
            receipt=json.loads((root/'v2/tts-usage.json').read_text())
            self.assertTrue(receipt['items'][0]['reused'])
            self.assertNotIn('fixture-secret',json.dumps(receipt))
            self.assertIsNone(receipt['account_remaining_quota'])

    def test_unknown_result_blocks_even_in_a_new_render_folder(self):
        calls=[]
        def fail(*args):calls.append(1);raise TimeoutError('fixture-secret')
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'DASHSCOPE_API_KEY':'fixture-secret'}):
            root=Path(tmp);cues=[{'id':1,'text':'sample'}]
            for version in ('v1','v2'):
                with self.assertRaises(b.BailianError):
                    b.generate(cues,self.cfg(),m.PROFILES['id-ID'],root/version/'speech',root/'cache',fail)
            self.assertEqual(len(calls),1)
            state=next((root/'cache').glob('*.json')).read_text()
            self.assertNotIn('fixture-secret',state)

    def test_minimax_uses_bailian_and_explicit_filipino_language(self):
        class Reply:
            def __enter__(self):return io.BytesIO(json.dumps({'output':{'base_resp':{'status_code':0},'data':{'audio':'0102'}},'usage':{'characters':4}}).encode())
            def __exit__(self,*args):pass
        class Opener:
            def open(self,request,timeout):
                self.request=request
                return Reply()
        opener=Opener()
        with patch.object(b.urllib.request,'build_opener',return_value=opener):
            audio,receipt=b.minimax('test',b.settings(self.cfg(),m.PROFILES['fil-PH']),'fixture')
        payload=json.loads(opener.request.data)
        self.assertEqual(payload['input']['action'],'tts')
        self.assertEqual(payload['input']['language_boost'],'Filipino')
        self.assertEqual(opener.request.host,'dashscope.aliyuncs.com')
        self.assertEqual(audio,b'\x01\x02')
        self.assertEqual(receipt['usage']['characters'],4)
