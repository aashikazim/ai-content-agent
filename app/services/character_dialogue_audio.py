"""Deterministic baseline character voices. This does not claim neural lip sync."""
from pathlib import Path
import re

from app.services.cloud_video_renderer import MediaValidationError, run
from app.services.tts_service import TTSService


def speaker_lines(production, shot):
    words = lambda text: re.findall(r'\w+',text.casefold())
    lines = [line for line in production.dialogue if line.scene_number == shot.scene_number and line.text in shot.spoken_text]
    if lines and words(' '.join(line.text for line in lines)) == words(shot.spoken_text):
        return [(line.character_id,line.text) for line in lines]
    if len(shot.character_ids) <= 1:
        return [(shot.character_ids[0] if shot.character_ids else None,shot.spoken_text)] if shot.spoken_text.strip() else []
    if shot.spoken_text.strip():
        raise MediaValidationError('Assign each dialogue line to a character before rendering fixed character voices')
    return []


def render_dialogue(production, shot, package, directory, renderer):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    voices={actor['character_id']:actor.get('tts_voice','en-us') for actor in package['characters']}
    paths=[]
    for index,(actor,text) in enumerate(speaker_lines(production,shot)):
        if not text.strip():continue
        path=directory/f'line_{index}.wav'
        TTSService(voice=voices.get(actor,'en-us'),**({'profile_id':next(a['speech_profile_id'] for a in package['characters'] if a['character_id']==actor)} if any(a.get('speech_profile_id') for a in package['characters'] if a['character_id']==actor) else {})).synthesize(text,path)
        path.chmod(0o600);paths.append(path)
    output=directory/'dialogue.wav'
    if not paths:
        run(['ffmpeg','-y','-v','error','-f','lavfi','-i','anullsrc=r=48000:cl=stereo',
             '-t',str(shot.duration_seconds),str(output)])
    else:
        # Private generated filenames only; text and user paths never enter the concat list.
        listing=directory/'lines.txt'
        listing.write_text(''.join(f"file '{path.name}'\n" for path in paths))
        combined=directory/'combined.wav'
        run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(listing),'-ar','48000','-ac','2',str(combined)])
        duration=renderer.audio_duration(combined)
        speed=max(1,duration/shot.duration_seconds)
        if speed>1.25:
            raise MediaValidationError('Character dialogue cannot fit its shot; shorten the dialogue')
        run(['ffmpeg','-y','-v','error','-i',str(combined),'-af',f'atempo={speed:.6f},apad',
             '-t',str(shot.duration_seconds),'-ar','48000','-ac','2',str(output)])
    output.chmod(0o600)
    return output
