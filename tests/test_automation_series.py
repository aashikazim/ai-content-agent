"""Offline series planning, adversarial story gates and authenticated API integration."""
import copy
import json
import random
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4
import pytest
from app.automation.config import load_settings
from app.automation.series.models import Bible,Episode,phase
from app.automation.series.service import SeriesService,read_series
from app.automation.series.validators import validate_outline
from app.automation.storage import IdempotencyConflict
from tests.test_generation_api import generation_api,sign_in

ROOT=Path(__file__).parents[1]

@pytest.fixture(autouse=True)
def no_real_calls(monkeypatch):
    forbidden=Mock(side_effect=AssertionError('No real network, inference or generation in tests'))
    monkeypatch.setattr('ollama.Client.chat',forbidden);monkeypatch.setattr('ollama.Client.generate',forbidden)
    monkeypatch.setattr('requests.sessions.Session.send',forbidden)
    monkeypatch.setattr('app.services.local_cinematic_service.LocalCinematicService.submit',forbidden)


def bible_data():
    return {'logline':'Two lighthouse keepers uncover the lost history of a flooded village.',
        'tone':'Grounded suspense with warmth and earned hope.','visual_style':'Coastal practical lighting, restrained handheld closeups.',
        'characters':[{'character_id':key,'name':name,'description':'A coastal engineer with weathered hands and dark curly hair.',
        'wardrobe':'Navy wool coat and canvas trousers.','voice_notes':'A calm, low-pitched measured voice.','motivation':'Recover the village records and protect the coast.',
        'secret':'Knows the reason the old lighthouse was abandoned.','introduced':1,'removed_after':30} for key,name in [('ada','Ada Reed'),('ben','Ben Vale')]],
        'locations':[{'location_id':'tower','description':'A stone lighthouse beside a dangerous tidal causeway.'}],
        'world_rules':['No supernatural events; every mystery has a physical explanation.'],
        'threads':[{'thread_id':'bell','question':'Who removed the bell from the old lighthouse?', 'plant_day':1,'payoff_day':12},
        {'thread_id':'ledger','question':'Where did the missing village ledger end up?', 'plant_day':2,'payoff_day':30}]}


def episodes_data():
    # Distinct fixture hooks; not actual generated artistic output.
    nouns=['amber compass pier storm map diver harbor rope cellar lantern','velvet lock cellar tunnel council watch witness boat smoke clock',
        'silver bell cliff sailor current anchor cable signal beach torch','crimson chart cave keeper ladder rope inspector fog ferry gate',
        'bronze token inlet cousin warning motor raven jetty wire engine']
    result=[]
    for n in range(1,31):
        words=random.Random(n).sample(' '.join(nouns).split(),7)
        # Six rotated words distinguish hooks deterministically while testing duplicate detection separately.
        hook=' '.join(words[:6])+f' threatens district {n}'
        advances=['bell'] if n==1 else ['ledger'] if n==2 else []
        resolves=['bell'] if n==12 else ['ledger'] if n==30 else []
        result.append({'ep_no':n,'phase':phase(n),'duration_seconds':450,'previous_hook':result[-1]['hook'] if result else '',
            'title':f'The lighthouse record {n}','goal':'Ada decodes the damaged coastal survey records.',
            'conflict':'Ben must cross the flooding causeway before the tide turns.','reveal':'A repaired chart exposes a concealed service tunnel.',
            'mini_arc':'They recover the chart, settle a dispute and agree on the next search.',
            'opening_ack':('Previously: '+result[-1]['hook']) if result else 'Ada checks the exact warning left on the recovered chart.',
            'hook':hook if n<30 else 'The restored bell rings over a safe and reunited village.',
            'unresolved':n<30,'characters':['ada','ben'],'locations':['tower'],'advances':advances,'resolves':resolves})
    return result


def fake_planner(*,invalid_once=False,uncertain=False):
    chunks=episodes_data();responses=[{'candidates':[{'title':t,'premise':p} for t,p in [
        ('The Drowned Bell','Two keepers search a flooded coastal village for records that could stop a corrupt seawall project.'),
        ('Orchard of Letters','A retired courier delivers letters hidden in an orchard and reunites the families displaced by a railway.'),
        ('Midnight Kitchen','Rival cooks combine their neighborhood kitchens to feed a city cut off by a prolonged mountain storm.')]]},bible_data()]
    if uncertain:return Mock(request=Mock(side_effect=RuntimeError('uncertain fixture')))
    for start in range(0,30,5):
        body={'episodes':[{k:v for k,v in e.items() if k not in ['ep_no','phase','duration_seconds','previous_hook']} for e in chunks[start:start+5]]}
        if start==0 and invalid_once:
            bad=copy.deepcopy(body);bad['episodes'][0]['unresolved']=False;responses.append(bad)
        responses.append(body)
    return Mock(request=Mock(side_effect=responses))


