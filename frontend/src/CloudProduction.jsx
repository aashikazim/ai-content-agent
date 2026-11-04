import React, { useEffect, useState } from 'react';

const post = (request, path, body) => request(path, { method: 'POST', body: JSON.stringify(body) });
const active = value => ['queued', 'processing', 'assembling'].includes(value?.status) || value?.shots?.some(shot => ['queued', 'submitting', 'processing', 'download_pending'].includes(shot.status));

function CharacterRegistry({ request, references, reload }) {
  const [form, setForm] = useState({ character_id: '', name: '', description: '', wardrobe: '', voice_notes: '', rights_note: '', rights_confirmed: false });
  const [file, setFile] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function upload() {
    if (!file || busy || !form.rights_confirmed) return;
    setBusy(true); setError('');
    try {
      if (file.size > 4 * 1024 * 1024) throw new Error('Choose an image smaller than 4MB.');
      const image = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = () => reject(new Error('Could not read this image.')); reader.readAsDataURL(file); });
      await post(request, '/generation/references', { ...form, image_base64: image });
      setFile(null); await reload();
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  return <details className="cloud-reference-registry"><summary>Character reference registry ({references.length})</summary>
    <p>For character images, use the exact character ID from your script. For composed scene start frames, use an ID such as scene_1. Images stay local until an explicitly authorized cloud request sends the selected references. Use images you have permission to use. Appearance, clothing and voices still require inspection.</p>
    <div className="generation-grid">{[['character_id', 'Character ID'], ['name', 'Character name'], ['description', 'Appearance description'], ['wardrobe', 'Clothing'], ['voice_notes', 'Voice notes'], ['rights_note', 'Image rights/source note']].map(([key, label]) => <div className="generation-field" key={key}><label htmlFor={`reference-${key}`}>{label}</label><input id={`reference-${key}`} maxLength={key === 'character_id' ? 32 : key === 'name' ? 80 : 500} value={form[key]} onChange={event => setForm({ ...form, [key]: event.target.value })} /></div>)}</div>
    <label htmlFor="reference-file">Reference image (PNG/JPEG/WebP, 128px minimum, 4MB maximum)</label><input id="reference-file" type="file" accept="image/png,image/jpeg,image/webp" onChange={event => setFile(event.target.files[0])} />
    <label className="checkbox-label"><input type="checkbox" checked={form.rights_confirmed} onChange={event => setForm({ ...form, rights_confirmed: event.target.checked })} /><span>I have permission to use this image and any depicted person.</span></label>
    <button type="button" className="button subtle" disabled={!file || !form.rights_confirmed || busy} onClick={upload}>Save character reference locally</button>{error && <p role="alert">{error}</p>}
    <div className="cloud-reference-grid">{references.map(reference => <figure key={reference.reference_id}><img src={reference.image_url} alt={reference.name} /><figcaption>{reference.name} · <code>{reference.character_id}</code><small>{reference.description}</small></figcaption></figure>)}</div>
  </details>;
}

