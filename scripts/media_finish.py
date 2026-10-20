"""Local loudness finishing and identity-checked picture reuse. No model/network calls."""
from pathlib import Path
import hashlib
import json
import math
import re
import subprocess


class FinishError(RuntimeError):
    pass


def save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def call(args):
    p=subprocess.run([str(a) for a in args], capture_output=True, timeout=3600)
    if p.returncode: raise FinishError(p.stderr.decode('utf-8','replace')[-1800:])
    return p.stdout.decode('utf-8','replace'), p.stderr.decode('utf-8','replace')


def settings(render):
    defaults={'enabled':True,'voice_i':-20.,'voice_tp':-3.,'delivery_i':-16.,'delivery_tp':-1.5,'lra':9.}
    cfg=render.get('loudness',{})
    if not isinstance(cfg,dict) or set(cfg)-set(defaults): raise FinishError('未知响度配置字段')
    cfg={**defaults,**cfg}
    if not isinstance(cfg['enabled'],bool): raise FinishError('loudness.enabled必须是布尔值')
    for key,lo,hi in [('voice_i',-30,-14),('voice_tp',-9,-1.5),('delivery_i',-24,-14),('delivery_tp',-9,-1.5),('lra',1,20)]:
        v=cfg[key]
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
            raise FinishError('响度参数越界: '+key)
    return cfg


def measure(path, log, target=-16, peak=-1.5, lra=9):
    _,stderr=call(['ffmpeg','-hide_banner','-nostats','-i',path,'-map','0:a:0',
        '-af',f'loudnorm=I={target}:TP={peak}:LRA={lra}:print_format=json','-f','null','-'])
    Path(log).write_text(stderr,encoding='utf-8')
    found=re.findall(r'\{\s*"input_i".*?\}',stderr,re.S)
    if not found: raise FinishError('FFmpeg未返回响度数据')
    data=json.loads(found[-1])
    if any(not math.isfinite(float(data[k])) for k in ('input_i','input_tp','input_lra','input_thresh','target_offset')):
        raise FinishError('静音或不可测响度，保留输入并阻断；不无限放大')
    return data


def normalize(path,out,target,peak,lra):
    out=Path(out)
    info=json.loads(call(['ffprobe','-v','error','-select_streams','a:0','-show_streams','-of','json',path])[0])['streams'][0]
    rate=int(info['sample_rate']); channels=int(info['channels'])
    # Inputs are PCM WAVs; time_base and duration_ts give exact sample length.
    num,den=map(int,info['time_base'].split('/'))
    frames=round(int(info['duration_ts'])*num/den*rate)
    first=measure(path,out.with_suffix('.pass1.log'),target,peak,lra)
    filt=(f'loudnorm=I={target}:TP={peak}:LRA={lra}:'
          f'measured_I={first["input_i"]}:measured_TP={first["input_tp"]}:'
          f'measured_LRA={first["input_lra"]}:measured_thresh={first["input_thresh"]}:'
          f'offset={first["target_offset"]}:linear=true:print_format=json,'
          f'aresample={rate},apad=whole_len={frames},atrim=end_sample={frames}')
    _,stderr=call(['ffmpeg','-hide_banner','-nostats','-n','-i',path,'-map','0:a:0',
                   '-af',filt,'-c:a','pcm_f32le',out])
    out.with_suffix('.pass2.log').write_text(stderr,encoding='utf-8')
    applied=json.loads(re.findall(r'\{\s*"input_i".*?\}',stderr,re.S)[-1])
    actual=measure(out,out.with_suffix('.actual.log'),target,peak,lra)
    after=json.loads(call(['ffprobe','-v','error','-select_streams','a:0','-show_streams','-of','json',out])[0])['streams'][0]
    anum,aden=map(int,after['time_base'].split('/'))
    after_frames=round(int(after['duration_ts'])*anum/aden*int(after['sample_rate']))
    if after_frames!=frames or int(after['sample_rate'])!=rate or int(after['channels'])!=channels:
        raise FinishError('响度处理改变采样长度/采样率/声道数')
    if abs(float(actual['input_i'])-target)>1 or float(actual['input_tp'])>peak+.2:
        raise FinishError('响度校准未达到目标；保留日志，不自动重试')
    return {'input':str(path),'input_sha256':sha(path),'output':str(out),'output_sha256':sha(out),
            'target_i':target,'target_tp':peak,'applied_mode':applied['normalization_type'],
            'before':first,'after':actual,'samples':frames,'sample_rate':rate,'channels':channels,
            'timeline_policy':'same sample count; no speed change or internal cuts'}


