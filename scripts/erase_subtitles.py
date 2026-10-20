"""Separate local subtitle-removal lane. Existing translation lanes are untouched."""
from pathlib import Path
import argparse,importlib.util,json,sys

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('erasure_core',ROOT/'scripts/localize.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def erase_asset(job_path,job,asset):
    base=job_path.parent;source=m.relative_path(asset['video'],base)
    if m.sha(source)!=asset['source_sha256']:raise m.Blocked('去字幕原视频身份变化；另建版本，不能复用旧结果')
    config={**job['providers']['erasure'],**asset.get('erasure',{}).get('model_override',{})}
    if config.get('engine')!='migan':raise m.Blocked('本地去字幕仅支持已部署MI-GAN，不自动换云端/模糊/裁切')
    model=Path(config.get('model',''))
    if not model.is_file() or m.sha(model)!=config.get('model_sha256'):raise m.Blocked('去字幕模型缺失或身份不符；不自动下载')
    erasure=asset.get('erasure',{});regions=erasure.get('regions',[])
    if not regions or not all(m.valid_region(r) for r in regions):raise m.Blocked('缺明确、有效的字幕区域；不默认擦整幅画面')
    if erasure.get('region_reviewed') is not True or not erasure.get('review_note'):
        raise m.Blocked('先实看原片确认字幕区域和敏感细节，再记录region_reviewed/review_note')
    protected=erasure.get('protected_regions',[])
    if not all(m.valid_region(r) for r in protected):raise m.Blocked('保护区域无效')
    dilation=config.get('mask_dilate',12)
    if isinstance(dilation,bool) or not isinstance(dilation,int) or not 6<=dilation<=20:raise m.Blocked('mask_dilate须为6至20像素；过大需重新检查商品纹理')
    info=m.probe(source);video=next(s for s in info['streams'] if s['codec_type']=='video')
    if video.get('r_frame_rate')!=video.get('avg_frame_rate') or video.get('tags',{}).get('rotate') or any(s.get('rotation',0) for s in video.get('side_data_list',[])):
        raise m.Blocked('当前去字幕通道只处理已规范方向的恒定帧率视频；不能静默改时轴')
    cache=erasure.get('repair_cache')
    if cache:
        old=m.read_json(cache['request'])
        if m.sha(cache['request'])!=cache['request_sha256'] or old['source_sha256']!=asset['source_sha256'] or old['regions']!=regions or old['config']['model_sha256']!=config['model_sha256'] or old['config'].get('mask_dilate',12)!=dilation:
            raise m.Blocked('修复候选缓存来源、模型或遮罩设置不符')
        old_receipt=m.read_json(cache['receipt'])
        if Path(old['output']).resolve()!=Path(cache['receipt']).resolve().parent:raise m.Blocked('缓存输出/回执目录不一致')
        config['repair_cache']={k:cache[k] for k in ('receipt','receipt_sha256')}
    identity={'source':asset['source_sha256'],'config':config,'regions':regions,'protected':protected,
              'worker':m.sha(ROOT/'scripts/subtitle_erase_worker.py'),'runner':m.sha(__file__),'version':m.VERSION}
    if job.get('_check_only'):
        return {'asset':asset['id'],'status':'configuration_present','runtime':'not_loaded','model_identity_checked':True}
    fingerprint=m.digest(identity);parent=base/'erasures'/asset['id'];parent.mkdir(parents=True,exist_ok=True)
    for old in sorted(parent.glob('v*/receipt.json')):
        record=m.read_json(old)
        if record.get('fingerprint')==fingerprint and all((old.parent/name).is_file() and m.sha(old.parent/name)==value for name,value in record['artifact_sha256'].items()):
            return {'asset':asset['id'],'status':'reused_checked_bytes','receipt':str(old)}
    versions=[int(p.name[1:]) for p in parent.glob('v*') if p.is_dir() and p.name[1:].isdigit()]
    work=parent/f'v{max(versions,default=0)+1}';work.mkdir()
    m.save_json(work/'state.json',{'fingerprint':fingerprint,'status':'started'})
    try:
        request=work/'worker-request.json';folder=work/'worker'
        m.save_json(request,{'source':str(source),'source_sha256':asset['source_sha256'],'output':str(folder),
          'fps':video['avg_frame_rate'],'regions':regions,'protected_regions':protected,
          'expected_subtitles':erasure.get('expected_subtitles',True),'config':config})
        m.run([config.get('python',sys.executable),ROOT/'scripts/subtitle_erase_worker.py',request],timeout=7200,error_log=work/'worker-error.log')
        generated=m.read_json(folder/'worker-receipt.json')
        if generated['request_sha256']!=m.sha(request) or generated['worker_sha256']!=identity['worker'] or generated['source_sha256']!=asset['source_sha256'] or generated['model_sha256']!=config['model_sha256']:
            raise m.Blocked('去字幕worker来源/请求/代码身份不符')
        if generated['outside_mask_changed_frames_before_encode']!=0 or not generated['all_frames_processed']:
            raise m.Blocked('遮罩外画面变化或存在漏帧')
        output=work/'clean.mp4'
        m.run(['ffmpeg','-v','error','-n','-i',generated['video'],'-i',source,'-map','0:v:0','-map','1:a?',
               '-c','copy','-map_metadata','1','-movflags','+faststart',output])
        out=m.probe(output);outv=next(s for s in out['streams'] if s['codec_type']=='video')
        if (outv['width'],outv['height'],outv.get('nb_frames'),outv['avg_frame_rate'])!=(video['width'],video['height'],video.get('nb_frames'),video['avg_frame_rate']) or abs(float(out['format']['duration'])-float(info['format']['duration']))>.05:
            raise m.Blocked('去字幕成片尺寸/帧数/帧率/时长变化')
        audio=m.verify_original_audio(source,output);m.save_json(work/'audio-preservation.json',audio)
        m.run(['ffmpeg','-v','error','-i',output,'-f','null','-'],timeout=3600)
        preview=work/'previews';preview.mkdir()
        for index in generated['samples']:
            m.run(['ffmpeg','-v','error','-n','-ss',str(index*float(video['avg_frame_rate'].split('/')[1])/float(video['avg_frame_rate'].split('/')[0])),
                   '-i',output,'-frames:v','1',preview/f'frame-{index:05}.png'])
        hashes={'clean.mp4':m.sha(output),'audio-preservation.json':m.sha(work/'audio-preservation.json'),
                'worker/worker-receipt.json':m.sha(folder/'worker-receipt.json'),'worker/frame-metrics.json':m.sha(folder/'frame-metrics.json')}
        record={'task_intent':'本地去除硬字幕，完整保留原声和原视频时序','target_environment':'MI-GAN ONNX CPU + OpenCV + FFmpeg',
          'acceptance_criteria':['文字/描边/阴影无残留','无明显闪烁或修复伪影','保护商品和人物细节','原音轨/帧数/尺寸/时轴保留','实际完整观看'],
          'artifacts':{name:str(work/name) for name in hashes},'artifact_sha256':hashes,'fingerprint':fingerprint,'mode':'erase_subtitles',
          'checks_run':['source/model/request/worker identity','all frames processed','outside-mask pixels identical before encode',
            'original audio codec packets and timestamps identical','frame count/dimensions/fps/duration','full decode','actual-output previews extracted'],
          'evidence':{'worker_receipt':str(folder/'worker-receipt.json'),'region_review':erasure['review_note'],
            'original_audio_preserved':True,'original_audio_streams':audio['audio_streams'],'frames':generated['frames'],
            'processed_frames':generated['processed_frames'],'preview_directory':str(preview),'reused_repair_frames':generated['reused_repair_frames']},
          'unresolved_issues':['须完整检查字幕残影、闪烁和商品/人物修复细节；不能从模型成功推定无痕'],
          'limitations':['仅定位区域内的中性白字黑描边，非通用OCR','逐帧神经修复，非时序视频模型','遮挡内容为推测恢复，不能保证原始纹理','遮罩外像素不变证据在编码前，MP4有正常压缩差异'],
          'status':'verification_incomplete','technical_checks_passed':True,'publication_authorized':False,'cloud_calls':0,'paid_api_calls':0}
        m.save_json(work/'receipt.json',record)
        m.save_json(work/'state.json',{'fingerprint':fingerprint,'status':'technical_checks_passed_needs_actual_review'})
        return {'asset':asset['id'],'status':record['status'],'receipt':str(work/'receipt.json')}
    except Exception as exc:
        m.save_json(work/'state.json',{'fingerprint':fingerprint,'status':'blocked','reason':str(exc)})
        raise

def execute(path,check_only=False):
    path=Path(path).resolve();job=m.read_json(path)
    if job.get('schema_version')!=1 or job.get('mode')!='erase_subtitles' or not job.get('assets'):
        raise m.Blocked('去字幕使用独立mode=erase_subtitles任务，不改变已有翻译通道')
    identifiers=[a.get('id','') for a in job['assets']]
    if len(set(identifiers))!=len(identifiers) or any(not m.re.fullmatch(r'[A-Za-z0-9_-]{1,100}',i) for i in identifiers):raise m.Blocked('资产ID无效或重复')
    results=[]
    job['_check_only']=check_only
    with (m.contextlib.nullcontext() if check_only else m.lock_job(path)):
        for asset in job['assets']:
            try:results.append(erase_asset(path,job,asset))
            except (m.Blocked,OSError,ValueError,KeyError,m.subprocess.TimeoutExpired) as exc:results.append({'asset':asset['id'],'status':'blocked','reason':str(exc)})
        result={'results':results,'counts':{s:sum(r['status']==s for r in results) for s in {r['status'] for r in results}}}
        if not check_only:m.save_json(path.parent/'erasure-batch-receipt.json',result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--job',required=True);p.add_argument('--check-only',action='store_true');args=p.parse_args()
    try:
        result=execute(args.job,args.check_only);print(json.dumps(result,ensure_ascii=False,indent=2))
        sys.exit(2 if result['counts'].get('blocked') else 0)
    except (m.Blocked,OSError,ValueError,KeyError,m.subprocess.TimeoutExpired) as exc:
        print(json.dumps({'status':'blocked','reason':str(exc)},ensure_ascii=False));sys.exit(2)
