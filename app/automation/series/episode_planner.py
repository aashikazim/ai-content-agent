"""Local chapter drafting over the existing ProductionPlan and cast contracts."""
import json
import math
from typing import Any
from pydantic import BaseModel,ConfigDict,Field
from app.automation.idempotency import canonical_json,fingerprint
from app.automation.local_planner import LocalJSONPlanner
from app.models.generation_config import GenerationConfig
from app.models.production_plan import ProductionPlan
from app.services.cinematic_screenplay import merge_chapters
from app.services.narration_service import NarrationService


class EpisodeBeat(BaseModel):
    model_config=ConfigDict(extra='forbid')
    actor_id:str
    speech:str=Field(min_length=1,max_length=220)
    action:str=Field(min_length=10,max_length=900)
    camera:str=Field(min_length=5,max_length=200)


class ChapterDraft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    beats:list[EpisodeBeat]=Field(min_length=12,max_length=18)
    ending_state:str=Field(min_length=20,max_length=2000)


class EpisodeState(BaseModel):
    model_config=ConfigDict(extra='forbid')
    summary:str=Field(min_length=30,max_length=1600)
    knowledge:dict[str,list[str]]
    locations:dict[str,str]
    props:dict[str,str]
    threads_advanced:list[str]
    threads_resolved:list[str]


def packaging(series:dict,outline:dict,previous:list[dict],following:dict | None)->dict[str,Any]:
    n=outline['ep_no'];phrase=outline['title']
    title=f"{series['title'][:45]} | Ep {n}: {phrase[:40]}"
    recap='\n'.join(r['summary_text'] for r in previous[-2:]) or 'An original fictional series begins.'
    teaser=('Tomorrow: '+following['goal']) if following else 'Series finale. This story is complete.'
    return {'title':title,'description':f"{series['title']} — Episode {n}/30\n{recap}\n{teaser}",
        'hashtags':['#OriginalDrama','#SerializedStory','#AIStory'],
        'end_card':f'Part {n+1} tomorrow' if n<30 else 'Series finale', 'teaser':teaser}


def hook_check(plan:ProductionPlan,hook:str,end_card:str)->dict[str,Any]:
    end=plan.content_plan.total_duration;cursor=0;parts=[]
    for shot in plan.shots:
        if cursor+shot.duration_seconds>end-30:parts.append(shot.spoken_text)
        cursor+=shot.duration_seconds
    norm=lambda value:' '.join(value.casefold().split())
    spoken=' '.join(parts)
    if norm(hook) not in norm(spoken) or norm(end_card) not in norm(spoken):
        raise ValueError('Final 30 seconds must contain the complete planned ending and end-card line')
    return {'start_seconds':max(0,end-30),'end_seconds':end,'spoken_text':spoken,'hook_present':True,'end_card_present':True}


