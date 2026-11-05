import React, { useEffect, useRef, useState } from 'react';
import ChapterRepair from './ChapterRepair.jsx';

export function PaidScriptAuthorization({ request, configuration, context, purpose, onChange, disabled = false }) {
  const [quote, setQuote] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const callback = useRef(onChange);
  callback.current = onChange;
  const fingerprint = JSON.stringify([configuration, context, purpose]);
  useEffect(() => { setQuote(null); setConfirmed(false); setError(''); callback.current?.(null); }, [fingerprint]);
  async function estimate() {
    if (busy || disabled) return;
    setBusy(true); setError(''); setQuote(null); setConfirmed(false); callback.current?.(null);
    try { setQuote(await request('/generation/script-quotes', { method: 'POST', body: JSON.stringify({ configuration, ...context, purpose }) })); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }
  return <div className="generation-paid-authorization">
    <h3>Authorize screenplay planning</h3>
    <p>{configuration.script_provider === 'anthropic' ? 'Claude' : 'OpenAI'} receives the supplied topic, script and revision context. Text generation only; video generation requires a separate confirmation. No automatic retries.</p>
    <button className="button subtle" type="button" onClick={estimate} disabled={busy || disabled}>{busy ? 'Checking access…' : 'Check access & estimate script cost'}</button>
    {error && <p role="alert">{error}</p>}
    {quote?.paid && <><p><strong>Maximum estimated token cost: ${quote.maximum_cost_usd}</strong></p>{quote.max_requests > 1 && <p>Authorize up to {quote.max_requests} chapter requests, with shared cast and prior-chapter context. Each chapter is saved; uncertain requests are never retried.</p>}<p>{quote.notice} Quote expires in ten minutes. Provider billing remains authoritative.</p>
      <label className="checkbox-label"><input type="checkbox" checked={confirmed} onChange={event => { setConfirmed(event.target.checked); callback.current?.(event.target.checked ? { quote_id: quote.quote_id, confirmed: true } : null); }} />
        <span>I authorize {quote.max_requests > 1 ? `up to ${quote.max_requests} chapter requests using` : 'one'} {configuration.script_model} {quote.max_requests > 1 ? 'for this film' : 'script request'} up to the displayed estimated token bound.</span></label></>}
  </div>;
}

export default function ScriptWorkshop({ configuration, request, category, region, limit, scope = 'all', onCompleted, disabled = false }) {
  const [authorization, setAuthorization] = useState(null);
  const [attempts, setAttempts] = useState([]);
  const [current, setCurrent] = useState(null);
  const [parent, setParent] = useState('');
  const [revision, setRevision] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [access, setAccess] = useState(null);
  const [uncertain, setUncertain] = useState(false);
  const [inspected, setInspected] = useState(false);
  const [reconciliation, setReconciliation] = useState('');
  const [reconcileConfirmed, setReconcileConfirmed] = useState(false);
  const [authorizationVersion, setAuthorizationVersion] = useState(0);
  const isCloud = ['openai', 'anthropic'].includes(configuration.script_provider);
  const missingStory = disabled || (configuration.source_mode === 'topic' && !configuration.topic?.trim()) || (configuration.source_mode === 'script' && !configuration.supplied_script?.trim());
  const inScope = item => scope === 'all' || (scope === 'local' ? item.configuration?.video_provider === 'local_cinematic' : item.configuration?.video_provider !== 'local_cinematic');
  const completedCallback = useRef(onCompleted); completedCallback.current = onCompleted;
  useEffect(() => { if (current?.status === 'completed') completedCallback.current?.(current); }, [current?.attempt_id, current?.status]);
  const context = { category, region: region.toUpperCase(), limit: Number(limit), parent_attempt_id: parent || null, revision_instructions: revision };
  const fingerprint = JSON.stringify([configuration, context]);
  useEffect(() => { setAuthorization(null); }, [fingerprint]);
  useEffect(() => { let active = true; request('/generation/scripts').then(data => { if (active) setAttempts((data.attempts || []).filter(inScope)); }).catch(() => {}); return () => { active = false; }; }, [request, scope]);
  useEffect(() => {
    if (!current?.attempt_id || !['queued', 'running'].includes(current.status)) return;
    let active = true;
    const poll = setInterval(async () => {
      try { const result = await request(`/generation/scripts/${current.attempt_id}`); if (active) { setCurrent(result); setAttempts(previous => [result, ...previous.filter(item => item.attempt_id !== result.attempt_id)]); } }
      catch (err) { if (active) setError(`${err.message} Inspect script history before another request.`); }
    }, 2000);
    return () => { active = false; clearInterval(poll); };
  }, [current?.attempt_id, current?.status, request]);
  async function generate() {
    if (busy || uncertain || missingStory || (isCloud && !authorization)) return;
    setBusy(true); setError(''); setUncertain(true);
    try {
      const result = await request('/generation/scripts', { method: 'POST', body: JSON.stringify({ configuration, ...context, purpose: 'script', ...(authorization || {}) }) });
      if (!/^[0-9a-f-]{36}$/i.test(result.attempt_id || '')) throw new Error('The server did not acknowledge a script attempt ID.');
      setCurrent(result); setAuthorization(null); setAuthorizationVersion(value => value + 1); setUncertain(false);
    } catch (err) { setError(`${err.message} Inspect script history before a new request; no retry is sent automatically.`); }
    finally { setBusy(false); }
  }
  async function inspect() { try { setAttempts(((await request('/generation/scripts')).attempts || []).filter(inScope)); } catch (err) { setError(err.message); } }
  const running = ['queued', 'running'].includes(current?.status);
  return <section className="generation-section script-workshop"><div className="generation-section-heading"><span>✎</span><div><h2>Script workshop</h2><p>Draft outlines, narration and character dialogue before producing video. Moving-video drafts include camera/action shot plans; news drafts remain unverified.</p></div></div>
    <p>{configuration.content_type === 'long_drama' ? '120–1800-second local fictional drama (cloud limits vary) with a shared cast and complete scene/shot timing. Large scripts can take up to 15 minutes; local Ollama needs adequate model context.' : '30–60-second script drafts.'} This never approves or publishes a video. Use the production panel for reference images and moving clips.</p>
    <label htmlFor="script-parent">Revise a completed draft</label><select id="script-parent" value={parent} onChange={event => setParent(event.target.value)} disabled={busy || running}><option value="">New script</option>{attempts.filter(item => item.status === 'completed' && !item.film_parent_attempt_id).map(item => <option key={item.attempt_id} value={item.attempt_id}>{item.production_plan?.content_plan?.title || item.attempt_id.slice(0, 8)}</option>)}</select>
    <label htmlFor="script-revision">Revision instructions (optional)</label><textarea id="script-revision" rows={3} maxLength={2000} value={revision} onChange={event => setRevision(event.target.value)} disabled={busy || running} />
    {isCloud && <>{configuration.script_provider === 'openai' && <><button className="button subtle" type="button" onClick={async () => { setError(''); try { setAccess((await request('/generation/openai-models')).models); } catch (err) { setError(err.message); } }}>Check available OpenAI models</button>
      {access && <p>Available supported models: {access.join(', ') || 'None. Check project permissions.'} Choose a model explicitly above; it never switches automatically.</p>}</>}
      <PaidScriptAuthorization disabled={missingStory} key={authorizationVersion} request={request} configuration={configuration} context={context} purpose="script" onChange={setAuthorization} /></>}
    {missingStory && <p>{disabled ? 'Choose a confirmed cast package before drafting with character continuity.' : 'Enter your story above before requesting a screenplay.'}</p>}
    <div className="generation-actions"><button className="button primary" type="button" disabled={busy || running || uncertain || missingStory || (isCloud && !authorization)} onClick={generate}>{busy ? 'Submitting…' : running ? 'Writing script…' : 'Generate script draft'}</button><button className="button subtle" type="button" onClick={inspect}>Refresh script history</button></div>
    {uncertain && <><p role="alert">Request outcome is unknown. Inspect history and provider usage before another request.</p><label className="checkbox-label"><input type="checkbox" checked={inspected} onChange={event => setInspected(event.target.checked)} /><span>I inspected script history and provider usage.</span></label><button type="button" className="button subtle" disabled={!inspected} onClick={() => { setUncertain(false); setAuthorization(null); setInspected(false); }}>Allow a separately authorized new request</button></>}{error && <p role="alert">{error}</p>}
    {current && <div className="generation-script-result"><h3>Script status: {current.status?.replaceAll('_', ' ')}</h3>{current.error && <p role="alert">{current.error}</p>}{current.production_plan && <><h3>{current.production_plan.content_plan.title}</h3><p>Content label: {current.production_plan.factual_status}. Manual factual and rights review required.</p><ol>{current.production_plan.outline.map((item, index) => <li key={index}>{item}</li>)}</ol>{current.production_plan.characters.map(character => <p key={character.character_id}><strong>{character.name} (<code>{character.character_id}</code>):</strong> {character.description} · {character.voice_notes}</p>)}{current.production_plan.content_plan.scenes.map(scene => <article key={scene.scene_number}><h4>Scene {scene.scene_number} · {scene.duration_seconds}s</h4><p>{scene.narration}</p><small>{scene.visual_prompt}</small></article>)}</>}
      {current.production_plan?.shots?.length > 0 && <details><summary>Camera and action shot plan ({current.production_plan.shots.length})</summary>{current.production_plan.shots.map(shot => <article key={shot.shot_number}><h4>Shot {shot.shot_number} · scene {shot.scene_number} · {shot.duration_seconds}s</h4><p>{shot.action}</p><p>Camera: {shot.camera}</p><p>Speech: {shot.spoken_text || 'No speech'}</p><p>Characters: {shot.character_ids.join(', ') || 'None'}</p></article>)}</details>}
      <p>Calculated token cost: {current.calculated_cost_usd == null ? 'Unavailable' : `$${current.calculated_cost_usd}`} · Actual billed cost: {current.actual_cost_usd == null ? 'Not reported by provider' : `$${current.actual_cost_usd}`}</p></div>}
    {current?.parts?.length > 0 && <details open><summary>Chapter planning ({current.parts.filter(part => part.status === 'completed').length}/{current.parts.length})</summary>{current.parts.map(part => <div key={part.part_number}><p>Chapter {part.part_number} · {part.duration}s · {part.status.replaceAll('_',' ')}</p>{['failed','needs_reconciliation','running'].includes(part.status) && ['failed','needs_reconciliation'].includes(current.status) && <ChapterRepair request={request} parent={current} part={part} onRefresh={async()=>setCurrent(await request(`/generation/scripts/${current.attempt_id}`))}/>}</div>)}{['failed','needs_reconciliation'].includes(current.status) && current.parts.every(part => ['queued','completed'].includes(part.status)) && <button type="button" className="button subtle" disabled={busy} onClick={async () => { setBusy(true); try { setCurrent(await request(`/generation/scripts/${current.attempt_id}/continue`,{method:'POST',body:JSON.stringify({confirmed:true})})); } catch(err) { setError(err.message); } finally { setBusy(false); } }}>Continue only never-submitted chapters</button>}</details>}
    {current?.status === 'needs_reconciliation' && !current.parts?.length && <div className="generation-paid-authorization"><label htmlFor="script-reconciliation">Provider usage inspection notes</label><input id="script-reconciliation" maxLength={500} value={reconciliation} onChange={event => setReconciliation(event.target.value)} /><label className="checkbox-label"><input type="checkbox" checked={reconcileConfirmed} onChange={event => setReconcileConfirmed(event.target.checked)} /><span>I checked provider usage and accept any prior charges.</span></label><button type="button" className="button subtle" disabled={!reconcileConfirmed || !reconciliation.trim()} onClick={async () => { try { setCurrent(await request(`/generation/scripts/${current.attempt_id}/reconcile`, { method: 'POST', body: JSON.stringify({ notes: reconciliation, confirmed: true }) })); setReconcileConfirmed(false); await inspect(); } catch (err) { setError(err.message); } }}>Close uncertain request</button></div>}
    {!!attempts.length && <details><summary>Script request history ({attempts.length})</summary>{attempts.map(item => <div className="script-history-row" key={item.attempt_id}><span>{item.model}{item.film_part_number ? ` · chapter ${item.film_part_number} revision` : ''} · {item.status.replaceAll('_', ' ')} · {item.attempt_id.slice(0, 8)}</span><button className="button subtle" type="button" onClick={() => setCurrent(item)}>Inspect script</button></div>)}</details>}
  </section>;
}
