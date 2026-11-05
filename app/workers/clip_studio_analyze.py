"""Isolated CPU-only source analysis; invoked with a private JSON file."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys


def main(task):
    os.umask(0o077)
    value=json.loads(Path(task).read_text()); source=value['source']; directory=Path(value['directory'])
    path=Path(source['path']) if source.get('path') else directory/'source.mp4'
    if not source.get('path'):
        path=download_source(source,directory,value['upload_limit'])
    if not path.is_file() or path.stat().st_size>value['upload_limit']:raise ValueError('Invalid source')
    probe=json.loads(subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)],check=True,capture_output=True,text=True).stdout)
    duration=float(probe['format']['duration'])
    if not 0<duration<=3600:raise ValueError('Duration exceeds limit')
    from scenedetect import detect, ContentDetector
    ranges=detect(str(path),ContentDetector(),start_in_scene=True)
    scenes=[]
    for index,(start,end) in enumerate(ranges[:600]):
        a,b=start.get_seconds(),end.get_seconds(); frames=[]
        for number,fraction in enumerate((.15,.5,.85)):
            frame=directory/f'scene-{index}-{number}.jpg'
            subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-ss',str(a+(b-a)*fraction),'-i',str(path),'-frames:v','1','-vf','scale=320:-2',str(frame)],check=True,capture_output=True)
            frames.append({'path':str(frame),'time':a+(b-a)*fraction})
        scenes.append({'source_start':a,'source_end':b,'thumbnails':frames})
    if len(ranges)>600:raise ValueError('Source has too many scenes; trim it first')
    segments=[]
    if any(s['codec_type']=='audio' for s in probe['streams']):
        import whisper
        checkpoint=Path(value['whisper_root'])/(value['whisper_model']+'.pt')
        if not checkpoint.is_file():raise AnalysisError('whisper_checkpoint_missing')
        model=whisper.load_model(str(checkpoint),device='cpu')
        result=model.transcribe(str(path),fp16=False,verbose=False)
        segments=[{'start':s['start'],'end':s['end'],'text':s['text']} for s in result['segments']]
    (directory/'analysis.json').write_text(json.dumps({'path':str(path),'duration_seconds':duration,'scenes':scenes,'transcript':segments}))


class AnalysisError(Exception):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


def download_options(directory,limit):
    downloaded={}
    def bound(event):
        # Video and audio are often separate downloads: enforce a combined cap.
        downloaded[event.get('filename','source')]=event.get('downloaded_bytes',0)
        if sum(downloaded.values())>limit:raise AnalysisError('source_too_large')
    class QuietLogger:
        def debug(self,*args):pass
        def warning(self,*args):pass
        def error(self,*args):pass
    runtimes={name:{} for name in ('deno','node') if shutil.which(name)}
    return {'outtmpl':str(directory/'source.%(ext)s'),
        # Keep room for audio and mux overhead. Unknown sizes remain bounded by
        # the aggregate progress hook. Prefer known sizes so unknown-size HLS
        # streams cannot outrank a smaller direct stream; unknown-size fallback is480p.
        'format':f'bv[height<=1080][filesize<={int(limit*.82)}]+ba[filesize<=?{int(limit*.13)}]/bv[height<=1080][filesize_approx<={int(limit*.82)}]+ba[filesize_approx<=?{int(limit*.13)}]/b[height<=1080][filesize<={int(limit*.95)}]/bv[height<=480]+ba/b[height<=480]',
        'merge_output_format':'mp4/mkv','noplaylist':True,'max_filesize':limit,
        'match_filter':lambda info,**_: 'Video exceeds one hour' if info.get('duration') is not None and info['duration']>3600 else None,
        'quiet':True,'no_warnings':True,'logger':QuietLogger(),'js_runtimes':runtimes,
        'retries':0,'fragment_retries':0,'extractor_retries':0,'socket_timeout':30,
        'progress_hooks':[bound],'allowed_extractors':['youtube'],'cachedir':False}


def download_source(source,directory,limit):
    from yt_dlp import YoutubeDL
    try:
        with YoutubeDL(download_options(directory,limit)) as downloader:
            info=downloader.extract_info(json.loads(source['metadata_json'])['url'],download=True)
            if not info:raise AnalysisError('source_rejected')
            # prepare_filename can name the pre-merge stream. Use postprocessor's
            # final filepath or the actual merged file, never assume .mp4 exists.
            candidates=[*[(str(directory/('source'+suffix))) for suffix in ('.mp4','.mkv','.webm')],info.get('filepath'),downloader.prepare_filename(info)]
            for name in candidates:
                if name:
                    path=Path(name).resolve()
                    if path.is_relative_to(directory.resolve()) and path.is_file() and path.stem=='source' and path.suffix in ('.mp4','.mkv','.webm'):
                        if path.stat().st_size>limit:raise AnalysisError('source_too_large')
                        return path
            raise AnalysisError('download_output_missing')
    except AnalysisError:
        cleanup_download_parts(directory)
        raise
    except Exception as error:
        cleanup_download_parts(directory)
        # Classify without returning arbitrary URLs, cookies, tokens or tracebacks.
        text=str(error).lower()
        checks=[('source_too_large',('exceeds limit','larger than max-filesize','too large')),
                ('youtube_login_required',('sign in','cookies','age-restricted','private video')),
                ('youtube_format_unavailable',('requested format','no video formats','javascript runtime','challenge solving')),
                ('youtube_access_denied',('403','429','forbidden','too many requests')),
                ('youtube_unavailable',('video unavailable','has been removed','not available in your country')),
                ('network_failure',('timed out','name resolution','unable to download webpage','connection'))]
        code=next((code for code,needles in checks if any(n in text for n in needles)),'youtube_download_failed')
        raise AnalysisError(code) from None


def cleanup_download_parts(directory):
    # Only generated component/sidecar files, never uploaded videos or complete exports.
    for pattern in ('source.f*.mp4','source.f*.webm','source.f*.m4a','source*.part','source*.ytdl'):
        for path in directory.glob(pattern):
            if path.is_file():path.unlink(missing_ok=True)


def execute(task):
    error_path=Path(task).parent/'analysis-error.json'
    error_path.unlink(missing_ok=True)
    try:
        main(task)
        return 0
    except Exception as error:
        code=error.code if isinstance(error,AnalysisError) else 'dependency_missing' if isinstance(error,ImportError) else 'tool_missing' if isinstance(error,FileNotFoundError) else 'media_analysis_failed'
        error_path.write_text(json.dumps({'code':code}))
        error_path.chmod(0o600)
        return 1


if __name__=='__main__':sys.exit(execute(sys.argv[1]))
