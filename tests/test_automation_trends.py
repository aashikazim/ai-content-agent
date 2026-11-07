"""Offline source contracts and selector simulations from saved JSON fixtures."""
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import Mock
import pytest
import requests
from app.automation.clock import FrozenClock
from app.automation.config import load_settings
from app.automation.storage import AutomationStorage
from app.automation.trends import TrendSignal
from app.automation.trends.collector import TrendCollector
from app.automation.trends.sources import YouTubeSource,GDELTSource,WikipediaSource,HackerNewsSource,GoogleTrendsSource,RedditSource,sources
from app.automation.selector import TopicSelector,similar,classify,cluster
from tests.test_generation_api import generation_api,sign_in

ROOT=Path(__file__).parents[1]
NOW=datetime(2026,11,7,tzinfo=timezone.utc)
CLOCK=FrozenClock(NOW)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session,'send',Mock(side_effect=AssertionError('Live network forbidden in tests')))


def fixture(name):return json.loads((ROOT/'tests/fixtures/automation'/name).read_text())


def session(body):
    response=Mock(status_code=200);response.json.return_value=body
    value=Mock();value.get.return_value=response;return value


def test_youtube_reuses_official_parser_and_ranking():
    from app.services.youtube_service import YouTubeService
    client=Mock();client.videos.return_value.list.return_value.execute.return_value=fixture('youtube.json')
    rows=YouTubeSource(YouTubeService(client),CLOCK).collect('US')
    assert rows and all(s.source=='youtube' and s.score>0 for s in rows)
    assert client.videos.return_value.list.call_args.kwargs['chart']=='mostPopular'
    assert client.videos.return_value.list.call_args.kwargs['maxResults']==50


@pytest.mark.parametrize('factory,name,minimum',[(GDELTSource,'gdelt.json',1),(WikipediaSource,'wikipedia.json',1)])
def test_public_adapter_saved_payload(factory,name,minimum):
    rows=factory(session(fixture(name)),CLOCK).collect('US')
    assert len(rows)>=minimum and rows[0].timestamp.tzinfo
    if factory is GDELTSource:assert rows[0].extra['reviewed'] is False


def test_hn_per_item_failure_and_optional_feeds(monkeypatch):
    transport=session({});transport.get.side_effect=[session([42,43]).get.return_value,session(fixture('hackernews.json')).get.return_value,requests.Timeout()]
    rows=HackerNewsSource(transport,CLOCK).collect('US');assert len(rows)==1
    rss=session({});rss.get.return_value.content=b'<rss><channel><item><title>Science discovery</title></item></channel></rss>'
    assert GoogleTrendsSource(rss,CLOCK).collect('US')[0].source=='google_trends'
    monkeypatch.setenv('AUTOMATION_REDDIT_ACCESS_TOKEN','fixture-token')
    assert RedditSource(session(fixture('reddit.json')),CLOCK).collect('US')
    monkeypatch.delenv('AUTOMATION_REDDIT_ACCESS_TOKEN')
    with pytest.raises(ValueError):RedditSource(session({}),CLOCK).collect('US')
    monkeypatch.delenv('AUTOMATION_GOOGLE_TRENDS_ENABLED',raising=False);monkeypatch.delenv('AUTOMATION_REDDIT_ENABLED',raising=False)
    assert set(sources(CLOCK))=={'youtube','gdelt','wikipedia','hackernews'}


def signal(title,source='youtube',external_id=None,**kwargs):
    return TrendSignal(source=source,external_id=external_id or title,title=title,timestamp=NOW,score=5,velocity=20,**kwargs)


def seed(storage):
    values=[signal(t) for t in ['Funny cat interrupts meeting','Comedy prank at dinner','Election senate policy','Drama mystery at midnight','Science rocket discovery','Comedian loses his lunch','New telescope launch']]
    source=Mock();source.collect.return_value=values
    assert TrendCollector(storage,{'youtube':source}).collect()['youtube']['count']==7
    return source


def test_selector_quotas_backups_read_only_and_collection_single_tick(tmp_path):
    store=AutomationStorage(tmp_path/'test.db',clock=CLOCK);source=seed(store)
    before=Path(store.db_path).read_bytes()
    preview=TopicSelector(store.db_path,CLOCK).preview(load_settings(ROOT/'config/lanes.yaml',environ={}).config.lanes['shorts_daily'])
    assert len(preview['topics'])==5 and len(preview['backups'])==2
    assert [t['category'] for t in preview['topics']]==['comedy','comedy','politics','drama','wildcard']
    assert all(t['reasons'] for t in preview['topics'])
    with store.connect() as conn:
        stored={r[0] for r in conn.execute('SELECT signal_id FROM trend_signals')}
    assert all(set(t['signal_ids'])<=stored for t in preview['topics'])
    assert before==Path(store.db_path).read_bytes()
    TrendCollector(store,{'youtube':source}).collect();source.collect.assert_called_once()


