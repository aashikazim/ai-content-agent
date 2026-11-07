"""Local Shorts recipes compile timed beats into the existing ProductionPlan."""
import json
import math
from typing import Any
from pydantic import BaseModel,ConfigDict,Field
from app.automation.config import Recipe
from app.automation.local_planner import LocalJSONPlanner
from app.models.generation_config import GenerationConfig
from app.models.production_plan import ProductionPlan


class Beat(BaseModel):
    model_config=ConfigDict(extra='forbid')
    narration:str=Field(min_length=1,max_length=300)
    action:str=Field(min_length=1,max_length=1200)
    camera:str=Field(min_length=1,max_length=200)


class ShortScript(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=100)
    description:str=Field(min_length=1,max_length=1500)
    hook:str=Field(min_length=1,max_length=200)
    beats:list[Beat]=Field(min_length=3,max_length=6)


def recipe_configuration(recipe: Recipe,topic: dict[str,Any]) -> GenerationConfig:
    if recipe.input_mode!='text' or recipe.model!='ltx-2.5' or recipe.cast_mode!='none':
        raise ValueError('Daily Shorts currently require independent LTX text mode; use approved-cast Local Video Studio for image/Wan recipes')
    return GenerationConfig(content_type='news' if recipe.style=='news_explainer' else 'realistic_short',
        script_provider='ollama',video_provider='local_cinematic',video_model=recipe.model,
        video_input_mode='text',source_mode='topic',topic=topic['title'][:1000],
        visual_style='2d_cartoon' if recipe.style=='animation' else 'cinematic',
        narration=recipe.narration,captions=recipe.captions,speech_profile_id=recipe.speech_profile_id,
        duration_seconds=recipe.duration_s,aspect_ratio=recipe.aspect,resolution='720p',fps=24,
        max_shots=12,require_motion=True,max_cost_usd='0')


def brief(topic: dict[str,Any],recipe: Recipe) -> dict[str,Any]:
    structures={'comedy_satire':'setup, escalation, punchline',
        'realistic_drama':'goal, conflict, reveal, final hook',
        'news_explainer':'general concept, context, uncertainty; no invented current facts',
        'animation':'visual setup, playful escalation, payoff'}
    return {'topic_signal':topic['title'],'structure':structures[recipe.style],
        'style':recipe.style,'duration_seconds':recipe.duration_s,
        'source_bundle':{'status':'auto_compiled','reviewed':False,'factual_status':'unverified','urls':topic.get('urls',[])},
        'instruction':'Create original material inspired only by the theme; do not copy the source title, script, song, actors or footage.'}


class ShortsPlanner:
    def __init__(self,config: GenerationConfig,topic: dict[str,Any],recipe: Recipe,planner: Any=None,receipt: Any=None) -> None:
        self.config,self.topic,self.recipe=config,topic,recipe
        self.planner=planner or LocalJSONPlanner(config.script_model)
        self.receipt=receipt

    def generate_production_plan(self,prompt: str,**kwargs: Any) -> ProductionPlan:
        count=math.ceil(self.config.duration_seconds/10)
        durations=[self.config.duration_seconds//count+(i<self.config.duration_seconds%count) for i in range(count)]
        request='Write exactly '+str(count)+' beats, one continuous moving shot per beat. Each narration has 8–18 words, no dialogue speakers, no on-screen text. '+json.dumps(brief(self.topic,self.recipe))
        schema=ShortScript.model_json_schema()
        schema['properties']['beats'].update(minItems=count,maxItems=count)
        errors=[]
        for attempt in range(2):
            raw=self.planner.request(request+('\nFix these received validation errors: '+json.dumps(errors) if errors else ''),schema,2500)
            if self.receipt:self.receipt(attempt+1,raw,[])
            try:
                script=ShortScript.model_validate(raw)
                if len(script.beats)!=count:raise ValueError(f'Expected {count} beats')
                scenes=[];shots=[]
                for index,(beat,duration) in enumerate(zip(script.beats,durations),1):
                    scenes.append({'scene_number':index,'duration_seconds':duration,'narration':beat.narration,
                        'visual_prompt':beat.action,'on_screen_text':'','visual_type':'generated_image'})
                    shots.append({'shot_number':index,'scene_number':index,'duration_seconds':duration,
                        'action':beat.action,'camera':beat.camera,'spoken_text':beat.narration,'character_ids':[]})
                value=ProductionPlan.model_validate({'content_type':self.config.content_type,
                    'factual_status':'unverified' if self.config.content_type=='news' else 'satire' if self.recipe.style=='comedy_satire' else 'fiction',
                    'outline':[b.action[:200] for b in script.beats], 'characters':[],'dialogue':[],'shots':shots,
                    'content_plan':{'topic':self.topic['title'],'category':self.topic['category'],'title':self.recipe.title_template.format(title=script.title,category=self.topic['category']),
                        'description':script.description+'\nAI-generated illustrative content. Human review required.',
                        'hashtags':[t.format(title=script.title,category=self.topic['category']) for t in self.recipe.hashtag_templates],'hook':script.hook,'target_audience':'General audience',
                        'scenes':scenes,'source_video_ids':[],'review_status':'draft'}})
                return value.validate_configuration(self.config)
            except ValueError as exc:
                errors=[str(exc)[:1500]]
                if self.receipt:self.receipt(attempt+1,raw,errors)
        raise ValueError('Local screenplay failed bounded validation: '+str(errors))


class ShortsLane:
    """Lane protocol adapter; produce returns a durable slot intent, not approval."""
    def __init__(self,selector: Any,service: Any) -> None:
        self.selector,self.service=selector,service

    def plan_day(self,context: Any) -> tuple:
        from app.automation.lanes import PlannedItem
        result=self.selector.preview(context.config)
        return tuple(PlannedItem(i,t['title']) for i,t in enumerate(result['topics'],1))

    def produce(self,context: Any,item: Any) -> str:
        plan=self.service.enqueue_day(context.plan_date,context.lane_id,confirmed=True)
        row=next(s for s in plan['slots'] if s['slot']==item.slot)
        return row['job_id'] or row['daily_plan_id']

    def publishable(self,context: Any) -> tuple[str,...]:
        from app.automation.reports import read_day
        return tuple(row['job_id'] for row in read_day(self.service.storage.db_path,context.plan_date,context.lane_id)['slots'] if row['job_status']=='approved')

    def describe(self) -> str:
        return 'Original local Shorts with durable day intent and human quality review'
