"""Licensed Pexels acquisition with bounded trusted-CDN transfers and provenance."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import requests

LICENSE_URL='https://www.pexels.com/license/'


def trusted_url(value,*,page=False):
    try:
        url=urlsplit(value)
        hosts={'www.pexels.com','pexels.com'} if page else {'images.pexels.com','videos.pexels.com'}
        return value if url.scheme=='https' and url.hostname in hosts and not url.username and not url.password and url.port in (None,443) else None
    except (ValueError,TypeError):return None


class StockService:
    def __init__(self,session=None):
        self.session=session or requests.Session();self.session.trust_env=False
        self.session.mount('https://',requests.adapters.HTTPAdapter(max_retries=0))

    def search(self,query,kind='video',orientation='portrait',count=8):
        if kind not in ('video','image') or orientation not in ('portrait','landscape','square') or not 1<=count<=20 or not query.strip() or len(query)>200:raise ValueError('Choose a valid stock query, media type and orientation')
        key=os.getenv('PEXELS_API_KEY')
        if not key:raise ValueError('PEXELS_API_KEY is not configured; add it locally and restart')
        endpoint='https://api.pexels.com/v1/videos/search' if kind=='video' else 'https://api.pexels.com/v1/search'
        try:
            response=self.session.get(endpoint,headers={'Authorization':key},params={'query':query,'orientation':orientation,'per_page':count},timeout=(10,30),allow_redirects=False)
            if response.status_code!=200:raise ValueError('Pexels search failed; inspect API access or rate limits. No automatic retry')
            data=response.json()
        except requests.RequestException:raise ValueError('Could not reach Pexels; no automatic retry') from None
        result=[]
        for item in data.get('videos' if kind=='video' else 'photos',[]):
            if not isinstance(item,dict) or 'id' not in item:continue
            page=trusted_url(item.get('url'),page=True)
            if not page:continue
            if kind=='video':
                files=[f for f in item.get('video_files',[]) if f.get('file_type')=='video/mp4' and trusted_url(f.get('link')) and type(f.get('width')) is int and type(f.get('height')) is int and 128<=f['width']<=1920 and 128<=f['height']<=1920]
                if not files:continue
                source=min(files,key=lambda f:abs(f['height']-720))
                author=(item.get('user') or {}).get('name','Pexels creator');url=source['link']
            else:
                url=trusted_url(item.get('src',{}).get('large2x'));author=item.get('photographer','Pexels creator')
                if not url:continue
            result.append({'id':str(item['id']),'provider':'pexels','kind':kind,'page_url':page,'download_url':url,
                'width':item.get('width'),'height':item.get('height'),'duration_seconds':item.get('duration'),
                'author':author,'attribution':f'{"Video" if kind=="video" else "Photo"} by {author} on Pexels ({page})',
                'license_url':LICENSE_URL,'license':'Pexels License; human review of depicted people, brands and intended use required'})
        return result

    def download(self,candidate,directory,*,confirmed=False):
        if not confirmed:raise ValueError('Confirm the Pexels license and intended-use rights before downloading')
        url=trusted_url(candidate.get('download_url'))
        if not url:raise ValueError('Stock asset must come from the trusted Pexels CDN')
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        suffix='.mp4' if candidate['kind']=='video' else '.jpg';path=directory/(str(uuid4())+suffix);size=0
        try:
            with self.session.get(url,stream=True,timeout=(10,120),allow_redirects=False) as response:
                if response.status_code!=200:raise ValueError('Pexels asset download failed; redirects are not followed')
                with path.open('xb') as file:
                    for block in response.iter_content(1024**2):
                        size+=len(block)
                        if size>256*1024**2:raise ValueError('Stock asset exceeds256MiB')
                        file.write(block)
            if not size:raise ValueError('Downloaded stock asset is empty')
            if candidate['kind']=='video':
                from app.services.clip_studio.media import probe_source
                measured=probe_source(path)
            else:
                from PIL import Image
                with Image.open(path) as image:
                    if image.width*image.height>32000000 or min(image.size)<128 or max(image.size)>8192:raise ValueError('Stock image exceeds32megapixels')
                    measured={'width':image.width,'height':image.height}
                    image.verify()
            path.chmod(0o600)
            with path.open('rb') as file:digest=hashlib.file_digest(file,'sha256').hexdigest()
            return path,{**{k:v for k,v in candidate.items() if k!='download_url'},'sha256':digest,'measured_media':measured,'rights_confirmed':True}
        except Exception:
            path.unlink(missing_ok=True);raise
