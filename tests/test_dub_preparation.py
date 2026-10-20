import array
from pathlib import Path
import tempfile
import unittest
import wave
import test_localizer as fixtures

m=fixtures.m


class SpeechEdgePreparationTests(unittest.TestCase):
    def test_preserves_quiet_speech_and_internal_pause_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'00001.wav'
            # Quiet speech well below the old -55dB threshold, with an internal pause.
            values=array.array('h',[0]*6000+[40,-40]*1200+[0]*3000+[40,-40]*1200+[0]*6000)
            with wave.open(str(raw),'wb') as f:
                f.setparams((1,2,24000,0,'NONE','not compressed'));f.writeframes(values.tobytes())
            digest=m.sha(raw)
            path=m.prepare_speech_edges(root,[raw])[0]
            with wave.open(str(path),'rb') as f:data=f.readframes(f.getnframes())
            first=6000-2880;last=len(values)-6000+2880
            self.assertEqual(data,values[first:last].tobytes())
            self.assertEqual(m.sha(raw),digest)
            self.assertGreater(m.duration(path),.5)

    def test_silent_speech_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'00001.wav'
            with wave.open(str(raw),'wb') as f:
                f.setparams((1,2,24000,0,'NONE','not compressed'));f.writeframes(b'\0'*48000)
            with self.assertRaises(m.Blocked):m.prepare_speech_edges(root,[raw])

    def test_fitting_logs_real_timeline_and_does_not_extend_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'00001.wav';fixtures.tone(raw,.5)
            m.assemble_audio(root,[{'id':1,'start':.1,'end':.8}],[raw],1.,1.25)
            timing=m.read_json(root/'timing.json')[0]
            self.assertEqual(timing['start'],.1)
            self.assertLessEqual(timing['speech_end'],timing['subtitle_end'])
            self.assertAlmostEqual(m.duration(root/'dub.wav'),1.)
