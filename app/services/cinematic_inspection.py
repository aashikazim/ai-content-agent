"""Private comparison sheets for human continuity review; no identity claims."""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from app.services.cloud_video_renderer import media_sha256, run, MediaValidationError


def comparison_sheet(service, production_id, attempt_id):
    value=service.storage.get(production_id)
    attempt=service.storage.attempt(attempt_id)
    if not value or not attempt or attempt['production_id']!=production_id or attempt['status']!='completed':
        raise ValueError('A completed local clip is required for continuity inspection')
    path=Path(attempt['clip_path']).resolve()
    if not path.is_relative_to(service.root) or not path.is_file():
        raise ValueError('The selected video asset is unavailable')
    quality=json.loads(attempt['media_quality_json'] or '{}')
    digest=media_sha256(path)
    if quality.get('sha256') and digest!=quality['sha256']:
        raise MediaValidationError('The clip changed after validation')
    package=service.cast.get(value['configuration']['cast_package_id']) if value['configuration'].get('cast_package_id') else None
    target=path.parent/attempt_id/'continuity'
    target.mkdir(parents=True,exist_ok=True,mode=0o700)
    key=digest+'_'+str(round(attempt['trim_start_seconds']*1000))+'_'+(package['sha256'][:12] if package else 'none')
    output=target/(key+'.jpg')
    if output.is_file():return output
    shot=json.loads(attempt['request_json'])['shot']
    refs=[]
    if package:
        refs=[service.references.verified([actor['primary_reference_id']])[0] for actor in package['characters'] if actor['character_id'] in shot['character_ids']]
    images=[]
    for ref in refs:
        images.append((ref['name']+' · approved identity',Path(ref['image_path'])))
    for index,fraction in enumerate((.05,.25,.5,.75,.95)):
        frame=target/f'{key}_{index}.png'
        run(['ffmpeg','-y','-v','error','-ss',str(attempt['trim_start_seconds']+shot['duration_seconds']*fraction),
             '-i',str(path),'-frames:v','1',str(frame)])
        frame.chmod(0o600);images.append((f'Shot {shot["shot_number"]} · {round(fraction*100)}%',frame))
    width,height=320,220
    sheet=Image.new('RGB',(width*3,height*((len(images)+2)//3)), '#18251f')
    draw=ImageDraw.Draw(sheet)
    for index,(label,file) in enumerate(images):
        with Image.open(file) as original:
            tile=ImageOps.contain(original.convert('RGB'),(width-12,height-34))
        x,y=(index%3)*width,(index//3)*height
        sheet.paste(tile,(x+(width-tile.width)//2,y+25))
        draw.text((x+8,y+6),label[:48],fill='white')
    sheet.save(output,quality=88);output.chmod(0o600)
    return output
