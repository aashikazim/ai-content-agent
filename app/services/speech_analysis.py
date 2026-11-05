"""Speech verification is technical evidence, never automatic content approval."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

from app.services.cloud_video_renderer import media_sha256,ass_time
from app.services.clip_studio.media import analysis_python


def words(text):return re.findall(r"[\w']+",text.casefold())


def word_error_rate(expected,actual):
    reference,hypothesis=words(expected),words(actual)
    if not reference:return None
    if max(len(reference),len(hypothesis))>5000:raise ValueError('Speech comparison exceeds5000words')
    row=list(range(len(hypothesis)+1))
    for index,token in enumerate(reference,1):
        current=[index]
        for j,other in enumerate(hypothesis,1):current.append(min(current[-1]+1,row[j]+1,row[j-1]+(token!=other)))
        row=current
    return row[-1]/len(reference)


def analyze_audio(audio,directory,*,expected='',forced=False,model='base'):
    if model not in ('tiny','base','small','medium'):raise ValueError('Unsupported speech analysis model')
    if forced and not expected.strip():raise ValueError('Forced alignment requires the intended transcript')
    audio=Path(audio).absolute();directory=Path(directory).absolute();directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    checkpoint=Path(os.getenv('CLIP_STUDIO_WHISPER_DIR','data/clip_studio_runtime/whisper'))/(model+'.pt')
    if not checkpoint.is_file():raise ValueError('Selected Whisper checkpoint is not installed; use setup_clip_studio.py')
    digest=hashlib.sha256(json.dumps({'audio':media_sha256(audio),'expected':expected,'forced':forced,'model':model,'profile':1},sort_keys=True).encode()).hexdigest()
    output=directory/(digest+'.json')
    if not output.is_file():
        request=directory/(digest+'.request.json')
        request.write_text(json.dumps({'audio':str(audio),'expected':expected,'forced':forced,
            'checkpoint':str(checkpoint.absolute()),'output':str(output)}));request.chmod(0o600)
        environment={k:os.environ[k] for k in ('PATH','HOME','LANG','TMPDIR') if k in os.environ}
        environment.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='2',NUMBA_NUM_THREADS='2')
        try:
            subprocess.run([str(analysis_python()),str(Path(__file__).absolute().parents[1]/'workers/speech_analysis_worker.py'),str(request)],
                env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=3600)
        except (OSError,subprocess.SubprocessError):
            raise ValueError('CPU speech analysis failed. Check the isolated Whisper runtime; forced alignment supports clips up to30seconds') from None
        finally:request.unlink(missing_ok=True)
    result=json.loads(output.read_text());duration=result['duration_seconds']
    for word in result.get('words',[])+result.get('aligned_words',[]):
        if not all(math.isfinite(word[v]) for v in ('start','end')) or not 0<=word['start']<=word['end']<=duration+.5:raise ValueError('Speech analysis returned invalid timestamps')
    result['word_error_rate']=word_error_rate(expected,result['text'])
    result['script_matches']=result['word_error_rate'] is not None and result['word_error_rate']<=.25 and not result.get('silent')
    result['human_review_required']=True
    output.write_text(json.dumps(result));output.chmod(0o600)
    return result


def write_word_captions(path,result,width,height):
    from app.services.cloud_video_renderer import write_captions
    # Reuse the established styling, then replace only dialogue lines.
    write_captions(path,'',result['duration_seconds'],width,height,True)
    content=Path(path).read_text();items=result.get('aligned_words') or result.get('words',[])
    for index in range(0,len(items),5):
        group=items[index:index+5]
        text=' '.join(w['word'].strip() for w in group)
        safe=re.sub(r'[\\{}\r\n\x00-\x1f]',' ',text)
        start,end=group[0]['start'],group[-1]['end']
        if end>start:content+=f'Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{safe}\n'
    Path(path).write_text(content)
