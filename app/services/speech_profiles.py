"""Immutable neural voice manifests; imports never load inference dependencies."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def root():return Path(os.getenv('SPEECH_RUNTIME_ROOT','data/speech_runtime')).absolute()


def verified(identifier):
    if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',identifier):raise ValueError('Invalid speech profile ID')
    try:
        value=json.loads((root()/'profiles'/(identifier+'.json')).read_text())
        folder=(root()/'voices'/identifier).resolve()
        if value['id']!=identifier or value['provider']!='piper' or not value.get('voice_license'):raise ValueError
        for name,digest in value['files'].items():
            path=(folder/name).resolve()
            if not path.is_relative_to(folder) or not path.is_file():raise ValueError
            with path.open('rb') as file:
                if hashlib.file_digest(file,'sha256').hexdigest()!=digest:raise ValueError
        if value['model'] not in value['files'] or value['config'] not in value['files']:raise ValueError
        return value
    except (OSError,ValueError,KeyError,TypeError):raise ValueError('The approved speech profile is missing, changed or incomplete; run setup_speech.py') from None


def catalog():
    profiles=[]
    for path in (root()/'profiles').glob('*.json'):
        try:
            value=verified(path.stem)
            profiles.append({k:value[k] for k in ('id','label','provider','voice_license','license_url')})
        except ValueError:continue
    return profiles


def synthesize(identifier,text,output):
    profile=verified(identifier);folder=root()/'voices'/identifier
    python=root()/'venv/bin/python'
    if not python.is_file():raise ValueError('Isolated Piper runtime is missing; run scripts/setup_speech.py')
    output=Path(output).absolute();output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    environment={k:os.environ[k] for k in ('PATH','HOME','LANG','TMPDIR') if k in os.environ}
    environment.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='2')
    worker=Path(__file__).absolute().parents[1]/'workers/speech_worker.py'
    try:
        subprocess.run([str(python),str(worker),str(folder/profile['model']),str(output)],input=text,
            text=True,check=True,capture_output=True,env=environment,timeout=300)
    except (subprocess.SubprocessError,OSError):raise ValueError('Neural speech synthesis failed; no eSpeak fallback was selected') from None
    if not output.is_file() or output.stat().st_size<=44:raise ValueError('Neural voice produced empty audio')
    output.chmod(0o600);return output
