"""Local white/outlined subtitle masking and MI-GAN repair. No remote calls."""
from pathlib import Path
import hashlib,json,sys,time,subprocess,zlib
import cv2,numpy as np

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def detect_mask(bgr,region,dilate=12,allow_short=True):
    h,w=bgr.shape[:2];x,y,rw,rh=region
    x0,y0,x1,y1=round(x*w),round(y*h),round((x+rw)*w),round((y+rh)*h)
    roi=bgr[y0:y1,x0:x1]
    core=((roi.min(2)>=200)&((roi.max(2).astype(int)-roi.min(2).astype(int))<=45)).astype(np.uint8)
    dark=(roi.max(2)<110)
    count,labels,stats,centers=cv2.connectedComponentsWithStats(core,8)
    seeds=[]
    for n in range(1,count):
        cx,cy,cw,ch,area=stats[n]
        if not (16<=area<=4000 and 12<=ch<=80 and cw<=100):continue
        small=(labels==n).astype(np.uint8)
        ring=cv2.dilate(small,np.ones((9,9),np.uint8))-small
        if (dark&(ring>0)).sum()/max(1,ring.sum())<.12:continue
        seeds.append((cx,cy,cw,ch))
    mask=np.zeros((h,w),np.uint8)
    if not seeds or (not allow_short and len(seeds)<3):return mask
    # Narrow, explicitly reviewed region; avoid treating a broad white object as text.
    left=min(s[0] for s in seeds);top=min(s[1] for s in seeds)
    right=max(s[0]+s[2] for s in seeds);bottom=max(s[1]+s[3] for s in seeds)
    if bottom-top>84:return mask
    if len(seeds)<3 or right-left<80:
        # A centered single Chinese character such as 嗯 is still a subtitle.
        center=(left+right)/2
        if not allow_short or right-left<18 or bottom-top<18 or abs(center-core.shape[1]/2)>core.shape[1]*.16:
            return mask
    bounded=np.zeros_like(core);bounded[max(0,top-3):min(core.shape[0],bottom+3),max(0,left-3):min(core.shape[1],right+3)]=1
    glyphs=core*bounded
    kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*dilate+1,2*dilate+1))
    local=cv2.dilate(glyphs,kernel)
    mask[y0:y1,x0:x1]=local*255
    return mask

def load_model(cfg):
    import onnxruntime as ort
    path=Path(cfg['model'])
    if sha(path)!=cfg['model_sha256']:raise RuntimeError('MI-GAN model identity changed')
    options=ort.SessionOptions();options.intra_op_num_threads=4;options.inter_op_num_threads=1
    session=ort.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider'])
    return session

def repair_frame(frame,mask,session):
    if not np.any(mask):return frame
    ys,xs=np.where(mask>0);h,w=mask.shape
    side=max(512,int(max(xs.max()-xs.min(),ys.max()-ys.min())+160))
    x0=max(0,min(w-side,int((xs.max()+xs.min()-side)/2)));x1=min(w,x0+side)
    y0=max(0,min(h-side,int((ys.max()+ys.min()-side)/2)));y1=min(h,y0+side)
    region=frame[y0:y1,x0:x1];local=mask[y0:y1,x0:x1]
    rgb=cv2.cvtColor(region,cv2.COLOR_BGR2RGB)
    result=session.run(None,{'image':rgb.transpose(2,0,1)[None],'mask':(255-local)[None,None]})[0]
    result=result[0].transpose(1,2,0)
    if result.shape!=rgb.shape:raise RuntimeError('Unexpected MI-GAN output dimensions')
    result=cv2.cvtColor(result,cv2.COLOR_RGB2BGR)
    alpha=np.minimum(cv2.distanceTransform((local>0).astype(np.uint8),cv2.DIST_L2,3)/3.,1.)[:,:,None]
    blend=np.clip(np.rint(region*(1-alpha)+result*alpha),0,255).astype(np.uint8)
    output=frame.copy();output[y0:y1,x0:x1]=blend
    if not np.array_equal(output[mask==0],frame[mask==0]):raise RuntimeError('Pixels outside mask changed')
    return output

