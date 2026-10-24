"""Bailian stock-voice TTS. Env-only credentials; no enrollment or paid retries."""
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import wave


class BailianError(RuntimeError):
    pass


def settings(cfg, profile):
    preset = profile.get('bailian')
    if not preset:
        raise BailianError('No verified Bailian route for this target; no language substitution')
    chosen = dict(preset)
    for key in ('model', 'voice', 'family'):
        if cfg.get(key) is not None and cfg[key] != preset[key]:
            raise BailianError('Model/voice override is outside the verified target preset')
    if cfg.get('voice_type') in {'male'} and preset['voice_type'] != cfg['voice_type']:
        raise BailianError('Selected stock voice does not match the requested voice type')
    if cfg.get('cloud_allowed') is not True or cfg.get('paid_tts_allowed') is not True:
        raise BailianError('Bailian requires text-transfer and paid-TTS authorization')
    for key, default, low, high in [('speech_rate',1.0,.5,1.10), ('volume',100,0,100), ('pitch',1.0,.5,2)]:
        value=cfg.get(key,default)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
            raise BailianError('Invalid Bailian '+key)
        chosen[key]=value
    chosen['api_key_env']=cfg.get('api_key_env','DASHSCOPE_API_KEY')
    return chosen


def sambert(text, cfg, key):
    import dashscope
    from dashscope.audio.tts import SpeechSynthesizer
    # Reuse the previous Beijing channel, never redirect credentials to a job URL.
    dashscope.base_websocket_api_url='wss://dashscope.aliyuncs.com/api-ws/v1/inference'
    result=SpeechSynthesizer.call(model=cfg['model'],text=text,api_key=key,
        format='wav',sample_rate=16000,volume=int(cfg['volume']),
        rate=cfg['speech_rate'],pitch=cfg['pitch'])
    response=result.get_response() or {}
    audio=result.get_audio_data()
    if not audio:
        raise BailianError('Sambert rejected request: '+str(response.get('code','no_audio')))
    return audio, {'request_id':response.get('request_id'), 'usage':dict(response.get('usage') or {})}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise BailianError('Unexpected provider redirect; credentials not forwarded')


def minimax(text, cfg, key):
    payload={'model':cfg['model'],'input':{'action':'tts','text':text,
        'voice_setting':{'voice_id':cfg['voice'],'speed':cfg['speech_rate'],
                         'vol':cfg['volume']/100,'pitch':0},
        'audio_setting':{'format':'wav','sample_rate':24000,'channel':1},
        'language_boost':cfg['language_boost'],'output_format':'hex'}}
    req=urllib.request.Request('https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation',
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    opener=urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req,timeout=120) as response:
            body=json.load(response)
    except urllib.error.HTTPError as exc:
        # Preserve only a service error code; never save headers or raw responses.
        try:
            error=json.loads(exc.read());code=error.get('code','unknown')
            message=str(error.get('message',''))[:600].replace(key,'[REDACTED]')
            message=re.sub(r'https?://\S+','[URL]',message)
        except Exception: code='unknown';message=''
        raise BailianError(f'Bailian HTTP {exc.code}: {code}: {message}') from None
    output=body.get('output') or {}
    if output.get('base_resp',{}).get('status_code',0)!=0:
        raise BailianError('MiniMax rejected request: '+str(output['base_resp']['status_code']))
    value=(output.get('data') or {}).get('audio') or output.get('audio')
    if not isinstance(value,str) or not value:
        raise BailianError('MiniMax returned no audio; no automatic retry')
    audio=bytes.fromhex(value)
    return audio, {'request_id':body.get('request_id'), 'usage':dict(body.get('usage') or {}),
        'provider_usage_characters':output.get('extra_info',{}).get('usage_characters')}


def write_json(path,value):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)


def generate(cues,cfg,profile,output,cache,transport=None):
    chosen=settings(cfg,profile)
    key=os.environ.get(chosen['api_key_env'])
    if not key:
        raise BailianError('Missing configured API-key environment variable')
    output=Path(output);cache=Path(cache)
    output.mkdir(parents=True,exist_ok=True);cache.mkdir(parents=True,exist_ok=True)
    receipt=[]
    for cue in cues:
        if not isinstance(cue['text'],str) or not 0<len(cue['text'])<10000:
            raise BailianError('Invalid or oversized TTS text')
        # Persistent across vN folders: a render failure cannot issue paid TTS again.
        identity={k:v for k,v in chosen.items() if k!='api_key_env'}
        identity['text']=cue['text']
        fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        state=cache/(fingerprint+'.json');raw=cache/(fingerprint+'.raw.wav');wav=cache/(fingerprint+'.wav')
        destination=output/f"{cue['id']:05}.wav"
        if destination.exists():
            raise BailianError('Refusing to overwrite existing speech')
        reused=state.exists()
        if state.exists():
            record=json.loads(state.read_text(encoding='utf-8'))
            if record.get('status')!='complete' or not wav.is_file() or hashlib.sha256(wav.read_bytes()).hexdigest()!=record.get('sha256'):
                raise BailianError('Prior paid request incomplete/unknown; reconcile before explicit retry')
        else:
            record={'status':'request_started','model':chosen['model'],'voice':chosen['voice'],
                    'input_characters':len(cue['text']),'created_at':time.time()}
            # Exclusive ledger creation blocks concurrent duplicate requests.
            with state.open('x',encoding='utf-8') as handle:json.dump(record,handle)
            try:
                call=transport or (sambert if chosen['family']=='sambert' else minimax)
                audio,metadata=call(cue['text'],chosen,key)
                record.update(metadata);record['status']='audio_received';write_json(state,record)
                raw.write_bytes(audio)
                subprocess.run(['ffmpeg','-v','error','-n','-i',str(raw),'-ac','1','-ar','24000','-c:a','pcm_s16le',str(wav)],
                               check=True,capture_output=True)
                with wave.open(str(wav),'rb') as handle:
                    if handle.getsampwidth()!=2 or handle.getnframes()<1200:
                        raise BailianError('Invalid synthesized PCM audio')
                record.update(status='complete',sha256=hashlib.sha256(wav.read_bytes()).hexdigest())
                write_json(state,record)
            except Exception as exc:
                record['status']='failed_or_unknown'
                # Known service codes only. Arbitrary exception text may contain secrets.
                record['error']=str(exc) if isinstance(exc,BailianError) else type(exc).__name__
                write_json(state,record)
                raise BailianError(record['error']+'; paid request will not auto-repeat') from None
        shutil.copyfile(wav,destination)
        receipt.append({'id':cue['id'],'model':chosen['model'],'voice':chosen['voice'],
            'request_id':record.get('request_id'),'usage':record.get('usage'),
            'provider_usage_characters':record.get('provider_usage_characters'),
            'input_characters':len(cue['text']),'reused':reused,'ledger':str(state)})
        write_json(output.parent/'tts-usage.json',{'provider':'aliyun-bailian','items':receipt,
            'account_remaining_quota':None,'quota_note':'TTS usage is not account balance or remaining quota'})
