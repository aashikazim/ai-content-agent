import React,{useCallback,useEffect,useRef,useState} from 'react';

export default function AutomationSeries({request}) {
 const [history,setHistory]=useState([]),[series,setSeries]=useState(null),[genre,setGenre]=useState('mystery drama'),[premise,setPremise]=useState('');
 const [confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [packages,setPackages]=useState([]),[packageId,setPackageId]=useState(''),[castConfirmed,setCastConfirmed]=useState(false);
 const [episodes,setEpisodes]=useState([]),[selected,setSelected]=useState(1),[editor,setEditor]=useState(''),[dirty,setDirty]=useState(false);
 const requestId=useRef(null),revisionId=useRef(null);
 const loadHistory=useCallback(async()=>setHistory((await request('/automation/series')).series),[request]);
 useEffect(()=>{loadHistory().catch(e=>setError(e.message));request('/generation/cast-packages').then(x=>setPackages(x.packages.filter(p=>!p.unavailable))).catch(e=>setError(e.message));},[loadHistory,request]);
 function accept(value){setSeries(value);setEpisodes(value.episodes);setDirty(false);setSelected(1);setEditor(value.episodes[0]?JSON.stringify(value.episodes[0],null,2):'');setCastConfirmed(false);}
 async function act(fn){setBusy(true);setError('');try{await fn();}catch(e){setError(e.message);}finally{setBusy(false);}}
 useEffect(()=>{
  if(!series||dirty||!['queued','running'].includes(series.planning_status))return;
  let cancelled=false;
  const timer=setInterval(()=>request(`/automation/series/${series.series_id}`).then(value=>{if(!cancelled)accept(value);}).catch(e=>{if(!cancelled)setError(e.message);}),5000);
  return()=>{cancelled=true;clearInterval(timer);};
 },[request,series?.series_id,series?.planning_status,dirty]);
 async function create(sourceId=null){
  const id=sourceId?revisionId:requestId;id.current ||= crypto.randomUUID();
  accept(await request('/automation/series',{method:'POST',body:JSON.stringify({request_id:id.current,genre,premise,confirmed:true,...(sourceId?{source_series_id:sourceId}:{})})}));
  id.current=null;setConfirmed(false);await loadHistory();
 }
 function select(n){setSelected(n);setEditor(JSON.stringify(episodes[n-1],null,2));}
 function stageEdit(){try{const parsed=JSON.parse(editor);if(parsed.ep_no!==selected)throw new Error('Keep the selected episode number unchanged.');setEpisodes(old=>old.map((e,i)=>i===selected-1?parsed:e));setDirty(true);setError('');}catch(e){setError(e.message);}}
 return <div className="generation-section">
  <h2>Serialized story planning</h2><p>Plan 30 original episodes locally, each 5–10 minutes. No videos are rendered on this page. Manual cast approval is required before a series becomes active.</p>
  {error&&<div className="notice notice-error" role="alert">{error}</div>}
  <div className="generation-grid"><label className="generation-field">Genre<input value={genre} maxLength={100} onChange={e=>{setGenre(e.target.value);setConfirmed(false);}}/></label>
  <label className="generation-field">Original premise (optional)<textarea value={premise} maxLength={2000} onChange={e=>{setPremise(e.target.value);setConfirmed(false);}} placeholder="Leave empty for three local premise candidates, or enter at least 40 characters."/></label></div>
  <label className="checkbox-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/><span>I authorize local story planning only. Cast approval, generation, content approval and publishing are separate actions.</span></label>
  <button className="button primary" disabled={busy||!confirmed||!!(premise&&premise.trim().length<40)} onClick={()=>act(()=>create())}>Plan a new 30-episode series</button>
  <p>After a request error, repeating this form retains its request ID. Change the form only after inspecting history.</p>
  <div className="generation-actions"><button className="button subtle" onClick={()=>act(loadHistory)}>Refresh series history</button>{history.map(s=><button className="button subtle" disabled={busy} key={s.series_id} onClick={()=>act(async()=>accept(await request(`/automation/series/${s.series_id}`)))}>{s.title} · {['failed','needs_reconciliation'].includes(s.planning_status)?s.planning_status:s.status}</button>)}</div>
  {series&&<><h3>{series.title}</h3><p>{series.premise}</p><p className="notice">{series.status} · Planning: {series.planning_status} · {series.stage}</p>
   {series.error&&<div role="alert" className="notice notice-error">{series.error}. Inspect the saved receipts before requesting a new series. Interrupted requests are never automatically repeated.</div>}
   <details><summary>Planning receipts and validator feedback</summary>{series.receipts.map((r,i)=><p key={i}>{r.stage} attempt {r.attempt_no}: {r.status} {r.errors.join('; ')}</p>)}</details>
   {series.planning_status==='failed'&&series.bible.characters&&<><p>Known failed planning can be revised in a new record, preserving this bible and the accepted days. Uncertain requests must be reconciled first. Check the local-planning authorization above to proceed.</p><button className="button subtle" disabled={busy||!confirmed} onClick={()=>act(()=>create(series.series_id))}>Revise remaining outline</button></>}
   <h3>30-day outline</h3><div className="generation-grid">{episodes.map(e=><article className="generation-section" key={e.ep_no}><button className="button subtle" onClick={()=>select(e.ep_no)}>Day {e.ep_no}: {e.title}</button><p>{e.phase} · {e.duration_seconds}s</p><p>{e.hook}</p></article>)}</div>
   {episodes.length===30&&<><label className="generation-field">Day {selected} outline JSON<textarea rows={16} value={editor} onChange={e=>setEditor(e.target.value)}/></label>
    <p>Stage an edit, then select another day if needed. A changed ending hook also requires updating the next day’s previous_hook and opening_ack. Save validates all 30 episodes together. Produced episodes lock the outline.</p>
    <div className="generation-actions"><button className="button subtle" disabled={busy} onClick={stageEdit}>Stage day edit</button><button className="button primary" disabled={busy||!dirty} onClick={()=>act(async()=>accept(await request(`/automation/series/${series.series_id}/outline`,{method:'PUT',body:JSON.stringify({episodes,expected_updated_at:series.updated_at})})))}>Save validated outline</button></div></>}
   {series.bible.characters&&<details open><summary>Cast setup instructions</summary><p>In Local Studio, upload approved actor reference views and create an approved cast package. Copy the exact IDs, names, appearance, wardrobe and voice notes below into that package. Use 2–3 actors and 1–6 approved views per actor. The frozen bible cannot be rewritten to fit an unrelated cast. Return here and refresh history after creating your package.</p>{series.bible.characters.map(a=><article className="notice" key={a.character_id}><strong>{a.name} · {a.character_id}</strong><p>Appearance: {a.description}</p><p>Wardrobe: {a.wardrobe}</p><p>Voice: {a.voice_notes}</p><p>Motivation: {a.motivation} · Secret: {a.secret}</p></article>)}</details>}
   {series.status==='awaiting_cast'&&<><label className="generation-field">Approved cast package<select value={packageId} onChange={e=>{setPackageId(e.target.value);setCastConfirmed(false);}}><option value="">Select an inspected package</option>{packages.map(p=><option key={p.package_id} value={p.package_id}>{p.name}</option>)}</select></label>
    <button className="button subtle" onClick={()=>act(async()=>setPackages((await request('/generation/cast-packages')).packages.filter(p=>!p.unavailable)))}>Refresh cast packages</button>
    <label className="checkbox-label"><input type="checkbox" checked={castConfirmed} onChange={e=>setCastConfirmed(e.target.checked)}/><span>I inspected the reference images and approve this exact cast for this series. Activation does not render or publish episodes.</span></label>
    <button className="button primary" disabled={!castConfirmed||!packageId||busy||dirty} onClick={()=>act(async()=>{accept(await request(`/automation/series/${series.series_id}/approve-cast`,{method:'POST',body:JSON.stringify({package_id:packageId,expected_updated_at:series.updated_at,confirmed:true})}));await loadHistory();})}>Approve cast and activate series</button></>}
  </>}
 </div>;
}
