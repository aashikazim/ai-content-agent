"""Bounded local media operations. All subprocesses use argument arrays."""
import json
import os
from pathlib import Path
import subprocess

from app.services.cloud_video_renderer import media_sha256, run


class StudioError(ValueError):
    """Only deliberate, credential-free messages may cross the worker boundary."""
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def analysis_python():
    return Path(os.getenv('CLIP_STUDIO_ANALYSIS_PYTHON', 'data/clip_studio_runtime/venv/bin/python')).absolute()


def probe_source(path):
    info = json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-show_format', '-show_streams', '-of', 'json', str(path)], timeout=30).stdout)
    video = next((v for v in info.get('streams', []) if v.get('codec_type') == 'video'), None)
    duration = float(info.get('format', {}).get('duration', 0))
    if not video or not 0 < duration <= 3600 or min(video['width'], video['height']) < 128 or max(video['width'], video['height']) > 8192:
        raise StudioError('Use a decodable video up to one hour, with dimensions from 128px to 8192px')
    return {'duration_seconds': duration, 'width': video['width'], 'height': video['height'],
            'audio': any(v.get('codec_type') == 'audio' for v in info['streams'])}


ANALYSIS_ERRORS = {
    'youtube_format_unavailable': 'YouTube did not provide a usable video/audio format. Update the isolated yt-dlp[default] package and ensure Node 22+ or Deno is available on the server PATH; alternatively upload the video file.',
    'youtube_login_required': 'YouTube requires sign-in, age verification or private-video access for this source. Upload a video file you have permission to use; browser cookies are not imported automatically.',
    'youtube_access_denied': 'YouTube denied the download (403/rate limit). Upload the source video or try again manually later.',
    'youtube_unavailable': 'The YouTube source is unavailable, removed or region restricted. Choose another authorized source or upload the video.',
    'network_failure': 'Could not reach YouTube. Check server network/DNS access or upload the source video.',
    'source_too_large': 'The combined source download exceeds the Clip Studio upload/download limit. Use a smaller source.',
    'source_rejected': 'The source was rejected by its duration/size limits. Use a video shorter than one hour and within the configured download cap.',
    'download_output_missing': 'The download did not produce a usable merged video. Check FFmpeg installation or upload a video file.',
    'youtube_download_failed': 'The YouTube source download failed. Check source availability and isolated yt-dlp setup, or upload a video file.',
    'dependency_missing': 'An isolated analysis dependency is missing. Run scripts/setup_clip_studio.py; the application and GPU environments are separate.',
    'tool_missing': 'A required analysis executable is missing. Check FFmpeg/FFprobe and the isolated analysis Python path.',
    'whisper_checkpoint_missing': 'The selected Whisper checkpoint is missing. Run scripts/setup_clip_studio.py --whisper-model with the selected size, or select the installed base model.',
    'media_analysis_failed': 'Scene detection or transcription failed. Check that the source decodes correctly and the analysis runtime is installed.',
}


