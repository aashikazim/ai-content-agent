"""Install isolated CPU Piper and an explicitly requested public-domain voice."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='data/speech_runtime')
    parser.add_argument('--download-voice',action='store_true');args=parser.parse_args()
    root=Path(args.root).absolute();python=root/'venv/bin/python'
    if not python.is_file():subprocess.run([sys.executable,'-m','venv',str(root/'venv')],check=True)
    subprocess.run([str(python),'-m','pip','install','piper-tts==1.8.0'],check=True)
    if not args.download_voice:return
    import requests
    identifier='piper-en-us-ljspeech-high';folder=root/'voices'/identifier
    if (root/'profiles'/(identifier+'.json')).exists():
        print('Existing immutable voice profile retained:',identifier);return
    revision=requests.get('https://huggingface.co/api/models/rhasspy/piper-voices',timeout=30).json()['sha']
    base=f'https://huggingface.co/rhasspy/piper-voices/resolve/{revision}/en/en_US/ljspeech/high/'
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    files={}
    for name in ('en_US-ljspeech-high.onnx','en_US-ljspeech-high.onnx.json','MODEL_CARD'):
        path=folder/name;temp=path.with_suffix(path.suffix+'.part');size=0
        with requests.get(base+name,stream=True,timeout=(10,120)) as response:
            response.raise_for_status()
            with temp.open('wb') as file:
                for block in response.iter_content(1024**2):
                    size+=len(block)
                    if size>150*1024**2:raise ValueError('Voice asset exceeds150MiB')
                    file.write(block)
        temp.chmod(0o600);temp.replace(path)
        files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    profile={'id':identifier,'label':'Piper · LJ Speech English (female)','provider':'piper',
        'model':'en_US-ljspeech-high.onnx','config':'en_US-ljspeech-high.onnx.json','files':files,
        'model_revision':revision,'sample_rate':22050,'voice_license':'LJ Speech dataset: public domain',
        'license_url':'https://keithito.com/LJ-Speech-Dataset/',
        'source_url':'https://huggingface.co/rhasspy/piper-voices/tree/'+revision+'/en/en_US/ljspeech/high',
        'engine_license':'GPL-3.0 (separate Piper runtime)'}
    directory=root/'profiles';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/(identifier+'.json');path.write_text(json.dumps(profile,indent=2));path.chmod(0o600)
    print('Registered voice:',identifier)


if __name__=='__main__':main()
