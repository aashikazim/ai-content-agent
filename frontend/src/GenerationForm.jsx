import React, { useEffect, useState } from 'react';
import './GenerationForm.css';
import ScriptWorkshop, { PaidScriptAuthorization } from './ScriptWorkshop.jsx';
import CloudProduction from './CloudProduction.jsx';

const categories = ['science', 'education', 'entertainment', 'gaming', 'news', 'sports', 'music', 'mixed'];
const titleCase = value => value.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase());
const draftConfig = draft => draft.configuration || draft.config;

function Symbol({ children = '✳' }) {
  return <span className="generation-symbol" aria-hidden="true">{children}</span>;
}

function Message({ children, error = false }) {
  return <div className={`generation-message ${error ? 'generation-message-error' : ''}`} role={error ? 'alert' : 'status'}>{children}</div>;
}

function Field({ name, label, hint, children }) {
  return <div className="generation-field"><label htmlFor={`create-${name}`}>{label}</label>{children}{hint && <p className="generation-hint">{hint}</p>}</div>;
}

function Section({ number, title, description, children }) {
  return <section className="generation-section"><div className="generation-section-heading"><span>{number}</span><div><h2>{title}</h2><p>{description}</p></div></div>{children}</section>;
}

function ProviderInfo({ provider, model }) {
  if (!provider) return null;
  const local = provider.id === 'local' || provider.id === 'ollama';
  return <div className={`generation-provider-info ${provider.implemented ? '' : 'provider-planned'}`}>
    <span className={`generation-badge ${provider.implemented ? 'available' : ''}`}>{provider.implemented ? 'Adapter implemented' : 'Integration planned'}</span>
    {!local && <strong>{provider.configured ? `Credential configured · ${provider.implemented ? 'account access unverified' : 'integration planned'}` : 'Credential not configured'}</strong>}
    <p>{provider.reason || (local ? 'Uses your configured local service. Runtime availability is checked when generation starts.' : 'This provider integration is planned.')}</p>
    {!local && <p>Credential presence does not confirm model access. Paid scripts and cloud video each need a separate cost quote and confirmation.</p>}
    {model?.verified_on && <small>Catalogue checked {model.verified_on}{model.source_url && <> · <a href={model.source_url} target="_blank" rel="noreferrer">Model documentation ↗</a></>}</small>}
    {model?.clip_duration_seconds && <small>Native clips: {model.durations ? model.durations.join(' or ') : `${model.clip_duration_seconds.min}–${model.clip_duration_seconds.max}`} seconds. Longer productions use planned shots and local assembly.</small>}
    {model?.fps && <small>{model.normalizes_fps ? 'Final FFmpeg frame rate' : 'Native frame rate'}: {model.fps.join(' / ')} fps. Aspect ratios: {model.aspect_ratios?.join(', ')}.</small>}
  </div>;
}

function plannedReasons(configuration, catalog) {
  const reasons = [];
  if (['google', 'runway'].includes(configuration.video_provider) && catalog.phase >= 3) return ['Use the script workshop and cloud production panel below; cloud video requires its own quote and confirmation.'];
  if (configuration.video_provider === 'kling' && catalog.phase >= 3) return ['Kling is retired. Explicitly select Google or the local pipeline.'];
  const type = catalog.content_types.find(item => item.id === configuration.content_type);
  if (!type?.executable) reasons.push(`${type?.label || 'This content type'} is planned for a later phase.`);
  for (const kind of ['script', 'video']) {
    const provider = catalog[`${kind}_providers`].find(item => item.id === configuration[`${kind}_provider`]);
    const model = provider?.models.find(item => item.id === configuration[`${kind}_model`]);
    if (!provider?.implemented || !model || model.implemented === false) reasons.push(`${provider?.label || titleCase(kind)} integration is planned.`);
  }
  if (configuration.script_provider === 'openai' && !catalog.script_providers.find(item => item.id === 'openai')?.configured) reasons.push('Configure OPENAI_API_KEY on the server to use OpenAI scripts.');
  if (configuration.duration_seconds < 30 || configuration.duration_seconds > 60) reasons.push('The local pipeline currently supports 30–60 seconds.');
  if (configuration.narration !== 'local_tts') reasons.push('Model-generated audio is planned for a later phase.');
  if (configuration.captions !== 'scene_text') reasons.push('Timed captions are planned for a later phase.');
  if (configuration.require_motion) reasons.push('The current local pipeline produces illustrated scenes, not generated motion footage.');
  if (configuration.character_reference_ids?.length) reasons.push('Character references are planned for a later phase.');
  return reasons;
}

