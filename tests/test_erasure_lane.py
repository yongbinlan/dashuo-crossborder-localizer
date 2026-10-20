from pathlib import Path
import importlib.util,shutil,sys,tempfile,unittest
from unittest.mock import patch
import test_localizer as fixtures

spec=importlib.util.spec_from_file_location('erase_runner',fixtures.MODULE.with_name('erase_subtitles.py'))
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)


class ErasureLaneTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.video=self.base/'input.mp4'
        e.m.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','color=c=0x234152:s=320x480:r=30:d=1',
                 '-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',self.video])
        model=self.base/'model.onnx';model.write_bytes(b'fixture-model; real MI-GAN tested separately')
        self.job={'schema_version':1,'mode':'erase_subtitles','providers':{'erasure':{'engine':'migan','python':sys.executable,
          'model':str(model),'model_sha256':e.m.sha(model)}},'assets':[{'id':'fixture','video':str(self.video),'source_sha256':e.m.sha(self.video),
          'erasure':{'regions':[[.1,.75,.8,.06]],'region_reviewed':True,'review_note':'synthetic no-text fixture','expected_subtitles':False}}]}
        self.path=self.base/'job.json';e.m.save_json(self.path,self.job);self.calls=0;self.real_run=e.m.run

    def tearDown(self):self.temp.cleanup()

    def fake_worker(self,command,**kwargs):
        if len(command)>1 and Path(str(command[1])).name=='subtitle_erase_worker.py':
            self.calls+=1;r=e.m.read_json(command[-1]);folder=Path(r['output']);folder.mkdir()
            output=folder/'silent.mp4';self.real_run(['ffmpeg','-v','error','-n','-i',r['source'],'-map','0:v:0','-c:v','copy','-an',output])
            e.m.save_json(folder/'frame-metrics.json',[])
            e.m.save_json(folder/'worker-receipt.json',{'source_sha256':r['source_sha256'],'model_sha256':r['config']['model_sha256'],
              'worker_sha256':e.m.sha(e.ROOT/'scripts/subtitle_erase_worker.py'),'request_sha256':e.m.sha(command[-1]),
              'outside_mask_changed_frames_before_encode':0,'all_frames_processed':True,'frames':30,'processed_frames':0,
              'video':str(output),'samples':[0,15,29],'reused_repair_frames':0})
            return ''
        return self.real_run(command,**kwargs)

    def test_no_translation_tts_or_separation_needed_and_audio_preserved_and_reused(self):
        with patch.object(e.m,'run',side_effect=self.fake_worker):
            result=e.execute(self.path);again=e.execute(self.path)
        self.assertEqual(result['counts'],{'verification_incomplete':1},result)
        self.assertEqual(again['counts'],{'reused_checked_bytes':1});self.assertEqual(self.calls,1)
        folder=Path(result['results'][0]['receipt']).parent
        self.assertEqual(e.m.audio_identity(self.video),e.m.audio_identity(folder/'clean.mp4'))
        self.assertFalse((folder/'dub.wav').exists());self.assertFalse((folder/'translated.srt').exists())

    def test_unreviewed_region_blocks_before_worker(self):
        self.job['assets'][0]['erasure']['region_reviewed']=False;e.m.save_json(self.path,self.job)
        with patch.object(e.m,'run',side_effect=self.fake_worker):result=e.execute(self.path)
        self.assertEqual(result['counts'],{'blocked':1});self.assertEqual(self.calls,0)

    def test_model_tamper_blocks_without_fallback(self):
        Path(self.job['providers']['erasure']['model']).write_bytes(b'changed')
        with patch.object(e.m,'run',side_effect=self.fake_worker):result=e.execute(self.path)
        self.assertEqual(result['counts'],{'blocked':1});self.assertEqual(self.calls,0)

    def test_source_tamper_does_not_reuse_prior_erasure(self):
        with patch.object(e.m,'run',side_effect=self.fake_worker):e.execute(self.path)
        self.video.write_bytes(self.video.read_bytes()+b'changed')
        with patch.object(e.m,'run',side_effect=self.fake_worker):result=e.execute(self.path)
        self.assertEqual(result['counts'],{'blocked':1});self.assertEqual(self.calls,1)

    def test_one_bad_asset_does_not_stop_next_asset(self):
        import copy
        bad=copy.deepcopy(self.job['assets'][0]);bad['id']='bad';bad['source_sha256']='wrong'
        self.job['assets'].insert(0,bad);e.m.save_json(self.path,self.job)
        with patch.object(e.m,'run',side_effect=self.fake_worker):result=e.execute(self.path)
        self.assertEqual(result['counts'],{'blocked':1,'verification_incomplete':1});self.assertEqual(self.calls,1)

    def test_check_only_never_runs_model_or_writes_render_state(self):
        with patch.object(e.m,'run',side_effect=self.fake_worker):result=e.execute(self.path,check_only=True)
        self.assertEqual(result['counts'],{'configuration_present':1});self.assertEqual(self.calls,0)
        self.assertFalse((self.base/'erasures').exists());self.assertFalse((self.base/'erasure-batch-receipt.json').exists())

    def test_erasure_review_cannot_skip_temporal_or_protected_detail_checks(self):
        with patch.object(e.m,'run',side_effect=self.fake_worker):result=e.execute(self.path)
        folder=Path(result['results'][0]['receipt']).parent
        receipt=e.m.read_json(folder/'receipt.json')
        evidence=self.base/'review.json';e.m.save_json(evidence,{'visual':True,'subtitle_erasure':True,
          'original_audio_preserved':True,'video_sha256':receipt['artifact_sha256']['clean.mp4'],'reviewer':'test','notes':'incomplete'})
        class Args:pass
        a=Args();a.variant=str(folder);a.evidence=str(evidence)
        with self.assertRaises(e.m.Blocked):e.m.record_review(a)


