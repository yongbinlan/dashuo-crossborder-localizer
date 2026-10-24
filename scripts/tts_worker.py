#!/usr/bin/env python3
"""Local TTS; optional synthetic-preset conditioning, no implicit human cloning."""
import asyncio
import json
import os
import math
import hashlib
from pathlib import Path
import sys


def local_chatterbox_tokenizer(cfg,language):
    """Avoid upstream 0.1.7 eagerly downloading unused Chinese resources for Malay.

    Its encode() calls the Cangjie converter only for language_id == 'zh'.
    Preserve all non-Chinese tokenization; fail if an unexpected call occurs.
    Chinese requires an explicit local segmentation model, not a silent fallback.
    """
    from chatterbox.models.tokenizers import tokenizer as tokenizers
    original=tokenizers.ChineseCangjieConverter
    if language!="zh":
        class UnusedChineseConverter:
            def __init__(self,*args,**kwargs):pass
            def __call__(self,text):
                raise RuntimeError("Unexpected Chinese normalization for a non-Chinese voice")
        tokenizers.ChineseCangjieConverter=UnusedChineseConverter
    else:
        class LocalChineseConverter(original):
            def _load_cangjie_mapping(self,model_dir=None):
                for entry in json.loads((Path(cfg["model_dir"])/"Cangjie5_TC.json").read_text(encoding="utf-8")):
                    word,code=entry.split("\t")[:2]
                    self.word2cj[word]=code;self.cj2word.setdefault(code,[]).append(word)
            def _init_segmenter(self):
                directory=cfg.get("pkuseg_model_dir")
                if not directory or not Path(directory).is_dir():
                    raise RuntimeError("Chatterbox Chinese requires a local pkuseg_model_dir; configured Qwen Chinese avoids this dependency")
                from spacy_pkuseg import pkuseg
                self.segmenter=pkuseg(model_name=directory)
        tokenizers.ChineseCangjieConverter=LocalChineseConverter


def main():
    request=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    cfg=request["config"]; profile=request["profile"]; cues=request["cues"]
    output=Path(request["output"]); output.mkdir(parents=True,exist_ok=True)
    engine=cfg["engine"]
    if engine=="bailian":
        from bailian_tts import generate, BailianError
        try:
            generate(cues,cfg,profile,output,output.parent.parent/'paid-tts-cache')
        except BailianError as exc:
            (output.parent/'tts-failure.json').write_text(json.dumps({'provider':'aliyun-bailian','reason':str(exc)},ensure_ascii=False),encoding='utf-8')
            raise
        return
    if engine=="edge" and cfg.get("cloud_allowed") is not True:
        raise RuntimeError("Online TTS requires explicit text-transfer permission")
    if engine in {"qwen","chatterbox"} and engine not in profile.get("local_tts_engines",["qwen","chatterbox"]):
        raise RuntimeError("The selected local model does not support this target language")
    generation={}
    reference=cfg.get('synthetic_voice_reference')
    if reference is not None:
        path=Path(reference.get('path',''))
        if engine!='chatterbox' or reference.get('kind')!='synthetic_preset' or not reference.get('provenance'):
            raise RuntimeError('Only provenance-bound synthetic preset references are supported')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=reference.get('sha256'):
            raise RuntimeError('Synthetic preset reference missing or changed')
    if engine=='chatterbox' and cfg.get('voice_type')=='female' and (not reference or reference.get('voice_type')!='female'):
        raise RuntimeError('Female voice requires a verified synthetic female preset reference')
    if engine=='chatterbox':
        for name,default,low,high in [('cfg_weight',.5,0,1),('exaggeration',.5,0,2),('temperature',.8,.05,2)]:
            value=cfg.get(name,default)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
                raise RuntimeError('Invalid Chatterbox '+name)
            generation[name]=value
    seed=cfg.get('seed')
    if seed is not None and (isinstance(seed,bool) or not isinstance(seed,int) or not 0<=seed<2**31):
        raise RuntimeError('Invalid TTS seed')
    if engine in {"qwen","chatterbox"}:
        os.environ["HF_HUB_OFFLINE"]="1"
        os.environ["TRANSFORMERS_OFFLINE"]="1"
        os.environ["TOKENIZERS_PARALLELISM"]="false"
        if engine=="chatterbox":
            os.environ["HF_HOME"]=str(output.parent/"runtime-cache")
            os.environ["PKUSEG_HOME"]=str(Path(cfg["model_dir"])/"pkuseg")
        import torch
        import soundfile as sf
        torch.set_num_threads(int(cfg.get('cpu_threads',4)))
        if engine=="qwen":
            if profile.get("qwen_language") is None: raise RuntimeError("Qwen does not support this language")
            from qwen_tts import Qwen3TTSModel
            device=cfg.get("device","cpu")
            precision=torch.float32 if device=="cpu" else (torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float32)
            model=Qwen3TTSModel.from_pretrained(cfg["model_dir"],device_map=device,
                dtype=precision,
                attn_implementation="sdpa",local_files_only=True)
        else:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
            local_chatterbox_tokenizer(cfg,profile["model_language"])
            model=ChatterboxMultilingualTTS.from_local(Path(cfg["model_dir"]),device=cfg.get("device","cpu"))
            if reference:
                model.prepare_conditionals(str(path),exaggeration=generation['exaggeration'])
        for cue in cues:
            if seed is not None:torch.manual_seed(seed+cue['id'])
            destination=output/f"{cue['id']:05}.wav"
            if destination.exists(): raise RuntimeError("Existing output must not be overwritten")
            if engine=="qwen":
                wavs,sr=model.generate_custom_voice(text=cue["text"],language=profile["qwen_language"],
                    speaker=cfg.get("speaker") or profile["speaker"],
                    instruct=cfg.get("instruction","Natural, clear product demonstration narration."),
                    max_new_tokens=cfg.get("max_new_tokens",1024))
                audio=wavs[0]
            else:
                wav=model.generate(text=cue["text"],language_id=profile["model_language"],**generation)
                audio=wav.squeeze().cpu().numpy(); sr=model.sr
            sf.write(destination,audio,sr,subtype="PCM_16")
    elif engine=="edge":
        import edge_tts
        import subprocess
        async def generate():
            voices=await edge_tts.list_voices()
            available={v["ShortName"] for v in voices}
            voice=cfg.get("voice") or profile["edge_voice"]
            if voice not in available: raise RuntimeError("Voice unavailable; do not silently change locale")
            expected_locale=profile["edge_voice"].rsplit("-",1)[0]
            if next(v for v in voices if v["ShortName"]==voice).get("Locale")!=expected_locale:
                raise RuntimeError("Selected online voice has a different target locale")
            if cfg.get('voice_type')=='female' and next(v for v in voices if v['ShortName']==voice).get('Gender')!='Female':
                raise RuntimeError('Selected online voice is not female')
            for cue in cues:
                mp3=output/f"{cue['id']:05}.mp3"; wav=mp3.with_suffix(".wav")
                if mp3.exists() or wav.exists(): raise RuntimeError("Refusing overwrite")
                await edge_tts.Communicate(cue["text"],voice).save(str(mp3))
                subprocess.run(["ffmpeg","-v","error","-n","-i",str(mp3),"-ac","1","-ar","24000",
                                "-c:a","pcm_s16le",str(wav)],check=True,capture_output=True)
        asyncio.run(generate())
    else: raise RuntimeError("Unsupported engine")


if __name__=="__main__": main()
