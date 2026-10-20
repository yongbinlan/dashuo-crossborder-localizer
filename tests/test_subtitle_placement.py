"""Regression for adjacent bilingual placement, including actual glyph pixels."""
from pathlib import Path
import shutil
import tempfile
import unittest
import test_localizer as fixtures

m=fixtures.m


class AdjacentPlacementTests(unittest.TestCase):
    def test_default_tracks_source_center_and_has_small_gap(self):
        hard,region=m.subtitle_layout({'hard_subtitles':'keep',
            'source_subtitle_region':[.2,.77,.6,.05]},'ms-MY')
        x,y,w,h=region
        self.assertEqual(hard,'keep')
        self.assertAlmostEqual(x+w/2,.5)
        self.assertAlmostEqual(.77-(y+h),.008)
        self.assertAlmostEqual(w,.6)

    @unittest.skipUnless(shutil.which('ffmpeg'),'requires FFmpeg')
    def test_actual_single_and_double_line_bottom_edges_stay_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            _,region=m.subtitle_layout({'hard_subtitles':'keep',
                'source_subtitle_region':[.1,.77,.8,.05]},'ms-MY')
            boxes=[]
            for name,text in [('single','Study together.'),('double','Study together.\nStay warm together.')]:
                m.write_ass(root/'captions.ass',[{'start':0,'end':1,'text':text}],320,480,
                            {'font_size':20},region,bottom_aligned=True)
                pixels=m.subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',
                    'color=black:s=320x480:d=1','-vf','ass=captions.ass','-frames:v','1',
                    '-f','rawvideo','-pix_fmt','gray','-'],cwd=root,check=True,
                    capture_output=True).stdout
                points=[(i%320,i//320) for i,v in enumerate(pixels) if v>160]
                self.assertTrue(points)
                xs,ys=zip(*points)
                boxes.append((min(xs),min(ys),max(xs),max(ys)))
            single,double=boxes
            self.assertLessEqual(abs(single[3]-double[3]),2)
            self.assertLess(double[1],single[1]-12)
            for left,top,right,bottom in boxes:
                self.assertLess(abs((left+right)/2-160),3)
                self.assertLess(bottom,480*.77)
                self.assertLess(480*.77-bottom,20)

    def test_chinese_target_keeps_no_duplicate_layer(self):
        self.assertEqual(m.subtitle_layout({'hard_subtitles':'keep',
            'source_subtitle_region':[.1,.77,.8,.05]},'zh-SG'),('keep',None))


if __name__=='__main__':unittest.main()
