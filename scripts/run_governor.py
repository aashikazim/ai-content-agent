"""Inspect Governor state, or explicitly execute one configured tick. No daemon."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).absolute().parents[1]))


def main():
    from dotenv import load_dotenv
    load_dotenv();os.umask(0o077)
    from app.automation.governor import Governor
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=Path(os.getenv('DATABASE_PATH','data/trends.db')))
    parser.add_argument('--tick',action='store_true',help='Use configured gates/mode once; does not enable disabled automation')
    args=parser.parse_args();service=Governor(args.database)
    print(json.dumps(service.tick() if args.tick else service.status(),indent=2))


if __name__=='__main__':main()
