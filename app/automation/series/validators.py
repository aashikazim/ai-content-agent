"""Deterministic gates, not a claim of semantic or artistic certification."""
from difflib import SequenceMatcher
import re
from app.automation.series.models import Bible,Episode,phase


def normalized(value:str)->str:
    return ' '.join(re.findall(r'\w+',value.casefold()))


def validate_outline(bible:Bible,episodes:list[Episode],*,complete:bool=True,minimum:int=300,maximum:int=600)->list[str]:
    errors=[]
    if (complete and len(episodes)!=30) or not 1<=len(episodes)<=30:errors.append('Outline must cover 30 episodes in order')
    actors={a.character_id:a for a in bible.characters};locations={l.location_id for l in bible.locations};threads={t.thread_id:t for t in bible.threads}
    planted=set();resolved=set();hooks=[]
    for n,ep in enumerate(episodes,1):
        prefix=f'Day {n}: '
        if ep.ep_no!=n:errors.append(prefix+'episode number/order mismatch')
        if ep.phase!=phase(n):errors.append(prefix+'incorrect structural phase')
        if not minimum<=ep.duration_seconds<=maximum:errors.append(prefix+'runtime outside configured limits')
        expected=episodes[n-2].hook if n>1 else ''
        if ep.previous_hook!=expected:errors.append(prefix+'previous hook reference changed or missing')
        if n>1 and normalized(expected) not in normalized(ep.opening_ack):errors.append(prefix+'opening must include the previous hook as an explicit recap')
        if n>1 and normalized(ep.opening_ack) in {'previously on the show','the story continues','they recall what happened'}:errors.append(prefix+'opening acknowledgment is generic')
        if n<30:
            norm=normalized(ep.hook)
            if not ep.unresolved:errors.append(prefix+'hook must remain unresolved')
            if len(norm.split())<6 or any(x in norm for x in ['what happens next','to be continued','stay tuned','find out tomorrow']):errors.append(prefix+'hook is generic; name a concrete unresolved question or danger')
            if any(SequenceMatcher(None,norm,old).ratio()>.86 for old in hooks):errors.append(prefix+'hook duplicates an earlier ending')
            hooks.append(norm)
        elif ep.unresolved:errors.append(prefix+'finale must resolve rather than introduce an unresolved hook')
        if len(set(ep.characters))!=len(ep.characters) or len(set(ep.locations))!=len(ep.locations):errors.append(prefix+'duplicate character/location references')
        for identifier in ep.characters:
            actor=actors.get(identifier)
            if not actor or not actor.introduced<=n<=actor.removed_after:errors.append(prefix+'character absent from bible or outside introduction/removal window: '+identifier)
        if set(ep.locations)-locations:errors.append(prefix+'unknown location')
        if set(ep.advances+ep.resolves)-set(threads):errors.append(prefix+'unknown thread')
        if len(set(ep.advances))!=len(ep.advances) or len(set(ep.resolves))!=len(ep.resolves):errors.append(prefix+'duplicate thread references')
        for identifier in ep.advances:
            thread=threads.get(identifier)
            if thread and (n<thread.plant_day or identifier in resolved):errors.append(prefix+'thread advanced before planting or after resolution: '+identifier)
            planted.add(identifier)
        for thread in bible.threads:
            if n==thread.plant_day and thread.thread_id not in ep.advances:errors.append(prefix+'missing thread planting: '+thread.thread_id)
            if n==thread.payoff_day and thread.thread_id not in ep.resolves:errors.append(prefix+'missing planned thread payoff: '+thread.thread_id)
        for identifier in ep.resolves:
            thread=threads.get(identifier)
            if thread and (identifier not in planted or identifier in resolved or n!=thread.payoff_day):errors.append(prefix+'thread payoff is unplanted, repeated or on the wrong day: '+identifier)
            resolved.add(identifier)
    if complete and resolved!=set(threads):errors.append('Finale leaves dangling bible threads')
    return errors
