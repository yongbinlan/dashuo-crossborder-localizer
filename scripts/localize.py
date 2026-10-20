#!/usr/bin/env python3
"""Independent, local-first ecommerce localization. No GhostCut API dependency."""
from __future__ import annotations

import argparse
import array
import contextlib
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.7.0"
_finish_spec = importlib.util.spec_from_file_location('dashuo_media_finish', ROOT/'scripts/media_finish.py')
finish = importlib.util.module_from_spec(_finish_spec)
_finish_spec.loader.exec_module(finish)
MODES = ("dub", "subtitles_only")
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
PROFILES = json.loads((ROOT / "references/languages.json").read_text(encoding="utf-8"))["profiles"]
STAMP = r"(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})"


class Blocked(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise urllib.error.HTTPError(req.full_url,code,"Translation redirects are disabled",headers,fp)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def run(command, cwd=None, timeout=600, env=None,error_log=None):
    result = subprocess.run([str(x) for x in command], cwd=cwd, env=env,
                            capture_output=True, timeout=timeout)
    if result.returncode:
        if error_log:
            details=result.stderr.decode("utf-8","replace")
            for key,value in os.environ.items():
                if re.search(r"KEY|TOKEN|SECRET",key,re.I) and len(value)>=8:
                    details=details.replace(value,"[REDACTED]")
            Path(error_log).write_text(details,encoding="utf-8")
        # Do not print external provider responses or credential-bearing command lines.
        if Path(str(command[0])).stem.lower() in {"ffmpeg", "ffprobe"}:
            detail = result.stderr.decode("utf-8", "replace")[-1800:]
        else:
            detail = "查看本地依赖、模型路径和配置；未自动改引擎或重试"
        raise Blocked(f"{Path(str(command[0])).name} exited {result.returncode}: {detail}")
    return result.stdout.decode("utf-8-sig", "replace")


def probe(path):
    return json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path]))


def duration(path):
    return float(probe(path)["format"]["duration"])


def parse_srt(path):
    text = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    cues = []
    previous_end = 0.0
    for block in re.split(r"\n\s*\n", text):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].isdigit():
            raise Blocked("字幕结构无效；需要编号、时间轴和正文")
        match = re.fullmatch(STAMP + r"\s*-->\s*" + STAMP, lines[1].strip())
        if not match:
            raise Blocked("字幕时间码无效")
        values = list(map(int, match.groups()))
        if any(values[i] >= 60 for i in (1, 2, 5, 6)):
            raise Blocked("字幕分秒越界")
        start, end = [v[0]*3600 + v[1]*60 + v[2] + v[3]/1000
                      for v in (values[:4], values[4:])]
        cue_id = int(lines[0])
        body = "\n".join(lines[2:]).strip()
        if not body or start < previous_end - .001 or end <= start or cue_id != len(cues) + 1:
            raise Blocked("字幕为空、重叠、逆序或编号不连续")
        cues.append({"id": cue_id, "start": start, "end": end, "text": body})
        previous_end = end
    if not cues:
        raise Blocked("字幕无有效台词")
    return cues


def timestamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"


def write_srt(path, cues):
    Path(path).write_text("\n\n".join(
        f"{i}\n{timestamp(c['start'])} --> {timestamp(c['end'])}\n{c['text']}"
        for i, c in enumerate(cues, 1)) + "\n", encoding="utf-8")


def relative_path(value, base):
    p = Path(value)
    return (base / p).resolve() if not p.is_absolute() else p.resolve()


def production_mode(job):
    mode = job.get("mode", "dub")
    if mode not in MODES:
        raise Blocked("未知制作通道：使用 dub 或 subtitles_only，不自动换通道")
    return mode


def load_job(path):
    path = Path(path).resolve()
    job = read_json(path)
    if job.get("schema_version") != 1 or not job.get("assets") or not job.get("targets"):
        raise Blocked("任务清单无效")
    production_mode(job)
    for target in job["targets"]:
        if target not in PROFILES:
            raise Blocked(f"未配置语种 {target}；先协商引擎，不自动替换语言")
    if len(set(job["targets"])) != len(job["targets"]):
        raise Blocked("目标语种重复")
    ids = [a["id"] for a in job["assets"]]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", i) for i in ids):
        raise Blocked("资产ID重复或包含路径字符")
    return path, job