def service(tmp_path,monkeypatch,**kwargs):
    monkeypatch.setenv('AUTOMATION_DATA_DIR',str(tmp_path/'private'))
    return SeriesService(tmp_path/'existing.db',load_settings(ROOT/'config/lanes.yaml',environ={}),**kwargs)


def test_outline_and_local_flow_with_bounded_repair(tmp_path,monkeypatch):
    s=service(tmp_path,monkeypatch,planner=fake_planner(invalid_once=True));identifier=str(uuid4())
    row=s.enqueue(identifier,confirmed=True);s.process(row['series_id']);s.process(row['series_id'])
    result=read_series(s.storage.db_path,row['series_id'])
    assert result['planning_status']=='completed' and result['status']=='awaiting_cast'
    assert len(result['episodes'])==30 and result['cast_package_ids']==[]
    assert s.planner.request.call_count==9
    schema=s.planner.request.call_args_list[2].args[1]
    assert schema['properties']['day_1']['properties']['characters']['items']['enum']==['ada','ben']
    assert schema['properties']['day_1']['properties']['advances']['items']['enum']==['bell']
    assert any(r['status']=='invalid' and r['errors'] for r in result['receipts'])
    assert s.enqueue(identifier,confirmed=True)['series_id']==row['series_id']
    with s.storage.connect() as c:
        assert c.execute('SELECT count(*) FROM series_episodes').fetchone()[0]==30
        assert c.execute('SELECT count(*) FROM app_work_queue').fetchone()[0]==1
    assert not validate_outline(Bible.model_validate(result['bible']),[Episode.model_validate(x) for x in result['episodes']])

@pytest.mark.parametrize('kind',['missing','order','phase','runtime','empty_hook','generic','duplicate','resolved_early','finale_hook','dangling','unknown_actor','before_intro','after_removal','unknown_location','unknown_thread','early_payoff','unplanted','repeat_payoff','previous','generic_open','mini_arc'])
def test_adversarial_outlines(kind):
    data=bible_data();eps=episodes_data()
    if kind=='missing':eps.pop()
    elif kind=='order':eps[0]['ep_no']=3
    elif kind=='phase':eps[8]['phase']='setup'
    elif kind=='runtime':eps[0]['duration_seconds']=299
    elif kind=='empty_hook':eps[0]['hook']=''
    elif kind=='generic':eps[0]['hook']='Stay tuned to find out what happens next tomorrow.'
    elif kind=='duplicate':eps[1]['hook']=eps[0]['hook'];eps[2]['previous_hook']=eps[1]['hook']
    elif kind=='resolved_early':eps[0]['unresolved']=False
    elif kind=='finale_hook':eps[-1]['unresolved']=True
    elif kind=='dangling':eps[-1]['resolves']=[]
    elif kind=='unknown_actor':eps[0]['characters']=['intruder']
    elif kind=='before_intro':data['characters'][0]['introduced']=2
    elif kind=='after_removal':data['characters'][0]['removed_after']=29
    elif kind=='unknown_location':eps[0]['locations']=['moon']
    elif kind=='unknown_thread':eps[0]['advances']=['missing']
    elif kind=='early_payoff':eps[10]['resolves']=['bell']
    elif kind=='unplanted':eps[0]['advances']=[]
    elif kind=='repeat_payoff':eps[12]['resolves']=['bell']
    elif kind=='previous':eps[1]['previous_hook']='Changed prior event'
    elif kind=='generic_open':eps[1]['opening_ack']='The story continues'
    elif kind=='mini_arc':eps[0]['mini_arc']=''
    try:parsed=[Episode.model_validate(e) for e in eps]
    except ValueError:return  # Schema gate precedes deterministic outline checks.
    assert validate_outline(Bible.model_validate(data),parsed)


