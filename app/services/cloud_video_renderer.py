"""Validate moving clips and assemble locally with existing FFmpeg/TTS services."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from app.services.video_renderer import VideoRenderer
from app.services.tts_service import TTSService


class MediaValidationError(ValueError):
    pass


def run(command):
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
    except (subprocess.SubprocessError, OSError):
        raise MediaValidationError('Local media processing failed; inspect the clip and FFmpeg installation') from None


def probe(path):
    try:
        return json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]).stdout)
    except (ValueError, KeyError):
        raise MediaValidationError('The video could not be decoded') from None


def validate_clip(path, duration, *, require_audio=False, expected_dimensions=None, expected_fps=None):
    metadata = probe(path)
    videos = [item for item in metadata.get('streams', []) if item.get('codec_type') == 'video']
    audio = [item for item in metadata.get('streams', []) if item.get('codec_type') == 'audio']
    if not videos or float(metadata.get('format', {}).get('duration', 0)) < duration - .35:
        raise MediaValidationError('A required clip is missing or shorter than its planned duration')
    measured = float(metadata['format']['duration'])
    if abs(measured - duration) > .6:
        raise MediaValidationError('Clip duration differs from the request; inspect or regenerate this shot')
    video = videos[0]
    if min(video.get('width', 0), video.get('height', 0)) < 128:
        raise MediaValidationError('Clip dimensions are unusable')
    if require_audio and not audio:
        raise MediaValidationError('Model audio was requested but the generated clip has no audio')
    if expected_dimensions and (video.get('width'), video.get('height')) != expected_dimensions:
        raise MediaValidationError('Final output dimensions do not match the configuration')
    if expected_fps:
        numerator, denominator = video.get('avg_frame_rate', '0/1').split('/')
        if not float(denominator) or abs(float(numerator) / float(denominator) - expected_fps) > .05:
            raise MediaValidationError('Final output frame rate does not match the configuration')
    # Sample normalized frames: rejects static cards/empty frames. It does not
    # establish good acting, identity continuity or perceptual quality.
    frames = run(['ffmpeg', '-v', 'error', '-i', str(path), '-an', '-vf', 'fps=1,scale=64:64,format=gray',
                  '-f', 'framemd5', '-']).stdout
    hashes = {line.rsplit(',', 1)[-1].strip() for line in frames.splitlines() if line and not line.startswith('#')}
    if len(hashes) < 2:
        raise MediaValidationError('The clip contains no measurable motion; static fallback is not allowed')
    run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'null', '-'])
    return {'duration_seconds': measured, 'width': video['width'], 'height': video['height'],
            'has_audio': bool(audio), 'motion_samples_distinct': len(hashes),
            'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(), 'technical_passed': True}


def caption_segments(text, duration):
    words = text.split()
    groups = [words[index:index + 6] for index in range(0, len(words), 6)]
    count = sum(len(group) for group in groups) or 1
    result, cursor = [], 0
    for group in groups:
        end = cursor + duration * len(group) / count
        result.append((cursor, end, ' '.join(group)))
        cursor = end
    return result


def ass_time(seconds):
    centiseconds = round(seconds * 100)
    return f'{centiseconds // 360000}:{centiseconds // 6000 % 60:02}:{centiseconds // 100 % 60:02}.{centiseconds % 100:02}'


def write_captions(path, text, duration, width, height, timed):
    # Caption text is data, never an FFmpeg filter or ASS override command.
    chunks = caption_segments(text, duration) if timed else [(0, duration, text)] if text else []
    font = max(20, round(width / 22))
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{font},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,35,35,{round(height * .13)},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    for start, end, caption in chunks:
        safe = re.sub(r'[\\{}\r\n\x00-\x1f]', ' ', caption)
        header += f'Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{safe}\n'
    Path(path).write_text(header, encoding='utf-8')
    return chunks


class CloudVideoRenderer:
    def __init__(self, configuration):
        self.config = configuration
        self.width, self.height = configuration.output_dimensions
        self.local_renderer = VideoRenderer(self.width, self.height, configuration.fps)

    def assemble(self, production, selected, output_dir):
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        rendered, scenes = [], []
        for scene in production.content_plan.scenes:
            clips = []
            for shot in [item for item in production.shots if item.scene_number == scene.scene_number]:
                candidate = selected.get(shot.shot_number)
                if not candidate or not Path(candidate['clip_path']).is_file():
                    raise MediaValidationError('Required shot assets are missing; rendering cannot proceed')
                raw = output_dir / f'raw_{shot.shot_number}.mp4'
                filters = f'scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,fps={self.config.fps},setsar=1,format=yuv420p'
                run(['ffmpeg', '-y', '-v', 'error', '-ss', str(candidate.get('trim_start_seconds', 0)), '-i', candidate['clip_path'],
                     '-t', str(shot.duration_seconds), '-vf', filters, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21',
                     '-an' if self.config.narration == 'local_tts' else '-c:a', *([] if self.config.narration == 'local_tts' else ['aac', '-ar', '48000', '-ac', '2']),
                     str(raw)])
                clips.append(raw)
            joined = output_dir / f'joined_{scene.scene_number}.mp4'
            self.local_renderer.concatenate(clips, joined)
            caption_file = output_dir / f'captions_{scene.scene_number}.ass'
            timing = write_captions(caption_file,
                scene.narration if self.config.captions == 'timed' else scene.on_screen_text,
                scene.duration_seconds, self.width, self.height, self.config.captions == 'timed')
            output = output_dir / f'scene_{scene.scene_number}.mp4'
            command = ['ffmpeg', '-y', '-v', 'error', '-i', str(joined)]
            audio_filter = 'apad'
            if self.config.narration == 'local_tts':
                narration = output_dir / f'scene_{scene.scene_number}.wav'
                TTSService(voice=os.getenv("TTS_VOICE", "en-us")).synthesize(scene.narration, narration)
                seconds = self.local_renderer.audio_duration(narration)
                speed = seconds / scene.duration_seconds
                if speed > 1.25:
                    raise MediaValidationError('Local narration cannot fit its scene; request a shorter script')
                audio_filter = f'atempo={max(1, speed):.6f},apad'
                command += ['-i', str(narration), '-map', '0:v:0', '-map', '1:a:0']
            else:
                command += ['-map', '0:v:0', '-map', '0:a:0']
            # Fixed generated caption filename; use cwd to avoid path/filter escaping.
            command += ['-vf', f'ass={caption_file.name}', '-af', audio_filter, '-c:v', 'libx264', '-preset', 'veryfast',
                        '-crf', '21', '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-t', str(scene.duration_seconds), '-movflags', '+faststart', str(output)]
            try:
                subprocess.run(command, cwd=output_dir, check=True, capture_output=True, timeout=180)
            except (OSError, subprocess.SubprocessError):
                raise MediaValidationError('Caption/audio assembly failed; verify FFmpeg libass support') from None
            # Preview image is extracted from real footage, never a text fallback.
            run(['ffmpeg', '-y', '-v', 'error', '-i', str(output), '-frames:v', '1', str(output_dir / f'scene_{scene.scene_number}.png')])
            scenes.append({'scene_number': scene.scene_number, 'status': 'available', 'duration': scene.duration_seconds,
                'asset_type': 'generated_video', 'model': self.config.video_model,
                'license': 'AI-generated; provider terms and human rights review apply',
                'attribution': ('Powered by Runway' if self.config.video_provider == 'runway' else 'Google Gemini Omni') + '; permission-confirmed user images where selected',
                'source_url': 'https://runwayml.com' if self.config.video_provider == 'runway' else 'https://aistudio.google.com/docs/omni',
                'caption_timing': 'estimated phrase timing; manually verify speech alignment', 'captions': timing})
            rendered.append(output)
        joined = output_dir / 'joined_final.mp4'
        self.local_renderer.concatenate(rendered, joined)
        final = output_dir / 'final_video.mp4'
        run(['ffmpeg', '-y', '-v', 'error', '-i', str(joined), '-c:v', 'copy', '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',
             '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-movflags', '+faststart', str(final)])
        quality = validate_clip(final, production.content_plan.total_duration, require_audio=True,
                                expected_dimensions=(self.width, self.height), expected_fps=self.config.fps)
        manifest = {'status': 'rendered', 'review_status': 'draft', 'title': production.content_plan.title,
                    'scenes': scenes, 'technical_quality': quality, 'factual_status': production.factual_status,
                    'warnings': ['Character/voice continuity and factual accuracy require human review.',
                                 'Caption timings are estimated, not forced-aligned to speech.'],
                    'output': str(final)}
        (output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        return final, quality