@contextlib.contextmanager
def lock_job(path):
    lock = Path(path).with_suffix(".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise Blocked("任务已被锁定；先核实原进程是否仍运行，不并发提交或盲删锁")
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def initialize(args):
    mode = production_mode({"mode":getattr(args,"mode","dub")})
    source = Path(args.input).resolve()
    out = Path(args.output).resolve()
    if (out / "job.json").exists():
        raise Blocked("已有任务，复用 job.json；新版本请使用新输出目录")
    targets = args.targets.split(",")
    if any(t not in PROFILES for t in targets) or len(targets) != len(set(targets)):
        raise Blocked("目标语言无效或重复，使用 languages 查看")
    files = [source] if source.is_file() else sorted(p for p in source.rglob("*") if p.is_file())
    files = [p.resolve() for p in files if p.suffix.lower() in VIDEO_EXTS and not p.resolve().is_relative_to(out)]
    if not files:
        raise Blocked("未找到视频")
    assets = []
    seen = set()
    for p in files:
        fingerprint = sha(p)
        asset_id = "video-" + digest({"path":str(p), "sha256":fingerprint})[:14]
        if asset_id in seen:
            continue
        seen.add(asset_id)
        assets.append({"id":asset_id, "title":p.stem, "video":str(p), "source_sha256":fingerprint,
                       "source_srt":str(p.with_suffix(".srt")) if p.with_suffix(".srt").exists() else None,
                       "background":None, "hard_subtitles":"unknown", "cover_region":None,
                       "source_voice_type":"unknown", "source_voice_evidence":None,
                       "source_subtitle_region":None,"target_subtitle_region":None})
    job = {"schema_version":1, "skill_version":VERSION, "mode":mode, "source_language":"zh", "targets":targets,
           "preserve": ["商品、动作、镜头顺序、卖点顺序、价格币种和事实"],
           "glossary":{}, "protected_terms":[], "cloud_allowed":False, "paid_calls_allowed":False,
           "require_voice_type_review":True,
           "providers": {"asr":{"python":sys.executable, "model_dir":None, "device":"cpu"},
                         "translation":{"engine":"host", "base_url":"http://127.0.0.1:11434/v1", "model":None},
                         "tts":{"engine":"chatterbox", "python":sys.executable, "model_dir":None, "device":"cpu"}},
           "render":{"background_mode":"bandit", "background_gain":0.7, "max_speedup":1.10,
                     "loudness":{"enabled":True,"voice_i":-20.,"voice_tp":-3.,"delivery_i":-16.,"delivery_tp":-1.5,"lra":9.},
                     "use_following_silence":True,"trim_edge_silence":True,"subtitle_mode":"bilingual",
                     "font":"Microsoft YaHei", "font_size":None, "bottom_margin":0.06},
           "assets":assets}
    runtime_path=getattr(args,'runtime',None)
    if runtime_path and getattr(args,'no_runtime',False):raise Blocked('--runtime与--no-runtime不能同时使用')
    if not runtime_path and not getattr(args,'no_runtime',False) and (ROOT/'references/local-runtime.json').is_file():
        runtime_path=ROOT/'references/local-runtime.json'
    if runtime_path:
        runtime=read_json(runtime_path)
        for key in ("providers","target_overrides"):
            if key in runtime: job[key]=runtime[key]
    out.mkdir(parents=True, exist_ok=True)
    save_json(out / "job.json", job)
    return {"job":str(out/"job.json"), "assets":len(assets), "variants":len(assets)*len(targets),
            "status":"configured", "mode":mode,
            "next":"核实硬字幕与中文稿；transcribe（如需）后requests；原音轨直接保留，无需TTS或分离底轨" if mode=="subtitles_only" else "核实硬字幕、背景声、配置本地模型；transcribe 后 requests"}


def source_cues(job_path, job, asset):
    video = relative_path(asset["video"], job_path.parent)
    if sha(video) != asset["source_sha256"]:
        raise Blocked("原视频已变化：新建任务版本，不复用旧字幕和配音")
    if asset.get("source_srt"):
        path = relative_path(asset["source_srt"], job_path.parent)
    else:
        path = job_path.parent / "sources" / asset["id"] / "source.srt"
        meta = path.with_suffix(".json")
        if not path.exists() or not meta.exists():
            raise Blocked("缺原文字幕：先运行 transcribe，或指定已校对 source_srt")
        record = read_json(meta)
        if record["source_sha256"] != sha(video) or record["srt_sha256"] != sha(path):
            raise Blocked("ASR字幕来源/内容发生变化；重新转写或显式绑定校对稿")
    cues = parse_srt(path)
    if cues[-1]["end"] > duration(video) + .03:
        raise Blocked("原文字幕超出视频时长")
    return video, path, cues


def transcribe(job_path, job):
    cfg = job.get("providers",{}).get("asr",{})
    model_dir = cfg.get("model_dir")
    records = []
    for asset in job["assets"]:
        if asset.get("source_srt"):
            source_cues(job_path, job, asset)
            records.append({"asset":asset["id"], "status":"existing_srt_checked"})
            continue
        if not model_dir or not Path(model_dir).is_dir():
            raise Blocked("ASR需要本地 faster-whisper 模型目录，不自动联网下载")
        video = relative_path(asset["video"], job_path.parent)
        if sha(video) != asset["source_sha256"]:
            raise Blocked("视频发生变化")
        out = job_path.parent / "sources" / asset["id"]
        out.mkdir(parents=True, exist_ok=True)
        request = out / "asr-request.json"
        save_json(request, {"video":str(video), "output":str(out/"source.srt"), **cfg})
        if (out / "source.srt").exists():
            source_cues(job_path, job, asset)
        else:
            run([cfg.get("python",sys.executable), ROOT/"scripts/asr_worker.py", request], timeout=3600)
            parse_srt(out/"source.srt")
            save_json(out/"source.json", {"source_sha256":sha(video), "srt_sha256":sha(out/"source.srt"),
                                         "evidence":"local_asr_unreviewed", "model":str(model_dir)})
        records.append({"asset":asset["id"], "status":"asr_generated_needs_text_review"})
    return records


def request_path(job_path, asset, target):
    return job_path.parent / "translations" / f"{asset['id']}.{target}.json"


def build_requests(job_path, job):
    written = []
    for asset in job["assets"]:
        _, srt, cues = source_cues(job_path, job, asset)
        for target in job["targets"]:
            dest = request_path(job_path, asset, target)
            if not dest.exists():
                save_json(dest, {"source_srt_sha256":sha(srt), "target":target, "reviewed":False,
                                 "items":[{"id":c["id"], "source":c["text"], "text":""} for c in cues]})
            written.append(str(dest))
    return {"status":"translation_inputs_ready", "files":written}


def protected(text):
    return sorted(re.findall(r"https?://[^\s<>]+|\{\{[^}]+\}\}|\$\{[^}]+\}|\d+(?:[.,]\d+)*|[%$¥￥€£]",text))


def validate_translation(record, cues, srt, target, terms):
    if record.get("target") != target or record.get("source_srt_sha256") != sha(srt):
        raise Blocked("译文绑定了错误语种/旧版原稿")
    items = record.get("items", [])
    if [i.get("id") for i in items] != [c["id"] for c in cues]:
        raise Blocked("译文漏句、重复或句序改变")
    for item, cue in zip(items, cues):
        if item.get("source") != cue["text"] or not str(item.get("text","")).strip():
            raise Blocked("译文为空或原句被替换")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]",item["text"]):
            raise Blocked("译文含控制字符")
        if protected(cue["text"]) != protected(item["text"]):
            raise Blocked("译文数字、货币符号、URL或占位符改变；逐句核实，不自动换算")
        for term in terms:
            if term in cue["text"] and term not in item["text"]:
                raise Blocked("商品/品牌保护词丢失")
    if record.get("reviewed") is not True:
        raise Blocked("译文尚未逐句核对；语义复核后设置 reviewed=true，并写 reviewer")
    if not record.get("reviewer"):
        raise Blocked("译文缺实际复核者记录")
    return [{**c, "text":item["text"].strip()} for c,item in zip(cues,items)]


def translate_api(job_path, job, asset, target, cues, srt):
    cfg = job["providers"]["translation"]
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    parsed = urllib.parse.urlparse(url)
    local = parsed.hostname in {"localhost","127.0.0.1","::1"}
    if parsed.scheme not in {"http","https"} or parsed.username or parsed.password or parsed.query:
        raise Blocked("翻译地址不安全或夹带凭据")
    if not local and not (job.get("cloud_allowed") is True and job.get("paid_calls_allowed") is True):
        raise Blocked("远程翻译可能上传文本并收费，需要任务级云端及费用授权")
    if not local and parsed.scheme != "https":
        raise Blocked("远程翻译地址必须使用HTTPS")
    if not cfg.get("model"):
        raise Blocked("缺翻译模型名")
    dest = request_path(job_path,asset,target)
    current = read_json(dest) if dest.exists() else None
    if current and any(i.get("text") for i in current.get("items",[])):
        return  # Never replace an existing partial/manual translation.
    pending = dest.with_suffix(".request-state.json")
    if pending.exists():
        raise Blocked("先前翻译请求已发出；核实其结果后恢复，不自动重复调用")
    save_json(pending, {"status":"request_started_no_auto_retry", "target":target})
    headers = {"Content-Type":"application/json"}
    key = os.environ.get(cfg.get("api_key_env","DASHUO_TRANSLATION_API_KEY"),"")
    if key:
        headers["Authorization"] = "Bearer " + key
    prompt = {"target":target, "market":PROFILES[target]["market"], "glossary":job.get("glossary",{}),
              "items":[{"id":c["id"], "source":c["text"]} for c in cues]}
    body = {"model":cfg["model"],"temperature":.2,"messages":[
        {"role":"system","content":"Translate Chinese ecommerce spoken copy faithfully and naturally for the target market. Preserve every selling point, negation, number, currency symbol, URL, placeholder and brand. No new claims, price conversion or omitted content. Keep each cue concise without changing meaning. Return JSON only: {\"items\":[{\"id\":1,\"text\":\"translation\"}]}"},
        {"role":"user","content":json.dumps(prompt,ensure_ascii=False)}]}
    try:
        opener=urllib.request.build_opener(NoRedirect)
        with opener.open(urllib.request.Request(url,json.dumps(body).encode(),headers),timeout=180) as r:
            response = json.load(r)
        content = response["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$","",content)
        translated = json.loads(content)["items"]
    except Exception:
        raise Blocked("翻译服务失败或JSON无效；未自动重试，未打印响应或凭据") from None
    items = [{"id":c["id"],"source":c["text"],"text":t["text"]} for c,t in zip(cues,translated)]
    if len(translated)!=len(cues) or [t["id"] for t in translated]!=[c["id"] for c in cues]:
        raise Blocked("翻译返回漏句或错序；保留请求状态，需核实后修复")
    save_json(dest,{"source_srt_sha256":sha(srt),"target":target,"reviewed":False,
                    "items":items,"provider_evidence":"generated_not_semantically_reviewed"})
    save_json(pending,{"status":"response_saved_needs_review","target":target})