def test_uncertain_and_exhausted_requests_never_replay(tmp_path,monkeypatch):
    s=service(tmp_path,monkeypatch,planner=fake_planner(uncertain=True));row=s.enqueue(str(uuid4()),confirmed=True)
    s.process(row['series_id']);s.process(row['series_id'])
    assert read_series(s.storage.db_path,row['series_id'])['planning_status']=='needs_reconciliation'
    assert s.planner.request.call_count==1
    other=service(tmp_path/'other',monkeypatch,planner=Mock(request=Mock(return_value={'candidates':[]})))
    row=other.enqueue(str(uuid4()),confirmed=True);other.process(row['series_id']);other.process(row['series_id'])
    assert other.planner.request.call_count==3
    assert read_series(other.storage.db_path,row['series_id'])['planning_status']=='failed'


def test_cast_gate_immutability_and_edit_conflict(tmp_path,monkeypatch):
    package_id=str(uuid4());cast=Mock();package={'package_id':package_id,'sha256':'fixture-hash','characters':bible_data()['characters']}
    cast.get.return_value=package
    s=service(tmp_path,monkeypatch,planner=fake_planner(),cast_service=cast)
    row=s.enqueue(str(uuid4()),confirmed=True);identifier=row['series_id']
    with pytest.raises(ValueError):s.approve_cast(identifier,package_id,row['updated_at'],confirmed=True)
    s.process(identifier);row=read_series(s.storage.db_path,identifier)
    with pytest.raises(ValueError):s.approve_cast(identifier,package_id,row['updated_at'])
    cast.get.return_value={**package,'characters':[package['characters'][0]]}
    with pytest.raises(ValueError,match='IDs'):s.approve_cast(identifier,package_id,row['updated_at'],confirmed=True)
    cast.get.return_value=package
    edited=copy.deepcopy(row['episodes']);edited[0]['title']='A revised first arrival'
    new=s.edit(identifier,edited,row['updated_at'])
    with pytest.raises(IdempotencyConflict):s.edit(identifier,edited,row['updated_at'])
    active=s.approve_cast(identifier,package_id,new['updated_at'],confirmed=True)
    assert active['status']=='active' and active['cast_package_ids']==[package_id]
    with pytest.raises(ValueError):s.approve_cast(identifier,package_id,active['updated_at'],confirmed=True)
    with s.storage.connect() as c:c.execute("UPDATE series_episodes SET status='scripted' WHERE series_id=? AND ep_no=1",(identifier,))
    with pytest.raises(ValueError,match='lock'):s.edit(identifier,edited,active['updated_at'])


def test_series_api_session_csrf_and_no_auto_activation(generation_api,monkeypatch):
    api=generation_api;monkeypatch.setenv('AUTOMATION_LANES_FILE',str(ROOT/'config/lanes.yaml'))
    monkeypatch.setenv('AUTOMATION_DATA_DIR',str(api.tmp_path/'automation'))
    launch=Mock();monkeypatch.setattr(SeriesService,'launch',launch)
    path='/api/v1/automation/series'
    assert api.client.get(path).status_code==401
    headers=sign_in(api);body={'request_id':str(uuid4()),'confirmed':True}
    assert api.client.post(path,json=body).status_code==403
    row=api.client.post(path,json=body,headers=headers);assert row.status_code==202
    data=row.json();identifier=data['series_id']
    assert api.client.post(path,json=body,headers=headers).json()['series_id']==identifier
    assert launch.call_count==1
    assert api.client.get(path+'/'+identifier).json()['status']=='planning'
    assert api.client.get(path).json()['series'][0]['series_id']==identifier
    rejected=api.client.post(path+'/'+identifier+'/approve-cast',json={'package_id':str(uuid4()),'confirmed':True,'expected_updated_at':data['updated_at']},headers=headers)
    assert rejected.status_code==422
    assert api.client.post(path,json={**body,'genre':'different'},headers=headers).status_code==409
    api.publisher.assert_not_called();api.execute.assert_not_called()


def test_read_no_database_initialization(tmp_path):
    path=tmp_path/'missing.db';assert read_series(path)==[];assert not path.exists()


def test_invalid_bible_feedback_identifies_actor():
    data=bible_data();data['characters'][0].update(introduced=5,removed_after=3)
    with pytest.raises(ValueError,match='ada: introduced=5, removed_after=3'):Bible.model_validate(data)


