"""Local, reviewed-plan marketing overlays; original video encoded once, AAC copied."""
from __future__ import annotations
import argparse
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]


class PackagingError(ValueError):
    pass


def localizer():
    spec=importlib.util.spec_from_file_location('packaging_localizer',ROOT/'scripts/localize.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def color(value):
    if not re.fullmatch(r'#[0-9A-Fa-f]{6}',value):
        raise PackagingError('Color must be #RRGGBB')
    return '&H'+value[5:7]+value[3:5]+value[1:3]+'&'


def escape(text):
    return text.replace('\\','＼').replace('{','｛').replace('}','｝').replace('\n',r'\N')


def overlap(a,b):
    return a[0]<b[0]+b[2] and a[0]+a[2]>b[0] and a[1]<b[1]+b[3] and a[1]+a[3]>b[1]


def rounded_rect(w,h,r=18):
    # ASS vector drawing in local coordinates, with cubic quarter-circles.
    r=min(r,w/2,h/2);k=r*.55228475
    return (f'm {r:g} 0 l {w-r:g} 0 b {w-r+k:g} 0 {w:g} {r-k:g} {w:g} {r:g} '
            f'l {w:g} {h-r:g} b {w:g} {h-r+k:g} {w-r+k:g} {h:g} {w-r:g} {h:g} '
            f'l {r:g} {h:g} b {r-k:g} {h:g} 0 {h-r+k:g} 0 {h-r:g} '
            f'l 0 {r:g} b 0 {r-k:g} {r-k:g} 0 {r:g} 0')


def compile_plan(plan,width,height,total,output,font_base=None):
    from PIL import ImageFont
    from fontTools.ttLib import TTFont
    if plan.get('schema_version')!=1 or plan.get('canvas')!=[width,height]:
        raise PackagingError('Plan schema/canvas differs from the video')
    if plan.get('semantic_review',{}).get('reviewed') is not True or not plan['semantic_review'].get('reviewer'):
        raise PackagingError('Marketing copy needs an actual semantic review record')
    if plan.get('target_language') is None:
        raise PackagingError('Missing target language')
    style=plan['style'];font_path=Path(style['font_file'])
    if not font_path.is_absolute():font_path=Path(font_base or '.')/font_path
    if not font_path.is_file():raise PackagingError('Font file missing; do not silently substitute')
    family=style['font_family']
    if re.search(r'[{},\r\n\\]',family):raise PackagingError('Invalid font family')
    # Check chosen font identity, not just its filename. Windows fonts are not bundled.
    probe_font=ImageFont.truetype(str(font_path),40)
    if probe_font.getname()[0].casefold()!=family.casefold():
        raise PackagingError('Font family does not match font_file')
    with TTFont(str(font_path),fontNumber=0,lazy=True) as face:
        supported={cp for table in face['cmap'].tables if table.isUnicode() for cp in table.cmap}
    palette={key:color(style[key]) for key in ['ink','accent','paper']}
    events=plan.get('events',[])
    if not events:raise PackagingError('Empty packaging plan')
    ids=set();records=[];dialogues=[]
    def stamp(t):
        n=round(t*100);return f'{n//360000}:{n//6000%60:02}:{n//100%60:02}.{n%100:02}'
    def emit(layer,event,tags,text):
        dialogues.append(f"Dialogue: {layer},{stamp(event['start'])},{stamp(event['end'])},Pack,,0,0,0,,{{{tags}}}{text}")
    def highlighted(text,keyword,base):
        if not keyword:return escape(text)
        if not isinstance(keyword,str) or keyword.casefold() not in text.casefold():
            raise PackagingError('Highlight must match an actual keyword')
        parts=re.split('('+re.escape(keyword)+')',text,flags=re.IGNORECASE)
        return ''.join((r'{\1c'+(palette['accent'] if part.casefold()==keyword.casefold() else base)+'}')+escape(part) for part in parts)
    for event in events:
        eid=event['id']
        if eid in ids:raise PackagingError('Duplicate overlay id')
        ids.add(eid)
        a,b=event['start'],event['end'];rect=event['rect']
        if not (math.isfinite(a) and math.isfinite(b) and 0<=a<b<=total+.01):
            raise PackagingError('Overlay outside video duration')
        if len(rect)!=4 or any(not math.isfinite(n) for n in rect):raise PackagingError('Invalid overlay rectangle')
        x,y,w,h=rect
        if min(w,h)<=0 or x<32 or y<32 or x+w>width-32 or y+h+10>height-32:
            raise PackagingError('Overlay or animation extends beyond safe canvas')
        if not event.get('source_quote') or not event.get('cue_ids'):
            raise PackagingError('Overlay needs source meaning and related caption ids')
        envelope=[x-12,y-12,w+24,h+24] if event.get('kind')=='emphasis' else [x,y,w,h+10]
        if envelope[0]<32 or envelope[1]<32 or envelope[0]+envelope[2]>width-32 or envelope[1]+envelope[3]>height-32:
            raise PackagingError('Emphasis outline or animation extends beyond safe canvas')
        if overlap(envelope,plan['subtitle_region']):raise PackagingError('Overlay overlaps subtitle area')
        for region in plan.get('protected_regions',[]):
            if a<region['end'] and b>region['start'] and overlap(envelope,region['rect']):
                raise PackagingError(f"Overlay {eid} blocks protected {region.get('label','region')}")
        for previous in records:
            if a<previous['end'] and b>previous['start'] and overlap(envelope,previous['envelope']):
                raise PackagingError('Simultaneous marketing overlays overlap')
        text=event['text'];eyebrow=event.get('eyebrow','')
        missing={ch for ch in text+eyebrow if not ch.isspace() and ord(ch) not in supported}
        if missing:raise PackagingError(f'Chosen font lacks glyphs: {sorted(missing)}')
        lines=text.splitlines();size=event.get('font_size',60)
        if not text.strip() or len(lines)>2 or not (20<=size<=120):raise PackagingError('Invalid text/line count/font size')
        font=ImageFont.truetype(str(font_path),size)
        inner=w-64
        if any(font.getlength(line)>inner for line in lines):raise PackagingError(f'Overlay {eid} needs natural rewrapping; no automatic truncation')
        if (len(lines)*size*1.24+64+(36 if eyebrow else 0))>h:
            raise PackagingError('Text too tall for overlay rectangle')
        if eyebrow and ImageFont.truetype(str(font_path),26).getlength(eyebrow)>inner:
            raise PackagingError('Eyebrow too wide')
        ms=round((b-a)*1000);fade_in=min(event.get('fade_in_ms',130),ms//3);fade_out=min(event.get('fade_out_ms',90),ms//3)
        motion=lambda px,py:rf'\an7\move({px:g},{py+8:g},{px:g},{py:g},0,{fade_in})\fad({fade_in},{fade_out})'
        if event.get('kind')=='title':
            ty=y+16
            if eyebrow:
                emit(12,event,motion(x+6,ty)+rf'\fs26\b1\bord2\shad0\1c{palette["accent"]}\3c{palette["ink"]}',escape(eyebrow))
                ty+=44
            emit(12,event,motion(x+4,ty)+rf'\fs{size}\b1\bord3\shad1\1c{palette["paper"]}\3c{palette["ink"]}',highlighted(text,event.get('highlight'),palette['paper']))
        elif event.get('kind')=='callout':
            emit(10,event,motion(x+3,y+5)+rf'\p1\bord0\shad0\1c{palette["ink"]}\1a&H9A&',rounded_rect(w,h))
            emit(11,event,motion(x,y)+rf'\p1\bord0\shad0\1c{palette["paper"]}\1a&H0D&',rounded_rect(w,h))
            emit(12,event,motion(x+16,y+20)+rf'\p1\bord0\shad0\1c{palette["accent"]}',rounded_rect(6,h-40,3))
            ty=y+26
            if eyebrow:
                emit(13,event,motion(x+36,ty)+rf'\fs26\b1\bord0\shad0\1c{palette["ink"]}',escape(eyebrow))
                ty+=40
            emit(13,event,motion(x+32,ty)+rf'\fs{size}\b1\bord0\shad0\1c{palette["ink"]}',escape(text))
        elif event.get('kind')=='emphasis':
            if eyebrow:raise PackagingError('Emphasis uses only the short main text; keep auxiliary labels separate')
            angle=event.get('angle',0)
            if not isinstance(angle,(int,float)) or not math.isfinite(angle) or abs(angle)>3:
                raise PackagingError('Emphasis angle exceeds the checked range')
            # An original restrained pop curve, not a copied template animation.
            pop=r'\fscx94\fscy94\t(0,90,\fscx103\fscy103)\t(90,170,\fscx100\fscy100)'
            tags=motion(x+32,y+26)+rf'\fs{size}\b1\frz{angle}'+pop
            emit(10,event,tags+rf'\bord9\shad3\1c{palette["paper"]}\3c{palette["paper"]}\4c{palette["ink"]}',escape(text))
            emit(11,event,tags+rf'\bord3\shad0\1c{palette["paper"]}\3c{palette["ink"]}',highlighted(text,event.get('highlight'),palette['paper']))
            if event.get('accent_rule',True):
                emit(9,event,motion(x+36,y+h-27)+rf'\p1\bord0\shad0\1c{palette["accent"]}',rounded_rect(min(inner*.38,190),7,3))
        else:raise PackagingError('Unknown overlay kind')
        records.append({'id':eid,'start':a,'end':b,'rect':rect,'envelope':envelope,'text':text,'eyebrow':eyebrow,'cue_ids':event['cue_ids']})
    header=f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pack,{family},60,&H00FFFFFF,&H00FFFFFF,&H00333333,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    Path(output).write_text(header+'\n'.join(dialogues)+'\n',encoding='utf-8-sig')
    return {'events':records,'font_file':str(font_path),'font_family':family,'layout_checks_passed':True,
            'limitations':['Protected rectangles are planned from inspected frames, not automatic object tracking.','Font metrics are preliminary; inspect actual rendered frames and animation.']}


def render(args):
    m=localizer();plan_path=Path(args.manifest).resolve();plan=m.read_json(plan_path)
    source=Path(args.source).resolve();captions=Path(args.captions).resolve();audio=Path(args.audio_reference).resolve()
    for path,key in [(source,'source_sha256'),(captions,'captions_sha256'),(audio,'audio_reference_sha256')]:
        if m.sha(path)!=plan.get(key):raise PackagingError(f'Stale input binding: {key}')
    info=m.probe(source);video=next(s for s in info['streams'] if s['codec_type']=='video')
    width,height=video['width'],video['height'];total=float(info['format']['duration'])
    audio_info=m.probe(audio)
    if abs(float(audio_info['format']['duration'])-total)>.15:raise PackagingError('Audio reference duration differs from source')
    if not any(s['codec_type']=='audio' for s in audio_info['streams']):raise PackagingError('Missing reference audio')
    if video.get('tags',{}).get('rotate') or any(s.get('rotation',0) for s in video.get('side_data_list',[])):
        raise PackagingError('Source rotation must be resolved before layout')
    # Do not let decorative text enter or replace the already-audited spoken captions.
    cues=m.subtitle_sync.parse_ass(captions)
    valid_ids={c['id'] for c in cues}
    for event in plan['events']:
        if not set(event.get('cue_ids',[]))<=valid_ids:raise PackagingError('Unknown related caption id')
        linked=[c for c in cues if c['id'] in event['cue_ids']]
        if not linked or event['end']<min(c['start'] for c in linked) or event['start']>max(c['end'] for c in linked):
            raise PackagingError('Overlay has no temporal relation to its caption group')
    out=Path(args.output_directory).resolve()
    out.mkdir(parents=True,exist_ok=False)
    try:
        shutil.copy2(captions,out/'captions.ass');shutil.copy2(plan_path,out/'packaging-plan.json')
        audit=compile_plan(plan,width,height,total,out/'marketing.ass',plan_path.parent)
        m.save_json(out/'packaging-layout.json',audit)
        output=out/'localized.mp4'
        m.run(['ffmpeg','-v','warning','-n','-i',source,'-i',audio,'-map','0:v:0','-map','1:a?',
               '-vf','ass=captions.ass,ass=marketing.ass','-fps_mode','passthrough','-c:v','libx264',
               '-preset','medium','-crf','19','-pix_fmt','yuv420p','-c:a','copy','-movflags','+faststart','-map_metadata','-1',output],cwd=out,timeout=3600,error_log=out/'render.log')
        preserved=m.verify_original_audio(audio,output);m.save_json(out/'audio-preservation.json',preserved)
        result=m.probe(output);v=next(s for s in result['streams'] if s['codec_type']=='video')
        if (v['width'],v['height'])!=(width,height) or abs(float(result['format']['duration'])-total)>.15:
            raise PackagingError('Output size/duration differs')
        m.run(['ffmpeg','-v','error','-i',output,'-f','null','-'],timeout=3600)
        if m.sha(out/'captions.ass')!=plan['captions_sha256']:raise PackagingError('Spoken captions changed')
        receipt={'task_intent':'依据已审核表达增加目标语标题和卖点花字','target_environment':plan['target_language'],
                 'acceptance_criteria':['口播字幕保持','现有全部音频包保持','从纯净原片一次合成','花字排版与实际帧检查'],
                 'artifacts':{'video':str(output),'captions':str(out/'captions.ass'),'marketing':str(out/'marketing.ass'),'plan':str(out/'packaging-plan.json')},
                 'checks_run':['source_caption_audio_identity','reviewed_copy_plan','layout_protected_rectangles','audio_packets_codecs_timestamps_identity','dimensions_duration','full_decode'],
                 'evidence':{'video_sha256':m.sha(output),'audio':preserved,'subtitle_ass_sha256':m.sha(captions),'layout':audit},
                 'unresolved_issues':['须检查花字入场/停留/退场实际帧及完整播放；本脚本不做听审'],
                 'limitations':['文字内容由执行者策划，不自动保证当地表达或转化','烧录视频有一次有损编码，音频原包复制'],
                 'technical_checks_passed':True,'status':'verification_incomplete','publication_authorized':False}
        m.save_json(out/'receipt.json',receipt)
        print(json.dumps({'video':str(output),'receipt':str(out/'receipt.json'),'status':receipt['status']},ensure_ascii=False))
    except Exception as error:
        m.save_json(out/'state.json',{'status':'blocked','reason':str(error)})
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',required=True);p.add_argument('--source',required=True)
    p.add_argument('--captions',required=True);p.add_argument('--audio-reference',required=True)
    p.add_argument('--output-directory',required=True)
    render(p.parse_args())
