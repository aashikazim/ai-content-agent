"""Run one real local 3–10-second shot; never approves, publishes or retries."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models.generation_config import GenerationConfig
from app.services import local_cinematic_models as models
from app.services.cloud_video_renderer import validate_clip
from app.services.local_cinematic_service import LocalCinematicService


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',choices=list(models.MODELS),default='ltx-2.5')
    parser.add_argument('--duration',type=int,default=5,choices=range(3,11))
    parser.add_argument('--aspect',choices=['16:9','9:16','1:1'],default='16:9')
    parser.add_argument('--prompt',default='A cinematic medium shot of a fictional actor wearing a blue jacket, walking into a warmly lit workshop. The actor turns toward the camera, smiles and waves. A slow tracking camera follows. Natural room ambience, no titles or captions.')
    parser.add_argument('--image',type=Path,help='Composed LTX start frame')
    parser.add_argument('--character-image',type=Path,help='Wan reference character in the desired clothing/location')
    parser.add_argument('--driving-video',type=Path,help='Permitted 3–10-second motion video, Wan-only')
    parser.add_argument('--rights-confirmed',action='store_true')
    parser.add_argument('--wait-seconds',type=int,default=7200)
    args=parser.parse_args()
    if (args.image or args.character_image or args.driving_video) and not args.rights_confirmed:
        parser.error('Confirm permission for all supplied media with --rights-confirmed')
    if args.model!='ltx-2.5' and not args.character_image:
        parser.error('Wan/hybrid requires --character-image')
    if args.model=='wan2.2-animate-2' and not args.driving_video:
        parser.error('Wan-only requires --driving-video')
    c=GenerationConfig(video_provider='local_cinematic',video_model=args.model,
        video_input_mode='image' if args.image or args.model=='wan2.2-animate-2' else 'text',
        narration='local_tts' if args.model=='wan2.2-animate-2' else 'model_audio')
    references=[]
    for path in [args.image,args.character_image,args.driving_video]:
        if path:
            path=path.resolve()
            with path.open('rb') as file: digest=hashlib.file_digest(file,'sha256').hexdigest()
            references.append({'path':str(path),'sha256':digest})
    if args.driving_video:
        validate_clip(args.driving_video,args.duration,require_audio=False)
    width,height=models.dimensions(args.aspect,args.model)
    adapter=LocalCinematicService(c)
    result=adapter.submit(args.prompt,aspect_ratio=args.aspect,local_inputs={
        'route': 'ltx' if args.model=='ltx-2.5' else 'wan' if args.model=='wan2.2-animate-2' else 'hybrid',
        'duration':args.duration,'shot_number':1,'width':width,'height':height,
        'start_image':str(args.image.resolve()) if args.image else None,
        'character_image':str(args.character_image.resolve()) if args.character_image else None,
        'character_prompt':args.prompt,'reference_files':references,
        'driving_video':str(args.driving_video.resolve()) if args.driving_video else None,
        'ltx_files':{key:str(models.runtime_root()/'models/ltx'/name) for key,name in models.LTX_FILES.items()}})
    identifier=result['interaction_id'];print('Local generation ID:',identifier,flush=True)
    deadline=time.monotonic()+args.wait_seconds
    previous=None
    while result['status']=='in_progress' and time.monotonic()<deadline:
        usage=result.get('usage',{})
        if usage.get('stage')!=previous:
            print('Stage:',usage.get('stage'),'elapsed seconds:',usage.get('elapsed_seconds',0),flush=True)
            previous=usage.get('stage')
        time.sleep(5)
        result=adapter.retrieve(identifier)
    if result['status']!='completed':
        print('Task status:',result['status'],result.get('provider_error') or 'Inspect saved task; a running worker was not cancelled or restarted.')
        return 1
    path=models.runtime_root()/'tasks'/identifier/'output.mp4'
    quality=validate_clip(path,args.duration,require_audio=c.narration=='model_audio')
    report={'generation_id':identifier,'model':args.model,'quality':quality,'performance':result['usage']}
    (path.parent/'smoke-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2));print('Video:',path)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