def test_corrupt_bible_and_mismatched_cast_never_activate(tmp_path,monkeypatch):
    cast=Mock();package_id=str(uuid4());members=bible_data()['characters'];members[0]['wardrobe']='Unapproved red suit'
    cast.get.return_value={'package_id':package_id,'sha256':'fixture','characters':members}
    s=service(tmp_path,monkeypatch,planner=fake_planner(),cast_service=cast)
    row=s.enqueue(str(uuid4()),confirmed=True);s.process(row['series_id']);row=read_series(s.storage.db_path,row['series_id'])
    with pytest.raises(ValueError,match='wardrobe'):s.approve_cast(row['series_id'],package_id,row['updated_at'],confirmed=True)
    with s.storage.connect() as c:c.execute("UPDATE series SET bible_sha256='changed' WHERE series_id=?",(row['series_id'],))
    with pytest.raises(ValueError,match='hash'):s.approve_cast(row['series_id'],package_id,row['updated_at'],confirmed=True)
    assert read_series(s.storage.db_path,row['series_id'])['status']=='awaiting_cast'


def test_same_key_changed_actor_rejected_and_disabled_auto_never_activates(tmp_path,monkeypatch):
    s=service(tmp_path,monkeypatch,planner=fake_planner());s.settings.config.lanes['story_series'].series_start_mode='auto'
    key=str(uuid4());row=s.enqueue(key,confirmed=True)
    with pytest.raises(IdempotencyConflict):s.enqueue(key,confirmed=True,actor='someone-else')
    s.process(row['series_id'])
    assert read_series(s.storage.db_path,row['series_id'])['status']=='awaiting_cast'


def test_model_is_frozen_and_transport_valueerror_is_uncertain(tmp_path,monkeypatch):
    monkeypatch.setenv('OLLAMA_MODEL','qwen3:8b')
    planner=Mock(request=Mock(side_effect=ValueError('transport ambiguity')))
    s=service(tmp_path,monkeypatch,planner=planner);row=s.enqueue(str(uuid4()),confirmed=True)
    with s.storage.connect() as c:
        payload=json.loads(c.execute('SELECT request_json FROM automation_series_requests').fetchone()[0])
        assert payload['config']['planner']['model']=='qwen3:8b'
    s.process(row['series_id']);s.process(row['series_id'])
    assert planner.request.call_count==1
    assert read_series(s.storage.db_path,row['series_id'])['planning_status']=='needs_reconciliation'



def test_decoding_schema_freezes_per_day_ledger_and_finale():
    from app.automation.series.models import chunk_schema
    bible=Bible.model_validate(bible_data())
    first=list(chunk_schema(bible,1)['properties'].values())
    assert first[0]['properties']['resolves']['maxItems']==0
    assert first[0]['properties']['advances']['items']['enum']==['bell']
    assert first[1]['properties']['advances']['items']['enum']==['ledger']
    last=chunk_schema(bible,26)['properties']['day_30']
    assert last['properties']['unresolved']['enum']==[False]
    assert last['properties']['resolves']['items']['enum']==['ledger']


def test_api_completed_outline_edit_and_cast_activation(generation_api,monkeypatch):
    api=generation_api;monkeypatch.setenv('AUTOMATION_LANES_FILE',str(ROOT/'config/lanes.yaml'))
    monkeypatch.setenv('AUTOMATION_DATA_DIR',str(api.tmp_path/'automation'));monkeypatch.setattr(SeriesService,'launch',Mock())
    headers=sign_in(api);base='/api/v1/automation/series'
    response=api.client.post(base,json={'request_id':str(uuid4()),'confirmed':True},headers=headers)
    assert response.status_code==202;identifier=response.json()['series_id']
    s=SeriesService(api.db_path,load_settings(ROOT/'config/lanes.yaml',environ={}),planner=fake_planner());s.process(identifier)
    row=api.client.get(base+'/'+identifier).json();assert row['status']=='awaiting_cast'
    edited=copy.deepcopy(row['episodes']);edited[0]['title']='A new coastal arrival'
    edit={'episodes':edited,'expected_updated_at':row['updated_at']}
    saved=api.client.put(base+'/'+identifier+'/outline',json=edit,headers=headers)
    assert saved.status_code==200
    assert api.client.put(base+'/'+identifier+'/outline',json=edit,headers=headers).status_code==409
    invalid=copy.deepcopy(saved.json()['episodes']);invalid[-1]['resolves']=[]
    assert api.client.put(base+'/'+identifier+'/outline',json={'episodes':invalid,'expected_updated_at':saved.json()['updated_at']},headers=headers).status_code==422
    package_id=str(uuid4());package={'package_id':package_id,'sha256':'mock-verified','characters':bible_data()['characters']}
    monkeypatch.setattr('app.services.cinematic_cast_service.CinematicCastService.get',Mock(return_value=package))
    cast={'package_id':package_id,'expected_updated_at':saved.json()['updated_at'],'confirmed':False}
    assert api.client.post(base+'/'+identifier+'/approve-cast',json=cast,headers=headers).status_code==422
    approved=api.client.post(base+'/'+identifier+'/approve-cast',json={**cast,'confirmed':True},headers=headers)
    assert approved.status_code==200 and approved.json()['status']=='active'
    api.publisher.assert_not_called();api.execute.assert_not_called()


