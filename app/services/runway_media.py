"""Local source preparation and candidate assembly; no provider or publishing calls."""
import hashlib
from pathlib import Path

from app.services.cloud_video_renderer import MediaValidationError, probe, run
from app.services.video_renderer import VideoRenderer


def source_file(storage, source):
    attempt = storage.attempt(source['attempt_id'])
    if not attempt or attempt['status'] != 'completed' or not attempt['clip_path']:
        raise ValueError('The quoted source clip is no longer available')
    path = Path(attempt['clip_path'])
    if not path.is_file():
        raise ValueError('The quoted source clip is missing')
    with path.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    if digest != source['sha256']:
        raise ValueError('The source clip changed after quotation; obtain a new quote')
    return path


def normalize(source, output, config, *, start=0, duration, audio=True):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = config.output_dimensions
    filters = f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={config.fps},setsar=1,format=yuv420p'
    command = ['ffmpeg','-y','-v','error','-ss',str(start),'-i',str(source),'-t',str(duration),
               '-vf',filters,'-c:v','libx264','-preset','veryfast','-crf','20']
    command += ['-c:a','aac','-ar','48000','-ac','2'] if audio else ['-an']
    run([*command, '-movflags','+faststart',str(output)])
    return output


def last_frame(source, output, duration):
    output = Path(output); output.parent.mkdir(parents=True,exist_ok=True)
    metadata = probe(source)
    video = next((stream for stream in metadata.get('streams',[]) if stream.get('codec_type')=='video'),{})
    duration = min(duration,float(video.get('duration') or metadata.get('format',{}).get('duration') or duration))
    run(['ffmpeg','-y','-v','error','-ss',str(max(0,duration-.1)),'-i',str(source),
         '-frames:v','1','-vf','scale=768:768:force_original_aspect_ratio=decrease,format=rgb24',str(output)])
    if not output.is_file():
        raise MediaValidationError('Could not extract the quoted source final frame')
    return output.read_bytes()


def finish_candidate(downloaded, output, parent, request, config):
    """Visual edits preserve speech; continuation joins only validated real clips."""
    output = Path(output)
    if request['operation'] == 'edit':
        source = request['source']
        duration = request['target_duration']
        has_audio = any(stream.get('codec_type') == 'audio' for stream in probe(parent).get('streams',[]))
        if has_audio:
            run(['ffmpeg','-y','-v','error','-i',str(downloaded),'-ss',str(source['trim_start_seconds']),'-i',str(parent),
                 '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-t',str(duration),'-movflags','+faststart',str(output)])
        else:
            normalize(downloaded,output,config,duration=duration,audio=False)
    else:
        duration = request['source']['duration_seconds']
        first = normalize(parent,Path(downloaded).parent/'parent_normalized.mp4',config,duration=duration,audio=config.narration=='model_audio')
        tail = normalize(downloaded,Path(downloaded).parent/'tail_normalized.mp4',config,duration=request['generation_duration'],audio=config.narration=='model_audio')
        joined = Path(downloaded).parent/'joined.mp4'
        VideoRenderer(*config.output_dimensions,config.fps).concatenate([first,tail],joined)
        joined.replace(output)
    return output
