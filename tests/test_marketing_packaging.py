import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
sp=importlib.util.spec_from_file_location('pack',ROOT/'scripts/marketing_packaging.py')
pack=importlib.util.module_from_spec(sp);sp.loader.exec_module(pack)


class MarketingPackagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        from PIL import ImageFont
        candidates=[Path(x) for x in [os.environ.get('DASHUO_TEST_FONT',''),
          'C:/Windows/Fonts/segoeuib.ttf','/System/Library/Fonts/Supplemental/Arial Bold.ttf',
          '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'] if x]
        font=next((x for x in candidates if x.is_file()),None)
        if font is None:self.skipTest('Set DASHUO_TEST_FONT to an installed TTF/OTF for packaging tests')
        family=ImageFont.truetype(str(font),40).getname()[0]
        self.plan={'schema_version':1,'target_language':'ms-MY','canvas':[1080,1920],
          'semantic_review':{'reviewed':True,'reviewer':'unit fixture'},
          'style':{'font_family':family,'font_file':str(font),'ink':'#452D29','paper':'#FFF6E7','accent':'#F7BF6A'},
          'subtitle_region':[0,1660,1080,260],
          'protected_regions':[{'start':0,'end':3,'rect':[300,600,600,900],'label':'product'}],
          'events':[{'id':'test','kind':'callout','start':.2,'end':2.5,'rect':[64,100,660,184],
                     'font_size':64,'eyebrow':'PILIHAN SAIZ','text':'Seisi keluarga',
                     'source_quote':'一家老小的尺码都有','cue_ids':[1]}]}

    def tearDown(self):self.tmp.cleanup()

    def compile(self):return pack.compile_plan(self.plan,1080,1920,3,self.root/'marketing.ass')

    def test_reviewed_local_plan_compiles_separate_overlay_not_spoken_subtitles(self):
        receipt=self.compile()
        self.assertTrue(receipt['layout_checks_passed'])
        self.assertEqual(len(receipt['events']),1)
        self.assertIn('Seisi keluarga',(self.root/'marketing.ass').read_text(encoding='utf-8-sig'))
        self.assertFalse((self.root/'captions.ass').exists())

    def test_unreviewed_copy_is_rejected_even_for_truthy_string(self):
        self.plan['semantic_review']['reviewed']='false'
        with self.assertRaisesRegex(pack.PackagingError,'semantic review'):self.compile()

    def test_product_and_subtitle_overlap_are_blocked(self):
        self.plan['events'][0]['rect'][1]=650
        with self.assertRaisesRegex(pack.PackagingError,'protected'):self.compile()
        self.plan['events'][0]['rect'][1]=1650
        with self.assertRaisesRegex(pack.PackagingError,'subtitle'):self.compile()

    def test_offscreen_animation_is_rejected(self):
        self.plan['events'][0]['rect'][0]=0
        with self.assertRaisesRegex(pack.PackagingError,'safe canvas'):self.compile()

    def test_overlong_translation_is_not_silently_truncated(self):
        self.plan['events'][0]['text']='A long translation '*20
        with self.assertRaisesRegex(pack.PackagingError,'rewrapping'):self.compile()

    def test_missing_glyph_or_font_cannot_silently_fallback(self):
        self.plan['events'][0]['text']='\U0010ffff'
        with self.assertRaisesRegex(pack.PackagingError,'lacks glyphs'):self.compile()
        self.plan['style']['font_file']=str(self.root/'missing.ttf')
        with self.assertRaisesRegex(pack.PackagingError,'Font file missing'):self.compile()

    def test_wrong_font_identity_is_rejected(self):
        self.plan['style']['font_family']='Uninstalled font'
        with self.assertRaisesRegex(pack.PackagingError,'does not match'):self.compile()

    def test_simultaneous_label_collision_is_blocked(self):
        other=copy.deepcopy(self.plan['events'][0]);other['id']='other'
        self.plan['events'].append(other)
        with self.assertRaisesRegex(pack.PackagingError,'overlap'):self.compile()

    def test_off_timeline_event_is_rejected(self):
        self.plan['events'][0]['end']=3.5
        with self.assertRaisesRegex(pack.PackagingError,'duration'):self.compile()

    def test_emphasis_keyword_and_pop_compile_without_changing_copy(self):
        e=self.plan['events'][0];e.update({'kind':'emphasis','highlight':'keluarga','angle':-1,'eyebrow':''})
        audit=self.compile()
        self.assertEqual(audit['events'][0]['text'],'Seisi keluarga')
        ass=(self.root/'marketing.ass').read_text(encoding='utf-8-sig')
        self.assertIn(r'\fscx103',ass)
        self.assertIn('keluarga',ass)

    def test_emphasis_invalid_keyword_is_blocked(self):
        self.plan['events'][0].update({'kind':'emphasis','highlight':'invented','eyebrow':''})
        with self.assertRaisesRegex(pack.PackagingError,'actual keyword'):self.compile()

    def test_emphasis_rotation_and_outline_envelope_are_checked(self):
        e=self.plan['events'][0];e.update({'kind':'emphasis','angle':9,'eyebrow':''})
        with self.assertRaisesRegex(pack.PackagingError,'angle'):self.compile()
        e['angle']=0;e['rect'][0]=32
        with self.assertRaisesRegex(pack.PackagingError,'safe canvas'):self.compile()

    def test_changed_source_is_blocked_before_output_creation(self):
        p=self.root/'plan.json';p.write_text(json.dumps({**self.plan,'source_sha256':'stale'}),encoding='utf-8')
        source=self.root/'source.mp4';source.write_bytes(b'new input')
        output=self.root/'render'
        args=SimpleNamespace(manifest=str(p),source=str(source),captions=str(source),audio_reference=str(source),output_directory=str(output))
        with self.assertRaisesRegex(pack.PackagingError,'Stale input'):pack.render(args)
        self.assertFalse(output.exists())


if __name__=='__main__':unittest.main()