def test_acknowledged_invalid_json_is_bounded_repair(tmp_path,monkeypatch):
    planner=fake_planner();responses=list(planner.request.side_effect)
    planner.request.side_effect=[json.JSONDecodeError('bad acknowledged local JSON','{',1),*responses]
    s=service(tmp_path,monkeypatch,planner=planner);row=s.enqueue(str(uuid4()),confirmed=True);s.process(row['series_id'])
    result=read_series(s.storage.db_path,row['series_id'])
    assert result['planning_status']=='completed' and planner.request.call_count==9
    assert result['receipts'][0]['status']=='invalid'


def test_bible_cannot_leave_days_without_an_actor():
    data=bible_data()
    for actor in data['characters']:actor['introduced']=2
    with pytest.raises(ValueError,match='every day'):Bible.model_validate(data)



def test_named_day_output_decodes_with_actual_wire_schema(tmp_path,monkeypatch):
    planner=fake_planner();responses=list(planner.request.side_effect)
    for i,body in enumerate(responses[2:]):
        responses[i+2]={f'day_{i*5+j+1}':ep for j,ep in enumerate(body['episodes'])}
    planner.request.side_effect=responses
    s=service(tmp_path,monkeypatch,planner=planner);row=s.enqueue(str(uuid4()),confirmed=True);s.process(row['series_id'])
    assert read_series(s.storage.db_path,row['series_id'])['planning_status']=='completed'


def test_schema_rejection_is_acknowledged_and_unloads_weights(tmp_path,monkeypatch):
    from ollama import ResponseError
    from app.automation.local_planner import LocalJSONPlanner,LocalRequestRejected
    monkeypatch.setattr('app.automation.local_planner.runtime_root',lambda:tmp_path/'runtime')
    client=Mock();client.chat.side_effect=ResponseError('fixture grammar rejection',status_code=400)
    planner=LocalJSONPlanner(client=client)
    with pytest.raises(LocalRequestRejected) as raised:planner.request('test',{})
    assert raised.value.uncertain is False
    client.generate.assert_called_once_with(model=planner.model,prompt='',keep_alive=0)
    s=service(tmp_path,monkeypatch,planner=planner);row=s.enqueue(str(uuid4()),confirmed=True);s.process(row['series_id'])
    result=read_series(s.storage.db_path,row['series_id'])
    assert result['planning_status']=='failed' and result['receipts'][0]['status']=='rejected'


def test_duplicate_hooks_receive_narrow_bounded_repair(tmp_path,monkeypatch):
    planner=fake_planner();responses=list(planner.request.side_effect)
    original=episodes_data()[5]['hook'];responses[3]['episodes'][0]['hook']=episodes_data()[0]['hook']
    responses.insert(4,{'day_6':original})
    planner.request.side_effect=responses
    s=service(tmp_path,monkeypatch,planner=planner);row=s.enqueue(str(uuid4()),confirmed=True);s.process(row['series_id'])
    result=read_series(s.storage.db_path,row['series_id'])
    assert result['planning_status']=='completed'
    assert result['episodes'][5]['hook']==original
    assert planner.request.call_args_list[4].args[1]['required']==['day_6']
    assert 'BANNED earlier/current endings' in planner.request.call_args_list[4].args[0]