def ass_escape(text):
    # Untrusted cue text may not introduce ASS overrides or tags.
    return text.replace("\\","＼").replace("{","｛").replace("}","｝").replace("\n",r"\N")


def ass_time(sec):
    cs=round(sec*100)
    return f"{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}"


def wrap_text(text, width):
    result=[]
    for original in text.splitlines():
        if re.search(r"[\u3000-\u9fff]",original):
            result.extend(original[i:i+width] for i in range(0,len(original),width))
        else:
            words=original.split(); line=""
            for word in words:
                if len(word)>width:
                    raise Blocked("字幕单词/URL过长，先人工断行或调整排版")
                if line and len(line)+len(word)+1>width:
                    result.append(line); line=word
                else:
                    line=(line+" "+word).strip()
            if line: result.append(line)
    return result


def write_ass(path,cues,width,height,cfg,region=None,*,bottom_aligned=False):
    size=int(cfg.get("font_size") or max(18,round(height*.032)))
    margin=int(height*cfg.get("bottom_margin",.06))
    font=cfg.get("font","Microsoft YaHei")
    if re.search(r"[,\r\n]",font):
        raise Blocked("字体名称包含非法字符")
    header=f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,{margin},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    if region:
        x,y,w,h=region
        text_width=width*w-16
        if h*height<size*2.6:
            raise Blocked("遮盖区不足两行字幕高度；调整区域或字体后复验")
        # Keep the bottom edge fixed above the source captions. A second line
        # grows upwards rather than moving the translation away from Chinese.
        anchor_y=y+h if bottom_aligned else y+h/2
        alignment=2 if bottom_aligned else 5
        position=r"{\an"+str(alignment)+r"\pos("+f"{int((x+w/2)*width)},{int(anchor_y*height)}"+r")}"
    else:
        text_width=width*.9
        position=""
    lines=[]
    for cue in cues:
        wrapped=[]
        for line in cue["text"].splitlines():
            cjk=bool(re.search(r"[\u3000-\u9fff]",line))
            limit=max(6,int(text_width/(size*(1.05 if cjk else .62))))
            wrapped.extend(wrap_text(line,limit))
        if len(wrapped)>int(cfg.get("max_lines",2)):
            raise Blocked("字幕超过可读行数；需保留完整意思重新断句/排版，不自动删文案")
        lines.append(f"Dialogue: 0,{ass_time(cue['start'])},{ass_time(cue['end'])},Default,,0,0,0,,{position}{ass_escape(chr(10).join(wrapped))}")
    Path(path).write_text(header+"\n".join(lines)+"\n",encoding="utf-8-sig")


def valid_region(region):
    return (isinstance(region,list) and len(region)==4 and
            all(isinstance(x,(int,float)) and math.isfinite(x) for x in region) and
            region[0]>=0 and region[1]>=0 and region[2]>0 and region[3]>0 and
            region[0]+region[2]<=1 and region[1]+region[3]<=1)


def subtitle_layout(asset,target):
    hard=asset.get("hard_subtitles","unknown")
    if hard not in {"none","keep","cover"}:
        raise Blocked("先检查原片：已有中文字幕用keep，干净原片用none；不默认擦字或遮盖")
    if hard=="cover":
        if not valid_region(asset.get("cover_region")): raise Blocked("字幕遮盖区域无效")
        return hard,asset["cover_region"]
    # A Chinese target retains the existing Chinese subtitles and avoids duplicate Chinese layers.
    if hard=="keep" and PROFILES[target]["model_language"]=="zh": return hard,None
    region=asset.get("target_subtitle_region")
    source=asset.get("source_subtitle_region")
    if region is None and hard=="keep":
        if not valid_region(source) or source[1]<.18:
            raise Blocked("保留原中文字幕：先实看片并设置source_subtitle_region或不重叠的target_subtitle_region")
        sx,sy,sw,sh=source
        region=[sx,sy-.168,sw,.16]
    if region is not None and not valid_region(region):raise Blocked("目标字幕区域无效")
    if hard=="keep" and source and region:
        if not valid_region(source): raise Blocked("原中文字幕区域无效")
        x,y,w,h=region; sx,sy,sw,sh=source
        if x<sx+sw and sx<x+w and y<sy+sh and sy<y+h:
            raise Blocked("新增字幕与原中文字幕区域重叠，调整位置后再合成")
    return hard,region


def display_cues(cues,source_cues,target,hard,mode):
    if mode not in {"bilingual","target_only"}: raise Blocked("字幕模式未知")
    if hard=="keep":
        return [] if PROFILES[target]["model_language"]=="zh" else cues
    if mode=="bilingual" and PROFILES[target]["model_language"]!="zh":
        return [{**cue,"text":cue["text"]+"\n"+source["text"]} for cue,source in zip(cues,source_cues)]
    return cues


def separation_signature(cfg):
    if cfg.get('engine')!='bandit':raise Blocked('保声通道需要本地BandIt配置，不自动改分离器')
    repo=Path(cfg.get('repo',''));checkpoint=Path(cfg.get('checkpoint',''))
    if not (repo/'src/models/bandit/bandit.py').is_file() or not checkpoint.is_file():
        raise Blocked('BandIt代码或本地权重缺失；不自动下载或回退到丢音效通道')
    if sha(checkpoint)!=cfg.get('checkpoint_sha256'):raise Blocked('BandIt权重身份不符')
    if cfg.get('device','cuda') not in {'cpu','cuda'}:raise Blocked('BandIt device无效')
    paths=sorted((repo/'src').rglob('*.py'))+[repo/'configs/models/bandit-mus64.yaml']
    return digest({'config':cfg,'worker':sha(ROOT/'scripts/bandit_worker.py'),
                   'author_code':[[str(p.relative_to(repo)),sha(p)] for p in paths]})


