"""Long screenplay validation and workflow; paid models are always mocked."""
import time
from decimal import Decimal
from unittest.mock import Mock

import pytest

from app.models.content_plan import ContentPlan
from app.models.generation_config import GenerationConfig
from app.models.long_content_plan import LongContentPlan
from app.models.production_plan import ProductionPlan, strict_production_schema
from app.services.content_storage import ContentStorage
from app.services.job_service import JobService
from app.services.openai_script_service import OpenAIScriptService
from app.services.provider_catalog import validate_for_script
from app.services.publishing_service import PublishingService
from app.services.publishing_storage import PublishingStorage
from app.services.script_generation_service import ScriptGenerationService
from app.services.web_dashboard_service import WebDashboardService
from tests.test_openai_scripts import production, response


def long_production(duration=120):
    data = production('short_drama').model_dump(mode='json')
    data.update(content_type='long_drama', dialogue=[], shots=[], outline=[])
    scene = data['content_plan']['scenes'][0]
    data['content_plan']['scenes'] = []
    for index in range(duration // 8):
        number = index + 1
        text = 'We will find a way.' if index % 2 == 0 else 'I am with you.'
        actor = 'actor_a' if index % 2 == 0 else 'actor_b'
        data['content_plan']['scenes'].append({**scene,'scene_number':number,'duration_seconds':8,
            'narration':text,'visual_prompt':'Two explorers walk through a changing landscape.'})
        data['dialogue'].append({'scene_number':number,'character_id':actor,'text':text})
        data['shots'].append({'shot_number':number,'scene_number':number,'duration_seconds':8,
            'action':'The explorers walk forward and exchange a glance.','camera':'Tracking shot',
            'spoken_text':text,'character_ids':['actor_a','actor_b']})
        data['outline'].append(f'Scene {number}: advance the explorers toward a resolution.')
    return ProductionPlan.model_validate(data)


def long_config(**changes):
    return GenerationConfig(content_type='long_drama',video_provider='runway',video_model='kling3.0_standard',
        duration_seconds=120,max_shots=30,script_provider='openai',script_model='gpt-6.1-sol',
        narration='model_audio',source_mode='topic',topic='Two explorers find their way home',max_cost_usd=500,**changes)


def test_legacy_and_long_contracts_remain_distinct():
    plan = long_production()
    assert isinstance(plan.content_plan, LongContentPlan)
    assert len(plan.shots) == 15
    assert plan.content_plan.total_duration == 120
    plan.validate_configuration(long_config())
    with pytest.raises(ValueError):
        ContentPlan.model_validate(plan.content_plan.model_dump())
    limited = plan.content_plan.model_dump();limited['scenes']=limited['scenes'][:2]
    for scene in limited['scenes']: scene['duration_seconds']=60
    with pytest.raises(ValueError,match='30-60'): ContentPlan.model_validate(limited)
    short = production()
    assert type(short.content_plan) is ContentPlan
    invalid = plan.model_dump(mode='json');invalid['content_type']='short_drama'
    with pytest.raises(ValueError,match='Only long_drama'):
        ProductionPlan.model_validate(invalid)
    assert long_production(1200).content_plan.total_duration == 1200


def test_long_plan_rejects_missing_scenes_bad_cast_or_timing():
    for kind in ['timing','cast','fiction','dialogue']:
        data = long_production().model_dump(mode='json')
        if kind == 'timing': data['shots'][0]['duration_seconds']=7
        elif kind == 'cast': data['shots'][0]['character_ids']=['unknown']
        elif kind == 'fiction': data['factual_status']='unverified'
        else: data['dialogue']=[]
        with pytest.raises(ValueError): ProductionPlan.model_validate(data)


def test_long_config_requires_runway_duration_capacity_and_preserves_local_short(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','fixture')
    conf = long_config();validate_for_script(conf)
    for changes in [{'video_provider':'local','video_model':'local-pipeline'}, {'duration_seconds':60}, {'max_shots':1}]:
        with pytest.raises(ValueError): validate_for_script(conf.model_copy(update=changes))
    with pytest.raises(ValueError): GenerationConfig(max_shots=13)
    assert GenerationConfig.from_legacy().duration_seconds == 45


def test_larger_script_cost_is_quoted_reserved_and_bound_to_configuration(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','fixture')
    fake = Mock()
    fake.generate_production_plan.return_value = long_production()
    fake.last_usage={};fake.last_response_id='resp_long_fixture'
    scripts = ScriptGenerationService(tmp_path/'scripts.db',openai_factory=lambda model:fake)
    conf = long_config()
    request = scripts.request(conf)
    quote = scripts.quote(request,'admin')
    assert Decimal(quote['maximum_cost_usd']) == Decimal('1.06')
    assert quote['max_output_tokens'] == 64000 and quote['max_input_bytes'] == 200000
    assert scripts.maximum_cost() == Decimal('.21')
    with pytest.raises(ValueError):scripts.submit(request,'admin',quote_id=quote['quote_id'],confirmed=False)
    identifier = scripts.submit(request,'admin',quote_id=quote['quote_id'],confirmed=True)
    scripts.run(identifier)
    assert scripts.get(identifier)['status']=='completed'
    assert scripts.get(identifier)['maximum_cost_usd']=='1.06'
    assert fake.generate_production_plan.call_args.kwargs['configuration'] == conf
    with pytest.raises(ValueError):scripts.submit(request,'admin',quote_id=quote['quote_id'],confirmed=True)


def test_openai_long_payload_has_strict_schema_and_long_timeout(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','fixture')
    session=Mock()
    session.post.return_value=response({'id':'resp_long','status':'completed','usage':{},
        'output':[{'type':'message','content':[{'type':'output_text','text':long_production().model_dump_json()}]}]})
    adapter=OpenAIScriptService(session=session)
    assert adapter.generate_production_plan('Write the full drama.',configuration=long_config()).content_type=='long_drama'
    call=session.post.call_args
    assert call.kwargs['timeout']==(5,900)
    assert call.kwargs['json']['max_output_tokens']==64000
    for name in ['ContentPlan','LongContentPlan']:
        assert strict_production_schema()['$defs'][name]['properties']['review_status']['const']=='draft'
    session.post.assert_called_once()


def test_long_running_scripts_are_not_marked_interrupted_after_five_minutes(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','fixture')
    scripts=ScriptGenerationService(tmp_path/'scripts.db',openai_factory=lambda model:Mock())
    request=scripts.request(long_config())
    quote=scripts.quote(request,'admin')
    identifier=scripts.submit(request,'admin',quote_id=quote['quote_id'],confirmed=True)
    with scripts.storage.connect() as conn:
        conn.execute("UPDATE script_generation_attempts SET status='running',updated_at=? WHERE attempt_id=?",(int(time.time())-600,identifier))
    assert scripts.get(identifier)['status']=='running'


def test_long_plan_is_reviewable_and_only_youtube_accepts_long_publication(tmp_path):
    jobs=JobService(tmp_path/'jobs.db');content=ContentStorage(jobs.db_path,tmp_path/'plans')
    plan=long_production().content_plan
    identifier,_=content.save_plan(plan)
    job=jobs.create_job()
    file=tmp_path/'final.mp4';file.write_bytes(b'fixture')
    jobs.update_job(job,'pending_review',plan_id=identifier,video_path=str(file))
    dashboard=WebDashboardService(jobs,content)
    assert len(dashboard.plan_for(jobs.get_job(job))['scenes'])==15
    jobs.review_job(job,'approved')
    publishing=PublishingService(jobs=jobs,content_storage=content,publications=PublishingStorage(jobs.db_path))
    with pytest.raises(ValueError,match='Long-form Facebook'):
        publishing.publish(job,'facebook',dry_run=False)
    assert publishing.publish(job,'youtube',dry_run=True)['status']=='dry_run'
