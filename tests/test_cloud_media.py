"""Actual FFmpeg clips/assembly, never cloud generation or uploads."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from app.models.generation_config import GenerationConfig
from app.models.production_plan import ProductionPlan
from app.services.cloud_video_renderer import CloudVideoRenderer, MediaValidationError, validate_clip, write_captions
from tests.test_gemini_video import cloud_production


def fixture_video(path,duration,moving=True,audio=True):
    command=['ffmpeg','-y','-v','error','-f','lavfi','-i',('testsrc2=size=160x284:rate=24' if moving else 'color=c=black:s=160x284:r=24')]
    if audio: command+=['-f','lavfi','-i','sine=frequency=300:sample_rate=48000']
    command+=['-t',str(duration),'-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p']
    if audio: command+=['-c:a','aac']
    subprocess.run([*command,str(path)],check=True,capture_output=True,timeout=60)


@pytest.fixture(autouse=True)
def tools_available():
    if not all(shutil.which(name) for name in ('ffmpeg','ffprobe')): pytest.skip('FFmpeg/FFprobe needed')


def test_missing_motion_audio_and_wrong_duration_block_review(tmp_path):
    path=tmp_path/'still.mp4';fixture_video(path,3,moving=False)
    with pytest.raises(MediaValidationError,match='motion'): validate_clip(path,3,require_audio=True)
    fixture_video(path,3,audio=False)
    with pytest.raises(MediaValidationError,match='audio'): validate_clip(path,3,require_audio=True)
    with pytest.raises(MediaValidationError,match='shorter'): validate_clip(path,8)


@pytest.mark.parametrize('provider,model,narration', [
    ('google', 'gemini-omni-1.1-flash', 'model_audio'),
    ('runway', 'kling3.0_standard', 'model_audio'),
    ('runway', 'gen4.5', 'local_tts'),
])
def test_actual_cloud_clip_assembly_has_captions_audio_and_correct_output(tmp_path, provider, model, narration):
    data=cloud_production().model_dump(mode='json')
    data['content_plan']['scenes'][1]['duration_seconds']=15
    data['shots']=data['shots'][:2]+[
        {**data['shots'][2],'duration_seconds':8,'spoken_text':data['content_plan']['scenes'][1]['narration']},
        {**data['shots'][3],'duration_seconds':7,'spoken_text':''}]
    production=ProductionPlan.model_validate(data)
    config=GenerationConfig(video_provider='google',video_model='gemini-omni-1.1-flash',fps=24,duration_seconds=30,resolution='720p',narration='model_audio',captions='timed')
    config = config.model_copy(update={'video_provider': provider, 'video_model': model, 'narration': narration})
    production.validate_configuration(config)
    clips={}
    for duration in (7,8):
        source=tmp_path/f'fixture_{duration}.mp4';fixture_video(source,duration);clips[duration]=source
    selected={shot.shot_number:{'clip_path':str(clips[shot.duration_seconds]),'trim_start_seconds':0} for shot in production.shots}
    output,quality=CloudVideoRenderer(config).assemble(production,selected,tmp_path/'render')
    assert quality['technical_passed'] and quality['has_audio']
    assert (quality['width'],quality['height'])==(720,1280)
    assert abs(quality['duration_seconds']-30)<.3
    manifest=json.loads((output.parent/'manifest.json').read_text())
    assert manifest['scenes'][0]['asset_type']=='generated_video'
    assert manifest['scenes'][0]['model'] == model
    if provider == 'runway':
        assert manifest['scenes'][0]['attribution'].startswith('Powered by Runway')
    assert 'estimated' in manifest['scenes'][0]['caption_timing']
    assert (output.parent/'scene_1.png').is_file()
    assert (output.parent/'captions_1.ass').is_file()
    selected.pop(1)
    with pytest.raises(MediaValidationError,match='missing'): CloudVideoRenderer(config).assemble(production,selected,tmp_path/'missing')


def test_caption_text_cannot_inject_ass_overrides(tmp_path):
    path=tmp_path/'captions.ass'
    segments=write_captions(path,r'hello {\pos(0,0)} world',8,720,1280,True)
    assert segments[0][0]==0 and segments[-1][1]==8
    assert '{' not in path.read_text() and '\\pos' not in path.read_text()
