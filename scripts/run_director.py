"""Explicit manual automation actions. No scheduler or publisher is started."""
import argparse
from datetime import date
import json
import logging
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).absolute().parents[1]))
from dotenv import load_dotenv
from app.automation.config import load_settings,ShortsLaneConfig
from app.automation.selector import TopicSelector
from app.automation.storage import AutomationStorage
from app.automation.trends.collector import TrendCollector
from app.automation.trends.sources import sources
from app.services.structured_logging import configure

logger=logging.getLogger(__name__)


def main() -> None:
    load_dotenv();os.umask(0o077);configure()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--database',type=Path,default=Path(os.getenv('DATABASE_PATH','data/trends.db')))
    parser.add_argument('--plan-date',type=date.fromisoformat)
    parser.add_argument('--region',default='US')
    parser.add_argument('--lane',default='shorts_daily')
    action=parser.add_mutually_exclusive_group()
    action.add_argument('--collect-trends',action='store_true')
    action.add_argument('--preview',action='store_true')
    parser.add_argument('--sources',nargs='+',default=None,help='Explicit collection source names')
    parser.add_argument('--output',type=Path,help='Private JSON result file')
    args=parser.parse_args();settings=load_settings(args.config)
    if args.collect_trends:
        available=sources()
        names=args.sources or list(available)
        if any(n not in available for n in names):parser.error('Source is unknown or disabled')
        result=TrendCollector(AutomationStorage(args.database),{n:available[n] for n in names},settings.trend_interval_hours).collect(args.region)
    else:
        lane=settings.config.lanes.get(args.lane)
        if not isinstance(lane,ShortsLaneConfig):parser.error('Choose a topic Shorts lane')
        result=TopicSelector(args.database).preview(lane,args.region)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x') as f:json.dump(result,f,indent=2)
    logger.info('%s',json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    main()