def analyze(source, directory, whisper_model, upload_limit):
    """Neural/decoder imports stay in a separate environment; Whisper runs on CPU."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    python = analysis_python()
    if not python.is_file():
        raise StudioError('Analysis runtime is missing. Run scripts/setup_clip_studio.py first')
    task = directory/'analysis-request.json'
    task.write_text(json.dumps({'source': source, 'directory': str(directory), 'whisper_model': whisper_model,
        'upload_limit': upload_limit, 'whisper_root': str(Path(os.getenv('CLIP_STUDIO_WHISPER_DIR', 'data/clip_studio_runtime/whisper')).absolute())}))
    task.chmod(0o600)
    worker = Path(__file__).absolute().parents[2]/'workers/clip_studio_analyze.py'
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'TMPDIR') if key in os.environ}
    env.update(CUDA_VISIBLE_DEVICES='', NUMBA_NUM_THREADS='2', OMP_NUM_THREADS='2')
    error_path=directory/'analysis-error.json'
    error_path.unlink(missing_ok=True)
    try:
        result = subprocess.run([str(python), str(worker), str(task)], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3600, check=False)
        if result.returncode:
            code=None
            try:
                if error_path.stat().st_size<4096:code=json.loads(error_path.read_text()).get('code')
            except (OSError,ValueError,AttributeError):pass
            raise StudioError(ANALYSIS_ERRORS.get(code,'The isolated analysis worker failed. Check its runtime and source file; no planning or GPU generation was started.'))
        data = json.loads((directory/'analysis.json').read_text())
        path = Path(data['path']).resolve()
        # Uploaded sources may be outside the analysis subfolder, but remain in this project.
        if not path.is_relative_to(directory.parent.resolve()) or not path.is_file() or path.stat().st_size > upload_limit:
            raise StudioError('Analysis returned an invalid source')
        return data
    except subprocess.TimeoutExpired:
        raise StudioError('Source analysis exceeded one hour and was stopped; use a shorter source. No planning or GPU generation was started.') from None
    finally:
        task.unlink(missing_ok=True)
        for pattern in ('*.part','*.ytdl','*.temp'):
            for path in directory.glob(pattern):
                if path.is_file():path.unlink(missing_ok=True)


def extract_reference(source, clip, directory):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if source['kind'] == 'images':
        return Path(source['path']), None
    frame, driver = directory/'reference.png', directory/'driver.mp4'
    start = clip.source_start or 0
    duration = min(clip.duration_seconds, clip.source_end-start)
    run(['ffmpeg','-v','error','-nostdin','-y','-ss',str(start),'-i',source['path'],
         '-frames:v','1','-vf','scale=1024:1024:force_original_aspect_ratio=decrease',str(frame)])
    run(['ffmpeg','-v','error','-nostdin','-y','-ss',str(start),'-i',source['path'],
         '-t',str(duration),'-an','-vf','fps=24,scale=896:896:force_original_aspect_ratio=decrease:force_divisible_by=2',
         '-c:v','libx264','-pix_fmt','yuv420p',str(driver)])
    return frame, driver


def assemble(paths, output, duration, aspect, *, audio=None):
    """Normalize every chunk; missing model audio becomes silence, never a test tone."""
    width,height={'16:9':(896,512),'9:16':(512,896),'1:1':(512,512)}[aspect]
    normalized=[]
    for index,path in enumerate(paths):
        info=json.loads(run(['ffprobe','-v','error','-show_streams','-of','json',str(path)]).stdout)
        has_audio=any(s['codec_type']=='audio' for s in info['streams'])
        dest=output.parent/f'normalized-{index}.mp4'
        args=['ffmpeg','-v','error','-nostdin','-y','-i',str(path)]
        if not has_audio: args += ['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
        args += ['-map','0:v:0','-map','0:a:0' if has_audio else '1:a:0','-vf',
            f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=24,setsar=1',
            '-c:v','libx264','-preset','fast','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2','-shortest',str(dest)]
        run(args);normalized.append(dest)
    # Paths are our generated numeric filenames, never user filenames.
    manifest=output.parent/'concat.txt'
    manifest.write_text(''.join(f"file '{p.name}'\n" for p in normalized))
    args=['ffmpeg','-v','error','-nostdin','-y','-f','concat','-safe','1','-i',str(manifest)]
    if audio: args += ['-i',str(audio),'-map','0:v:0','-map','1:a:0','-af','apad']
    args += ['-t',str(duration),'-c:v','copy','-c:a','aac','-movflags','+faststart',str(output)]
    run(args)
    thumbnail=output.parent/'thumbnail.jpg'
    run(['ffmpeg','-v','error','-nostdin','-y','-i',str(output),'-frames:v','1',str(thumbnail)])
    for path in (output,thumbnail):path.chmod(0o600)
    for path in normalized:path.unlink(missing_ok=True)
    manifest.unlink(missing_ok=True)
    return output,thumbnail