def test_hook_repair_never_overwrites_other_fields():
    from app.automation.series.repairs import hook_repair
    from app.automation.series.models import OutlineChunk
    drafts=[{k:v for k,v in x.items() if k not in ['ep_no','phase','duration_seconds','previous_hook']} for x in episodes_data()[:5]]
    chunk=OutlineChunk(episodes=drafts)
    assert hook_repair(chunk,['Day 1: unknown location'],1,[],Bible.model_validate(bible_data())) is None
    _,_,merge=hook_repair(chunk,['Day 2: hook duplicates an earlier ending'],1,[],Bible.model_validate(bible_data()))
    fixed=merge({'day_2':'The final ferry vanishes behind a newly locked floodgate.'})
    assert fixed['episodes'][1]['goal']==drafts[1]['goal']
    assert fixed['episodes'][0]==drafts[0]
    assert fixed['episodes'][1]['hook']!=drafts[1]['hook']


def test_explicit_revision_reuses_valid_prefix_and_never_replays_uncertain(tmp_path,monkeypatch):
    initial=fake_planner();responses=list(initial.request.side_effect)
    initial.request.side_effect=[*responses[:3],{'episodes':[]},{'episodes':[]},{'episodes':[]}]
    s=service(tmp_path,monkeypatch,planner=initial);parent=s.enqueue(str(uuid4()),confirmed=True);s.process(parent['series_id'])
    old=read_series(s.storage.db_path,parent['series_id']);assert old['planning_status']=='failed' and len(old['episodes'])==5
    s.planner=Mock(request=Mock(side_effect=responses[3:]));key=str(uuid4())
    child=s.enqueue(key,source_series_id=parent['series_id'],confirmed=True)
    assert child['bible_sha256']==old['bible_sha256'] and child['episodes']==old['episodes']
    s.process(child['series_id']);s.process(child['series_id'])
    result=read_series(s.storage.db_path,child['series_id']);assert result['status']=='awaiting_cast' and len(result['episodes'])==30
    assert s.planner.request.call_count==5
    assert s.enqueue(key,source_series_id=parent['series_id'],confirmed=True)['series_id']==child['series_id']
    assert read_series(s.storage.db_path,parent['series_id'])['episodes']==old['episodes']
    with pytest.raises(IdempotencyConflict):s.enqueue(str(uuid4()),source_series_id=parent['series_id'],actor='different',confirmed=True)
    with s.storage.connect() as c:c.execute("UPDATE automation_series_requests SET status='needs_reconciliation' WHERE series_id=?",(parent['series_id'],))
    assert s.enqueue(key,source_series_id=parent['series_id'],confirmed=True)['series_id']==child['series_id']
    with pytest.raises(ValueError,match='reconcile'):s.enqueue(str(uuid4()),source_series_id=parent['series_id'],confirmed=True)


def test_structural_and_hook_agents_have_separate_finite_budgets(tmp_path,monkeypatch):
    planner=fake_planner();responses=list(planner.request.side_effect)
    first=responses[2];bad=first['episodes'][0]['hook'];first['episodes'][1]['hook']=bad
    planner.request.side_effect=[*responses[:3],{'day_2':bad},{'day_2':bad},{'day_2':bad}]
    s=service(tmp_path,monkeypatch,planner=planner);row=s.enqueue(str(uuid4()),confirmed=True);s.process(row['series_id'])
    result=read_series(s.storage.db_path,row['series_id'])
    assert result['planning_status']=='failed' and result['episodes']==[]
    assert len([r for r in result['receipts'] if r['stage']=='hooks_1'])==3
    assert planner.request.call_count==6


def test_story_decoding_controls_preserve_short_defaults(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app.automation.local_planner import LocalJSONPlanner
    monkeypatch.setattr('app.automation.local_planner.runtime_root',lambda:tmp_path/'runtime')
    client=Mock();client.chat.return_value=SimpleNamespace(message=SimpleNamespace(content='{}'))
    LocalJSONPlanner(client=client).request('fixture',{})
    assert client.chat.call_args.kwargs['options']['temperature']==.5
    assert 'repeat_penalty' not in client.chat.call_args.kwargs['options']
    LocalJSONPlanner(client=client,temperature=.9,repeat_penalty=1.2).request('fixture',{})
    assert client.chat.call_args.kwargs['options']['temperature']==.9
    assert client.chat.call_args.kwargs['options']['repeat_penalty']==1.2



def test_opening_must_actually_acknowledge_previous_hook():
    eps=episodes_data();eps[1]['opening_ack']='A completely unrelated scene opens with a pleasant breakfast.'
    errors=validate_outline(Bible.model_validate(bible_data()),[Episode.model_validate(e) for e in eps])
    assert any('explicit recap' in error for error in errors)