def bandit_background(asset,job,base,work):
    cfg=job['providers'].get('separation',{});signature=separation_signature(cfg)
    key=digest({'source':asset['source_sha256'],'signature':signature})
    parent=base/'separations'/key;parent.mkdir(parents=True,exist_ok=True)
    for path in sorted(parent.glob('v*/stems/receipt.json'),reverse=True):
        record=read_json(path)
        if record.get('signature')==signature and record.get('source_sha256')==asset['source_sha256']:
            if set(record.get('stems',{}))=={'speech','music','sfx'} and all(
                Path(s['file']).is_file() and sha(s['file'])==s['sha256'] for s in record['stems'].values()):
                save_json(work/'separation-reference.json',{'receipt':str(path),'sha256':sha(path),'reused':True,'key':key})
                return {'engine':'bandit','receipt':str(path),'stems':record['stems']}
    attempts=[p for p in parent.glob('v*') if p.is_dir() and p.name[1:].isdigit()]
    if any((p/'blocked.json').exists() for p in attempts):
        raise Blocked('同一BandIt分离已失败，未按语种重复重试；修复后更新separation.retry_revision再运行')
    attempt=parent/f'v{max((int(p.name[1:]) for p in attempts),default=0)+1}';attempt.mkdir()
    request=attempt/'request.json';folder=attempt/'stems'
    save_json(request,{'config':cfg,'source':str(relative_path(asset['video'],base)),
                       'source_sha256':asset['source_sha256'],'signature':signature,'output':str(folder)})
    try:
        run([cfg.get('python',sys.executable),ROOT/'scripts/bandit_worker.py',request],timeout=7200,error_log=attempt/'error.log')
        receipt=folder/'receipt.json';record=read_json(receipt)
        if record.get('signature')!=signature or record.get('source_sha256')!=asset['source_sha256'] or set(record.get('stems',{}))!={'speech','music','sfx'}:
            raise Blocked('BandIt分离来源或轨道无效')
        for stem in record['stems'].values():
            if sha(stem['file'])!=stem['sha256']:raise Blocked('分离轨道内容变化')
        save_json(work/'separation-reference.json',{'receipt':str(receipt),'sha256':sha(receipt),'reused':False,'key':key})
        return {'engine':'bandit','receipt':str(receipt),'stems':record['stems']}
    except Exception as exc:
        save_json(attempt/'blocked.json',{'error':str(exc),'retry':'repair and change retry_revision; no automatic retry'})
        raise


def mix_bandit(work,bg,dub,total,render):
    raw_dub=dub
    dub=finish.voice(work,dub,render)
    stems=bg['stems'];gains={}
    for stem,target,maximum in [('music',-27.,12.),('sfx',-28.,9.)]:
        record=stems[stem]
        gain=max(-12.,min(target-record['rms_dbfs'],maximum,-3.-record['peak_dbfs']))
        gain=render.get(stem+'_gain_db',gain)
        if isinstance(gain,bool) or not isinstance(gain,(int,float)) or not math.isfinite(gain) or not -24<=gain<=18:
            raise Blocked('音乐/音效增益需在-24至18dB，调整后重听')
        gains[stem]={'gain_db':gain,'linear':10**(gain/20)}
    mixed=work/'mix.wav';bed=work/'background.wav'
    # Write the background first. Concurrent two-output graphs can lose the
    # final background buffer at EOF; separate PCM stages preserve the tail.
    filters=(f"[0:a]aresample=48000,pan=stereo|c0=c0|c1=c0,apad=pad_dur=1[sc];"
      f"[1:a]volume={gains['music']['linear']:.8f},apad=pad_dur=1[m];"
      "[m][sc]sidechaincompress=threshold=0.04:ratio=2.5:attack=15:release=220:makeup=1[md];"
      f"[2:a]volume={gains['sfx']['linear']:.8f},apad=pad_dur=1[fx];"
      "[md][fx]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.8:level=false:latency=true[bed]")
    run(['ffmpeg','-v','error','-n','-i',dub,'-i',stems['music']['file'],'-i',stems['sfx']['file'],
         '-filter_complex',filters,'-map','[bed]','-t',str(total),'-c:a','pcm_f32le',bed])
    run(['ffmpeg','-v','error','-n','-i',dub,'-i',bed,'-filter_complex',
         '[0:a]aresample=48000,pan=stereo|c0=c0|c1=c0,apad=pad_dur=1[v];[1:a]apad=pad_dur=1[b];[v][b]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95:level=false:latency=true[out]',
         '-map','[out]','-t',str(total),'-c:a','pcm_f32le',mixed])
    save_json(work/'mix-receipt.json',{'engine':'bandit_three_stem','separation_receipt':bg['receipt'],
      'inputs':{k:{'file':stems[k]['file'],'sha256':sha(stems[k]['file'])} for k in ('music','sfx')},
      'voice_sha256':sha(dub),'raw_voice_sha256':sha(raw_dub),'original_dialogue_in_mix':False,'gains':gains,
      'ducking':'music follows replacement voice; attack15ms release220ms ratio2.5',
      'output_sha256':sha(mixed),'background_sha256':sha(bed),'full_audio_review':False})
    return finish.delivery(work,mixed,render)


def mix_dub(work,bg,dub,total,render):
    if isinstance(bg,dict):return mix_bandit(work,bg,dub,total,render)
    calibrated=finish.voice(work,dub,render)
    command=['ffmpeg','-v','error','-n','-i',calibrated]
    filters='[0:a]aresample=48000,pan=stereo|c0=c0|c1=c0[v];'
    if bg:
        gain=render.get('background_gain',.7)
        if isinstance(gain,bool) or not isinstance(gain,(int,float)) or not math.isfinite(gain) or not 0<=gain<=4:
            raise Blocked('背景增益无效')
        command+=['-i',bg]
        filters+=f'[1:a]volume={gain}[b];[v][b]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95:level=false:latency=true[out]'
    else:filters+='[v]anull[out]'
    mixed=work/'mix.wav'
    run(command+['-filter_complex',filters,'-map','[out]','-t',str(total),'-c:a','pcm_f32le',mixed])
    return finish.delivery(work,mixed,render)


def pacing_report(work,cues,target):
    rows=read_json(work/'timing.json');metrics=[]
    for row,cue in zip(rows,cues):
        count=len(re.findall('[aeiou]',cue['text'].lower())) if PROFILES[target]['model_language']=='ms' else len(re.sub(r'\s','',cue['text']))
        seconds=row['speech_end']-row['start']
        metrics.append({'id':row['id'],'speedup':row['speedup'],'rate_proxy':count/seconds})
    ratios=[metrics[i]['rate_proxy']/metrics[i-1]['rate_proxy'] for i in range(1,len(metrics)) if metrics[i-1]['rate_proxy']]
    save_json(work/'pacing-report.json',{'metrics':metrics,'adjacent_proxy_ratios':ratios,
      'proxy_only':True,'full_listening_required':True,'note':'vowel letters for Malay, characters otherwise; not pronunciation or naturalness proof'})


def background(asset,job,base,work):
    mode=job["render"].get("background_mode","required")
    if asset.get("background"):
        p=relative_path(asset["background"],base)
        if not p.is_file() or not any(s.get("codec_type")=="audio" for s in probe(p)["streams"]):
            raise Blocked("指定背景声不可用")
        if duration(p)+.05<duration(relative_path(asset["video"],base)):
            raise Blocked("背景声长度不足，未自动循环或截掉原片")
        return p
    if mode=="none":
        return None  # Explicit job setting; original Chinese voice is never mixed back in.
    if mode=='bandit':return bandit_background(asset,job,base,work)
    if mode=="demucs":
        cfg=job["providers"].get("separation",{})
        if not cfg.get("repo") or not Path(cfg["repo"]).is_dir():
            raise Blocked("Demucs需要已下载的本地模型repo，不自动下载")
        src=relative_path(asset["video"],base)
        audio=work/"separation-input.wav"
        run(["ffmpeg","-v","error","-n","-i",src,"-vn","-ar","44100",audio])
        model=cfg.get("model","htdemucs")
        if not re.fullmatch(r"[A-Za-z0-9_-]+",model): raise Blocked("Demucs模型名非法")
        run([cfg.get("python",sys.executable),"-m","demucs.separate","--two-stems","vocals",
             "--repo",cfg["repo"],"-n",model,"-o",work/"separated",audio],timeout=3600)
        stem=work/"separated"/model/audio.stem/"no_vocals.wav"
        if not stem.is_file(): raise Blocked("分离没有返回背景声")
        return stem
    raise Blocked("缺背景声：提供去人声底轨或配置本地Demucs；仅明确接受去BGM时用 background_mode=none")


