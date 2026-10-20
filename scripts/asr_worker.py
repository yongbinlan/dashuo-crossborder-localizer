#!/usr/bin/env python3
"""Local-only faster-whisper worker, launched with a separately configured Python."""
import json
import os
from pathlib import Path
import sys

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"


def main():
    from faster_whisper import WhisperModel
    cfg=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    model=WhisperModel(cfg["model_dir"],device=cfg.get("device","cpu"),
                       compute_type="int8" if cfg.get("device","cpu")=="cpu" else "float16",
                       local_files_only=True)
    segments,_=model.transcribe(cfg["video"],language=cfg.get("language","zh"),beam_size=5,vad_filter=True)
    sys.path.insert(0,str(Path(__file__).parent))
    from localize import write_srt
    cues=[{"id":i,"start":s.start,"end":s.end,"text":s.text.strip()}
          for i,s in enumerate(segments,1) if s.text.strip()]
    if not cues: raise RuntimeError("ASR found no speech")
    output=Path(cfg["output"])
    if output.exists(): raise RuntimeError("Refusing to overwrite an existing source transcript")
    write_srt(output,cues)


if __name__=="__main__": main()
