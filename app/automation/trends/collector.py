"""One explicit collection tick, idempotent within the configured interval."""
import re
import os
from typing import Any, Mapping
from datetime import timezone
from app.automation.clock import utc_now
from app.automation.events import EventLog
from app.automation.idempotency import canonical_json,stable_id
from app.automation.storage import AutomationStorage
from app.automation.trends import TrendSource,TrendSignal


class TrendCollector:
    def __init__(self, storage: AutomationStorage, sources: Mapping[str,TrendSource], interval_hours: int=3) -> None:
        if not 1<=interval_hours<=24: raise ValueError('Interval must be 1–24 hours')
        self.storage,self.sources,self.interval=storage,dict(sources),interval_hours

    def collect(self,region: str='US') -> dict[str,Any]:
        if not re.fullmatch('[A-Z]{2}',region): raise ValueError('Use a two-letter region')
        now=utc_now(self.storage.clock)
        bucket=int(now.timestamp())//(self.interval*3600)
        report={}
        for name,source in self.sources.items():
            claim,fresh=self.storage.claim('trend_collection',f'{name}:{region}:{bucket}',{'source':name,'region':region,'bucket':bucket})
            if not fresh:
                report[name]={'status':'already_collected','count':0};continue
            try:
                signals=[TrendSignal.model_validate(s) for s in source.collect(region)]
                if os.getenv('AUTOMATION_CLASSIFY_WITH_OLLAMA','false').lower()=='true':
                    from app.automation.selector import classify
                    from app.automation.trends.classifier import ollama_category
                    for signal in signals:
                        signal.category_hint=classify(signal.title,signal.category_hint,ollama_category)[0]
                stamp=self.storage.timestamp()
                with self.storage.connect() as conn:
                    for signal in signals[:200]:
                        if signal.source!=name: raise ValueError('Source identity mismatch')
                        conn.execute('''INSERT INTO trend_signals
                            (signal_id,source,external_id,title,category_hint,score,velocity,region,url,collected_at,raw_json,created_at,updated_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,external_id,collected_at) DO NOTHING''',
                            (stable_id('signal',name,signal.external_id,region,bucket),name,signal.external_id,signal.title,
                             signal.category_hint,signal.score,signal.velocity,region,signal.url,stamp,
                             signal.model_dump_json(),stamp,stamp))
                result={'status':'collected','count':min(len(signals),200)}
            except Exception as exc:
                # No provider exception strings: API keys can be in transport URLs.
                result={'status':'unavailable','count':0,'error_type':type(exc).__name__}
            self.storage.complete_claim(claim['claim_token'],result)
            EventLog(self.storage).append('director','trends.collected','trend_source',name,result)
            report[name]=result
        return report
