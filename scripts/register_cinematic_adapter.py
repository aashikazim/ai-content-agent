"""Register an owner-tested local character LoRA; never trains or downloads models."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file',required=True)
    parser.add_argument('--name',required=True)
    parser.add_argument('--license-note',required=True)
    parser.add_argument('--trigger',required=True)
    parser.add_argument('--scale',type=float,default=.8)
    parser.add_argument('--confirmed-compatible',action='store_true')
    args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1]/'.env')
    from app.services.cinematic_adapter_service import CinematicAdapterService
    result=CinematicAdapterService().register(args.file,name=args.name,license_note=args.license_note,
        trigger=args.trigger,scale=args.scale,confirmed=args.confirmed_compatible)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
