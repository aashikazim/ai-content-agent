#!/usr/bin/env python3
"""Install CPU analysis dependencies into a separate venv, leaving GPU env unchanged.

By default reuse the existing local runtime's read-only packages (notably PyTorch).
No model is downloaded unless --whisper-model is explicitly supplied.
"""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',default='data/clip_studio_runtime')
    parser.add_argument('--gpu-python',default='data/local_cinematic/venv/bin/python')
    parser.add_argument('--whisper-model',choices=['tiny','base','small','medium'])
    args=parser.parse_args();root=Path(args.root).absolute();python=root/'venv/bin/python'
    if not python.exists():subprocess.run([sys.executable,'-m','venv',str(root/'venv')],check=True)
    gpu=Path(args.gpu_python).absolute()
    if gpu.is_file():
        sites=subprocess.run([str(gpu),'-c','import site,json;print(json.dumps(site.getsitepackages()))'],capture_output=True,text=True,check=True)
        import json
        target=subprocess.run([str(python),'-c','import site;print(site.getsitepackages()[0])'],capture_output=True,text=True,check=True).stdout.strip()
        (Path(target)/'local-torch-readonly.pth').write_text('\n'.join(json.loads(sites.stdout))+'\n')
    subprocess.run([str(python),'-m','pip','install','yt-dlp[default]','scenedetect>=0.6.7,<0.8','opencv-python-headless','openai-whisper'],check=True)
    if args.whisper_model:
        subprocess.run([str(python),'-c',"import whisper,sys;whisper.load_model(sys.argv[1],device='cpu',download_root=sys.argv[2])",args.whisper_model,str(root/'whisper')],check=True)
    print('Analysis Python:',python)


if __name__=='__main__':main()
