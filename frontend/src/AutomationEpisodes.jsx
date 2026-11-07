import React,{useCallback,useEffect,useState} from 'react';
import CloudProduction from './CloudProduction.jsx';

export default function AutomationEpisodes({request,seriesId,onNavigate}){
 const [rows,setRows]=useState([]),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[inspect,setInspect]=useState(null);
 const [skipNumber,setSkipNumber]=useState(''),[skipNotes,setSkipNotes]=useState(''),[skipConfirmed,setSkipConfirmed]=useState(false);
 const refresh=useCallback(async()=>setRows((await request(`/automation/series/${seriesId}/episodes`)).episodes),[request,seriesId]);
 useEffect(()=>{refresh().catch(e=>setError(e.message));const timer=setInterval(()=>refresh().catch(()=>{}),10000);return()=>clearInterval(timer);},[refresh]);
 async function act(fn){setBusy(true);setError('');try{await fn();await refresh();}catch(e){setError(e.message);}finally{setBusy(false);}}
 return <details className="generation-section"><summary>Episode production and buffer</summary><p>Uses the frozen cast, prior three summaries, story state and the next outline. Local rendering does not approve or publish content. Existing episode IDs are reused; uncertain work is held.</p>
 {error&&<div className="notice notice-error" role="alert">{error}</div>}
 <label className="checkbox-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/><span>I authorize local episode generation with this approved cast.</span></label>
 <button className="button subtle" disabled={!confirmed||busy} onClick={()=>act(async()=>{await request('/automation/series-tick',{method:'POST',body:JSON.stringify({confirmed:true})});setConfirmed(false);})}>Fill available episode buffer</button>
 <p>Buffer automation requires AUTOMATION_ENABLED. Generate an individual episode below for an explicit manual run. Complete its predecessor’s script first.</p>
 <div className="generation-grid">{rows.map(ep=><article className="generation-section" key={ep.episode_id}><h4>Episode {ep.ep_no}</h4><p>{ep.job_status||ep.production_status||ep.status}</p>{ep.production_error&&<p className="notice notice-error" role="alert">{ep.production_error}</p>}{ep.summary_text&&<p>{ep.summary_text}</p>}
 <button className="button subtle" disabled={!confirmed||busy||ep.status!=='outlined'||!!ep.production_status} onClick={()=>act(async()=>{await request(`/automation/series/${seriesId}/episodes/${ep.ep_no}/run`,{method:'POST',body:JSON.stringify({confirmed:true})});setConfirmed(false);})}>Queue episode {ep.ep_no}</button>
 {ep.job_id&&<video controls preload="metadata" style={{width:'100%',maxHeight:280}} src={`/api/v1/jobs/${ep.job_id}/video`}/>}
 {ep.production_id&&ep.script_plan_id&&<button className="button subtle" onClick={()=>setInspect(ep)}>Inspect episode quality</button>}
 {ep.job_id&&['pending_review','approved','published'].includes(ep.job_status)&&<button className="button subtle" onClick={()=>onNavigate?.('review',ep.job_id)}>Open review</button>}</article>)}</div>
 {inspect&&<div><button className="button subtle" onClick={()=>setInspect(null)}>Close episode inspection</button><CloudProduction request={request} scope="local" scriptAttemptId={inspect.script_plan_id} productionId={inspect.production_id}/></div>}
 <details><summary>Explicitly skip an episode</summary><p>A skip changes story order and is recorded. It does not cancel running work or reconcile an upload.</p><label className="generation-field">Episode to skip<input type="number" min="1" max="30" value={skipNumber} onChange={e=>setSkipNumber(e.target.value)}/></label><label className="generation-field">Skip reason<textarea value={skipNotes} maxLength={1000} onChange={e=>setSkipNotes(e.target.value)}/></label><label className="checkbox-label"><input type="checkbox" checked={skipConfirmed} onChange={e=>setSkipConfirmed(e.target.checked)}/><span>I explicitly choose to skip this episode and continue in order.</span></label><button className="button subtle" disabled={busy||!skipConfirmed||!skipNumber||skipNotes.trim().length<10} onClick={()=>act(async()=>{await request(`/automation/series/${seriesId}/episodes/${skipNumber}/skip`,{method:'POST',body:JSON.stringify({confirmed:true,notes:skipNotes})});setSkipConfirmed(false);})}>Record episode skip</button></details>
 </details>;
}