def voice(work,dub,render):
    cfg=settings(render)
    if not cfg['enabled']:
        save(Path(work)/'loudness-receipt.json',{'enabled':False,'reason':'explicit job configuration'})
        return dub
    out=Path(work)/'dub-calibrated.wav'
    record=normalize(dub,out,cfg['voice_i'],cfg['voice_tp'],cfg['lra'])
    save(Path(work)/'loudness-receipt.json',{'enabled':True,'settings':cfg,'voice':record,'audio_full_playback':False})
    return out


def delivery(work,mixed,render):
    cfg=settings(render)
    if not cfg['enabled']: return mixed
    out=Path(work)/'mix-delivery.wav'
    record=normalize(mixed,out,cfg['delivery_i'],cfg['delivery_tp'],cfg['lra'])
    path=Path(work)/'loudness-receipt.json'; receipt=json.loads(path.read_text(encoding='utf-8'))
    receipt['delivery']=record;save(path,receipt)
    return out


def verify_encoded(work,video,render):
    cfg=settings(render)
    if not cfg['enabled']: return {'enabled':False}
    data=measure(video,Path(work)/'encoded-loudness.log',cfg['delivery_i'],cfg['delivery_tp'],cfg['lra'])
    if abs(float(data['input_i'])-cfg['delivery_i'])>1 or float(data['input_tp'])>cfg['delivery_tp']+.5:
        raise FinishError('最终AAC响度或真峰值不合格，保留版本与日志')
    path=Path(work)/'loudness-receipt.json';receipt=json.loads(path.read_text(encoding='utf-8'))
    receipt['final_encoded']={'file':str(video),'sha256':sha(video),'measurements':data}
    save(path,receipt)
    return receipt['final_encoded']


def video_identity(path):
    data=json.loads(call(['ffprobe','-v','error','-select_streams','v:0','-show_streams','-show_packets',
        '-show_data_hash','sha256','-show_entries',
        'stream=codec_name,profile,width,height,pix_fmt,time_base,extradata_hash,color_range,color_space,color_transfer,color_primaries:packet=pts,dts,duration,size,data_hash',
        '-of','json',path])[0])
    if not data.get('packets') or any(not p.get('data_hash') for p in data['packets']):
        raise FinishError('无法核验视频包身份')
    # ffprobe also emits incidental tags/disposition. A muxer encoder tag is not
    # encoded picture data; bind only codec configuration and actual packet timing.
    keys=('codec_name','profile','width','height','pix_fmt','time_base','extradata_hash',
          'color_range','color_space','color_transfer','color_primaries')
    data['streams']=[{k:s.get(k) for k in keys} for s in data['streams']]
    data['packets']=[{k:p.get(k) for k in ('pts','dts','duration','size','data_hash')} for p in data['packets']]
    raw=json.dumps(data,sort_keys=True).encode()
    return {'packet_count':len(data['packets']),'all_packets_codec_timestamps_sha256':hashlib.sha256(raw).hexdigest()}


def picture_signature(source,ass,vf):
    return hashlib.sha256(json.dumps({'source_sha256':sha(source),'ass_sha256':sha(ass) if vf else None,
        'filter':vf,'encoding':'libx264/medium/CRF19/yuv420p/passthrough-v1'},sort_keys=True).encode()).hexdigest()


def find_picture(parent,signature):
    for path in sorted(Path(parent).glob('v*/picture-preservation.json'),reverse=True):
        try:
            record=json.loads(path.read_text(encoding='utf-8'))
            receipt=json.loads((path.parent/'receipt.json').read_text(encoding='utf-8'))
            candidate=path.parent/'localized.mp4'
            if (record['signature']==signature and receipt.get('technical_checks_passed')
                and sha(candidate)==record['output_sha256'] and video_identity(candidate)==record['output_video_identity']):
                return candidate
        except (OSError,KeyError,ValueError,FinishError):
            continue  # Damaged candidates are preserved; encode from original instead.
    return None


def picture_receipt(work,source,output,signature,copied_from):
    actual=video_identity(output)
    reference=video_identity(copied_from) if copied_from else None
    if reference is not None and reference!=actual:
        raise FinishError('复制的视频包/编解码配置/时间戳不一致；不静默重编码')
    record={'signature':signature,'source':str(source),'source_sha256':sha(source),
        'output_sha256':sha(output),'output_video_identity':actual,
        'mode':'stream_copy_verified' if copied_from else 'single_encode_from_original',
        'copied_from':str(copied_from) if copied_from else None,
        'copied_video_identity':reference,'no_additional_video_encode':bool(copied_from),
        'scope':'copy identity relative to copied_from; a prior subtitle encode may already be lossy',
        'visual_review':False}
    save(Path(work)/'picture-preservation.json',record)
    return record