function ShotCard({ shot, production, request, refresh, onQuote, disabled, editModels }) {
  const [operation, setOperation] = useState('generate');
  const [instructions, setInstructions] = useState('');
  const [extension, setExtension] = useState(3);
  const [editModel,setEditModel] = useState('aleph2');
  const runway = production.configuration?.video_provider === 'runway';
  const local = production.configuration?.video_provider === 'local_cinematic';
  const [trim, setTrim] = useState(shot.trim_start_seconds || 0);
  const [notes, setNotes] = useState('');
  const [remote, setRemote] = useState('');
  const [closeAttempt, setCloseAttempt] = useState(false);
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function act(action) { if (busy) return; setBusy(true); setError(''); try { await action(); await refresh(); } catch (err) { setError(err.message); } finally { setBusy(false); } }
  return <article className={`cloud-shot ${shot.selected ? 'cloud-shot-selected' : ''}`}><div className="cloud-shot-heading"><h3>Shot {shot.shot_number} · v{shot.version}</h3><span>{shot.status.replaceAll('_', ' ')}{shot.selected ? ' · selected' : ''}</span></div>
    {shot.video_url && <video src={shot.video_url} controls preload="metadata" aria-label={`Shot ${shot.shot_number} version ${shot.version}`} />}
    {shot.error && <p role="alert">{shot.error}</p>}
    {local && shot.usage && <div><p>{shot.usage.stage?.replaceAll('_',' ')} · {shot.usage.elapsed_seconds || 0}s elapsed · peak GPU allocation {shot.usage.peak_gpu_memory_mb ?? 'pending'} MiB · peak process memory {shot.usage.peak_rss_mb ?? 'pending'} MiB</p><progress aria-label="Local generation progress" max="1" value={shot.usage.progress || 0} /></div>}
    <p>{production.production_plan.shots.find(item => item.shot_number === shot.shot_number)?.action}</p><p>Operation: {shot.operation}. Estimated request: ${shot.estimate_usd}. Reported usage cost: {shot.calculated_cost_usd == null ? 'unavailable' : `$${shot.calculated_cost_usd}`}. Actual invoice: not reported.</p>
    {shot.interaction_id && <small>Generation ID: {shot.generation_id || shot.interaction_id}</small>}
    {shot.status === 'completed' && <div className="cloud-shot-select"><label>Trim start (seconds)<input type="number" min="0" max="120" step="0.1" value={trim} onChange={event => setTrim(Number(event.target.value))} /></label><button type="button" className="button subtle" disabled={disabled || busy} onClick={() => act(() => post(request, `/generation/videos/${production.production_id}/select`, { attempt_id: shot.attempt_id, trim_start_seconds: trim }))}>Use this shot & assemble</button></div>}
    {['completed', 'failed', 'validation_failed'].includes(shot.status) && <details><summary>Create another candidate</summary><p>The original stays available. Voice editing is unsupported. Extended footage must be manually inspected and trimmed to the planned shot window; it does not lengthen the script automatically.</p>
      <label>Shot operation<select value={operation} onChange={event => setOperation(event.target.value)}><option value="generate">Regenerate this shot</option>{!local && <><option value="edit" disabled={shot.status !== 'completed'}>Edit this shot</option><option value="extend" disabled={shot.status !== 'completed'}>Extend this shot</option></>}</select></label>
      {runway && operation === 'edit' && <label>Runway edit model<select value={editModel} onChange={event => setEditModel(event.target.value)}>{(editModels.length ? editModels : [{id:'aleph2',label:'Aleph 2'}]).map(model => <option key={model.id} value={model.id}>{model.label}</option>)}</select><small>Visual edit of the planned shot window; original audio is retained locally. O3 accepts up to 10 seconds; Aleph 2 and Kling 4 accept up to 30.</small></label>}
      {runway && operation === 'extend' && <p>Continue from the parent’s final frame using the generation model, then join locally. Only added seconds are billed. Inspect the result and choose a trim window; the screenplay duration stays fixed.</p>}
      <label>Shot change instructions<textarea rows={3} maxLength={2000} value={instructions} onChange={event => setInstructions(event.target.value)} /></label>
      {operation === 'extend' && <label>Extension seconds<input type="number" min="3" max="10" value={extension} onChange={event => setExtension(Number(event.target.value))} /></label>}
      <button type="button" className="button subtle" disabled={disabled || busy} onClick={() => act(async () => onQuote(await post(request, `/generation/videos/${production.production_id}/shot-quotes`, { shot_number: shot.shot_number, parent_attempt_id: shot.attempt_id, operation, instructions, extension_seconds: extension, ...(runway && operation==='edit' ? {edit_model:editModel} : {}) }), 'change', production.production_id))}>Estimate this shot change</button></details>}
    {['needs_reconciliation', 'download_failed'].includes(shot.status) && <details><summary>Reconcile this request manually</summary><label>Provider inspection notes<input value={notes} maxLength={500} onChange={event => setNotes(event.target.value)} /></label><label>Interaction ID found in provider records (optional)<input value={remote} maxLength={200} onChange={event => setRemote(event.target.value)} /></label><label className="checkbox-label"><input type="checkbox" checked={checked} onChange={event => setChecked(event.target.checked)} /><span>I inspected provider usage and accept any prior charge.</span></label><label className="checkbox-label"><input type="checkbox" checked={closeAttempt} onChange={event => setCloseAttempt(event.target.checked)} /><span>Close this attempt locally. This does not cancel or refund provider generation.</span></label><button type="button" className="button subtle" disabled={busy || !checked || !notes.trim()} onClick={() => act(() => post(request, `/generation/videos/${production.production_id}/shots/${shot.attempt_id}/reconcile`, { notes, confirmed: true, interaction_id: remote || null, close_attempt: closeAttempt }))}>Record reconciliation without resubmitting</button></details>}
    {error && <p role="alert">{error}</p>}
  </article>;
}

