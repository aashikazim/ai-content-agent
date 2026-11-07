"""Strict bounded contracts for original story planning."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator

class StoryModel(BaseModel):
    model_config=ConfigDict(extra='forbid',hide_input_in_errors=True)

class Premise(StoryModel):
    title:str=Field(min_length=4,max_length=150)
    premise:str=Field(min_length=40,max_length=2000)

class Premises(StoryModel):
    candidates:list[Premise]=Field(min_length=3,max_length=3)

class Actor(StoryModel):
    character_id:str=Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    name:str=Field(min_length=2,max_length=100)
    description:str=Field(min_length=20,max_length=800)
    wardrobe:str=Field(min_length=10,max_length=300)
    voice_notes:str=Field(min_length=10,max_length=300)
    motivation:str=Field(min_length=10,max_length=500)
    secret:str=Field(min_length=10,max_length=500)
    introduced:int=Field(default=1,ge=1,le=29,strict=True)
    removed_after:int=Field(default=30,ge=1,le=30,strict=True)

class Location(StoryModel):
    location_id:str=Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    description:str=Field(min_length=15,max_length=500)

class Thread(StoryModel):
    thread_id:str=Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    question:str=Field(min_length=15,max_length=500)
    plant_day:int=Field(ge=1,le=29,strict=True)
    payoff_day:int=Field(ge=2,le=30,strict=True)

class Bible(StoryModel):
    logline:str=Field(min_length=30,max_length=1000)
    tone:str=Field(min_length=10,max_length=500)
    visual_style:str=Field(min_length=10,max_length=500)
    characters:list[Actor]=Field(min_length=2,max_length=3)
    locations:list[Location]=Field(min_length=1,max_length=5)
    world_rules:list[str]=Field(min_length=1,max_length=6)
    threads:list[Thread]=Field(min_length=2,max_length=5)

    @model_validator(mode='after')
    def references(self):
        for items,key in [(self.characters,'character_id'),(self.locations,'location_id'),(self.threads,'thread_id')]:
            if len({getattr(x,key) for x in items})!=len(items):raise ValueError('Duplicate bible IDs')
        for actor in self.characters:
            if actor.introduced>actor.removed_after:raise ValueError(f'Character {actor.character_id}: introduced={actor.introduced}, removed_after={actor.removed_after}; set removed_after >= introduced (use 30 for a recurring actor)')
        if any(t.plant_day>=t.payoff_day for t in self.threads):raise ValueError('Thread payoff must follow planting')
        if any(not any(a.introduced<=day<=a.removed_after for a in self.characters) for day in range(1,31)):raise ValueError('At least one bible actor must be present on every day1–30')
        if max(t.payoff_day for t in self.threads)!=30:raise ValueError('Reserve at least one thread payoff for the finale')
        return self

class EpisodeDraft(StoryModel):
    title:str=Field(min_length=4,max_length=150)
    goal:str=Field(min_length=15,max_length=600)
    conflict:str=Field(min_length=15,max_length=600)
    reveal:str=Field(min_length=15,max_length=600)
    mini_arc:str=Field(min_length=20,max_length=800)
    opening_ack:str=Field(min_length=15,max_length=800)
    hook:str=Field(min_length=15,max_length=500)
    unresolved:bool=Field(strict=True)
    characters:list[str]=Field(min_length=1,max_length=3)
    locations:list[str]=Field(min_length=1,max_length=5)
    advances:list[str]=Field(max_length=5)
    resolves:list[str]=Field(max_length=5)

class Episode(EpisodeDraft):
    ep_no:int=Field(ge=1,le=30,strict=True)
    phase:Literal['setup','escalation','climax','resolution']
    duration_seconds:int=Field(ge=300,le=600,strict=True)
    previous_hook:str=Field(max_length=500)

class OutlineChunk(StoryModel):
    episodes:list[EpisodeDraft]=Field(min_length=5,max_length=5)

def phase(number:int)->str:
    return 'setup' if number<=8 else 'escalation' if number<=22 else 'climax' if number<=29 else 'resolution'


def chunk_schema(bible:Bible,start:int)->dict:
    """Constrain each day at decoding time as well as after receipt.

    Creative text remains model-written. IDs, lifecycle and ledger dates are
    already frozen facts, so the decoder must not invent alternate values.
    """
    import copy
    schema=OutlineChunk.model_json_schema()
    base=schema['$defs']['EpisodeDraft'];items=[]
    for day in range(start,start+5):
        item=copy.deepcopy(base);props=item['properties']
        props['unresolved']={'type':'boolean','enum':[day<30]}
        def identifiers(field,values,required=None):
            value={'type':'array','items':{'type':'string','enum':values},'maxItems':len(values)} if values else {'type':'array','maxItems':0,'items':{'type':'string'}}
            if required is not None:
                value={'type':'array','minItems':len(required),'maxItems':len(required),
                    'items':{'type':'string','enum':required}} if required else {'type':'array','maxItems':0,'items':{'type':'string'}}
            elif field in ('characters','locations'):value['minItems']=1
            props[field]=value
        identifiers('characters',[a.character_id for a in bible.characters if a.introduced<=day<=a.removed_after])
        identifiers('locations',[l.location_id for l in bible.locations])
        planting=[t.thread_id for t in bible.threads if t.plant_day==day]
        identifiers('advances',[t.thread_id for t in bible.threads if t.plant_day<=day<=t.payoff_day],planting if planting else None)
        identifiers('resolves',[],[t.thread_id for t in bible.threads if t.payoff_day==day])
        items.append(item)
    # Named fields avoid tuple/prefixItems grammar rejected by installed Ollama.
    names=[f'day_{day}' for day in range(start,start+5)]
    return {'type':'object','properties':dict(zip(names,items)),'required':names,'additionalProperties':False}
