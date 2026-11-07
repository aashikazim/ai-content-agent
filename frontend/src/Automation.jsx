import React,{useCallback,useEffect,useRef,useState} from 'react';
import CloudProduction from './CloudProduction.jsx';
import AutomationSeries from './AutomationSeries.jsx';
import './GenerationForm.css';

export default function Automation({request,onNavigate}) {
 const [showSeries,setShowSeries]=useState(false);
 const editedDay=useRef(false);
 const [day,setDay]=useState(new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York'}).format(new Date()));
 const [metadata,setMetadata]=useState(null),[plan,setPlan]=useState(null),[preview,setPreview]=useState(null),[report,setReport]=useState('');
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[confirmed,setConfirmed]=useState(false),[inspect,setInspect]=useState(null);
 const refresh=useCallback(async()=>{
  const [p,r]=await Promise.all([request(`/automation/plan/${day}`),request(`/automation/reports/${day}`)]);
  setPlan(p);setReport(r.markdown);
 },[request,day]);
 useEffect(()=>{request('/automation/lanes').then(meta=>{setMetadata(meta);if(!editedDay.current)setDay(new Intl.DateTimeFormat('en-CA',{timeZone:meta.timezone||'America/New_York'}).format(new Date()));}).catch(e=>setError(e.message));},[request]);
 useEffect(()=>{refresh().catch(e=>setError(e.message));const timer=setInterval(()=>refresh().catch(()=>{}),10000);return()=>clearInterval(timer);},[refresh]);
 useEffect(()=>setConfirmed(false),[day]);
 async function act(fn){setBusy(true);setError('');try{await fn();}catch(e){setError(e.message);}finally{setBusy(false);}}
 const forecast=plan?.run?.forecast;
 if(showSeries)return <section className="generation-workspace"><button className="button subtle" onClick={()=>setShowSeries(false)}>Back to daily Shorts</button><AutomationSeries request={request}/></section>;
 return <section className="generation-workspace"><div className="generation-section">
  <div className="generation-section-heading"><div><h2>Daily production</h2><button className="button subtle" onClick={()=>setShowSeries(true)}>Series planning</button><p>Local generation, durable plans and human quality review.</p></div></div>
  {error&&<div className="notice notice-error" role="alert">{error}</div>}
  {metadata&&!metadata.enabled&&<p className="notice">Automation is disabled. Preview remains available. Enable AUTOMATION_ENABLED locally before requesting production.</p>}
  <div className="generation-grid"><label className="generation-field">Plan date<input type="date" value={day} onChange={e=>{editedDay.current=true;setDay(e.target.value);}}/></label></div>
  <div className="generation-actions"><button className="button subtle" disabled={busy} onClick={()=>act(async()=>setPreview(await request('/automation/plan-preview',{method:'POST',body:JSON.stringify({lane:'shorts_daily',region:'US'})})))}>Preview topics</button><button className="button subtle" onClick={()=>act(refresh)}>Refresh plan</button></div>
  {preview&&<div><h3>Topic preview</h3>{preview.topics.map(t=><article className="notice" key={t.topic_id}><strong>{t.title}</strong><p>{t.category} · {t.reasons.join(' · ')}</p></article>)}{preview.warnings.map(w=><p key={w}>{w}</p>)}<p>{preview.backups.length} ranked backup topics. Preview does not create jobs.</p></div>}
  <label className="checkbox-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/><span>I authorize this local rendering batch. This does not approve or publish its output.</span></label>
  <button className="button primary" disabled={!confirmed||!metadata?.enabled||busy||!!plan?.run} onClick={()=>act(async()=>{await request(`/automation/plan/${day}/run`,{method:'POST',body:JSON.stringify({confirmed:true})});setConfirmed(false);await refresh();})}>Create daily plan and queue Shorts</button>
  {plan?.run&&<p className="notice">Day status: {plan.run.status}. Existing plans are reused; refreshing never resubmits generation.</p>}
  {forecast&&<div><h3>GPU forecast</h3><p>{Math.round(forecast.estimated_seconds/60)} estimated minutes / {Math.round(forecast.capacity_seconds/3600)} available hours. {forecast.assumption}</p>{forecast.items.map(i=><p key={i.slot}>Slot {i.slot}: {i.rate_source} rate · {i.duration_s}s output · {i.skipped?'skipped':i.planned_start} {i.degradations.join(', ')}</p>)}</div>}
  <div className="generation-grid">{plan?.slots?.map(slot=><article className="generation-section" key={slot.daily_plan_id}><h3>Slot {slot.slot}: {slot.recipe.topic.title}</h3><p>{slot.job_status||slot.status}</p><p>{slot.notes}</p>{slot.job_id&&<video controls preload="metadata" style={{width:'100%',maxHeight:360,objectFit:'contain',background:'var(--green)',borderRadius:8}} src={`/api/v1/jobs/${slot.job_id}/video`}/>}<p>{slot.metrics?`${slot.metrics.neural_seconds.toFixed(2)} measured neural seconds`:'GPU time not measured yet'}</p>{slot.attempts?.at(-1)?.script_attempt_id&&<button className="button subtle" onClick={()=>setInspect({script:slot.attempts.at(-1).script_attempt_id,production:slot.attempts.at(-1).production_id})}>Inspect production quality</button>}{slot.job_status==='pending_review'&&<button className="button subtle" onClick={()=>onNavigate?.('review',slot.job_id)}>Open manual review</button>}</article>)}</div>
  {inspect&&<div><button className="button subtle" onClick={()=>setInspect(null)}>Close quality inspection</button><CloudProduction request={request} scope="local" scriptAttemptId={inspect.script} productionId={inspect.production}/></div>}
  <details><summary>Daily report</summary><pre style={{whiteSpace:'pre-wrap'}}>{report}</pre></details>
 </div></section>;
}