def tts_settings(job,target):
    cfg={**job["providers"]["tts"],**job.get("target_overrides",{}).get(target,{})}
    engine=cfg["engine"]
    if engine not in {"qwen","chatterbox","edge","provided"}:
        raise Blocked("TTS引擎未知")
    if engine=="qwen" and PROFILES[target]["qwen_language"] is None:
        raise Blocked(f"Qwen本地音色不支持{target}；选择本地Chatterbox，不将马来语冒充印尼语")
    if engine=="edge" and job.get("cloud_allowed") is not True:
        raise Blocked("Edge是在线TTS，需要明确允许向微软发送译文，不能标为本地")
    if engine=="provided" and not cfg.get("audio_dir"):
        raise Blocked("provided模式需要明确audio_dir，不能默认扫描任务目录")
    if engine in {"qwen","chatterbox"} and (not cfg.get("model_dir") or not Path(cfg["model_dir"]).is_dir()):
        raise Blocked("缺本地TTS模型目录，先部署模型，不自动切到云端")
    return cfg


def voice_settings(job,asset,target,cfg,base):
    """Resolve voice presentation, not a person's gender or identity."""
    cfg=dict(cfg)
    source=asset.get("source_voice_type","unknown")
    requested=cfg.get("voice_type","unknown")
    if source not in {"female","male","unknown","mixed"} or requested not in {"female","male","unknown"}:
        raise Blocked("声音类型字段无效")
    if job.get("require_voice_type_review") and source in {"unknown","mixed"} and requested=="unknown":
        raise Blocked("原声音类型未确认或多人混合；先听检或询问目标音色，不能由人物画面推断")
    if source=="female" and requested=="male":
        raise Blocked("原片女声需要目标女声；不能静默切男声")
    selected="female" if source=="female" else requested
    if selected!="female":return cfg
    cfg["voice_type"]="female"
    if cfg["engine"]=="qwen":
        females={"Serena","Vivian","Ono_Anna","Sohee"}
        speaker=cfg.get("speaker")
        if speaker and speaker not in females:
            raise Blocked("已配置的Qwen音色不是已核验女声；选择Serena/Vivian/Ono_Anna/Sohee")
        cfg["speaker"]=speaker or ("Ono_Anna" if target=="ja-JP" else "Serena")
    elif cfg["engine"]=="chatterbox":
        reference=cfg.get("synthetic_voice_reference",{})
        if not reference and cfg.get('female_voice_preset'):
            reference={'preset':cfg['female_voice_preset']}
        if reference.get('preset'):
            presets=read_json(ROOT/'references/voice-presets.json')
            if reference['preset'] not in presets:raise Blocked('未知合成女声预设')
            reference={**presets[reference['preset']]}
            reference['path']=str(ROOT/reference['path'])
        if reference.get("kind")!="synthetic_preset" or reference.get("voice_type")!="female" or not reference.get("provenance"):
            raise Blocked("Chatterbox女声需要可追溯的合成女声预设参考；不默认克隆真人或退回默认音色")
        path=relative_path(reference.get("path",""),base)
        if not path.is_file() or sha(path)!=reference.get("sha256"):
            raise Blocked("合成女声参考缺失或内容已变化")
        cfg["synthetic_voice_reference"]={**reference,"path":str(path)}
    elif cfg["engine"]=="provided":
        if requested!="female" or not cfg.get("voice_evidence"):
            raise Blocked("外部逐句配音须记录女声音色来源并听检；文件名不证明声音类型")
    elif cfg["engine"]=="edge":
        # Worker verifies the actual service voice metadata before generating.
        cfg["voice_type"]="female"
    return cfg


def synthesize(work,cues,cfg,target,base):
    dest=work/"speech"
    dest.mkdir(exist_ok=True)
    expected=[dest/f"{c['id']:05}.wav" for c in cues]
    if cfg["engine"]=="provided":
        supplied=relative_path(cfg.get("audio_dir",""),base)
        for output in expected:
            source=supplied/output.name
            if not source.is_file(): raise Blocked("缺用户提供的逐句配音WAV")
            run(["ffmpeg","-v","error","-n","-i",source,"-ac","1","-ar","24000","-c:a","pcm_s16le",output])
    else:
        request=work/"tts-request.json"
        save_json(request,{"cues":cues,"config":cfg,"profile":PROFILES[target],"output":str(dest)})
        run([cfg.get("python",sys.executable),ROOT/"scripts/tts_worker.py",request],timeout=7200,error_log=work/"tts-error.log")
    for p in expected:
        if not p.is_file() or p.stat().st_size<100 or duration(p)<.05:
            raise Blocked("TTS返回缺句或空音频")
        with wave.open(str(p),"rb") as f:
            if f.getsampwidth()!=2:raise Blocked("TTS需要可检测的PCM16音频")
            samples=array.array("h",f.readframes(f.getnframes()))
            if sys.byteorder!="little":samples.byteswap()
        if not samples or max(abs(x) for x in samples)<10 or sum(x*x for x in samples)/len(samples)<10:
            raise Blocked("TTS音频为静音或几乎静音，不能交付有音轨但无配音的视频")
    return expected


