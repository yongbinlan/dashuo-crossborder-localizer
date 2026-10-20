import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
import math
import struct

MODULE=Path(__file__).resolve().parents[1]/"scripts/localize.py"
spec=importlib.util.spec_from_file_location("dashuo_localizer",MODULE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def tone(path,seconds=0.45,hz=660):
    with wave.open(str(path),"wb") as f:
        f.setparams((1,2,24000,0,"NONE","not compressed"))
        f.writeframes(b"".join(struct.pack("<h",int(1500*math.sin(2*math.pi*hz*i/24000)))
                               for i in range(round(seconds*24000))))


class SubtitleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.srt=self.root/"中文.srt"
        m.write_srt(self.srt,[{"id":1,"start":0,"end":2,"text":"100元，容量2L，ABC"}])
        self.cues=m.parse_srt(self.srt)
        self.record={"source_srt_sha256":m.sha(self.srt),"target":"en-SG","reviewed":True,
                     "reviewer":"unit-test-fixture","items":[{"id":1,"source":self.cues[0]["text"],"text":"100 yuan, 2L, ABC"}]}

    def tearDown(self): self.temp.cleanup()

    def test_preserves_non_ascii_source_and_timing(self):
        result=m.validate_translation(self.record,self.cues,self.srt,"en-SG",["ABC"])
        self.assertEqual(result[0]["start"],0);self.assertEqual(result[0]["end"],2)

    def test_cjk_attached_number_loss_blocks(self):
        self.record["items"][0]["text"]="Cheap box, ABC"
        with self.assertRaises(m.Blocked):m.validate_translation(self.record,self.cues,self.srt,"en-SG",[])

    def test_source_change_blocks(self):
        self.srt.write_text(self.srt.read_text(encoding="utf-8").replace("2L","3L"),encoding="utf-8")
        with self.assertRaises(m.Blocked):m.validate_translation(self.record,self.cues,self.srt,"en-SG",[])

    def test_wrong_target_blocks(self):
        with self.assertRaises(m.Blocked):m.validate_translation(self.record,self.cues,self.srt,"ms-MY",[])

    def test_brand_loss_blocks(self):
        self.record["items"][0]["text"]="100 yuan, 2L"
        with self.assertRaises(m.Blocked):m.validate_translation(self.record,self.cues,self.srt,"en-SG",["ABC"])

    def test_unreviewed_translation_blocks(self):
        self.record["reviewed"]=False
        with self.assertRaises(m.Blocked):m.validate_translation(self.record,self.cues,self.srt,"en-SG",[])

    def test_extra_cue_blocks(self):
        self.record["items"].append(copy.deepcopy(self.record["items"][0]))
        with self.assertRaises(m.Blocked):m.validate_translation(self.record,self.cues,self.srt,"en-SG",[])

    def test_overlapping_source_blocks(self):
        m.write_srt(self.srt,[{"start":0,"end":2,"text":"一"},{"start":1,"end":3,"text":"二"}])
        with self.assertRaises(m.Blocked):m.parse_srt(self.srt)

    def test_ass_text_cannot_inject_override(self):
        self.assertNotIn("{",m.ass_escape(r"{\pos(10,10)}text"))

    def test_malay_never_uses_qwen_or_indonesian(self):
        with self.assertRaises(m.Blocked):m.tts_settings({"providers":{"tts":{"engine":"qwen"}}},"ms-MY")

    def test_remote_tts_needs_consent(self):
        with self.assertRaises(m.Blocked):m.tts_settings({"providers":{"tts":{"engine":"edge"}}},"en-SG")

    def test_job_lock_prevents_second_executor(self):
        path=self.root/"job.json";path.write_text("{}")
        with m.lock_job(path):
            with self.assertRaises(m.Blocked):
                with m.lock_job(path):pass
        self.assertFalse(path.with_suffix(".lock").exists())

    def test_local_model_path_is_mandatory(self):
        with self.assertRaises(m.Blocked):m.tts_settings({"providers":{"tts":{"engine":"chatterbox"}}},"ms-MY")

    def test_keep_subtitles_requires_known_position(self):
        with self.assertRaises(m.Blocked):m.subtitle_layout({"hard_subtitles":"keep"},"en-SG")

    def test_keep_subtitles_refuses_overlapping_translation(self):
        with self.assertRaises(m.Blocked):m.subtitle_layout({"hard_subtitles":"keep","source_subtitle_region":[.1,.8,.8,.1],"target_subtitle_region":[.1,.82,.8,.1]},"en-SG")

    def test_bilingual_retains_complete_chinese_and_translation(self):
        target=[{"id":1,"start":0,"end":2,"text":"Look at the box."}]
        source=[{"id":1,"start":0,"end":2,"text":"看看这个盒子。"}]
        result=m.display_cues(target,source,"en-SG","none","bilingual")
        self.assertEqual(result[0]["text"],"Look at the box.\n看看这个盒子。")
        self.assertEqual(m.display_cues(target,source,"en-SG","keep","bilingual"),target)
        self.assertEqual(m.display_cues(source,source,"zh-SG","keep","bilingual"),[])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"requires FFmpeg")
class MediaFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/"原片 空格";self.source.mkdir()
        self.video=self.source/"演示.mp4"
        m.run(["ffmpeg","-v","error","-n","-f","lavfi","-i","color=c=0x234152:s=320x480:r=12:d=3",
               "-f","lavfi","-i","sine=frequency=300:duration=3","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",self.video])
        self.srt=self.video.with_suffix(".srt")
        m.write_srt(self.srt,[{"start":.5,"end":2.5,"text":"看看这个盒子。"}])
        self.jobdir=self.root/"输出 空格"
        class Args:pass
        a=Args();a.input=str(self.source);a.output=str(self.jobdir);a.targets="en-SG,zh-SG,ms-MY,en-MY,ja-JP,en-US"
        a.no_runtime=True  # Deterministic model-free fixtures must not load machine overrides.
        m.initialize(a)
        self.path,self.job=m.load_job(self.jobdir/"job.json")
        audio=self.root/"人工配音";audio.mkdir();tone(audio/"00001.wav")
        bg=self.root/"background.wav";tone(bg,3,240)
        self.job["providers"]["tts"]={"engine":"provided","audio_dir":str(audio)}
        # Fixture is a synthetic tone, not an unreviewed human source voice.
        self.job['require_voice_type_review']=False
        self.job["assets"][0]["hard_subtitles"]="none";self.job["assets"][0]["background"]=str(bg)
        m.save_json(self.path,self.job)
        m.build_requests(self.path,self.job)
        text={"en-SG":"Look at this box.","zh-SG":"看看这个盒子。","ms-MY":"Lihat kotak ini.",
              "en-MY":"Look at this box.","ja-JP":"この箱を見て。","en-US":"Look at this box."}
        for target in self.job["targets"]:
            p=m.request_path(self.path,self.job["assets"][0],target);r=m.read_json(p)
            r["items"][0]["text"]=text[target];r["reviewed"]=True;r["reviewer"]="synthetic-test-fixture-not-native-review"
            m.save_json(p,r)

    def tearDown(self): self.temp.cleanup()

    def test_six_market_variants_render_and_resume_without_fake_approval(self):
        result=m.execute(self.path,self.job)
        self.assertEqual(result["counts"],{"verification_incomplete":6},result)
        for item in result["results"]:
            receipt=m.read_json(item["receipt"])
            self.assertFalse(receipt["publication_authorized"])
            self.assertTrue(receipt["technical_checks_passed"])
            self.assertEqual(receipt["status"],"verification_incomplete")
        resumed=m.execute(self.path,self.job)
        self.assertEqual(resumed["counts"],{"reused_checked_bytes":6})

    def test_missing_hardsub_decision_blocks(self):
        self.job["assets"][0]["hard_subtitles"]="unknown"
        result=m.execute(self.path,self.job)
        self.assertEqual(result["counts"],{"blocked":6})

    def test_missing_background_never_mixes_back_chinese(self):
        self.job["assets"][0]["background"]=None
        result=m.execute(self.path,self.job)
        self.assertEqual(result["counts"],{"blocked":6})
        self.assertFalse(list(self.jobdir.rglob("localized.mp4")))

    def test_new_translation_preserves_previous_video(self):
        self.job["targets"]=["en-SG"]
        first=m.execute(self.path,self.job)["results"][0]
        old=Path(first["receipt"]).parent/"localized.mp4";original_sha=m.sha(old)
        p=m.request_path(self.path,self.job["assets"][0],"en-SG");r=m.read_json(p)
        r["items"][0]["text"]="Have a look.";m.save_json(p,r)
        second=m.execute(self.path,self.job)["results"][0]
        self.assertNotEqual(first["receipt"],second["receipt"]);self.assertEqual(m.sha(old),original_sha)

    def test_too_long_audio_blocks_instead_of_truncating(self):
        self.job["targets"]=["en-SG"]
        directory=Path(self.job["providers"]["tts"]["audio_dir"]);(directory/"00001.wav").unlink();tone(directory/"00001.wav",3.5)
        result=m.execute(self.path,self.job)
        self.assertEqual(result["counts"],{"blocked":1})
        self.assertFalse(list(self.jobdir.rglob("localized.mp4")))

    def test_following_silence_is_used_without_moving_next_cue(self):
        clip=self.root/"long.wav";tone(clip,2.1)
        cues=[{"id":1,"start":0,"end":1,"text":"A"},{"id":2,"start":2.7,"end":3,"text":"B"}]
        last=self.root/"short.wav";tone(last,.1)
        extended=m.use_existing_silence(cues,[clip,last],3,True)
        self.assertGreater(extended[0]["end"],2)
        self.assertEqual(extended[1]["start"],2.7)
        self.assertLess(extended[0]["end"],extended[1]["start"])
        self.assertEqual(cues[0]["end"],1)
        locked=m.use_existing_silence(cues,[clip,last],3,False)
        self.assertEqual(locked,cues)

    def test_incomplete_actual_review_cannot_mark_pending_user_review(self):
        self.job["targets"]=["en-SG"]
        item=m.execute(self.path,self.job)["results"][0]
        evidence=self.root/"fake-review.json";m.save_json(evidence,{"visual":True})
        class Args:pass
        args=Args();args.variant=str(Path(item["receipt"]).parent);args.evidence=str(evidence)
        with self.assertRaises(m.Blocked):m.record_review(args)
        self.assertEqual(m.read_json(item["receipt"])["status"],"verification_incomplete")

    def test_path_like_asset_id_is_rejected(self):
        self.job["assets"][0]["id"]="../escape";m.save_json(self.path,self.job)
        with self.assertRaises(m.Blocked):m.load_job(self.path)

    def test_identical_basename_in_different_directories_has_unique_id(self):
        other=self.source/"第二份";other.mkdir();shutil.copy2(self.video,other/self.video.name)
        class Args:pass
        a=Args();a.input=str(self.source);a.output=str(self.root/"另一个任务");a.targets="ja-JP"
        m.initialize(a);_,job=m.load_job(Path(a.output)/"job.json")
        self.assertEqual(len({x["id"] for x in job["assets"]}),2)

    def test_supplied_audio_edit_invalidates_reuse(self):
        self.job["targets"]=["en-SG"]
        first=m.execute(self.path,self.job)["results"][0]
        p=Path(self.job["providers"]["tts"]["audio_dir"])/"00001.wav";p.unlink();tone(p,.5,880)
        second=m.execute(self.path,self.job)["results"][0]
        self.assertNotEqual(first["receipt"],second["receipt"])

    def test_one_failed_video_does_not_block_the_next_video(self):
        self.job["targets"]=["en-SG"]
        second=copy.deepcopy(self.job["assets"][0]);second["id"]+="-second"
        self.job["assets"].append(second)
        m.build_requests(self.path,self.job)
        r=m.read_json(m.request_path(self.path,self.job["assets"][0],"en-SG"))
        m.save_json(m.request_path(self.path,second,"en-SG"),r)
        self.job["assets"][0]["background"]=None
        result=m.execute(self.path,self.job)
        self.assertEqual(result["counts"],{"blocked":1,"verification_incomplete":1})
        self.assertEqual(result["results"][1]["asset"],second["id"])

    def test_silent_voice_is_not_counted_as_success(self):
        self.job["targets"]=["en-SG"]
        path=Path(self.job["providers"]["tts"]["audio_dir"])/"00001.wav"
        with wave.open(str(path),"wb") as f:
            f.setparams((1,2,24000,0,"NONE","not compressed"));f.writeframes(bytes(24000))
        result=m.execute(self.path,self.job)
        self.assertEqual(result["counts"],{"blocked":1})
        self.assertIn("静音",result["results"][0]["reason"])


if __name__=="__main__":unittest.main(verbosity=2)
