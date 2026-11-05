"""Source-linked research dossiers and measurable script checks; never certify truth."""
import re
from urllib.parse import urlsplit
import requests


def script_quality(text,duration_seconds=None):
    if not text.strip() or len(text)>50000:raise ValueError('Provide a script of1–50000characters')
    tokens=re.findall(r"[\w']+",text)
    sentences=[s.strip() for s in re.split(r'[.!?]+',text) if s.strip()]
    normalized=[s.casefold() for s in sentences]
    repetition=(len(normalized)-len(set(normalized)))/max(1,len(normalized))
    wpm=len(tokens)*60/duration_seconds if duration_seconds else None
    warnings=[]
    if wpm and not 90<=wpm<=180:warnings.append('Narration pace is outside90–180words per minute; inspect timing')
    if repetition>.15:warnings.append('Repeated sentences may make the script repetitive')
    if any(len(s.split())>35 for s in sentences):warnings.append('Long sentences may be difficult to narrate')
    if re.search(r'\b(always|never|guaranteed|proven|cures?)\b',text,re.I):warnings.append('Absolute or medical claims need supporting sources and human review')
    return {'word_count':len(tokens),'sentence_count':len(sentences),'estimated_speech_seconds':len(tokens)/160*60,
        'words_per_minute':wpm,'repetition_ratio':repetition,'warnings':warnings,'factual_status':'unverified','human_review_required':True}


def research(query,session=None):
    if not query.strip() or len(query)>200:raise ValueError('Enter a research topic up to200characters')
    session=session or requests.Session();session.trust_env=False
    # Search a fixed public index; arbitrary article URLs are never fetched server-side.
    try:
        response=session.get('https://api.gdeltproject.org/api/v2/doc/doc',params={'query':query,'mode':'artlist','format':'json','maxrecords':12,'sort':'datedesc'},timeout=(10,45),allow_redirects=False)
        if response.status_code!=200:raise ValueError('Research index is unavailable; retry manually later')
        body=response.json()
    except (requests.RequestException,ValueError):raise ValueError('Could not retrieve the research index. No facts were verified; try manually later') from None
    sources=[]
    for article in body.get('articles',[]):
        try:
            url=urlsplit(article['url'])
            if url.scheme not in ('http','https') or url.username or url.password or not url.hostname:continue
            sources.append({'title':str(article.get('title',''))[:500],'url':article['url'],'publisher':url.hostname,'reported_date':article.get('seendate'),'checked':False})
        except (ValueError,KeyError,TypeError):continue
    return {'query':query,'sources':sources,'factual_status':'unverified','human_review_required':True,
        'notice':'Discovery links are leads, not verified evidence. Read primary sources, record claim-specific support and review rights before writing news.'}
