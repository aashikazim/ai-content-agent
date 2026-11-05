import React, {useEffect,useState} from 'react';

export default function ChapterRepair({request,parent,part,onRefresh}) {
  const [instructions,setInstructions]=useState('Correct this chapter’s validation errors while preserving the approved cast, story state and exact duration.');
  const [quote,setQuote]=useState(null),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [notes,setNotes]=useState(''),[inspected,setInspected]=useState(false),[uncertain,setUncertain]=useState(false);
  const existing=(parent.part_revisions || []).filter(item=>item.part_number===part.part_number).at(-1);
  const [revision,setRevision]=useState(existing?.attempt_id || null);
  useEffect(()=>{setQuote(null);setConfirmed(false);},[instructions,part.status]);
  useEffect(()=>{
    if(!revision)return;
    let active=true;
    const poll=setInterval(async()=>{try{const value=await request(`/generation/scripts/${revision}`);if(active && !['queued','running'].includes(value.status)){setRevision(null);await onRefresh();if(value.status!=='completed')setError('This revision did not complete. Inspect script history and provider usage before another request.');}}catch(err){if(active)setError(err.message);}},2000);
    return()=>{active=false;clearInterval(poll);};
  },[revision,request]);
  async function act(action){if(busy)return;setBusy(true);setError('');try{await action();}catch(err){setError(err.message);}finally{setBusy(false);}}
  return <details><summary>Resolve chapter {part.part_number} · {part.status.replaceAll('_',' ')}</summary>
    {part.error && <p>{part.error}</p>}
    {existing?.status==='completed' && <button type="button" className="button subtle" disabled={busy} onClick={()=>act(async()=>{await request(`/generation/scripts/${parent.attempt_id}/parts/revisions/${existing.attempt_id}/apply`,{method:'POST',body:JSON.stringify({confirmed:true})});await onRefresh();})}>Use saved chapter revision without another paid request</button>}
    {['running','needs_reconciliation'].includes(part.status)?<><p>The previous outcome is uncertain. Inspect provider usage before closing this attempt. Closing does not cancel, refund or resend it.</p><label>Chapter inspection notes<input maxLength={500} value={notes} onChange={e=>setNotes(e.target.value)}/></label><label className="checkbox-label"><input type="checkbox" checked={inspected} onChange={e=>setInspected(e.target.checked)}/><span>I inspected this chapter’s provider outcome and accept any prior charges.</span></label><button type="button" className="button subtle" disabled={!inspected || !notes.trim() || busy} onClick={()=>act(async()=>{await request(`/generation/scripts/${parent.attempt_id}/parts/${part.part_number}/reconcile`,{method:'POST',body:JSON.stringify({notes,confirmed:true})});await onRefresh();})}>Close uncertain chapter without retry</button></>
      : <><p>Completed chapters remain saved. A revision of this known failed chapter is a new request with its own quote and confirmation. Local Ollama has no provider fee.</p><label>Chapter revision instructions<textarea maxLength={2000} value={instructions} onChange={e=>setInstructions(e.target.value)}/></label><button type="button" className="button subtle" disabled={busy || !!revision || uncertain} onClick={()=>act(async()=>{setQuote(await request(`/generation/scripts/${parent.attempt_id}/parts/${part.part_number}/quote`,{method:'POST',body:JSON.stringify({revision_instructions:instructions})}));setConfirmed(false);})}>Estimate this chapter revision</button>
      {quote && <><p>Maximum estimated new request: ${quote.maximum_cost_usd}. Prior reserved film script estimate: ${quote.prior_reserved_usd}. {quote.notice}</p><label className="checkbox-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/><span>I authorize this one new chapter revision at the displayed estimate.</span></label><button type="button" className="button primary" disabled={!confirmed || busy || uncertain || !!revision} onClick={()=>act(async()=>{setUncertain(true);const value=await request(`/generation/scripts/${parent.attempt_id}/parts/revisions`,{method:'POST',body:JSON.stringify({quote_id:quote.quote_id,confirmed:true})});setRevision(value.attempt_id);setQuote(null);setConfirmed(false);setUncertain(false);})}>Generate this chapter revision</button></>}
      {revision && <p>Revision {revision.slice(0,8)} is being inspected through safe status reads. No new request is repeated.</p>}
      {uncertain && <p role="alert">Submission outcome is unknown. Inspect script history/provider usage before any new request.</p>}</>}
    {error && <p role="alert">{error}</p>}
  </details>;
}