export default function GenerationForm({ request, onCreated }) {
  const [catalog, setCatalog] = useState(null);
  const [configuration, setConfiguration] = useState(null);
  const [drafts, setDrafts] = useState([]);
  const [selectedDraft, setSelectedDraft] = useState('');
  const [category, setCategory] = useState('science');
  const [region, setRegion] = useState('US');
  const [limit, setLimit] = useState(5);
  const [busy, setBusy] = useState('');
  const [loadError, setLoadError] = useState('');
  const [draftError, setDraftError] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [preflight, setPreflight] = useState(null);
  const [jobAttempted, setJobAttempted] = useState(false);
  const [reload, setReload] = useState(0);
  const [videoAuthorization, setVideoAuthorization] = useState(null);

  useEffect(() => {
    let active = true;
    setLoadError('');
    Promise.allSettled([request('/generation/catalog'), request('/generation/configurations')]).then(([catalogResult, draftResult]) => {
      if (!active) return;
      if (catalogResult.status === 'fulfilled') {
        setCatalog(catalogResult.value);
        setConfiguration(catalogResult.value.defaults);
      } else setLoadError(catalogResult.reason.message);
      if (draftResult.status === 'fulfilled') {
        setDrafts(draftResult.value.configurations || []);
        setDraftError('');
      } else setDraftError(`Saved settings could not be loaded. ${draftResult.reason.message}`);
    });
    return () => { active = false; };
  }, [request, reload]);

  function change(values) {
    setVideoAuthorization(null);
    setConfiguration(current => ({ ...current, ...values }));
    setPreflight(null);
    setError('');
    setMessage('');
    // A submitted request stays locked until the administrator inspects the workspace.
    // Changing settings must not accidentally repeat a request with an unknown outcome.
  }

  function changeProvider(kind, id) {
    if (configuration[`${kind}_provider`] === id) return;
    const provider = catalog[`${kind}_providers`].find(item => item.id === id);
    const model = provider.models[0];
    const values = { [`${kind}_provider`]: id, [`${kind}_model`]: model.id };
    if (kind === 'video') { values.video_input_mode = id === 'runway' ? model.input_modes?.[0] || 'text' : 'text'; values.start_image_reference_id = null; values.shot_image_reference_ids = {}; }
    if (kind === 'video' && model.fps?.length === 1) values.fps = model.fps[0];
    change(values);
    if (values.fps && values.fps !== configuration.fps) setMessage(`${provider.label} uses ${values.fps} fps. The frame rate has been set to its native output.`);
  }

  function loadDraft() {
    const draft = drafts.find(item => item.config_id === selectedDraft);
    if (!draft || !draftConfig(draft)) return;
    change(draftConfig(draft));
    setMessage('Saved settings loaded. Check the configuration before creating a video.');
  }

  async function saveDraft() {
    if (busy) return;
    setBusy('save');
    setError('');
    setMessage('');
    try {
      const result = await request('/generation/configurations', { method: 'POST', body: JSON.stringify({ configuration }) });
      setDrafts(current => [result, ...current].slice(0, 20));
      setSelectedDraft(result.config_id);
      setMessage('Settings saved locally. No generation has started.');
    } catch (err) { setError(err.message); }
    finally { setBusy(''); }
  }

  async function checkConfiguration() {
    const result = await request('/generation/preflight', { method: 'POST', body: JSON.stringify({ configuration }) });
    setPreflight(result);
    return result;
  }

  async function check() {
    if (busy) return;
    setBusy('check');
    setError('');
    setMessage('');
    try { await checkConfiguration(); }
    catch (err) { setError(err.message); }
    finally { setBusy(''); }
  }

  async function create(event) {
    event.preventDefault();
    if (busy || jobAttempted) return;
    if (configuration.script_provider === 'openai' && !videoAuthorization) { setError('Review and confirm the OpenAI script cost before creating this video.'); return; }
    setBusy('create');
    setError('');
    setMessage('');
    let submitted = false;
    try {
      const result = await checkConfiguration();
      if (!result.executable) return;
      submitted = true;
      setJobAttempted(true);
      const job = await request('/jobs', {
        method: 'POST',
        body: JSON.stringify({ category, region: region.toUpperCase(), limit: Number(limit), image_mode: configuration.image_mode, generation_config: result.configuration || configuration,
          ...(videoAuthorization ? { script_quote_id: videoAuthorization.quote_id, script_cost_confirmed: true } : {}) })
      });
      setMessage(`Generation requested${job.job_id ? ` · ${job.job_id}` : ''}. Follow its progress in the overview. Review and publishing remain separate manual actions.`);
      await onCreated?.(job);
    } catch (err) {
      setError(submitted ? `${err.message} Check the overview before requesting another video; the job may already exist.` : err.message);
    } finally { setBusy(''); }
  }

  if (loadError) return <div className="generation-load"><Message error>{loadError}</Message><button className="button subtle" onClick={() => setReload(value => value + 1)}>Reload generation settings</button></div>;
  if (!catalog || !configuration) return <div className="loading-panel"><div className="spinner" /><p>Loading generation settings…</p></div>;

  const scriptProvider = catalog.script_providers.find(item => item.id === configuration.script_provider);
  const videoProvider = catalog.video_providers.find(item => item.id === configuration.video_provider);
  const scriptModel = scriptProvider?.models.find(item => item.id === configuration.script_model);
  const videoModel = videoProvider?.models.find(item => item.id === configuration.video_model);
  const blockers = plannedReasons(configuration, catalog);
  const contentType = catalog.content_types.find(item => item.id === configuration.content_type);
  const isCloud = configuration.script_provider !== 'ollama' || configuration.video_provider !== 'local';

  return <div className="generation-workspace">
    <div className="generation-intro"><Symbol /><div><strong>A clear plan before the first frame.</strong><p>Choose script and visual models independently. Local video and short-form script drafts are available. OpenAI scripts and cloud video require separate cost authorization. Final assembly runs locally.</p></div><span className="generation-badge available">PHASE {catalog.phase}</span></div>
    <form onSubmit={create}>
      <fieldset disabled={Boolean(busy)} className="generation-fieldset">
        <div className="generation-layout">
          <div className="generation-main">
            <Section number="01" title="What are we creating?" description="Choose a format. Short-form scripts are available in the workshop; video support is shown below.">
              <div className="generation-types" role="radiogroup" aria-label="Content type">
                {catalog.content_types.map(type => <label key={type.id} className={`generation-type ${configuration.content_type === type.id ? 'selected' : ''}`}>
                  <input type="radio" name="content-type" value={type.id} checked={configuration.content_type === type.id} onChange={() => change({ content_type: type.id, ...(type.id === 'long_drama' ? {duration_seconds:Math.max(120,configuration.duration_seconds === 45 ? 300 : configuration.duration_seconds),max_shots:Math.max(60,configuration.max_shots)} : {duration_seconds:Math.min(60,configuration.duration_seconds),max_shots:Math.min(12,configuration.max_shots)}) })} />
                  <span><strong>{type.label}</strong><small>{type.executable ? 'Available locally' : type.cloud_executable ? 'cloud video · manual quality checks' : `Planned${type.phase ? ` · Phase ${type.phase}` : ''}`}</small></span><span className="generation-radio" aria-hidden="true" />
                </label>)}
              </div>
              <p className="generation-type-description">{contentType?.description}</p>
              <div className="generation-grid">
                <Field name="source" label="Story source"><select id="create-source" value={configuration.source_mode} onChange={event => change({ source_mode: event.target.value, topic: event.target.value === 'topic' ? configuration.topic : '', supplied_script: event.target.value === 'script' ? configuration.supplied_script : '' })}><option value="trends">Discover trends</option><option value="topic">My topic</option><option value="script">My script</option></select></Field>
                <Field name="category" label="Topic category"><select id="create-category" value={category} onChange={event => setCategory(event.target.value)}>{categories.map(item => <option key={item} value={item}>{titleCase(item)}</option>)}</select></Field>
                <Field name="region" label="Region code"><input id="create-region" value={region} onChange={event => setRegion(event.target.value.toUpperCase())} minLength={2} maxLength={2} pattern="[A-Z]{2}" required /></Field>
                <Field name="limit" label="Trend candidates"><input id="create-limit" type="number" min="1" max="50" value={limit} onChange={event => setLimit(event.target.value)} required /></Field>
              </div>
              {configuration.source_mode === 'topic' && <Field name="topic" label="Your topic" hint="Saved with the configuration. A topic can be used with either script provider."><textarea id="create-topic" rows={3} maxLength={1000} value={configuration.topic} onChange={event => change({ topic: event.target.value })} /></Field>}
              {configuration.source_mode === 'script' && <Field name="script" label="Your script" hint="Saved with the configuration. The selected model adapts this script into the chosen short-form plan."><textarea id="create-script" rows={6} maxLength={20000} value={configuration.supplied_script} onChange={event => change({ supplied_script: event.target.value })} /></Field>}
            </Section>

            <Section number="02" title="The models behind the story" description="Your script model and visual model are separate choices.">
              <div className="generation-model-columns">
                <div>
                  <div className="generation-model-heading"><Symbol>≋</Symbol><h3>Script & storyboard</h3></div>
                  <Field name="script-provider" label="Script provider"><select id="create-script-provider" value={configuration.script_provider} onChange={event => changeProvider('script', event.target.value)}>{catalog.script_providers.map(item => <option key={item.id} value={item.id}>{item.label}{item.implemented ? '' : ' · planned'}</option>)}</select></Field>
                  <Field name="script-model" label="Script model"><select id="create-script-model" value={configuration.script_model} onChange={event => change({ script_model: event.target.value })}>{scriptProvider?.models.map(item => <option key={item.id} value={item.id}>{item.label || item.id}</option>)}</select></Field>
                  <ProviderInfo provider={scriptProvider} model={scriptModel} />
                </div>
                <div>
                  <div className="generation-model-heading"><Symbol>▧</Symbol><h3>Visuals & video</h3></div>
                  <Field name="video-provider" label="Video provider"><select id="create-video-provider" value={configuration.video_provider} onChange={event => changeProvider('video', event.target.value)}>{!catalog.video_providers.some(item => item.id === configuration.video_provider) && <option value={configuration.video_provider} disabled>{configuration.video_provider} · retired selection</option>}{catalog.video_providers.map(item => <option key={item.id} value={item.id}>{item.label}{item.implemented ? '' : ' · planned'}</option>)}</select></Field>
                  <Field name="video-model" label="Video model"><select id="create-video-model" value={configuration.video_model} onChange={event => change({ video_model: event.target.value })}>{!videoProvider && <option value={configuration.video_model} disabled>{configuration.video_model} · retired</option>}{videoProvider?.models.map(item => <option key={item.id} value={item.id}>{item.label || item.id}</option>)}</select></Field>
                  <ProviderInfo provider={videoProvider} model={videoModel} />
                </div>
              </div>
              {configuration.video_provider === 'runway' && <div className="generation-local-assets"><Field name="video-input-mode" label="Runway input mode"><select id="create-video-input-mode" value={configuration.video_input_mode || 'text'} onChange={event => change({video_input_mode:event.target.value, start_image_reference_id:null, shot_image_reference_ids:{}})}><option value="text" disabled={!videoProvider?.models.find(item => item.id === configuration.video_model)?.input_modes?.includes('text')}>Text to video</option><option value="image">Image to video</option></select></Field><p>Choose image-to-video for Gen-4 Turbo, then select scene start frames after completing the script. Gen-4 models and Kling 2.5 use local narration. Audio-capable Kling models can generate dialogue. Native resolution and cost vary by model; FFmpeg normalizes final output.</p></div>}
              {configuration.video_provider === 'local' && <div className="generation-local-assets"><Field name="image-mode" label="Local visual assets"><select id="create-image-mode" value={configuration.image_mode} onChange={event => change({ image_mode: event.target.value })}><option value="text">Text cards · baseline quality</option><option value="diffusion">Generated images · local diffusion</option></select></Field><p>{configuration.image_mode === 'text' ? 'Text cards are explicit baseline output. Choose diffusion for generated scene images; neither option creates motion footage.' : 'Requires the configured image model and compatible GPU inference setup. Missing image assets must fail the job.'}</p>{configuration.image_mode === 'diffusion' && <Field name="image-model" label="Local image model"><select id="create-image-model" value={configuration.image_model} onChange={event => change({ image_model: event.target.value })}>{catalog.image_models?.map(item => <option key={item.id} value={item.id}>{item.label || item.id}</option>)}</select></Field>}</div>}
            </Section>

            <Section number="03" title="Set the frame" description="These settings travel with the job and remain available for review.">
              <div className="generation-grid generation-grid-three">
                <Field name="duration" label="Duration (seconds)" hint="Short form: 30–60 seconds. Runway long drama: 120–1200 seconds."><input id="create-duration" type="number" min="30" max="1200" value={configuration.duration_seconds} onChange={event => change({ duration_seconds: Number(event.target.value) })} required /></Field>
                <Field name="aspect" label="Aspect ratio" hint={videoModel?.aspect_ratios && !videoModel.aspect_ratios.includes(configuration.aspect_ratio) ? 'This model does not support the selected aspect ratio. Choose a native ratio before saving.' : undefined}><select id="create-aspect" value={configuration.aspect_ratio} onChange={event => change({ aspect_ratio: event.target.value })}>{catalog.aspect_ratios.map(item => <option key={item} value={item} disabled={videoModel?.aspect_ratios && !videoModel.aspect_ratios.includes(item)}>{item}{item === '9:16' ? ' · portrait' : item === '16:9' ? ' · landscape' : ' · square'}</option>)}</select></Field>
                <Field name="style" label="Visual style"><select id="create-style" value={configuration.visual_style} onChange={event => change({ visual_style: event.target.value })}>{catalog.styles.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></Field>
                <Field name="resolution" label="Resolution"><select id="create-resolution" value={configuration.resolution} onChange={event => change({ resolution: event.target.value })}>{catalog.resolutions.map(item => <option key={item} value={item}>{item}</option>)}</select></Field>
                <Field name="fps" label="Frame rate"><select id="create-fps" value={configuration.fps} onChange={event => change({ fps: Number(event.target.value) })}>{catalog.fps.map(item => <option key={item} value={item} disabled={videoModel?.fps && !videoModel.fps.includes(item)}>{item} fps</option>)}</select></Field>
                <Field name="shots" label="Maximum scenes"><input id="create-shots" type="number" min="1" max={configuration.content_type === 'long_drama' ? 240 : 12} value={configuration.max_shots} onChange={event => change({ max_shots: Number(event.target.value) })} required /></Field>
                <Field name="narration" label="Narration"><select id="create-narration" value={configuration.narration} onChange={event => change({ narration: event.target.value })}><option value="local_tts">Local TTS · current baseline</option><option value="model_audio">Selected model audio</option></select></Field>
                <Field name="captions" label="Captions"><select id="create-captions" value={configuration.captions} onChange={event => change({ captions: event.target.value })}><option value="scene_text">Scene text · current baseline</option><option value="timed">Timed phrases · cloud assembly</option></select></Field>
                <Field name="budget" label="Maximum budget (USD)" hint="Quotes reserve estimated script and clip costs against this limit. Provider billing remains authoritative."><input id="create-budget" type="number" min="0" max="1000" step="0.01" value={configuration.max_cost_usd} onChange={event => change({ max_cost_usd: event.target.value })} required /></Field>
              </div>
              <label className="generation-motion"><input type="checkbox" checked={configuration.require_motion} onChange={event => change({ require_motion: event.target.checked })} /><span><strong>Require motion footage</strong><small>Cloud clips are checked for motion. Local still-image previews cannot satisfy this requirement.</small></span></label>
              <div className="generation-coming"><span className="generation-badge">Planned</span><p>Cloud references, model audio, captions and shot controls are in the production panel below. Local previews retain their existing features.</p></div>
            </Section>
          </div>

          <aside className="generation-summary" aria-label="Generation summary">
            <div className="generation-summary-top"><span className="eyebrow">YOUR PRODUCTION PLAN</span><Symbol /></div>
            <div className={`generation-frame frame-${configuration.aspect_ratio.replace(':', '-')}`} aria-hidden="true"><span>YOUR NEXT<br /><em>ORIGINAL.</em></span><div className="generation-frame-lines"><i /><i /><i /></div><small>{configuration.aspect_ratio} · {configuration.duration_seconds}s</small></div>
            <h2>{contentType?.label}</h2>
            <dl><div><dt>Script</dt><dd>{scriptModel?.label || configuration.script_model}</dd></div><div><dt>Video</dt><dd>{videoModel?.label || configuration.video_model}</dd></div><div><dt>Output</dt><dd>{configuration.resolution} · {configuration.fps} fps</dd></div><div><dt>Cloud cost</dt><dd>Script quote required for OpenAI</dd></div></dl>
            <div className={`generation-readiness ${blockers.length ? 'planned' : ''}`}><strong><span />{blockers.length ? 'Configuration only' : 'Local configuration'}</strong><p>{['google', 'runway'].includes(configuration.video_provider) && catalog.phase >= 3 ? 'Draft a shot plan in the script workshop, then authorize cloud clips in the production panel below.' : blockers.length ? 'Save these settings for a future integration. Generation is currently unavailable for this combination.' : 'Check your settings, then start a local generation job. Model and service setup must be complete.'}</p></div>
            {blockers.length > 0 && <ul className="generation-blockers">{blockers.map(reason => <li key={reason}>{reason}</li>)}</ul>}
            {isCloud && <p className="generation-cost-note">An API credential never authorizes a charge. Scripts and cloud video need separate quotes and confirmation.</p>}
            {catalog.phase >= 2 && configuration.script_provider === 'openai' && configuration.video_provider === 'local' && <PaidScriptAuthorization request={request} configuration={configuration} context={{ category, region: region.toUpperCase(), limit: Number(limit) }} purpose="video" onChange={setVideoAuthorization} />}
            <div className="generation-actions"><button className="button subtle" type="button" onClick={check} disabled={Boolean(busy)}>{busy === 'check' ? 'Checking…' : 'Check configuration'}</button><button className="button primary" type="submit" disabled={Boolean(busy) || blockers.length > 0 || jobAttempted || (configuration.script_provider === 'openai' && !videoAuthorization)}>{busy === 'create' ? 'Starting…' : jobAttempted ? 'Request submitted' : 'Start local generation'}<span aria-hidden="true">↗</span></button><button className="button generation-save" type="button" onClick={saveDraft} disabled={Boolean(busy)}>{busy === 'save' ? 'Saving…' : 'Save settings'}</button></div>
            <p className="generation-human-note"><span aria-hidden="true">◇</span> Generation creates a draft video. Approval and publishing each require your separate action.</p>
          </aside>
        </div>

        <section className="generation-drafts"><div><h2>Pick up a saved configuration</h2><p>Stored on this machine. Loading settings never starts a job.</p></div><div className="generation-draft-controls"><label htmlFor="create-draft" className="visually-hidden">Saved configuration</label><select id="create-draft" value={selectedDraft} onChange={event => setSelectedDraft(event.target.value)}><option value="">{drafts.length ? 'Choose saved settings' : 'No saved settings yet'}</option>{drafts.map(draft => <option key={draft.config_id} value={draft.config_id}>{titleCase(draftConfig(draft)?.content_type || 'Configuration')} · {draftConfig(draft)?.duration_seconds}s · {draft.config_id.slice(0, 8)}</option>)}</select><button className="button subtle" type="button" disabled={Boolean(busy) || !selectedDraft} onClick={loadDraft}>Load settings</button></div>{draftError && <p className="generation-draft-error">{draftError}</p>}</section>
      </fieldset>
      <div className="generation-feedback" aria-live="polite">
        {preflight && <Message error={!preflight.executable}><strong>{preflight.executable ? 'Configuration check passed.' : 'This configuration cannot run yet.'}</strong>{preflight.errors?.length > 0 && <ul>{preflight.errors.map(item => <li key={item}>{item}</li>)}</ul>}{preflight.warnings?.length > 0 && <ul>{preflight.warnings.map(item => <li key={item}>{item}</li>)}</ul>}<p>No paid request was sent.</p></Message>}
        {error && <Message error>{error}</Message>}
        {message && <Message>{message}</Message>}
      </div>
    </form>
    {catalog.phase >= 2 && <ScriptWorkshop request={request} configuration={configuration} category={category} region={region} limit={limit} />}
    {catalog.phase >= 3 && <CloudProduction editModels={catalog.runway_edit_models || []} request={request} />}
  </div>;
}
