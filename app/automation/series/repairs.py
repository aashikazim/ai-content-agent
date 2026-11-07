"""Narrow local repairs preserve already valid creative fields and fixed metadata."""
import re
from app.automation.idempotency import canonical_json
from app.automation.series.models import OutlineChunk


def hook_repair(chunk:OutlineChunk,errors:list[str],start:int,previous:list,bible):
    if not errors or any('hook duplicates' not in e and 'hook is generic' not in e for e in errors):return None
    days=sorted({int(re.match(r'Day (\d+):',e)[1]) for e in errors})
    keys=[f'day_{n}' for n in days]
    banned=[ep.hook for ep in previous]+[ep.hook for ep in chunk.episodes]
    context=[{'day':n,**chunk.episodes[n-start].model_dump(include={'title','goal','conflict','reveal'})} for n in days]
    prompt=('Rewrite ONLY the rejected episode endings in this original fictional series. Each needs ONE SHORT SENTENCE (10–25 words, at most 180 characters): a DISTINCT new concrete unresolved danger or question. '
        'Use a different event, physical object and consequence for each ending, while respecting its goal/conflict/reveal and the frozen world rules. '
        'Do not paraphrase or copy ANY banned ending. Return the exact named fields. Context: '+canonical_json(context)+
        '\nWorld rules: '+canonical_json(bible.world_rules)+'\nBANNED earlier/current endings: '+canonical_json(banned)+'\nErrors: '+canonical_json(errors))
    schema={'type':'object','properties':{key:{'type':'string','minLength':30,'maxLength':180} for key in keys},'required':keys,'additionalProperties':False}
    def merge(body):
        if not isinstance(body,dict) or set(body)!=set(keys):return {'episodes':[]}
        drafts=[e.model_dump() for e in chunk.episodes]
        for n,key in zip(days,keys):drafts[n-start]['hook']=body[key]
        return {'episodes':drafts}
    return prompt,schema,merge