def test_source_failure_isolated_and_secret_not_saved(tmp_path):
    store=AutomationStorage(tmp_path/'test.db',clock=CLOCK)
    bad=Mock();bad.collect.side_effect=ValueError('secret-api-key')
    good=Mock();good.collect.return_value=[signal('Funny kitten')]
    result=TrendCollector(store,{'youtube':good,'broken':bad}).collect()
    assert result['youtube']['count']==1 and result['broken']['status']=='unavailable'
    assert 'secret-api-key' not in Path(store.db_path).read_bytes().decode(errors='ignore')


def test_cluster_cross_source_repetition_and_cooldown(tmp_path):
    store=AutomationStorage(tmp_path/'test.db',clock=CLOCK);seed(store)
    lane=load_settings(ROOT/'config/lanes.yaml',environ={}).config.lanes['shorts_daily']
    topics=cluster([signal('Senate election policy'),signal('Senate election policy','gdelt'),signal('Senate election policy')],NOW)
    assert len(topics)==1 and len(topics[0].sources)==2
    assert similar('The funny kitten prank!','Funny kitten prank')
    with store.connect() as conn:
        conn.execute("INSERT INTO topic_candidates(topic_id,normalized_topic,category,signal_ids_json,last_used_at,created_at,updated_at) VALUES ('used','funny cat interrupts meeting','comedy','[]',?,?,?)",(store.timestamp(),)*3)
    value=TopicSelector(store.db_path,CLOCK).preview(lane)
    assert value['cooldown_excluded']==1 and len(value['topics'])==5
    assert classify('Unknown subject',fallback=lambda _: 'drama')==('drama','local classifier')
    future=TopicSelector(store.db_path,FrozenClock(NOW+timedelta(days=15))).preview(lane)
    assert not future['topics'] and future['warnings']


def test_missing_database_preview_does_not_create_files(tmp_path):
    path=tmp_path/'missing.db';lane=load_settings(ROOT/'config/lanes.yaml',environ={}).config.lanes['shorts_daily']
    assert not TopicSelector(path,CLOCK).preview(lane)['topics'];assert not path.exists()


def test_api_preview_session_csrf_and_no_generation(generation_api,monkeypatch):
    api=generation_api
    assert api.client.post('/api/v1/automation/plan-preview',json={}).status_code==401
    headers=sign_in(api)
    assert api.client.post('/api/v1/automation/plan-preview',json={}).status_code==403
    monkeypatch.setenv('AUTOMATION_LANES_FILE',str(ROOT/'config/lanes.yaml'))
    response=api.client.post('/api/v1/automation/plan-preview',json={},headers=headers)
    assert response.status_code==200 and response.json()['side_effects'] is False
    api.execute.assert_not_called();api.publisher.assert_not_called()


def test_local_classifier_is_explicit_mocked_and_serialized(monkeypatch,tmp_path):
    from types import SimpleNamespace
    import app.automation.trends.classifier as classifier
    client=Mock();client.chat.return_value=SimpleNamespace(message=SimpleNamespace(content='{"category":"drama"}'))
    monkeypatch.setattr(classifier,'runtime_root',lambda:tmp_path)
    monkeypatch.setattr(classifier,'LLMService',lambda **kwargs:SimpleNamespace(client=client,model='local-model'))
    assert classifier.ollama_category('A fictional surprise')=='drama'
    assert client.chat.call_args.kwargs['keep_alive']==0


def test_scoring_quota_shortage_and_expired_cooldown(tmp_path):
    fast=signal('Rocket telescope launch',external_id='fast')
    slow=fast.model_copy(update={'title':'Ancient pottery exhibition','external_id':'slow','timestamp':NOW-timedelta(days=3),'velocity':0,'score':0})
    ranked=cluster([slow,fast],NOW)
    assert ranked[0].title==fast.title and ranked[0].score>ranked[1].score
    store=AutomationStorage(tmp_path/'test.db',clock=CLOCK)
    source=Mock();source.collect.return_value=[signal(title,external_id=str(i)) for i,title in enumerate(['Astronaut fixes telescope','Painter restores ancient mural','Robot learns garden planting','Ocean tide reveals hidden canyon','Mountain cyclist repairs wheel','Chef experiments with spices'])]
    TrendCollector(store,{'youtube':source}).collect()
    lane=load_settings(ROOT/'config/lanes.yaml',environ={}).config.lanes['shorts_daily']
    with store.connect() as conn:
        old=(NOW-timedelta(days=15)).isoformat()
        conn.execute("INSERT INTO topic_candidates(topic_id,normalized_topic,category,signal_ids_json,last_used_at,created_at,updated_at) VALUES ('old','science discovery 0','wildcard','[]',?,?,?)",(old,old,old))
    # There are enough topics but not enough categories; record quota substitution.
    preview=TopicSelector(store.db_path,CLOCK).preview(lane)
    assert preview['cooldown_excluded']==0 and not preview['warnings'] and len(preview['topics'])==5
    assert any('quota fallback' in ' '.join(t['reasons']) for t in preview['topics'])