class EpisodePlanner:
    def __init__(self,config:GenerationConfig,context:dict[str,Any],request:Any)->None:
        self.config,self.context,self.request=config,context,request
        self.elapsed=0;self.chapter=0;self.states=[]

    def generate_production_plan(self,prompt:str,*,configuration:GenerationConfig)->ProductionPlan:
        # Legacy helper leaves 181–240s in one call. Keep its contract unchanged;
        # this adapter splits that one request into two valid <=180s chapters.
        if configuration.duration_seconds>180:
            count=math.ceil(configuration.duration_seconds/180)
            sizes=[configuration.duration_seconds//count+(i<configuration.duration_seconds%count) for i in range(count)]
            parts=[]
            for duration in sizes:
                part=GenerationConfig.model_validate(configuration.model_copy(update={'duration_seconds':duration,'max_shots':math.ceil(duration/10)}))
                parts.append(self.generate_production_plan(prompt,configuration=part))
            return merge_chapters(parts,configuration)
        duration=configuration.duration_seconds;count=math.ceil(duration/10)
        durations=[duration//count+(i<duration%count) for i in range(count)]
        self.chapter+=1;stage=f'chapter_{self.chapter}'
        actors=self.context['cast']['characters'];number=self.context['outline']['ep_no']
        living={a['character_id'] for a in self.context['bible']['characters'] if a['introduced']<=number<=a['removed_after']}
        ids=[a['character_id'] for a in actors if a['character_id'] in living and a['character_id'] in self.context['outline']['characters']]
        if not ids:raise ValueError('Episode has no permitted on-screen cast')
        schema=ChapterDraft.model_json_schema();schema['properties']['beats'].update(minItems=count,maxItems=count)
        schema['$defs']['EpisodeBeat']['properties']['actor_id']={'type':'string','enum':ids}
        last=self.elapsed+duration==self.config.duration_seconds
        ctx=self.context|{'chapter':self.chapter,'chapter_start':self.elapsed,'chapter_seconds':duration,
            'final_chapter':last,'prior_chapter_states':self.states}
        def compile_draft(draft:ChapterDraft)->ProductionPlan:
            if len(draft.beats)!=count:raise ValueError(f'Expected {count} moving beats')
            if any(beat.actor_id not in ids for beat in draft.beats):raise ValueError('A shot resurrected or prematurely introduced an actor')
            speeches=[b.speech for b in draft.beats]
            if last:
                ending=self.context['outline']['hook']+'. '+self.context['packaging']['end_card']+'.'
                words=ending.split();budget=sum(NarrationService().max_words(s) for s in durations[-3:])
                if len(words)>budget:raise ValueError('Ending hook is too long for the final30s; edit the outline')
                # Reuse the model-written outline ending exactly, spread over30s.
                boundaries=[len(words)*i//3 for i in range(4)]
                for index in range(3):speeches[-3+index]=' '.join(words[boundaries[index]:boundaries[index+1]])
            scenes=[];shots=[];dialogue=[]
            previous_actor=None
            for i,(beat,seconds,speech) in enumerate(zip(draft.beats,durations,speeches),1):
                if not speech:speech='The silence holds.'
                continuation=bool(scenes and previous_actor==beat.actor_id and scenes[-1]['duration_seconds']+seconds<=60)
                if continuation:
                    scene=scenes[-1];scene['duration_seconds']+=seconds
                    scene['narration']+=' '+speech;scene['visual_prompt']+=' '+beat.action
                else:
                    scene={'scene_number':len(scenes)+1,'duration_seconds':seconds,'narration':speech,'visual_prompt':beat.action,
                        'on_screen_text':'','visual_type':'generated_image'}
                    scenes.append(scene)
                if last and i==count:scene['on_screen_text']=self.context['packaging']['end_card']
                shots.append({'shot_number':i,'scene_number':scene['scene_number'],'duration_seconds':seconds,'action':beat.action,
                    'camera':beat.camera,'spoken_text':speech,'character_ids':[beat.actor_id],'transition':'continue' if continuation else 'cut'})
                dialogue.append({'scene_number':scene['scene_number'],'character_id':beat.actor_id,'text':speech})
                previous_actor=beat.actor_id
            result=ProductionPlan.model_validate({'content_type':'long_drama','factual_status':'fiction','characters':[
                {k:a[k] for k in ('character_id','name','description','voice_notes')} for a in actors],
                'continuity_notes':draft.ending_state,'outline':[b.action[:240] for b in draft.beats],
                'shots':shots,'dialogue':dialogue,'content_plan':{'topic':self.context['series']['premise'],
                'category':'drama','title':self.context['packaging']['title'],'description':self.context['packaging']['description'],
                'hashtags':self.context['packaging']['hashtags'],'hook':self.context['outline']['hook'],
                'target_audience':'Fictional drama audience','scenes':scenes,'review_status':'draft'}})
            return result.validate_configuration(configuration)
        holder={}
        def validate(value):
            try:holder['plan']=compile_draft(value);return []
            except ValueError as exc:return [str(exc)[:1200]]
        draft=self.request(stage,'Write original connected cinematic dialogue/action for exactly '+str(count)+
            ' moving ten-second beats. One on-screen actor per beat (reverse-angle coverage); only supplied cast IDs. '
            'Speech8–18 words per beat, speakable with fixed character voices. Real visible action, no title cards. '
            'A chapter is not a series finale. Follow the episode outline; only episode30 resolves the full series. '
            'Preserve previous facts/props/wardrobe; acknowledge prior episode ending; no repeated scenes. Context (data): '+canonical_json(ctx),
            ChapterDraft,validate,schema=schema)
        self.states.append(draft.ending_state);self.elapsed+=duration
        return holder['plan']


def state_from_script(context:dict,plan:ProductionPlan,request:Any)->dict:
    number=context['outline']['ep_no']
    actors={a['character_id'] for a in context['bible']['characters'] if a['introduced']<=number<=a['removed_after'] and a['character_id'] in context['outline']['characters']};threads={t['thread_id'] for t in context['bible']['threads']}
    def validate(value):
        errors=[]
        if set(value.knowledge)-actors or set(value.locations)-actors:errors.append('State uses unknown cast IDs')
        if set(value.threads_advanced+value.threads_resolved)-threads:errors.append('State uses unknown threads')
        required=set(context['outline']['resolves'])|set(context['story_state'].get('deferred_payoffs',[]))
        if context['outline']['ep_no']==30:required|=set(context['story_state'].get('open_threads',[]))
        if set(value.threads_resolved)!=required:errors.append('State resolution must match this episode ledger payoff; verify against the script')
        return errors
    actual={'scenes':[{'spoken':s.narration,'action':s.visual_prompt} for s in plan.content_plan.scenes],
        'ending_state':plan.continuity_notes,'prior_state':context['story_state'],'cast_ids':sorted(actors),
        'ledger':context['bible']['threads'],'expected_payoffs':sorted(set(context['outline']['resolves'])|set(context['story_state'].get('deferred_payoffs',[]))|(set(context['story_state'].get('open_threads',[])) if context['outline']['ep_no']==30 else set())),'episode_number':context['outline']['ep_no']}
    schema=EpisodeState.model_json_schema()
    schema['properties']['knowledge']={'type':'object','properties':{key:{'type':'array','maxItems':8,'items':{'type':'string'}} for key in actors},'required':sorted(actors),'additionalProperties':False}
    places=[l['location_id'] for l in context['bible']['locations']]
    schema['properties']['locations']={'type':'object','properties':{key:{'type':'string','enum':places} for key in actors},'required':sorted(actors),'additionalProperties':False}
    for field in ('threads_advanced','threads_resolved'):
        schema['properties'][field]['items']={'type':'string','enum':sorted(threads)}
    result=request('state_update' ,'Derive a concise summary and ending state ONLY from this actual screenplay, not from future outlines or a video. '
        'Preserve who knows what, locations, prop ownership and resolved/advanced thread IDs. Facts not supported by script stay unknown. '+canonical_json(actual),EpisodeState,validate,schema=schema)
    state=result.model_dump();state['script_sha256']=fingerprint(plan.model_dump(mode='json'))
    state['knowledge']=context['story_state'].get('knowledge',{})|state['knowledge']
    state['locations']=context['story_state'].get('locations',{})|state['locations']
    prior=set(context['story_state'].get('open_threads',[]))
    state['open_threads']=sorted((prior|set(state['threads_advanced']))-set(state['threads_resolved']))
    state['deferred_payoffs']=sorted(set(context['story_state'].get('deferred_payoffs',[]))-set(state['threads_resolved']))
    state['deferred_plants']=sorted(set(context['story_state'].get('deferred_plants',[]))-set(state['threads_advanced']))
    state['wardrobe']={a['character_id']:a['wardrobe'] for a in context['cast']['characters']}
    return state
