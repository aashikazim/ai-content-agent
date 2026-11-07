"""Bounded, injectable official signal adapters. Provider bodies never enter logs."""
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
from urllib.parse import quote
import xml.etree.ElementTree as ET
from typing import Any
import requests
from app.automation.clock import Clock, SystemClock, utc_now
from app.automation.trends import TrendSignal


def get_json(session: Any, url: str, **kwargs: Any) -> Any:
    response = session.get(url, timeout=(8, 25), allow_redirects=False, **kwargs)
    response.raise_for_status()
    if response.status_code != 200: raise ValueError('Unexpected source response')
    return response.json()


def session_for_sources() -> requests.Session:
    session = requests.Session(); session.trust_env = False
    session.headers['User-Agent'] = 'AIContentAgent/1.0 (local topic research; https://github.com/aashikazim/ai-content-agent)'
    return session


class YouTubeSource:
    name = 'youtube'
    def __init__(self, youtube: Any = None, clock: Clock | None = None) -> None:
        self.youtube, self.clock = youtube, clock or SystemClock()

    def collect(self, region: str) -> list[TrendSignal]:
        # Reuse real discovery parsing and ranking without writing legacy videos.
        from app.services.youtube_service import YouTubeService
        from app.services.ranking_service import rank_videos
        now = utc_now(self.clock)
        videos = rank_videos((self.youtube or YouTubeService()).get_popular_videos(region=region, limit=50), now=now)
        return [TrendSignal(source=self.name, external_id=v.video_id, title=v.title[:500],
            category_hint={'23':'comedy', '25':'politics', '24':'drama'}.get(v.category_id),
            score=max(v.score, 0), velocity=v.views / max((now-v.published_at).total_seconds()/3600, 1),
            timestamp=v.published_at, url=v.url, extra={'category_id':v.category_id}) for v in videos]


class GDELTSource:
    name = 'gdelt'
    def __init__(self, session: Any = None, clock: Clock | None = None) -> None:
        self.session, self.clock = session, clock or SystemClock()
    def collect(self, region: str) -> list[TrendSignal]:
        from app.services.research_service import research
        result = research('(election OR government OR science OR entertainment)', self.session or session_for_sources())
        values = []
        for item in result['sources']:
            try: stamp = datetime.strptime(item['reported_date'], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
            except (ValueError, TypeError): stamp = utc_now(self.clock)
            values.append(TrendSignal(source=self.name, external_id=hashlib.sha256(item['url'].encode()).hexdigest(),
                title=item['title'], score=1, timestamp=stamp, url=item['url'],
                extra={'bundle_status':'auto_compiled','reviewed':False,'factual_status':'unverified'}))
        return values


class WikipediaSource:
    name = 'wikipedia'
    def __init__(self, session: Any = None, clock: Clock | None = None) -> None:
        self.session, self.clock = session or session_for_sources(), clock or SystemClock()
    def collect(self, region: str) -> list[TrendSignal]:
        # Two days back avoids incomplete UTC daily aggregates; global English signal.
        day = (utc_now(self.clock)-timedelta(days=2)).date()
        body = get_json(self.session, 'https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/'+day.strftime('%Y/%m/%d'))
        values=[]
        for item in body['items'][0]['articles'][:100]:
            title=item['article']
            if ':' in title or title in {'Main_Page','-'}: continue
            values.append(TrendSignal(source=self.name, external_id=title, title=title.replace('_',' '),
                score=item['views'], velocity=item['views']/24, timestamp=datetime.combine(day,datetime.min.time(),timezone.utc),
                url='https://en.wikipedia.org/wiki/'+quote(title, safe=''), extra={'scope':'global_en','requested_region':region}))
        return values


class HackerNewsSource:
    name = 'hackernews'
    def __init__(self, session: Any = None, clock: Clock | None = None) -> None:
        self.session, self.clock = session or session_for_sources(), clock or SystemClock()
    def collect(self, region: str) -> list[TrendSignal]:
        base='https://hacker-news.firebaseio.com/v0/'
        identifiers=get_json(self.session,base+'topstories.json')[:20]
        values=[]
        for identifier in identifiers:
            if type(identifier) is not int: continue
            try:
                item=get_json(self.session,base+f'item/{identifier}.json')
                if not item or item.get('deleted') or item.get('dead') or item.get('type')!='story': continue
                stamp=datetime.fromtimestamp(item['time'],timezone.utc)
                score=max(item.get('score',0),0)
                values.append(TrendSignal(source=self.name,external_id=str(identifier),title=item['title'][:500],
                    score=score,velocity=score/max((utc_now(self.clock)-stamp).total_seconds()/3600,1),
                    timestamp=stamp,url=f'https://news.ycombinator.com/item?id={identifier}',extra={'scope':'global'}))
            except (requests.RequestException,ValueError,KeyError,TypeError): continue
        return values


class GoogleTrendsSource:
    name='google_trends'
    def __init__(self, session: Any = None, clock: Clock | None = None) -> None:
        self.session,self.clock=session or session_for_sources(),clock or SystemClock()
    def collect(self, region: str) -> list[TrendSignal]:
        response=self.session.get('https://trends.google.com/trending/rss',params={'geo':region},timeout=(8,25),allow_redirects=False)
        response.raise_for_status()
        if len(response.content)>2_000_000: raise ValueError('Trend feed exceeds limit')
        root=ET.fromstring(response.content)
        return [TrendSignal(source=self.name,external_id=item.findtext('title',''),title=item.findtext('title',''),
            score=1,timestamp=utc_now(self.clock),url='https://trends.google.com/trending?geo='+region)
            for item in root.findall('./channel/item')[:30] if item.findtext('title')]


class RedditSource:
    name='reddit'
    def __init__(self, session: Any = None, clock: Clock | None = None) -> None:
        self.session,self.clock=session or session_for_sources(),clock or SystemClock()
    def collect(self, region: str) -> list[TrendSignal]:
        token=os.getenv('AUTOMATION_REDDIT_ACCESS_TOKEN')
        if not token: raise ValueError('Reddit OAuth access is not configured')
        body=get_json(self.session,'https://oauth.reddit.com/r/popular/hot',params={'limit':25},headers={'Authorization':'Bearer '+token})
        values=[]
        for row in body['data']['children']:
            item=row['data']; identifier=item['id']
            if not re.fullmatch('[a-z0-9]+',identifier): continue
            values.append(TrendSignal(source=self.name,external_id=identifier,title=item['title'][:500],
                score=max(item.get('score',0),0),timestamp=datetime.fromtimestamp(item['created_utc'],timezone.utc),
                url='https://www.reddit.com/comments/'+identifier))
        return values


def sources(clock: Clock | None = None) -> dict[str, Any]:
    values={item.name:item for item in (YouTubeSource(clock=clock),GDELTSource(clock=clock),WikipediaSource(clock=clock),HackerNewsSource(clock=clock))}
    if os.getenv('AUTOMATION_GOOGLE_TRENDS_ENABLED','false').lower()=='true': values['google_trends']=GoogleTrendsSource(clock=clock)
    if os.getenv('AUTOMATION_REDDIT_ENABLED','false').lower()=='true': values['reddit']=RedditSource(clock=clock)
    return values
