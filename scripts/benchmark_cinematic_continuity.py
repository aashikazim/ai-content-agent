"""Explicit local-only 90s comparison, with fixed synthetic actors and no script API.

Creates three separate 30s films: image anchors, connected anchors, and hybrid.
Every neural task uses the existing global GPU lock, memory guards and deadlines.
Run only with --run. Completed outputs are never approved or published.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def screenplay():
    from app.models.production_plan import ProductionPlan
    characters = [
        {'character_id':'theo','name':'Theo','description':'A young dark-haired man in a blue jacket in a softly lit recording studio','voice_notes':'Warm natural baritone'},
        {'character_id':'maya','name':'Maya','description':'A young dark-haired woman in a burgundy blouse in the same recording studio','voice_notes':'Warm natural alto'},
    ]
    lines = ['The light is still on.', 'Someone must have stayed late.', "Let us wait another moment.",
             'I found what was missing.', 'We can finally head home.', 'That was worth the wait.']
    actions = ['Theo glances toward a studio lamp and rests his hand on the desk.',
        'Theo gently turns his head toward the doorway, keeping his hand on the desk.',
        'Theo relaxes his shoulders and smiles slightly, then looks back at the lamp.',
        'Maya lifts a small notebook from the studio desk and opens it.',
        'Maya closes the notebook and looks toward the doorway with a smile.',
        'Maya rests the notebook on the desk and gives a small relieved nod.']
    scenes, shots, dialogue = [], [], []
    for scene in (1,2):
        actor = characters[scene-1]
        scenes.append({'scene_number':scene,'duration_seconds':15,'narration':' '.join(lines[(scene-1)*3:scene*3]),
            'visual_prompt': actor['description'] + '. Consistent studio light, desk, outfit and camera framing.',
            'on_screen_text':''})
        for index in range((scene-1)*3,scene*3):
            shots.append({'shot_number':index+1,'scene_number':scene,'duration_seconds':5,'action':actions[index],
                'camera':'Fixed medium close-up, soft shallow depth of field.', 'spoken_text':lines[index],
                'character_ids':[actor['character_id']], 'transition':'continue' if index%3 else 'cut'})
            dialogue.append({'scene_number':scene,'character_id':actor['character_id'],'text':lines[index]})
    return ProductionPlan.model_validate({'schema_version':1,'content_type':'short_drama','factual_status':'fiction',
        'outline':['Theo waits in the studio','Maya finds the notebook and they can leave'],
        'characters':characters,'dialogue':dialogue,'shots':shots,'content_plan':{'topic':'The studio notebook',
        'category':'entertainment','title':'The Studio Notebook','description':'A fictional continuity comparison.',
        'hashtags':['#Fiction'],'hook':'A missing notebook delays two colleagues.','target_audience':'Adults',
        'scenes':scenes}})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--output',required=True)
    parser.add_argument('--theo-image',required=True)
    parser.add_argument('--maya-image',required=True)
    args=parser.parse_args()
    if not args.run:
        print('No generation started. Supply --run to authorize this local-only 90s comparison.')
        return
    root=Path(args.output).resolve();root.mkdir(parents=True,mode=0o700,exist_ok=True)
    if (root/'started.json').exists():
        raise SystemExit('This comparison was already started. Inspect its saved IDs; no automatic restart.')
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    for variable, folder in [('CLOUD_VIDEOS_DIR','videos'),('CONTENT_PLANS_DIR','plans'),('CHARACTER_REFERENCES_DIR','references')]:
        os.environ[variable]=str(root/folder)
    from app.services.cloud_video_service import CloudVideoService
    from app.services.script_generation_service import ScriptGenerationService
    from app.models.generation_config import GenerationConfig
    from app.services.local_cinematic_service import private_json
    service=CloudVideoService(root/'validation.db')
    scripts=ScriptGenerationService(root/'validation.db')
    base=screenplay(); refs=[]
    for actor,image in zip(base.characters,[args.theo_image,args.maya_image]):
        refs.append(service.references.create({'character_id':actor.character_id,'name':actor.name,
            'description':actor.description,'wardrobe':'Blue jacket' if actor.character_id=='theo' else 'Burgundy blouse',
            'voice_notes':actor.voice_notes,'rights_note':'Existing owner-generated fictional validation actor',
            'rights_confirmed':True,'image_base64':base64.b64encode(Path(image).read_bytes()).decode()}))
    cast=service.cast.create({'name':'Studio Notebook comparison cast','confirmed':True,'location_notes':'Same recording studio, desk and warm lights',
        'characters':[{'character_id':ref['character_id'],'name':ref['name'],'description':ref['description'],
            'wardrobe':ref['wardrobe'],'voice_notes':ref['voice_notes'],'reference_ids':[ref['reference_id']],
            'primary_reference_id':ref['reference_id']} for ref in refs]})
    private_json(root/'started.json',{'created_at':time.time(),'cast_package_id':cast['package_id'],'paid_calls':0,'approval':'manual required'})
    report={'status':'running','arms':[],'paid_calls':0,'identity_review':'pending visual inspection'}
    private_json(root/'report.json',report)
    for arm in ('anchored','connected','hybrid'):
        plan=base.model_copy(deep=True)
        if arm=='anchored':
            for shot in plan.shots:shot.transition='cut'
        config=GenerationConfig(video_provider='local_cinematic',video_model='ltx-wan-hybrid' if arm=='hybrid' else 'ltx-2.5',
            video_input_mode='image',continuity_mode='anchored' if arm=='anchored' else 'connected',cast_package_id=cast['package_id'],
            content_type='short_drama',source_mode='script',supplied_script='Fixed fictional studio screenplay; no model script call.',
            duration_seconds=30,max_shots=6,aspect_ratio='16:9',resolution='720p',fps=24,narration='model_audio',captions='timed')
        identifier=str(uuid4());now=int(time.time())
        with scripts.storage.connect() as conn:
            conn.execute('''INSERT INTO script_generation_attempts
                (attempt_id,actor,request_json,provider,model,status,result_json,maximum_cost_usd,created_at,updated_at)
                VALUES (?,'local-benchmark',?,'ollama',?,'completed',?,'0',?,?)''',
                (identifier,json.dumps(scripts.request(config)),config.script_model,plan.model_dump_json(),now,now))
        quote=service.quote(identifier,'local-benchmark',scene_image_reference_ids={1:refs[0]['reference_id'],2:refs[1]['reference_id']})
        production=service.create(quote['quote_id'],'local-benchmark',True)
        item={'arm':arm,'production_id':production['production_id'],'job_id':production['job_id'],'status':'running','started_at':time.time()}
        report['arms'].append(item);private_json(root/'report.json',report)
        print(json.dumps(item),flush=True)
        while True:
            value=service.process(production['production_id'],submit_queued=True)
            private_json(root/(arm+'-state.json'),value)
            item.update(status=value['status'],elapsed_seconds=round(time.time()-item['started_at'],2))
            private_json(root/'report.json',report)
            if value['status']=='technical_ready':
                item['video_path']=service.jobs.get_job(value['job_id'])['video_path']
                item['quality']=value['quality'];break
            if value['status'] in {'failed','needs_reconciliation','assembly_failed','continuity_stale'}:
                report['status']='stopped';item['error']=value['error'];private_json(root/'report.json',report)
                raise SystemExit('Comparison stopped. Saved task IDs and originals retained; no retries.')
            time.sleep(5)
        print(json.dumps(item),flush=True)
    report['status']='completed';private_json(root/'report.json',report)


if __name__=='__main__':
    main()