export default function CloudProduction({ request, editModels=[] }) {
  const [scripts, setScripts] = useState([]);
  const [references, setReferences] = useState([]);
  const [productions, setProductions] = useState([]);
  const [selectedScript, setSelectedScript] = useState('');
  const [referenceIds, setReferenceIds] = useState([]);
  const [startFrame, setStartFrame] = useState('');
  const [shotFrames, setShotFrames] = useState({});
  const [driving, setDriving] = useState([]);
  const [drivers, setDrivers] = useState({});
  const [motion, setMotion] = useState(null);
  const [motionName, setMotionName] = useState('');
  const [motionRights, setMotionRights] = useState('');
  const [motionAllowed, setMotionAllowed] = useState(false);
  const [credits, setCredits] = useState(null);
  const [current, setCurrent] = useState(null);
  const [sceneFilter,setSceneFilter] = useState('all');
  useEffect(()=>setSceneFilter('all'),[current?.production_id]);
  const [quote, setQuote] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [checks, setChecks] = useState([false, false, false]);
  const [resumeConfirmed, setResumeConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [inspected, setInspected] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setChecks([false,false,false]); }, [current?.production_id, JSON.stringify(current?.shots?.filter(shot => shot.selected).map(shot => [shot.attempt_id, shot.trim_start_seconds]))]);
  async function load() {
    const [drafts, refs, videos] = await Promise.all([request('/generation/scripts'), request('/generation/references'), request('/generation/videos')]);
    setScripts((drafts.attempts || []).filter(item => item.status === 'completed' && item.production_plan?.shots?.length));
    setReferences(refs.references || []); setProductions(videos.productions || []);
    if (current) { const value = (videos.productions || []).find(item => item.production_id === current.production_id); if (value) setCurrent(value); }
  }
  useEffect(() => { load().catch(err => setError(err.message)); }, [request]);
  useEffect(() => { setQuote(null); setConfirmed(false); }, [selectedScript, JSON.stringify(referenceIds), startFrame, JSON.stringify(shotFrames), JSON.stringify(drivers)]);
  useEffect(() => { setStartFrame(''); setShotFrames({}); setDrivers({}); }, [selectedScript]);
  useEffect(() => {
    if (!current || !active(current) || uncertain) return;
    let cancelled = false, refreshing = false;
    const interval = setInterval(async () => { if (refreshing) return; refreshing = true; try { await post(request, `/generation/videos/${current.production_id}/refresh`, {}); const value = await request(`/generation/videos/${current.production_id}`); if (!cancelled) setCurrent(value); } catch (err) { if (!cancelled) setError(`${err.message} Safe retrieval can be refreshed; no paid submission is repeated.`); } finally { refreshing = false; } }, 5000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [current?.production_id, current?.status, uncertain, request]);
  async function act(action) { if (busy) return; setBusy(true); setError(''); try { await action(); } catch (err) { setError(err.message); } finally { setBusy(false); } }
  function setAuthorization(value, kind, productionId) { setQuote({ ...value, kind, productionId }); setConfirmed(false); }
  async function submit() {
    if (!confirmed || !quote || uncertain || busy) return;
    setBusy(true); setError(''); setUncertain(true);
    try {
      const response = await post(request, quote.kind === 'create' ? '/generation/videos' : `/generation/videos/${quote.productionId}/shots`, { quote_id: quote.quote_id, confirmed: true });
      if (!/^[a-f0-9-]{36}$/i.test(response.production_id || '')) throw new Error('No production acknowledgement was received.');
      const created = await request(`/generation/videos/${response.production_id}`); setUncertain(false); setQuote(null); setConfirmed(false); await load(); setCurrent(created);
    } catch (err) { setError(`${err.message} Inspect production history and provider usage before any new paid request. No retry was sent.`); }
    finally { setBusy(false); }
  }
  async function refresh() { if (current) setCurrent(await request(`/generation/videos/${current.production_id}`)); }
  const selectedPlan = scripts.find(item => item.attempt_id === selectedScript);
  const config = selectedPlan?.configuration || {};
  const local = config.video_provider === 'local_cinematic';
  const wan = local && config.video_model === 'wan2.2-animate-2';
  useEffect(() => { if (wan) request('/generation/driving-references').then(value => setDriving(value.references || [])).catch(err => setError(err.message)); }, [wan, request]);
  async function saveMotion() {
    if (!motion || !motionAllowed || motion.size > 20 * 1024 * 1024) throw new Error('Choose a permitted motion MP4 no larger than 20MB.');
    const encoded = await new Promise((resolve,reject) => { const reader = new FileReader(); reader.onload=()=>resolve(reader.result.split(',')[1]); reader.onerror=()=>reject(new Error('Could not read driving video.')); reader.readAsDataURL(motion); });
    await post(request,'/generation/driving-references',{name:motionName,rights_note:motionRights,rights_confirmed:true,video_base64:encoded});
    setDriving((await request('/generation/driving-references')).references || []);
  }
  const draftProviderName = local ? 'Local cinematic' : config.video_provider === 'runway' ? 'Runway' : 'Gemini';
  const providerName = quote?.provider_label || draftProviderName;
  const imageInput = ['runway','local_cinematic'].includes(config.video_provider) && !wan && config.video_input_mode === 'image';
  const localQuote = quote?.paid_request === false;
  const submitRequirement = uncertain
    ? 'A previous submission has an unknown outcome. Inspect production history and provider usage before requesting a new quote.'
    : busy
      ? 'A request is in progress. Wait for it to finish.'
      : !confirmed
        ? localQuote ? 'Check “Confirm local GPU work” above to enable Start local cinematic generation.' : `Check “Authorize ${providerName} video cost” above to enable this button. Script-cost authorization is separate.`
        : localQuote ? 'Local GPU work confirmed. Generation starts only when you click Start local cinematic generation.' : 'Video cost authorized. Generation starts only when you click “Confirm paid video generation”.';
  return <section className="generation-section cloud-production"><div className="generation-section-heading"><span>▶</span><div><h2>Cloud video production / local cinematic</h2><p>Completed script → planned moving shots → local FFmpeg assembly → human quality check → manual review.</p></div></div>
    <p>Generate a script with Google, Runway or Local cinematic selected. The saved model and shot plan are reused here. Select start frames for image-to-video; character portraits and scene start frames serve different purposes. Runway long drama supports 2–20 minutes with a shared cast; inspect every scene for continuity.</p>
    <p><a href="https://runwayml.com" target="_blank" rel="noreferrer">Powered by Runway</a> · Runway model choice is manual; no automatic model substitution.</p>
    <button type="button" className="button subtle" disabled={busy} onClick={() => act(async () => setCredits(await request('/generation/runway/credits')))}>Check Runway credits</button>{credits && <p>Runway available credits: {credits.available_credits ?? 'unavailable'} (current snapshot).</p>}
    <CharacterRegistry request={request} references={references} reload={load} />
    <label htmlFor="cloud-script">Completed script with a shot plan</label><select id="cloud-script" value={selectedScript} onChange={event => setSelectedScript(event.target.value)}><option value="">Choose a completed script</option>{scripts.map(script => <option key={script.attempt_id} value={script.attempt_id}>{script.production_plan.content_plan.title} · {script.configuration?.video_provider || 'google'} / {script.configuration?.video_model || 'saved model'} · {script.attempt_id.slice(0, 8)}</option>)}</select>
    <fieldset className="generation-fieldset"><legend>References to send with these shots (maximum 3)</legend>{references.map(reference => <label className="checkbox-label" key={reference.reference_id}><input type="checkbox" checked={referenceIds.includes(reference.reference_id)} disabled={referenceIds.length >= 3 && !referenceIds.includes(reference.reference_id)} onChange={event => setReferenceIds(previous => event.target.checked ? [...previous, reference.reference_id] : previous.filter(id => id !== reference.reference_id))} /><span>{reference.name} · {reference.character_id}</span></label>)}</fieldset>
    {imageInput && <fieldset className="generation-fieldset"><legend>Scene start frames</legend><p>Select a composed starting scene for each shot. A shared frame is an explicit choice to start multiple shots from the same image. Images are sent only after paid confirmation.</p>
      <label htmlFor="cloud-start-frame">Shared start frame (optional)</label><select id="cloud-start-frame" value={startFrame} onChange={event => setStartFrame(event.target.value)}><option value="">Select per shot below</option>{references.map(ref => <option key={ref.reference_id} value={ref.reference_id}>{ref.name}</option>)}</select>
      {selectedPlan.production_plan.shots.map(shot => <label key={shot.shot_number}>Shot {shot.shot_number}: {shot.action}<select aria-label={`Start frame for shot ${shot.shot_number}`} value={shotFrames[shot.shot_number] || ''} onChange={event => setShotFrames(previous => ({...previous, [shot.shot_number]: event.target.value}))}><option value="">Use shared frame</option>{references.map(ref => <option key={ref.reference_id} value={ref.reference_id}>{ref.name}</option>)}</select></label>)}
    </fieldset>}
    {wan && <fieldset className="generation-fieldset"><legend>Driving motion · local only</legend><p>Use permitted 3–10-second motion clips. Each Wan shot animates one reference character; choose its identity image above and motion below.</p>
      <label>Motion clip name<input value={motionName} maxLength={80} onChange={e=>setMotionName(e.target.value)} /></label>
      <label>Motion source / rights note<input value={motionRights} maxLength={500} onChange={e=>setMotionRights(e.target.value)} /></label>
      <label>Driving MP4 (20MB maximum)<input type="file" accept="video/mp4" onChange={e=>setMotion(e.target.files[0])} /></label>
      <label className="checkbox-label"><input type="checkbox" checked={motionAllowed} onChange={e=>setMotionAllowed(e.target.checked)} /><span>I have permission to animate from this video and any depicted person.</span></label>
      <button type="button" className="button subtle" disabled={busy || !motionAllowed || !motion || !motionName || !motionRights} onClick={()=>act(saveMotion)}>Save driving motion locally</button>
      {selectedPlan.production_plan.shots.map(shot=><label key={shot.shot_number}>Driving video for shot {shot.shot_number}<select value={drivers[shot.shot_number] || ''} onChange={e=>setDrivers(previous=>({...previous,[shot.shot_number]:e.target.value}))}><option value="">Select motion</option>{driving.map(item=><option key={item.reference_id} value={item.reference_id}>{item.name}</option>)}</select></label>)}
    </fieldset>}
    <div className="generation-actions"><button type="button" className="button primary" disabled={!selectedScript || busy || uncertain} onClick={() => act(async () => setAuthorization(await post(request, '/generation/video-quotes', { script_attempt_id: selectedScript, reference_ids: referenceIds, ...(local ? {shot_driving_video_ids:Object.fromEntries(Object.entries(drivers).filter(([,id])=>id))} : {}), ...(imageInput ? { start_image_reference_id: startFrame || null, shot_image_reference_ids: Object.fromEntries(Object.entries(shotFrames).filter(([, id]) => id)) } : {}) }), 'create'))}>Estimate {draftProviderName} video cost</button><button type="button" className="button subtle" onClick={() => act(load)}>Refresh production history</button></div>
    {quote && <div className="generation-paid-authorization"><h3>{localQuote ? 'Review local GPU request' : 'Review paid video request'}</h3><p>Model: <strong>{quote.model || 'saved model'}</strong></p><p>Estimated request: <strong>${quote.estimate_usd}</strong> · {quote.shot_count} shot(s) · production reserved estimate after this request: ${quote.projected_estimate_usd}.</p><p>{quote.request_summary?.operation} {quote.request_summary?.shot_number ? `shot ${quote.request_summary.shot_number}` : "shot batch"}{quote.request_summary?.instructions ? `: ${quote.request_summary.instructions}` : ""}</p>{quote.request_summary?.source && <p>Source {quote.request_summary.source.attempt_id} · start {quote.request_summary.source.trim_start_seconds}s · source length {quote.request_summary.source.duration_seconds}s · generated length {quote.request_summary.durations?.join(', ')}s · candidate length {quote.request_summary.candidate_duration}s.</p>}<p>{quote.notice} Quote expires in ten minutes. Review the selected script and references before confirming.</p>
      {quote.estimated_credits != null && <p>Estimated credits: <strong>{quote.estimated_credits}</strong>. Available at quote time: {quote.available_credits ?? 'unavailable'}. <a href="https://docs.dev.runwayml.com/guides/pricing/" target="_blank" rel="noreferrer">Runway pricing</a></p>}
      <div className="generation-video-consent"><label className="checkbox-label" htmlFor="cloud-video-cost-consent"><input id="cloud-video-cost-consent" type="checkbox" checked={confirmed} disabled={busy || uncertain} onChange={event => setConfirmed(event.target.checked)} /><span><strong>{localQuote ? 'Confirm local GPU work' : `Authorize ${providerName} video cost`}</strong><span>{localQuote ? 'I confirm local generation using these models and selected references. No provider video fee applies.' : `I authorize these ${providerName} requests and transfer of the selected material at the displayed estimated cost.`}</span></span></label></div>
      <p id="cloud-video-submit-requirement" className="generation-submit-requirement" role="status">{submitRequirement}</p>
      <button type="button" className="button primary" aria-describedby="cloud-video-submit-requirement" disabled={!confirmed || busy || uncertain} onClick={submit}>{localQuote ? 'Start local cinematic generation' : 'Confirm paid video generation'}</button></div>}
    {uncertain && <div role="alert"><p>Submission outcome is unknown. Inspect history and provider usage; an automatic retry will never be sent.</p><label className="checkbox-label"><input type="checkbox" checked={inspected} onChange={event => setInspected(event.target.checked)} /><span>I inspected production history and provider usage.</span></label><button type="button" className="button subtle" disabled={!inspected} onClick={() => { setUncertain(false); setQuote(null); setConfirmed(false); setInspected(false); }}>Allow a separately quoted request</button></div>}
    {error && <p role="alert">{error}</p>}
    {!!productions.length && <label>Open an existing production<select aria-label="Existing production" value={current?.production_id || ''} onChange={event => { setCurrent(productions.find(item => item.production_id === event.target.value)); setChecks([false,false,false]); setQuote(null); setConfirmed(false); }}><option value="">Choose a production</option>{productions.map(value => <option key={value.production_id} value={value.production_id}>{value.production_plan.content_plan.title} · {value.status.replaceAll('_', ' ')} · {value.production_id.slice(0,8)}</option>)}</select></label>}
    {current && <div className="cloud-production-current"><h3>{current.production_plan.content_plan.title}</h3><p>Production status: <strong>{current.status.replaceAll('_',' ')}</strong>. Job {current.job_id}. Factual label: {current.production_plan.factual_status}.</p>
      {current.error && <p role="alert">{current.error}</p>}
      <button type="button" className="button subtle" disabled={busy} onClick={() => act(async () => { await post(request, `/generation/videos/${current.production_id}/refresh`, {}); await refresh(); })}>Retrieve existing interactions & refresh</button>
      {current.shots.some(item => item.status === 'queued') && <div><label className="checkbox-label"><input type="checkbox" checked={resumeConfirmed} onChange={event => setResumeConfirmed(event.target.checked)} /><span>Continue only previously authorized, never-submitted shots after inspecting this batch.</span></label><button type="button" className="button subtle" disabled={!resumeConfirmed || busy} onClick={() => act(async () => { await post(request, `/generation/videos/${current.production_id}/continue`, { confirmed:true }); setResumeConfirmed(false); await refresh(); })}>Continue unsubmitted authorized shots</button><button type="button" className="button subtle" disabled={busy} onClick={() => act(async () => { setCurrent(await post(request, `/generation/videos/${current.production_id}/discard-queued`, { confirmed:true })); setResumeConfirmed(false); })}>Discard never-submitted shots</button></div>}
      {current.production_plan.content_plan.scenes?.length > 12 && <label>Scene to inspect<select value={sceneFilter} onChange={event=>setSceneFilter(event.target.value)}><option value="all">All scenes</option>{current.production_plan.content_plan.scenes.map(scene=><option key={scene.scene_number} value={String(scene.scene_number)}>Scene {scene.scene_number}</option>)}</select></label>}
      <div className="cloud-shots">{current.shots.filter(shot=>sceneFilter==='all' || String(shot.scene_number)===sceneFilter).map(shot => <ShotCard key={shot.attempt_id} shot={shot} production={current} request={request} refresh={refresh} onQuote={setAuthorization} editModels={editModels} disabled={busy || active(current) || uncertain || current.status === 'pending_review'} />)}</div>
      {current.status === 'technical_ready' && <div className="generation-paid-authorization"><h3>Check the assembled video</h3><video controls preload="metadata" src={`/api/v1/jobs/${current.job_id}/video`} aria-label="Assembled video quality preview" /><p>Caption times are estimated phrase timings, not speech-forced alignment. Inspect every selected shot, voice and factual claim before review.</p>{['I checked character appearance, clothing, actions and voice continuity.','I checked spoken words, caption timing, readability and audio synchronization.','I checked factual claims, fiction/satire labeling, reference rights and provider media terms.'].map((text,index) => <label className="checkbox-label" key={text}><input type="checkbox" checked={checks[index]} onChange={event => setChecks(previous => previous.map((value,i) => i === index ? event.target.checked : value))} /><span>{text}</span></label>)}<button type="button" className="button primary" disabled={!checks.every(Boolean) || busy} onClick={() => act(async () => { setCurrent(await post(request, `/generation/videos/${current.production_id}/review`, { continuity_confirmed:checks[0], captions_confirmed:checks[1], rights_confirmed:checks[2] })); setChecks([false,false,false]); })}>Send video to manual review</button></div>}
      <details><summary>Generation events</summary>{current.events?.map(event => <p key={event.event_id}>{event.event.replaceAll('_',' ')} · {event.message}</p>)}</details>
    </div>}
  </section>;
}