def isolated_gap_mask(frame,current,previous,following):
    """Repair a detector dropout only with matching adjacent glyphs + actual ink."""
    if np.any(current):return current
    union=(previous>0)|(following>0)
    if not np.any(union):return current
    if ((previous>0)&(following>0)).sum()/union.sum()<.8:return current
    pixels=frame[union]
    white=(pixels.min(1)>=200)&((pixels.max(1).astype(int)-pixels.min(1).astype(int))<=45)
    dark=pixels.max(1)<110
    if white.sum()<80 or dark.sum()<80:return current
    return union.astype(np.uint8)*255

def main():
    request=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
    source=Path(request['source']);folder=Path(request['output']);cfg=request['config']
    if sha(source)!=request['source_sha256']:raise RuntimeError('Source identity changed')
    folder.mkdir(parents=True,exist_ok=False)
    session=load_model(cfg)
    capture=cv2.VideoCapture(str(source))
    if not capture.isOpened():raise RuntimeError('Cannot open source video')
    width=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH));height=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps=float(capture.get(cv2.CAP_PROP_FPS));expected=int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    # Lightweight first pass stores compressed masks, not full-resolution frames.
    # It permits adjacent-frame confirmation of a rare one-frame detector dropout.
    scanner=cv2.VideoCapture(str(source));raw_masks=[]
    while True:
        okay,frame=scanner.read()
        if not okay:break
        mask=np.zeros((height,width),np.uint8)
        for region in request['regions']:mask=cv2.bitwise_or(mask,detect_mask(frame,region,cfg.get('mask_dilate',12)))
        raw_masks.append(zlib.compress(mask.tobytes(),1))
    scanner.release()
    if len(raw_masks)!=expected:raise RuntimeError('Mask scan did not cover every frame')
    def raw_mask(i):return np.frombuffer(zlib.decompress(raw_masks[i]),np.uint8).reshape(height,width)
    silent=folder/'silent.mp4'
    rows=[];changed=0;started=time.monotonic();outside_changed=0;reused=0;cache_capture=None
    cache=cfg.get('repair_cache')
    if cache:
        receipt=json.loads(Path(cache['receipt']).read_text(encoding='utf-8'))
        if sha(cache['receipt'])!=cache['receipt_sha256'] or receipt['source_sha256']!=request['source_sha256'] or receipt['model_sha256']!=cfg['model_sha256']:
            raise RuntimeError('Repair cache source/model/receipt identity changed')
        if sha(receipt['video'])!=receipt['video_sha256']:raise RuntimeError('Cached video identity changed')
        cache_capture=cv2.VideoCapture(receipt['video'])
        if not cache_capture.isOpened() or int(cache_capture.get(cv2.CAP_PROP_FRAME_COUNT))!=expected:
            raise RuntimeError('Invalid repair cache frame count')
    stderr=(folder/'encode.log').open('wb')
    encoder=subprocess.Popen(['ffmpeg','-v','error','-n','-f','rawvideo','-pix_fmt','bgr24','-s',f'{width}x{height}',
      '-r',request['fps'],'-i','pipe:0','-an','-c:v','libx264','-preset','medium','-crf','16','-pix_fmt','yuv420p',str(silent)],stdin=subprocess.PIPE,stderr=stderr)
    previews=folder/'previews';previews.mkdir()
    boundaries=[];previous_mask=None;previous_gray=None
    try:
        index=0
        while True:
            okay,frame=capture.read()
            if not okay:break
            mask=raw_mask(index)
            temporal_gap=False
            if 0<index<expected-1 and not np.any(mask):
                corrected=isolated_gap_mask(frame,mask,raw_mask(index-1),raw_mask(index+1))
                temporal_gap=bool(np.any(corrected));mask=corrected
            for protected in request.get('protected_regions',[]):
                x,y,w,h=protected;x0,y0,x1,y1=round(x*width),round(y*height),round((x+w)*width),round((y+h)*height)
                if np.any(mask[y0:y1,x0:x1]):raise RuntimeError('manual_review_required: subtitle overlaps protected region')
            count=int((mask>0).sum())
            if count>width*height*.04:raise RuntimeError('Mask exceeds 4% frame safety budget; inspect region/style')
            cached=None
            if cache_capture is not None:
                okay,cached=cache_capture.read()
                if not okay:raise RuntimeError('Repair cache truncated')
            if count:
                old_mask=np.zeros_like(mask)
                if cached is not None:
                    for region in request['regions']:
                        old_mask=cv2.bitwise_or(old_mask,detect_mask(frame,region,cfg.get('mask_dilate',12),allow_short=False))
                if cached is not None and np.array_equal(mask,old_mask):
                    result=frame.copy();result[mask>0]=cached[mask>0];reused+=1
                else:result=repair_frame(frame,mask,session)
                changed+=1
            else:result=frame
            # This invariant is before codec conversion/compression, not final MP4 pixel identity.
            outside_changed+=int(np.any(result[mask==0]!=frame[mask==0]))
            union=(mask>0)
            iou=1.
            if previous_mask is not None:
                combined=np.logical_or(union,previous_mask).sum()
                iou=float(np.logical_and(union,previous_mask).sum()/combined) if combined else 1.
            gray=cv2.resize(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),(96,96))
            cut=float(np.mean(np.abs(gray.astype(float)-previous_gray))) if previous_gray is not None else 0.
            if index%30==15 or iou<.65 or index==0 or index==expected-1:
                y0=round(min(r[1] for r in request['regions'])*height)-20
                y1=round(max(r[1]+r[3] for r in request['regions'])*height)+20
                y0=max(0,y0);y1=min(height,y1)
                pair=np.vstack([frame[y0:y1],result[y0:y1]])
                cv2.imencode('.png',pair)[1].tofile(str(previews/f'comparison-{index:05}.png'))
                cv2.imencode('.png',result)[1].tofile(str(previews/f'frame-{index:05}.png'))
                cv2.imencode('.png',mask)[1].tofile(str(previews/f'mask-{index:05}.png'))
                boundaries.append(index)
            rows.append({'frame':index,'time':index/fps,'mask_pixels':count,'mask_iou_previous':iou,'scene_change_proxy':cut,'isolated_gap_recovered':temporal_gap})
            encoder.stdin.write(result.tobytes());index+=1;previous_mask=union;previous_gray=gray.astype(float)
            if index%30==0:print(json.dumps({'frames':index,'total':expected,'elapsed_seconds':round(time.monotonic()-started,1)}),flush=True)
        if index!=expected:raise RuntimeError(f'Decoded {index}/{expected} frames; not silently truncating')
        encoder.stdin.close()
        if encoder.wait()!=0:raise RuntimeError('Video encoder failed')
    except BaseException:
        if encoder.poll() is None:encoder.kill();encoder.wait()
        raise
    finally:
        capture.release();stderr.close()
        if cache_capture is not None:cache_capture.release()
    if not changed and request.get('expected_subtitles',True):raise RuntimeError('No supported subtitle found; not proof input is text-free')
    if outside_changed:raise RuntimeError('Pixels outside masks changed')
    receipt={'source_sha256':request['source_sha256'],'model_sha256':cfg['model_sha256'],
      'engine':'migan_onnx_cpu','frames':len(rows),'processed_frames':changed,'width':width,'height':height,
      'outside_mask_changed_frames_before_encode':outside_changed,'all_frames_processed':True,'elapsed_seconds':time.monotonic()-started,
      'reused_repair_frames':reused,'request_sha256':sha(sys.argv[1]),'worker_sha256':sha(__file__),'regions':request['regions'],
      'isolated_gap_recovered_frames':[r['frame'] for r in rows if r['isolated_gap_recovered']],
      'mask_max_pixels':max(r['mask_pixels'] for r in rows),'samples':boundaries,'temporal_model':False,
      'limitation':'Per-frame neural fill; temporal flicker and protected-detail recovery require visual review',
      'video':str(silent),'video_sha256':sha(silent),'full_visual_review':False}
    (folder/'frame-metrics.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    (folder/'worker-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(receipt,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
