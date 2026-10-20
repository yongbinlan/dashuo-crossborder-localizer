"""Offline BandIt v2 three-stem worker; paths and identities supplied by caller."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,time,types

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    request=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
    cfg=request['config'];source=Path(request['source']);output=Path(request['output'])
    if source.resolve()==output.resolve() or output.exists():raise RuntimeError('Refusing existing separation output')
    if sha(source)!=request['source_sha256'] or sha(cfg['checkpoint'])!=cfg['checkpoint_sha256']:
        raise RuntimeError('Source/checkpoint identity changed')
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    repo=Path(cfg['repo']);sys.path.insert(0,str(repo))
    import torch,numpy as np,soundfile as sf,yaml
    from src.models.bandit.bandit import Bandit
    torch.set_num_threads(4)
    device=cfg.get('device','cuda')
    if device=='cuda' and not torch.cuda.is_available():raise RuntimeError('Configured CUDA unavailable; no silent fallback')
    model_config=yaml.safe_load((repo/'configs/models/bandit-mus64.yaml').read_text(encoding='utf-8'))['kwargs']
    state=torch.load(cfg['checkpoint'],map_location='cpu',weights_only=True)
    weights={k.removeprefix('model.'):v for k,v in state['state_dict'].items() if k.startswith('model.')}
    model=Bandit(fs=48000,stems=['speech','music','sfx'],**model_config)
    model.load_state_dict(weights,strict=True);del weights,state
    model=model.eval().to(device)
    path=repo/'src/system/inference_handler.py';code=path.read_text(encoding='utf-8')
    old='from torchaudio.io import StreamReader'
    if code.count(old)!=1:raise RuntimeError('Author compatibility adapter changed; inspect before using')
    code='from __future__ import annotations\n'+code.replace(old,'# Unused legacy StreamReader import omitted in memory')
    module=types.ModuleType('bandit_author_tensor_handler');module.__file__=str(path)
    exec(compile(code,str(path),'exec'),module.__dict__)
    handler=module.StandardTensorChunkedInferenceHandler(chunk_size_seconds=8.,hop_size_seconds=1.,inference_batch_size=1,fs=48000).to(device)
    output.mkdir(parents=True)
    audio=output/'source-48k-stereo.wav'
    subprocess.run(['ffmpeg','-v','error','-n','-i',str(source),'-vn','-ar','48000','-ac','2','-c:a','pcm_f32le',str(audio)],check=True,capture_output=True)
    x,sr=sf.read(audio,dtype='float32',always_2d=True)
    if not np.isfinite(x).all():raise RuntimeError('Non-finite source')
    # Author reflect padding requires at least the front pad length (14s).
    # Pad short clips, infer, then remove only the added tail. Original samples retained.
    inference_x=np.pad(x,((0,max(0,handler.front_pad_samples+1-len(x))),(0,0)))
    mixture=torch.from_numpy(inference_x.T.copy())[None].to(device)
    started=time.monotonic()
    with torch.inference_mode():result=handler(mixture,model)
    if device=='cuda':torch.cuda.synchronize()
    if set(result['estimates'])!={'speech','music','sfx'}:raise RuntimeError('Missing expected stems')
    stems={}
    for stem,value in result['estimates'].items():
        data=value['audio'][0].detach().cpu().numpy().T[:len(x)]
        if data.shape!=x.shape or not np.isfinite(data).all():raise RuntimeError('Invalid stem dimensions/data')
        dest=output/f'{stem}.wav';sf.write(dest,data,sr,subtype='FLOAT')
        stems[stem]={'file':str(dest),'sha256':sha(dest),'shape':list(data.shape),
         'rms_dbfs':float(20*np.log10(np.sqrt(np.mean(data.astype(np.float64)**2))+1e-12)),
         'peak_dbfs':float(20*np.log10(np.max(np.abs(data))+1e-12))}
    record={'source_sha256':request['source_sha256'],'signature':request['signature'],
     'checkpoint_sha256':cfg['checkpoint_sha256'],'weights_loaded_strictly':True,
     'sample_rate':sr,'samples':len(x),'stems':stems,'elapsed_seconds':time.monotonic()-started,
     'inference':{'chunk_seconds':8,'hop_seconds':1,'batch':1,'device':device,'short_tail_pad_samples':len(inference_x)-len(x)},
     'full_audio_review':False,'status':'separated_needs_listening'}
    (output/'receipt.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':record['status'],'elapsed_seconds':record['elapsed_seconds']}),flush=True)

if __name__=='__main__':main()
