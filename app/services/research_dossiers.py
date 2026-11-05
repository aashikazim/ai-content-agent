"""Immutable, human-reviewed claim/source bundles; no automated truth label."""
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID,uuid4


def root():return Path(os.getenv('PRODUCTION_TOOLS_DIR','data/production_tools')).absolute()/'dossiers'


def create(topic,claims,confirmed):
    if confirmed is not True:raise ValueError('Confirm you personally inspected the source support for these claims')
    if not topic.strip() or not claims or len(claims)>30:raise ValueError('Provide a topic and1–30source-supported claims')
    from urllib.parse import urlsplit
    for claim in claims:
        if not claim.get('claim','').strip() or len(claim['claim'])>2000 or not 1<=len(claim.get('sources',[]))<=5:raise ValueError('Each claim needs text and1–5source URLs')
        for source in claim['sources']:
            try:
                url=urlsplit(source)
                if url.scheme!='https' or not url.hostname or url.username or url.password:raise ValueError
            except (ValueError,TypeError):raise ValueError('Use HTTPS primary-source URLs without credentials') from None
    value={'topic':topic,'claims':claims,'reviewed_by_human':True,'factual_status':'unverified'}
    payload=json.dumps(value,sort_keys=True,ensure_ascii=False)
    if len(payload.encode())>16000:raise ValueError('Keep the research dossier below16KB so screenplay requests remain within their authorized budget')
    identifier=str(uuid4())
    folder=root();folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    file=folder/(identifier+'.json');file.write_text(json.dumps({'data':value,'sha256':hashlib.sha256(payload.encode()).hexdigest()}));file.chmod(0o600)
    return {'dossier_id':identifier,**value}


def get(identifier):
    try:
        identifier=str(UUID(str(identifier)));value=json.loads((root()/(identifier+'.json')).read_text())
        if hashlib.sha256(json.dumps(value['data'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()!=value['sha256']:raise ValueError
        if value['data']['reviewed_by_human'] is not True:raise ValueError
        return {'dossier_id':identifier,**value['data']}
    except (OSError,ValueError,TypeError,KeyError):raise ValueError('Research dossier is unavailable or changed; review a new version') from None


def list_dossiers():
    result=[]
    for file in sorted(root().glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:100]:
        try:result.append(get(file.stem))
        except ValueError:continue
    return result