def prepare_speech_edges(work,clips):
    """Trim boundary silence only, with 120ms guards; keep raw files."""
    folder=work/"prepared-speech";folder.mkdir()
    prepared=[];evidence=[]
    for clip in clips:
        with wave.open(str(clip),"rb") as f:
            params=f.getparams();frames=f.readframes(f.getnframes())
        if params.sampwidth!=2:raise Blocked("配音准备仅支持PCM16")
        values=array.array('h',frames)
        if sys.byteorder!="little":values.byteswap()
        active=[i//params.nchannels for i,v in enumerate(values) if abs(v)>19]
        if not active:raise Blocked("配音准备未检测到有效发声")
        guard=round(params.framerate*.12)
        first=max(0,active[0]-guard);last=min(params.nframes,active[-1]+guard+1)
        output=folder/clip.name
        with wave.open(str(output),'wb') as f:
            f.setparams(params);f.writeframes(frames[first*params.nchannels*2:last*params.nchannels*2])
        prepared.append(output)
        evidence.append({"file":clip.name,"raw_sha256":sha(clip),"prepared_sha256":sha(output),
                         "trim_start_seconds":first/params.framerate,
                         "trim_end_seconds":(params.nframes-last)/params.framerate,
                         "internal_pauses_removed":False,"guard_seconds":.12})
    save_json(work/'speech-preparation.json',evidence)
    return prepared


def assemble_audio(work,cues,clips,total,max_speedup):
    if not 1<=max_speedup<=1.5: raise Blocked("max_speedup须在1至1.5；不以极端变速塞入文案")
    total_frames=round(total*24000)
    timeline=bytearray(total_frames*2)
    measured=[]
    for cue,clip in zip(cues,clips):
        seconds=duration(clip)
        slot=cue["end"]-cue["start"]
        speed=max(1.0,seconds/max(slot-.025,.01))
        if speed>max_speedup+.001:
            raise Blocked(f"第{cue['id']}句配音超时（{seconds:.2f}s / {slot:.2f}s）；保留镜头和完整文案，重配或协商节奏")
        fitted=work/f"fit-{cue['id']:05}.wav"
        run(["ffmpeg","-v","error","-n","-i",clip,"-af",f"atempo={speed:.8f}","-ar","24000","-ac","1","-c:a","pcm_s16le",fitted])
        with wave.open(str(fitted),"rb") as f:
            data=f.readframes(f.getnframes())
        start=round(cue["start"]*24000)*2
        allowed=round(slot*24000)*2
        if len(data)>allowed:
            raise Blocked("配音适配后仍超出字幕窗口，未截断")
        if start+len(data)>len(timeline):
            raise Blocked("配音超出视频尾部，未截断")
        timeline[start:start+len(data)]=data
        measured.append({"id":cue["id"],"start":cue["start"],"subtitle_end":cue["end"],
                         "speech_end":cue["start"]+len(data)/48000,
                         "original_seconds":seconds,"slot_seconds":slot,"speedup":speed})
    out=work/"dub.wav"
    with wave.open(str(out),"wb") as f:
        f.setparams((1,2,24000,0,"NONE","not compressed")); f.writeframes(timeline)
    save_json(work/"timing.json",measured)
    return out


def use_existing_silence(cues,clips,total,enabled):
    adjusted=[dict(c) for c in cues]
    if enabled:
        for i,(cue,clip) in enumerate(zip(adjusted,clips)):
            bound=adjusted[i+1]["start"]-.03 if i+1<len(adjusted) else total-.03
            wanted=cue["start"]+duration(clip)+.03
            cue["end"]=max(cue["end"],min(wanted,bound))
    return adjusted


def audio_identity(path):
    """Bind all original encoded audio packets, codec configuration and timestamps.

    Comparing only duration or decoded loudness could miss replaced audio.
    Audio stream ordinals are stable even when container stream indices differ.
    """
    info=json.loads(run(["ffprobe","-v","error","-select_streams","a",
                         "-show_streams","-show_packets","-show_data_hash","sha256",
                         "-show_entries","stream=index,codec_name,profile,sample_rate,channels,channel_layout,extradata_hash:packet=stream_index,pts_time,dts_time,duration_time,data_hash,side_data_list",
                         "-of","json",path]))
    identities=[]
    for stream in info.get("streams",[]):
        packets=[p for p in info.get("packets",[]) if p["stream_index"]==stream["index"]]
        data=[p.get("data_hash") for p in packets]
        if any(not value for value in data):
            raise Blocked("无法核验原音频数据包身份，未将仅字幕版本标作技术通过")
        timings=[{key:(round(float(p[key]),6) if key.endswith("_time") else p[key])
                  for key in ("pts_time","dts_time","duration_time","side_data_list") if key in p}
                 for p in packets]
        identities.append({"codec":{key:stream.get(key) for key in
                                    ("codec_name","profile","sample_rate","channels","channel_layout","extradata_hash")},
                           "packet_count":len(packets),"packet_data_sha256":digest(data),
                           "packet_timing_sha256":digest(timings)})
    return identities


def verify_original_audio(video, output):
    source=audio_identity(video)
    rendered=audio_identity(output)
    if source!=rendered:
        raise Blocked("仅字幕通道的音轨内容、数量或时间戳发生变化；保留失败版本，不转码或静默丢失原声")
    return {"method":"all_audio_streams_codec_packet_data_and_timestamps_sha256",
            "source":source,"output":rendered,"audio_streams":len(source),
            "original_audio_preserved":True,"audio_full_playback":False}


def variant_fingerprint(job_path,job,asset,target,srt,translation,cfg):
    if production_mode(job)=="subtitles_only":
        visual_asset={key:asset.get(key) for key in ("id","title","video","source_sha256","source_srt",
                      "hard_subtitles","source_subtitle_region","target_subtitle_region","cover_region")}
        visual_render={key:job["render"].get(key) for key in
                       ("subtitle_mode","font","font_size","bottom_margin")}
        return digest({"version":VERSION,"mode":"subtitles_only","implementation":sha(__file__),
                       "source":asset["source_sha256"],"srt":sha(srt),"translation":translation,
                       "asset":visual_asset,"target":target,"render":visual_render,
                       "audio_policy":"all_original_streams_copy_and_verify"})
    bg=asset.get("background")
    audio_files={}
    if cfg["engine"]=="provided":
        directory=relative_path(cfg.get("audio_dir",""),job_path.parent)
        audio_files={p.name:sha(p) for p in sorted(directory.glob("*.wav"))}
    model_files=[]
    if cfg.get("model_dir"):
        directory=Path(cfg["model_dir"])
        for p in sorted(directory.rglob("*")):
            if p.is_file() and ".cache" not in p.relative_to(directory).parts and p.suffix in {".pt",".bin",".json",".safetensors",".txt"}:
                st=p.stat();model_files.append([str(p.relative_to(directory)),st.st_size,st.st_mtime_ns,
                                               sha(p) if p.suffix==".json" else None])
    return digest({"version":VERSION,"implementation":[sha(__file__),sha(ROOT/"scripts/tts_worker.py"),sha(ROOT/"scripts/media_finish.py")],
                   "model_files_metadata":model_files,"source":asset["source_sha256"],"srt":sha(srt),
                   "translation":translation,"asset":asset,"target":target,"tts":cfg,
                   "render":job["render"],"background_sha":sha(relative_path(bg,job_path.parent)) if bg else None,
                   "separation":job["providers"].get("separation"),"supplied_audio":audio_files,
                   "bandit_signature":separation_signature(job['providers'].get('separation',{})) if job['render'].get('background_mode')=='bandit' and not bg else None})


def render_variant(job_path,job,asset,target):
    mode=production_mode(job)
    video,srt,cues=source_cues(job_path,job,asset)
    cfg=voice_settings(job,asset,target,tts_settings(job,target),job_path.parent) if mode=="dub" else {}
    hard,region=subtitle_layout(asset,target)
    dest=request_path(job_path,asset,target)
    translation_engine=job["providers"]["translation"]["engine"]
    if translation_engine not in {"host","openai_compatible"}:
        raise Blocked("未知翻译引擎，不自动切换")
    if translation_engine=="openai_compatible":
        translate_api(job_path,job,asset,target,cues,srt)
    if not dest.exists():
        raise Blocked("缺译文，先运行 requests")
    translation=read_json(dest)
    translated=validate_translation(translation,cues,srt,target,job.get("protected_terms",[]))
    if mode=="dub" and cfg["engine"]=="provided":
        audio_dir=relative_path(cfg["audio_dir"],job_path.parent)
        if any(not (audio_dir/f"{c['id']:05}.wav").is_file() for c in translated):
            raise Blocked("缺用户提供的逐句配音WAV，未创建新版本或重复合成")
    fingerprint=variant_fingerprint(job_path,job,asset,target,srt,translation,cfg)
    parent=job_path.parent/"variants"/asset["id"]/target
    parent.mkdir(parents=True,exist_ok=True)
    for old in sorted(parent.glob("v*/receipt.json")):
        record=read_json(old)
        if record.get("fingerprint")==fingerprint and record.get("technical_checks_passed"):
            if all((old.parent/name).is_file() and sha(old.parent/name)==h for name,h in record["artifact_sha256"].items()):
                return {"asset":asset["id"],"target":target,"status":"reused_checked_bytes", "receipt":str(old)}
    versions=[int(p.name[1:]) for p in parent.glob("v*") if p.is_dir() and p.name[1:].isdigit()]
    work=parent/f"v{max(versions,default=0)+1}"
    work.mkdir()
    save_json(work/"state.json",{"fingerprint":fingerprint,"status":"started","target":target,"mode":mode})
    try:
        info=probe(video); vs=next(s for s in info["streams"] if s["codec_type"]=="video")
        total=float(info["format"]["duration"]); width,height=vs["width"],vs["height"]
        if vs.get("tags",{}).get("rotate") or any(s.get("rotation",0) for s in vs.get("side_data_list",[])):
            raise Blocked("原片含旋转元数据，先无损规范方向并绑定新版本，避免字幕坐标错位")
        bg=None
        if mode=="dub":
            bg=background(asset,job,job_path.parent,work)
            clips=synthesize(work,translated,cfg,target,job_path.parent)
            if job['render'].get('trim_edge_silence',False):
                clips=prepare_speech_edges(work,clips)
            translated=use_existing_silence(translated,clips,total,job["render"].get("use_following_silence",True))
            dub=assemble_audio(work,translated,clips,total,job["render"].get("max_speedup",1.25))
            pacing_report(work,translated,target)
        else:
            save_json(work/"timing.json",[{"id":c["id"],"start":c["start"],"end":c["end"],
                                          "timing_source":"source_srt","tts_used":False} for c in translated])
        write_srt(work/"translated.srt",translated)
        bilingual=display_cues(translated,cues,target,"none","bilingual")
        write_srt(work/"bilingual.srt",bilingual)
        displayed=display_cues(translated,cues,target,hard,job["render"].get("subtitle_mode","bilingual"))
        display_cfg={**job["render"],"max_lines":4 if hard=="none" and job["render"].get("subtitle_mode","bilingual")=="bilingual" else 2}
        write_ass(work/"captions.ass",displayed,width,height,display_cfg,region,
                  bottom_aligned=hard=="keep")
        # Static relative ASS filename: no path or subtitle text enters an ffmpeg expression.
        vf=""
        if hard=="cover":
            x,y,w,h=region
            vf=f"drawbox=x={int(x*width)}:y={int(y*height)}:w={int(w*width)}:h={int(h*height)}:color=black:t=fill,"
        if displayed or mode=='subtitles_only':vf+="ass=captions.ass"
        vf=vf.rstrip(',')
        if mode=="subtitles_only":
            command=["ffmpeg","-v","error","-n","-i",video,"-map","0:v:0","-map","0:a?",
                     "-vf",vf,"-fps_mode","passthrough","-c:v","libx264","-preset","medium","-crf","19",
                     "-pix_fmt","yuv420p","-c:a","copy","-movflags","+faststart","-map_metadata","0",work/"localized.mp4"]
        else:
            mixed=mix_dub(work,bg,dub,total,job['render'])
            picture_sig=finish.picture_signature(video,work/'captions.ass',vf)
            copied_from=finish.find_picture(parent,picture_sig) if vf else video
            command=['ffmpeg','-v','error','-n','-i',copied_from or video,'-i',mixed,'-map','0:v:0','-map','1:a:0']
            if copied_from:command+=['-c:v','copy']
            else:command+=['-vf',vf,'-fps_mode','passthrough','-c:v','libx264','-preset','medium','-crf','19','-pix_fmt','yuv420p']
            command += ['-t',str(total),'-c:a','aac','-b:a','192k','-movflags','+faststart','-map_metadata','-1',work/'localized.mp4']
        run(command,cwd=work,timeout=3600)
        output=work/"localized.mp4"; out=probe(output)
        outv=next(s for s in out["streams"] if s["codec_type"]=="video")
        if (outv["width"],outv["height"])!=(width,height) or abs(float(out["format"]["duration"])-total)>.15:
            raise Blocked("成片尺寸或时长与原片不符")
        audio_evidence=None
        if mode=="subtitles_only":
            audio_evidence=verify_original_audio(video,output)
            save_json(work/"audio-preservation.json",audio_evidence)
        elif not any(s["codec_type"]=="audio" for s in out["streams"]):
            raise Blocked("成片缺音轨")
        if mode=='dub':
            loudness_evidence=finish.verify_encoded(work,output,job['render'])
            picture_evidence=finish.picture_receipt(work,video,output,picture_sig,copied_from)
        run(["ffmpeg","-v","error","-i",output,"-f","null","-"],timeout=3600)
        for i,t in enumerate(sorted({min(max((c['start']+c['end'])/2,0),max(total-.05,0)) for c in translated})):
            run(["ffmpeg","-v","error","-n","-ss",str(t),"-i",output,"-frames:v","1",work/f"preview-{i+1:03}.png"])
        artifacts={p.name:sha(p) for p in work.iterdir() if p.suffix in {".mp4",".srt",".ass",".wav",".png"}
                   or p.name in {"audio-preservation.json","timing.json","speech-preparation.json","pacing-report.json","mix-receipt.json","separation-reference.json","loudness-receipt.json","picture-preservation.json"}}
        receipt={"task_intent":"中文电商视频本地化，保留商品与原片节奏", "target_environment":target,
                 "acceptance_criteria":["完整译文","真实配音","字幕可读","原片尺寸时长","视听与语义检查"],
                 "artifacts":{name:str(work/name) for name in artifacts},"checks_run":["subtitle_structure_and_source_binding","number_currency_and_terms","speech_slot_fitting_no_cut","full_decode","dimensions_duration_audio","preview_extraction"],
                 "evidence":{"translation_reviewer":translation["reviewer"],"background":"provided_or_local_demucs" if bg else "explicit_none","tts_engine":cfg.get("engine","not_used")},
                 "unresolved_issues":["须实际检查画面、全片听音、目标语言语义及原中文残留"],
                 "limitations":[PROFILES[target]["accent"],"cover是可见遮盖，非神经擦字"] if hard=="cover" else [PROFILES[target]["accent"]],
                 "status":"verification_incomplete","technical_checks_passed":True,"fingerprint":fingerprint,
                 "artifact_sha256":artifacts,"publication_authorized":False}
        receipt["mode"]=mode
        if mode=='dub':
            receipt['checks_run']+=['voice_and_delivery_loudness_matching','final_encoded_loudness_true_peak','picture_encoding_lineage_or_verified_stream_copy']
            receipt['evidence'].update({'background':'bandit_three_stem' if isinstance(bg,dict) else receipt['evidence']['background'],
             'loudness':loudness_evidence,'picture':picture_evidence,
             'source_voice_type':asset.get('source_voice_type','unknown'),'target_voice_type':cfg.get('voice_type','unknown'),
             'speaker':cfg.get('speaker'),'synthetic_voice_reference':cfg.get('synthetic_voice_reference'),
             'separation_reference':read_json(work/'separation-reference.json') if (work/'separation-reference.json').exists() else None})
        if mode=="subtitles_only":
            receipt.update({"task_intent":"仅添加目标语言字幕，保留原片、原中文字幕和所有原音轨",
                            "acceptance_criteria":["完整译文","字幕可读且同步","原片尺寸时长","所有原音轨内容与时间戳保持","实际字幕与原音检查"],
                            "checks_run":["subtitle_structure_and_source_binding","number_currency_and_terms",
                                          "all_original_audio_streams_copy_verified","full_decode",
                                          "dimensions_duration_audio","preview_extraction"],
                            "evidence":{"translation_reviewer":translation["reviewer"],
                                        "original_audio_preserved":audio_evidence["original_audio_preserved"],
                                        "original_audio_streams":audio_evidence["audio_streams"],
                                        "audio_policy":"original_copy","tts_used":False,"separation_used":False},
                            "limitations":["原中文旁白、音乐和音效有意保留，不生成外语配音"],
                            "unresolved_issues":["须实际检查字幕语义、断行、同步、遮挡与原音播放；原中文旁白为预期内容"]})
        save_json(work/"receipt.json",receipt)
        save_json(work/"state.json",{"fingerprint":fingerprint,"status":"technical_checks_passed_needs_actual_review"})
        return {"asset":asset["id"],"target":target,"status":receipt["status"],"receipt":str(work/"receipt.json")}
    except Exception as e:
        save_json(work/"state.json",{"fingerprint":fingerprint,"status":"blocked","reason":str(e)})
        raise


def execute(job_path,job):
    results=[]
    for asset in job["assets"]:
        for target in job["targets"]:
            try:
                results.append(render_variant(job_path,job,asset,target))
            except (Blocked,finish.FinishError,OSError,ValueError,KeyError,subprocess.TimeoutExpired) as e:
                results.append({"asset":asset["id"],"target":target,"status":"blocked","reason":str(e)})
    result={"results":results,"counts":{k:sum(r["status"]==k for r in results) for k in {r["status"] for r in results}}}
    save_json(job_path.parent/"batch-receipt.json",result)
    return result


def record_review(args):
    directory=Path(args.variant).resolve()
    receipt=read_json(directory/"receipt.json")
    evidence=read_json(args.evidence)
    if receipt.get('mode')=='erase_subtitles':
        if receipt.get('evidence',{}).get('original_audio_preserved') is not True:raise Blocked('去字幕通道缺完整原音轨保留证据')
        required={'visual','subtitle_erasure','temporal_consistency','protected_details','original_audio_preserved'}
        if receipt['evidence'].get('original_audio_streams',0):required.add('audio_full_playback')
    elif receipt.get("mode","dub")=="subtitles_only":
        if receipt.get("evidence",{}).get("original_audio_preserved") is not True:
            raise Blocked("仅字幕通道缺原音轨保留证据，不能记录通过")
        required={"visual","translation_semantics","subtitle_sync","original_audio_preserved"}
        if receipt["evidence"].get("original_audio_streams",0):
            required.add("audio_full_playback")
    else:
        required={"visual","audio_full_playback","translation_semantics","subtitle_sync","background","no_unintended_chinese"}
    if not required.issubset(evidence) or any(evidence[k] is not True for k in required):
        raise Blocked("实际视听/语义检查不完整或存在缺陷，先修复新版本，不改名过关")
    if not evidence.get("reviewer") or not evidence.get("notes"):
        raise Blocked("缺实际审阅者和具体观察记录")
    video_name='clean.mp4' if receipt.get('mode')=='erase_subtitles' else 'localized.mp4'
    if evidence.get("video_sha256")!=receipt["artifact_sha256"][video_name]:
        raise Blocked("审阅证据未绑定当前视频")
    for name,h in receipt["artifact_sha256"].items():
        if not (directory/name).is_file() or sha(directory/name)!=h:
            raise Blocked("产物在审阅后改变")
    if (directory/"review.json").exists(): raise Blocked("已有审阅记录，保留原记录；修订成果请建新版本")
    save_json(directory/"review.json",evidence)
    receipt["status"]="pending_user_review"
    receipt["unresolved_issues"]=[]
    receipt["checks_run"].append("actual_result_review_recorded")
    receipt["evidence"]["actual_review"]=str(directory/"review.json")
    save_json(directory/"receipt.json",receipt)
    return {"status":"pending_user_review","receipt":str(directory/"receipt.json"),"publication_authorized":False}


def doctor(args):
    report={"version":VERSION,"python":sys.executable,"ffmpeg":shutil.which("ffmpeg"),"ffprobe":shutil.which("ffprobe"),"network_calls":0}
    report["packages_current_python"]={m:bool(importlib.util.find_spec(m)) for m in ["faster_whisper","qwen_tts","chatterbox","edge_tts"]}
    if args.job:
        path,job=load_job(args.job)
        readiness=[]
        for target in job["targets"]:
            if production_mode(job)=="subtitles_only":
                readiness.append({"target":target,"configuration":"subtitle_lane","engine":"not_used",
                                  "tts_required":False,"separation_required":False,"original_audio":"copy_and_verify"})
                continue
            try: cfg=tts_settings(job,target); readiness.append({"target":target,"configuration":"present","engine":cfg["engine"],"runtime":"not_loaded_by_doctor"})
            except Blocked as e: readiness.append({"target":target,"configuration":"blocked","reason":str(e)})
        report["targets"]=readiness
        report["mode"]=production_mode(job)
    report["note"]="配置存在不等于模型能运行；doctor不下载、不上传、不合成、不证明语音质量"
    return report


def main():
    p=argparse.ArgumentParser(description="大硕跨境电商批量翻译配音 — independent local-first workflow")
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("languages")
    d=sub.add_parser("doctor"); d.add_argument("--job")
    i=sub.add_parser("init"); i.add_argument("--input",required=True);i.add_argument("--output",required=True);i.add_argument("--targets",required=True);i.add_argument("--runtime")
    i.add_argument("--mode",choices=MODES,default="dub",help="dub:完整配音；subtitles_only:仅加字幕并保留全部原音轨")
    i.add_argument('--no-runtime',action='store_true',help='不读取本机环境文件；用于独立部署或无模型测试')
    for name in ("transcribe","requests","run"):
        s=sub.add_parser(name);s.add_argument("--job",required=True)
    r=sub.add_parser("review");r.add_argument("--variant",required=True);r.add_argument("--evidence",required=True)
    e=sub.add_parser('erase');e.add_argument('--job',required=True,help='独立去字幕任务；不要求目标语言/译文/TTS')
    e.add_argument('--check-only',action='store_true',help='只读检查去字幕配置和输入身份，不运行模型')
    args=p.parse_args()
    try:
        if args.command=="languages": result=PROFILES
        elif args.command=="doctor": result=doctor(args)
        elif args.command=="init": result=initialize(args)
        elif args.command=="review": result=record_review(args)
        elif args.command=='erase':result=json.loads(run([sys.executable,ROOT/'scripts/erase_subtitles.py','--job',args.job]+(['--check-only'] if args.check_only else []),timeout=14400,error_log=Path(args.job).resolve().parent/'erasure-run-error.log'))
        else:
            path,job=load_job(args.job)
            with lock_job(path):
                result={"transcribe":transcribe,"requests":build_requests,"run":execute}[args.command](path,job)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 2 if isinstance(result,dict) and result.get("counts",{}).get("blocked") else 0
    except (Blocked,OSError,ValueError,KeyError,subprocess.TimeoutExpired) as e:
        print(json.dumps({"status":"blocked","reason":str(e)},ensure_ascii=False));return 2


if __name__=="__main__":
    sys.exit(main())