@unittest.skipUnless(importlib.util.find_spec('cv2'),'requires OpenCV for actual mask tests')
class ErasureMaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import cv2,numpy
        cls.cv=cv2;cls.np=numpy
        spec=importlib.util.spec_from_file_location('erase_worker',fixtures.MODULE.with_name('subtitle_erase_worker.py'))
        cls.w=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.w)

    def test_zero_mask_is_exact_passthrough(self):
        frame=self.np.zeros((100,100,3),self.np.uint8);mask=self.np.zeros((100,100),self.np.uint8)
        self.assertIs(self.w.repair_frame(frame,mask,None),frame)

    def test_unmasked_pixels_preserved_even_if_model_changes_entire_context(self):
        np=self.np;frame=np.full((700,700,3),90,np.uint8);mask=np.zeros((700,700),np.uint8);mask[330:350,330:370]=255
        class FakeModel:
            def run(self,_,feeds):return [np.full_like(feeds['image'],200)]
        result=self.w.repair_frame(frame,mask,FakeModel())
        self.assertTrue(np.array_equal(result[mask==0],frame[mask==0]));self.assertTrue(np.any(result[mask>0]!=frame[mask>0]))

    def test_centered_single_chinese_glyph_not_missed(self):
        from PIL import Image,ImageDraw,ImageFont
        font=Path('C:/Windows/Fonts/msyh.ttc')
        if not font.exists():self.skipTest('needs actual Chinese test font')
        image=Image.new('RGB',(1080,1920),(110,80,50));draw=ImageDraw.Draw(image)
        draw.text((516,1468),'嗯',font=ImageFont.truetype(str(font),44),fill='white',stroke_fill='black',stroke_width=4)
        mask=self.w.detect_mask(self.np.asarray(image)[:,:,::-1],[.12,.755,.76,.055])
        self.assertGreater(int((mask>0).sum()),500)
        self.assertEqual(int((mask[:1440]>0).sum()),0)

    def test_isolated_gap_requires_matching_neighbors_and_visible_ink(self):
        np=self.np;frame=np.zeros((100,100,3),np.uint8);frame[40:50,30:70]=255
        neighbor=np.zeros((100,100),np.uint8);neighbor[35:55,25:75]=255
        empty=np.zeros_like(neighbor)
        recovered=self.w.isolated_gap_mask(frame,empty,neighbor,neighbor)
        self.assertTrue(np.array_equal(recovered,neighbor))
        self.assertFalse(np.any(self.w.isolated_gap_mask(np.full_like(frame,100),empty,neighbor,neighbor)))
        different=np.zeros_like(neighbor);different[5:15,5:15]=255
        self.assertFalse(np.any(self.w.isolated_gap_mask(frame,empty,neighbor,different)))
