"""Pure topic clustering/selection. Preview uses SQLite read-only and no network."""
from dataclasses import dataclass,asdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any,Callable
import json
import math
import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from app.automation.clock import Clock,SystemClock,utc_now
from app.automation.config import ShortsLaneConfig
from app.automation.idempotency import stable_id
from app.automation.trends import TrendSignal

STOP={'the','a','an','and','to','of','in','on','for','with','is','it','at','by','new','official','video','trailer','hd'}
RULES={
 'politics':{'election','president','government','parliament','senate','minister','vote','politics','congress','trump','harris','policy'},
 'comedy':{'comedy','funny','laugh','prank','comedian','satire','joke','sketch','fails'},
 'drama':{'drama','story','film','movie','trailer','episode','mystery','betrayal','rescue','love'},
}


def normalize(title: str) -> str:
    return ' '.join(re.findall(r'[^\W_]+',unicodedata.normalize('NFKC',title).casefold()))


def similar(one: str,two: str) -> bool:
    a,b=set(normalize(one).split())-STOP,set(normalize(two).split())-STOP
    if not a or not b: return normalize(one)==normalize(two)
    return len(a&b)/len(a|b)>=.55 or SequenceMatcher(None,' '.join(sorted(a)),' '.join(sorted(b))).ratio()>=.87


def classify(title: str,hint: str | None=None,fallback: Callable[[str],str] | None=None) -> tuple[str,str]:
    words=set(normalize(title).split())
    for category,keywords in RULES.items():
        if words&keywords:return category,'keyword: '+', '.join(sorted(words&keywords))
    if hint in RULES:return hint,'source category hint'
    if fallback:
        try:
            value=fallback(title)
            if value in {*RULES,'wildcard'}:return value,'local classifier'
        except Exception:pass
    return 'wildcard','no category rule matched'


@dataclass(frozen=True)
class Topic:
    topic_id: str
    title: str
    normalized_topic: str
    category: str
    score: float
    signal_ids: tuple[str,...]
    sources: tuple[str,...]
    urls: tuple[str,...]
    reasons: tuple[str,...]


def cluster(signals: list[TrendSignal],now: datetime) -> list[Topic]:
    # One observation per source/external ID: repeated collection is not consensus.
    unique={(s.source,s.external_id):s for s in sorted(signals,key=lambda x:x.timestamp)}
    groups: list[list[TrendSignal]]=[]
    for signal in sorted(unique.values(),key=lambda x:(normalize(x.title),x.source,x.external_id)):
        group=next((g for g in groups if similar(g[0].title,signal.title)),None)
        if group is None: groups.append([signal])
        else:group.append(signal)
    topics=[]
    for group in groups:
        representative=max(group,key=lambda s:(s.score,s.title))
        title=representative.title; normalized=normalize(title)
        category,classification=classify(title,representative.category_hint)
        names=tuple(sorted({s.source for s in group}))
        age=max(0,(now-max(s.timestamp for s in group)).total_seconds()/3600)
        # Saturated log metrics prevent raw Wikipedia views dominating HN/YouTube.
        velocity=min(1,math.log1p(max(s.velocity for s in group))/math.log(100001))
        popularity=min(1,math.log1p(max(s.score for s in group))/math.log(100001))
        recency=math.exp(-age/72)
        score=3*(len(names)-1)+2*velocity+recency+.5*popularity
        topics.append(Topic(stable_id('topic',normalized),title,normalized,category,round(score,5),
            tuple(sorted(s.extra.get('_storage_id',stable_id('source_item',s.source,s.external_id)) for s in group)),names,
            tuple(sorted({s.url for s in group if s.url})),
            (classification,f'{len(names)} independent source(s)',f'age {age:.1f}h; velocity {velocity:.3f}; recency {recency:.3f}')))
    return sorted(topics,key=lambda t:(-t.score,t.normalized_topic))


class TopicSelector:
    def __init__(self,db_path: str | Path,clock: Clock | None=None) -> None:
        self.db_path=Path(db_path).absolute();self.clock=clock or SystemClock()

    def inputs(self,region: str,cooldown_days: int) -> tuple[list[TrendSignal],list[str]]:
        if not self.db_path.exists():return [],[]
        now=utc_now(self.clock)
        with sqlite3.connect(self.db_path.as_uri()+'?mode=ro',uri=True) as conn:
            names={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            signals=[];used=[]
            if 'trend_signals' in names:
                for row in conn.execute('SELECT signal_id,raw_json FROM trend_signals WHERE region=? AND collected_at>=? ORDER BY collected_at LIMIT 10000',
                        (region,(now-timedelta(days=7)).isoformat())):
                    try:
                        signal=TrendSignal.model_validate_json(row[1])
                        signal.extra['_storage_id']=row[0]
                        signals.append(signal)
                    except ValueError:continue
            if 'topic_candidates' in names:
                used=[r[0] for r in conn.execute('SELECT normalized_topic FROM topic_candidates WHERE last_used_at>=?',
                    ((now-timedelta(days=cooldown_days)).isoformat(),))]
            # Legacy-produced topics also count, not just new automation history.
            if 'jobs' in names and 'content_plans' in names:
                cols={r[1] for r in conn.execute('PRAGMA table_info(content_plans)')}
                if {'plan_id','title'}<=cols:
                    used.extend(r[0] for r in conn.execute('''SELECT p.title FROM content_plans p JOIN jobs j ON j.plan_id=p.plan_id
                        WHERE j.created_at>=? AND j.status NOT IN ('failed','rejected')''',((now-timedelta(days=cooldown_days)).isoformat(),)))
        return signals,used

    def preview(self,config: ShortsLaneConfig,region: str='US') -> dict[str,Any]:
        signals,used=self.inputs(region,config.cooldown_days)
        all_topics=cluster(signals,utc_now(self.clock))
        eligible=[t for t in all_topics if not any(similar(t.normalized_topic,u) for u in used)]
        picked=[];remaining=list(eligible)
        shortages=[]
        for category,count in config.quotas.items():
            candidates=[t for t in remaining if t.category==category]
            for topic in candidates[:count]:picked.append((topic,category));remaining.remove(topic)
            shortages.extend([category]*max(0,count-len(candidates)))
        for wanted in shortages:
            if remaining:picked.append((remaining.pop(0),wanted))
        def item(topic: Topic,wanted: str | None=None) -> dict[str,Any]:
            value=asdict(topic);value['reasons']=list(topic.reasons)
            if wanted:
                value['quota_category']=wanted
                value['reasons'].append('quota '+wanted if wanted==topic.category else f'quota fallback: {wanted} unavailable; selected {topic.category}')
            return value
        return {'topics':[item(t,w) for t,w in picked], 'backups':[item(t) for t in remaining[:max(5,config.daily_count*2)]],
            'available':len(eligible),'cooldown_excluded':len(all_topics)-len(eligible),
            'warnings':[] if len(picked)==config.daily_count else ['Insufficient fresh topics; collect more signals. No topics fabricated.'],
            'side_effects':False}
