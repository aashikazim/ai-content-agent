import React, {useEffect, useRef, useState} from 'react';
import './ClipStudio.css';

function identifier() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = bytes[6] & 15 | 64; bytes[8] = bytes[8] & 63 | 128;
  const hex = [...bytes].map(value => value.toString(16).padStart(2,'0')).join('');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
const running = project => project?.jobs?.some(job => ['queued','running'].includes(job.status));
const stageLabel = value => value?.startsWith('generate:') ? `Generate clip ${value.split(':')[1]}` : ({queued:'Queued',ingest:'Ingest',analyze:'Analyze',plan:'Plan',assemble:'Assemble exports',finished:'Finished'}[value] || value);

export default function ClipStudio({request,onNavigate}) {
  const [castPackages,setCastPackages] = useState([]), [outcomesChecked,setOutcomesChecked] = useState(false), [retryPaid,setRetryPaid] = useState(false);
  const [catalog,setCatalog] = useState(null), [projects,setProjects] = useState([]), [current,setCurrent] = useState(null);
  const [instruction,setInstruction] = useState(''), [sourceKind,setSourceKind] = useState('youtube'), [sourceUrl,setSourceUrl] = useState('');
  const [files,setFiles] = useState([]), [rights,setRights] = useState(false), [busy,setBusy] = useState(false), [error,setError] = useState('');
  const [settings,setSettings] = useState({video_model:'ltx',planner_provider:'anthropic',planner_model:'',clip_count:3,duration_seconds:5,aspect_ratio:'16:9',seed:null,reattach_audio:false,backend:'native',continuity:'anchored',cast_package_id:null,paid_planning_confirmed:false,narration:'model',whisper_model:'base'});
  const projectRequest = useRef(null), actions = useRef({}), selected = useRef(null);
  const mounted = useRef(false);
  const uploadInput = useRef(null);
  async function history() { const data = await request('/clip-studio/projects'); setProjects(data.projects); }
  async function open(id) {
    selected.current = id;setOutcomesChecked(false);setRetryPaid(false);
    const project = await request(`/clip-studio/projects/${id}`);
    if (selected.current !== id) return;
    setCurrent(project);window.history.replaceState(null,'',`#clip-studio/${id}`);
  }
  useEffect(() => {
    mounted.current=true;
    let active=true;
    request('/clip-studio/catalog').then(data => {if(active){setCatalog(data);setSettings(previous=>({...previous,planner_model:data.planner_providers.find(p=>p.id==='anthropic')?.models[0]?.id || ''}));}}).catch(err=>active && setError(err.message));
    history().catch(err=>active && setError(err.message));
    request('/generation/cast-packages').then(data=>active && setCastPackages(data.packages || data.cast_packages || (Array.isArray(data)?data:[]))).catch(()=>{});
    const id=window.location.hash.match(/^#clip-studio\/([0-9a-f-]{36})$/i)?.[1];
    if(id)open(id).catch(err=>active && setError(err.message));
    return()=>{active=false;mounted.current=false;selected.current=null;};
  },[request]);
  const active = running(current);
  useEffect(()=>{
    if(!active || !current?.project_id)return;
    let live=true;
    const id=current.project_id;
    const timer=setInterval(async()=>{try{const project=await request(`/clip-studio/projects/${id}`);if(live && selected.current===id){setCurrent(project);if(!running(project))await history();}}catch(err){if(live)setError(err.message);}},2000);
    return()=>{live=false;clearInterval(timer);};
  },[current?.project_id,active,request]);
  function change(values) {setSettings(previous=>({...previous,...values}));}
  async function generate(event) {
    event.preventDefault();if(busy || !rights || (!catalog?.mock && !settings.paid_planning_confirmed))return;
    setBusy(true);setError('');
    projectRequest.current ||= identifier();
    const form=new FormData();
    form.set('payload',JSON.stringify({request_id:projectRequest.current,instruction,source_kind:sourceKind,source_url:sourceKind==='youtube'?sourceUrl:null,rights_confirmed:true,settings}));
    files.forEach(file=>form.append('files',file));
    try {
      const project=await request('/clip-studio/projects',{method:'POST',body:form});
      if(!mounted.current)return;
      selected.current=project.project_id;setCurrent(project);window.history.replaceState(null,'',`#clip-studio/${project.project_id}`);
      await history();
    } catch(err) {setError(`${err.message} Refresh project history before another request. Repeating this form uses the same request ID.`);}
    finally {setBusy(false);}
  }
  async function mutate(path, key, extra={}) {
    if(busy)return;setBusy(true);setError('');actions.current[key] ||= identifier();
    try {await request(path,{method:'POST',body:JSON.stringify({request_id:actions.current[key],confirmed:true,...extra})});if(!mounted.current)return;delete actions.current[key];await open(current.project_id);await history();}
    catch(err){setError(`${err.message} Inspect the saved project before repeating; no automatic retry was sent.`);}
    finally{setBusy(false);}
  }
  const provider=catalog?.planner_providers.find(p=>p.id===settings.planner_provider);
  const job=current?.jobs?.[0];
  return <div className="clip-studio generation-workspace">
    <div className="notice notice-info" role="status"><div><strong>{catalog?.mock?'Mock workflow':'Local Clip Studio'}</strong><p>{catalog?.notice || 'Loading Clip Studio…'} {catalog?.mock?'Mock audio is a test tone; no API calls or charges.':'Paid planning requires consent below. Video rendering uses local models.'}</p></div></div>
    
    {error && <div className="notice notice-error" role="alert">{error}</div>}
    <div className="clip-studio-layout"><section className="generation-section"><div className="section-heading"><div><h2>Direct your clips</h2><p>Choose a source and describe the clips you want.</p></div></div>
      <form onSubmit={generate}>
        <label htmlFor="clip-instruction">Instructions</label><textarea id="clip-instruction" rows={5} maxLength={5000} value={instruction} onChange={e=>setInstruction(e.target.value)} placeholder="Create three cinematic clips with warm lighting and a hopeful theme." required/>
        <div className="segmented clip-source-tabs" role="tablist" aria-label="Clip source">{[['youtube','YouTube link'],['video','Upload video'],['images','Upload images']].map(([id,label])=><button type="button" role="tab" aria-selected={sourceKind===id} key={id} className={sourceKind===id?'selected':''} onClick={()=>{setSourceKind(id);setFiles([]);setSourceUrl('');setRights(false);if(uploadInput.current)uploadInput.current.value='';}}>{label}</button>)}</div>
        {sourceKind==='youtube'?<label>YouTube URL<input type="url" value={sourceUrl} onChange={e=>{setSourceUrl(e.target.value);setRights(false);}} placeholder="https://www.youtube.com/watch?v=…" required/></label>:<label>{sourceKind==='video'?'Source video':'Reference images (up to 8)'}<input ref={uploadInput} key={sourceKind} type="file" accept={sourceKind==='video'?'.mp4,.mov,.webm,.mkv':'image/png,image/jpeg,image/webp'} multiple={sourceKind==='images'} required onChange={e=>{setFiles([...e.target.files]);setRights(false);}}/></label>}
        <div className="generation-grid"><label>Planning provider<select value={settings.planner_provider} onChange={e=>{const next=catalog.planner_providers.find(p=>p.id===e.target.value);change({planner_provider:next.id,planner_model:next.models[0].id});}}>{catalog?.planner_providers.map(p=><option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
          <label>Planning model<select value={settings.planner_model} onChange={e=>change({planner_model:e.target.value})}>{provider?.models.map(m=><option key={m.id} value={m.id}>{m.label}</option>)}</select></label>
          <label>Local video model<select value={settings.video_model} onChange={e=>change({video_model:e.target.value})}><option value="ltx">LTX-2</option><option value="wan">Wan</option><option value="hybrid">LTX + Wan hybrid</option></select></label>
          <label>Number of clips<input type="number" min="1" max="12" value={settings.clip_count} onChange={e=>change({clip_count:Number(e.target.value)})} required/></label>
          <label>Seconds per clip<input type="number" min="2" max="300" value={settings.duration_seconds} onChange={e=>change({duration_seconds:Number(e.target.value)})} required/></label>
          <label>Aspect ratio<select value={settings.aspect_ratio} onChange={e=>change({aspect_ratio:e.target.value})}>{['16:9','9:16','1:1'].map(r=><option key={r}>{r}</option>)}</select></label>
          <label>Seed (optional)<input type="number" min="0" max="2147483647" value={settings.seed ?? ''} onChange={e=>change({seed:e.target.value===''?null:Number(e.target.value)})}/></label>
          <label>Generation backend<select value={settings.backend} onChange={e=>change({backend:e.target.value})}><option value="native">Existing native LTX / Wan runtime</option><option value="comfyui">ComfyUI · exported workflows required</option></select></label>
          <label>Approved cast package<select value={settings.cast_package_id || ''} onChange={e=>change({cast_package_id:e.target.value || null})}><option value="">Source references only (LTX)</option>{castPackages.map(p=><option key={p.package_id} value={p.package_id} disabled={p.unavailable}>{p.name}</option>)}</select></label>
          <label>Continuity<select value={settings.continuity} onChange={e=>change({continuity:e.target.value})}><option value="anchored">Anchor each cut to approved references</option><option value="connected">Connected action · carry prior frames</option></select></label>
          <label>Audio<select value={settings.narration} onChange={e=>change({narration:e.target.value})}><option value="model">Model audio (LTX / hybrid)</option><option value="source">Reattach original source audio</option><option value="character_tts">Fixed cast voices (eSpeak)</option></select></label>
          <label>Whisper analysis model<select value={settings.whisper_model} onChange={e=>change({whisper_model:e.target.value})}>{['tiny','base','small','medium'].map(m=><option key={m}>{m}</option>)}</select></label>
        </div>
        <details><summary>Character and continuity guide</summary><p>Create an approved cast package in Local Video Studio first, then select it here. The same canonical face, clothing and voice are reused. LTX can use source frames without a cast, but recurring actors work best with approved images. Wan requires one actor, a source video for driving motion, anchored continuity, and source or fixed-character audio. Hybrid uses Wan on cuts and LTX on continuous action. Multi-actor identity and lip sync still need human review.</p><p>Example: upload a cafe scene, choose your approved actor package, native hybrid, connected continuity, three 10-second clips and fixed cast voices. Instruction: “Keep Maya’s face and blue jacket throughout. Maya enters the cafe, continues walking to the counter, then orders tea. Use the same calm voice; include short dialogue.” The planner marks true continuations and cuts. Longer clips are split into connected shots and assembled locally.</p><p>ComfyUI needs your tested API workflow exports and conditioning bindings. Native rendering reuses the installed models. Source clips supply scene frames; native LTX performs image-to-video, while Wan transfers source driving motion. Model audio is not guaranteed dialogue or a consistent voice; fixed eSpeak voices prioritize consistency and do not provide lip sync.</p></details>
        {!catalog?.mock && <label className="checkbox-label"><input type="checkbox" checked={settings.paid_planning_confirmed} onChange={e=>change({paid_planning_confirmed:e.target.checked})}/><span>I authorize the selected planning provider: estimated up to ${Number(catalog?.planning_estimate_usd || .4).toFixed(2)}, including at most one invalid-JSON repair (two paid calls total). This is an estimate, not an account billing cap. I also authorize local rendering.</span></label>}
        <label className="checkbox-label"><input type="checkbox" checked={rights} onChange={e=>setRights(e.target.checked)}/><span>I have the rights to use this source.</span></label>
        <div className="generation-actions"><button className="button primary" disabled={busy || !rights || !instruction.trim() || (!catalog?.mock && !settings.paid_planning_confirmed) || active}>{catalog?.mock?'Generate mock clips':'Generate clips'}</button><button type="button" className="button subtle" disabled={busy} onClick={()=>{projectRequest.current=null;selected.current=null;setCurrent(null);setError('');window.history.replaceState(null,'','#clip-studio');}}>New project</button></div>
      </form>
    </section>
    <aside className="generation-section"><h2>Saved projects</h2><p>Projects and clip versions remain available after refreshing. Use New project to start another request; regenerating keeps the saved project settings.</p><label>Reopen project<select value={current?.project_id || ''} onChange={e=>e.target.value && open(e.target.value).catch(err=>setError(err.message))}><option value="">Choose a project</option>{projects.map(p=><option key={p.project_id} value={p.project_id}>{p.instruction.slice(0,70)} · {p.status}</option>)}</select></label><button type="button" className="button subtle" onClick={()=>history().catch(err=>setError(err.message))}>Refresh project history</button></aside></div>
    {current && <section className="generation-section clip-project"><div className="section-heading"><div><h2>Your clip project</h2><p>{current.request.instruction}</p><small>Project {current.project_id}</small></div><span className="soft-tag">{current.mode==='real'?'LOCAL OUTPUT':'MOCK OUTPUT'}</span></div>
      {job && <><p role="status">{job.status.replaceAll('_',' ')} · {stageLabel(job.stage)}</p><progress aria-label="Clip Studio job progress" max="1" value={job.progress}/><ol className="clip-stages">{['ingest','analyze','plan','generate','assemble'].map(stage=><li key={stage}>{stageLabel(stage)}</li>)}</ol>{job.error && <div className="notice notice-error" role="alert"><div><strong>Clip workflow failed</strong><p>{job.error}</p></div></div>}{job.status==='queued' && <button className="button subtle" disabled={busy} onClick={()=>mutate(`/clip-studio/projects/${current.project_id}/start-queued`,'wake-worker')}>Wake queued worker</button>}{['failed','partial','interrupted'].includes(job.status) && <>{current.mode==='real' && current.clips.length===0 && <label className="checkbox-label"><input type="checkbox" checked={retryPaid} onChange={e=>setRetryPaid(e.target.checked)}/><span>I authorize up to two new paid planning calls at the displayed estimate for this retry.</span></label>}<button className="button subtle" disabled={busy || active || (current.mode==='real' && current.clips.length===0 && !retryPaid)} onClick={()=>mutate(`/clip-studio/projects/${current.project_id}/jobs/${job.job_id}/retry`,`retry:${job.job_id}`,{paid_planning_confirmed:retryPaid})}>Retry failed work</button></>}</>}
      <div className="clip-results">{current.clips.map(clip=>{
        const selectedAttempt=clip.attempts.find(a=>a.attempt_id===clip.selected_attempt_id);
        const latest=clip.attempts.at(-1);
        const shown=selectedAttempt || latest;
        return <article className="panel clip-result" key={clip.clip_id}><h3>{clip.plan.title}</h3>{shown?.video_url && <video controls preload="none" src={shown.video_url} poster={shown.thumbnail_url} aria-label={`${clip.plan.title} preview`}/>}
          <p>{clip.plan.generation_prompt}</p><p>Source reference: {clip.plan.source_start==null?'Reference image':`${clip.plan.source_start}s–${clip.plan.source_end}s`} · {clip.plan.duration_seconds}s · {shown?.model.toUpperCase()} · seed {shown?.seed}</p><p>{shown?.video_url && `Previewing selected version ${shown.version}. `}Latest attempt: {latest?.status} · version {latest?.version}</p>{latest?.error && <p role="alert">{latest.error}</p>}
          <div className="generation-actions">{shown?.video_url && <a className="button subtle" href={`${shown.video_url}?download=true`} download>Download clip</a>}<button className="button subtle" disabled={busy || active || shown?.status==='uncertain'} onClick={()=>mutate(`/clip-studio/projects/${current.project_id}/clips/${clip.clip_id}/regenerate`,`regenerate:${clip.clip_id}`)}>Regenerate {clip.plan.title}</button></div>
          {shown?.stale_continuity && <p role="alert">The preceding selected clip changed. Regenerate this continuation and manually inspect continuity.</p>}
          {current.mode==='real' && shown?.video_url && <div className="generation-actions">{shown.review_job_id?<><span>Review job: {shown.review_job_id}</span>{onNavigate && <button className="button subtle" onClick={()=>onNavigate('review')}>Open review queue</button>}</>:<button className="button subtle" disabled={busy || active || shown.stale_continuity} onClick={async()=>{setBusy(true);try{await request(`/clip-studio/projects/${current.project_id}/clips/${clip.clip_id}/review`,{method:'POST',body:JSON.stringify({attempt_id:shown.attempt_id,confirmed:true})});await open(current.project_id);}catch(err){setError(err.message);}finally{setBusy(false);}}}>Send selected version to review</button>}</div>}
          {latest?.chunks?.length>0 && <details><summary>Saved generation IDs and chunks</summary>{latest.chunks.map(c=><p key={c.position}>Chunk {c.position+1}: {c.status} · {c.external_id || 'No acknowledged ID'}</p>)}</details>}
          {clip.attempts.length>1 && <details><summary>Versions ({clip.attempts.length})</summary>{clip.attempts.map(a=><div className="clip-version" key={a.attempt_id}><span>v{a.version} · {a.status}{a.attempt_id===clip.selected_attempt_id?' · selected':''}</span>{a.video_url && <><video controls preload="none" src={a.video_url} aria-label={`${clip.plan.title} version ${a.version}`}/><button className="button subtle" disabled={busy || active || a.attempt_id===clip.selected_attempt_id} onClick={async()=>{setBusy(true);try{setCurrent(await request(`/clip-studio/projects/${current.project_id}/clips/${clip.clip_id}/select`,{method:'POST',body:JSON.stringify({attempt_id:a.attempt_id,confirmed:true})}));}catch(err){setError(err.message);}finally{setBusy(false);}}}>Use version {a.version}</button></>}</div>)}</details>}
        </article>;
      })}</div>
      {['uncertain','interrupted'].includes(job?.status) && current.mode==='real' && <div className="notice notice-warning"><div><p>Inspect the saved generation IDs and provider usage. Resume only polls known requests before continuing authorized work. Reconciliation abandons uncertain work and allows a later explicit retry; stop any abandoned generation first.</p><label className="checkbox-label"><input type="checkbox" checked={outcomesChecked} onChange={e=>setOutcomesChecked(e.target.checked)}/><span>I checked saved IDs and provider usage, and stopped any work I intend to abandon.</span></label><button className="button subtle" disabled={busy || !outcomesChecked} onClick={()=>mutate(`/clip-studio/projects/${current.project_id}/reconcile`,'resume',{resume:true,outcomes_checked:true})}>Resume saved generation IDs</button><button className="button subtle" disabled={busy || !outcomesChecked} onClick={()=>mutate(`/clip-studio/projects/${current.project_id}/reconcile`,'reconcile',{resume:false,outcomes_checked:true})}>Mark outcomes reconciled · no generation</button></div></div>}
      {current.planning_calls?.length>0 && <details><summary>Planning calls and response IDs</summary>{current.planning_calls.map(c=><p key={c.call_id}>{c.provider} · {c.status} · {c.external_id || c.call_id}</p>)}</details>}
      <details><summary>Stage history</summary>{current.events.slice(0,25).map(e=><p key={e.event_id}><strong>{stageLabel(e.stage)}</strong> · {e.message}</p>)}</details>
      <p className="generation-human-note">Validated initial real exports appear in the existing Review queue. After you approve there, open Publishing and explicitly upload to YouTube or Facebook. Clip Studio never approves or publishes. Regenerated versions preserve the selected original until you explicitly choose a replacement.</p>
    </section>}
  </div>;
}
