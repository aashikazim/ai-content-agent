"""Provider-contract regressions: no hosted requests or GPU inference."""
import json
from unittest.mock import Mock

import jsonschema
import pytest

from app.services.clip_studio.media import StudioError
from app.services.clip_studio.planner import (
    HostedPlanner, MockPlanner, compact_analysis, planning_prompt,
    strict_schema, validate_hosted_plan,
)
from app.services.clip_studio.schemas import ProjectRequest
from app.services.clip_studio.storage import ClipStudioStorage
from tests.test_clip_studio import specification


def request_for(provider='anthropic', **settings):
    value=specification('video',provider)
    value['settings'].update(video_model='ltx',clip_count=5,duration_seconds=15,narration='source',paid_planning_confirmed=True)
    value['settings'].update(settings)
    return ProjectRequest.model_validate(value)


def candidate(request):
    value=MockPlanner().plan(request,[{'metadata_json':'{"duration_seconds":713.748}'}]).model_dump(mode='json')
    for clip in value['clips']:
        clip['reference_mode']='image-to-video'
        clip.pop('character_ids');clip.pop('dialogue')
    return value


def response(provider,value):
    result=Mock(status_code=200)
    if provider=='anthropic':
        result.json.return_value={'id':'msg-fixture','stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(value)}],'usage':{'input_tokens':100,'output_tokens':200}}
    else:
        result.json.return_value={'id':'resp-fixture','status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(value)}]}],'usage':{}}
    return result


@pytest.mark.parametrize('provider',['anthropic','openai'])
def test_no_cast_five_fifteen_second_contract_matches_local_validation(provider):
    request=request_for(provider)
    schema=strict_schema(request)
    jsonschema.Draft202012Validator.check_schema(schema)
    value=candidate(request)
    jsonschema.validate(value,schema)
    plan=validate_hosted_plan(json.dumps(value),request,{'duration_seconds':713.748})
    assert len(plan.clips)==5 and all(c.duration_seconds==15 for c in plan.clips)
    properties=schema['$defs']['PlannedClip']['properties']
    assert 'character_ids' not in properties and 'dialogue' not in properties
    assert properties['recommended_model']['enum']==['ltx']
    assert properties['duration_seconds']['enum']==[15]
    assert properties['reference_mode']['enum']==['image-to-video']
    assert all(c.character_ids==[] and c.dialogue==[] for c in plan.clips)


def test_documented_enum_case_and_json_integer_spelling_do_not_spend_a_repair(tmp_path,monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','fixture')
    request=request_for();value=candidate(request)
    for c in value['clips']:
        c.update(recommended_model='LTX',reference_mode='Image-to-video',transition='Cut',duration_seconds=15.0)
    session=Mock();session.post.return_value=response('anthropic',value)
    storage=ClipStudioStorage(tmp_path/'existing.db')
    plan,history=HostedPlanner(storage,'fixture',session).plan(request,[],{'duration_seconds':713.748})
    assert session.post.call_count==1 and len(history)==1
    assert all(c.recommended_model=='ltx' and c.duration_seconds==15 for c in plan.clips)
    with storage.connect() as conn:
        saved=conn.execute('SELECT response_json,validation_json FROM clip_studio_plan_responses').fetchone()
    assert 'Image-to-video' in saved['response_json'] and saved['validation_json']=='[]'


@pytest.mark.parametrize('mutation',[
    {'duration_seconds':14.5}, {'duration_seconds':'15'},
    {'recommended_model':'wan'}, {'source_end':900}, {'character_ids':['invented_actor']},
])
def test_equivalent_spelling_normalization_does_not_change_actual_semantics(mutation):
    request=request_for();value=candidate(request);value['clips'][0].update(mutation)
    with pytest.raises(ValueError):validate_hosted_plan(json.dumps(value),request,{'duration_seconds':713.748})


def test_repair_includes_exact_errors_and_private_responses_are_persisted(tmp_path,monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','fixture')
    request=request_for();bad=candidate(request);bad['clips'][0]['character_ids']=['invented_actor']
    session=Mock();session.post.side_effect=[response('anthropic',bad),response('anthropic',candidate(request))]
    storage=ClipStudioStorage(tmp_path/'existing.db')
    plan,_=HostedPlanner(storage,'fixture',session).plan(request,[],{'duration_seconds':713.748})
    assert len(plan.clips)==5 and session.post.call_count==2
    repair=session.post.call_args.kwargs['json']['messages'][0]['content']
    assert 'clips.0.character_ids' in repair and 'No approved cast package exists' in repair
    with storage.connect() as conn:
        rows=conn.execute('SELECT response_json,validation_json FROM clip_studio_plan_responses ORDER BY rowid').fetchall()
    assert len(rows)==2 and 'invented_actor' in rows[0]['response_json']
    assert 'approved cast' in rows[0]['validation_json'] and rows[1]['validation_json']=='[]'


def test_second_semantic_failure_has_field_specific_error_not_invalid_json(tmp_path,monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','fixture')
    request=request_for();bad=candidate(request);bad['clips'][0]['reference_mode']='video-to-video'
    session=Mock();session.post.return_value=response('anthropic',bad)
    storage=ClipStudioStorage(tmp_path/'existing.db')
    with pytest.raises(StudioError,match=r'clips.0.reference_mode') as error:
        HostedPlanner(storage,'fixture',session).plan(request,[],{'duration_seconds':713.748})
    assert session.post.call_count==2 and 'invalid JSON' not in str(error.value)


def test_unreadable_provider_envelope_never_sends_a_repair(tmp_path,monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','fixture')
    session=Mock();session.post.return_value=Mock(status_code=200)
    session.post.return_value.json.side_effect=ValueError('private-provider-details')
    storage=ClipStudioStorage(tmp_path/'existing.db')
    with pytest.raises(StudioError,match='envelope') as error:
        HostedPlanner(storage,'fixture',session).plan(request_for(),[],{'duration_seconds':713.748})
    assert error.value.uncertain and session.post.call_count==1
    assert 'private-provider-details' not in str(error.value)


def test_long_transcript_fits_budget_and_keeps_beginning_and_end():
    analysis={'duration_seconds':3600,'scenes':[{'source_start':0,'source_end':3600}],
              'transcript':[{'start':i*2,'end':i*2+2,'text':f'Original sentence {i}. '+('word '*30)} for i in range(1800)]}
    packed=compact_analysis(analysis)
    assert packed['sampled'] and len(json.dumps(packed,ensure_ascii=False,separators=(',',':')).encode())<=28000
    assert packed['transcript_start_end_text'][0][0]==0
    assert packed['transcript_start_end_text'][-1][1]==3600
    assert len(planning_prompt(request_for(),analysis).encode())<48000


def test_cast_schema_uses_only_canonical_ids():
    cast={'characters':[{'character_id':'Maya'}]}
    schema=strict_schema(request_for(),cast)
    assert schema['$defs']['PlannedClip']['properties']['character_ids']['items']['enum']==['Maya']
    assert schema['$defs']['DialogueLine']['properties']['character_id']['enum']==['Maya']


def test_source_timestamp_contract_does_not_allow_nulls_for_video_or_numbers_for_images():
    request=request_for()
    schema=strict_schema(request)
    properties=schema['$defs']['PlannedClip']['properties']
    assert properties['source_start']['type']==properties['source_end']['type']=='number'
    assert properties['transition']['enum']==['cut']
    value=candidate(request);value['clips'][0].update(source_start=None,source_end=None)
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(value,schema)
    image_request=ProjectRequest.model_validate({**request.model_dump(mode='json'),'source_kind':'images','source_url':None})
    image_properties=strict_schema(image_request)['$defs']['PlannedClip']['properties']
    assert image_properties['source_start']['type']==image_properties['source_end']['type']=='null'
