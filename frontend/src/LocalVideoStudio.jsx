import ResearchDossierPicker from './ResearchDossierPicker.jsx';
import SpeechOptions from './SpeechOptions.jsx';
import React, { useEffect, useState } from 'react';
import ScriptWorkshop from './ScriptWorkshop.jsx';
import CloudProduction from './CloudProduction.jsx';
import LocalModelGuide from './LocalModelGuide.jsx';
import CinematicCast from './CinematicCast.jsx';
import './GenerationForm.css';
import './LocalVideoStudio.css';

const EXAMPLE = 'The Missing Lunch. A fictional office comedy. Maya wears a mustard cardigan; Theo wears a blue shirt. In the same bright office kitchen, Maya discovers her lunch is missing. Theo insists he knows nothing while holding her clearly labelled lunchbox. Maya points at it. Theo says, “Evidence can be misleading.” End on Maya’s amused reaction. Keep the same clothing, kitchen and voices throughout. Use six connected five-second shots with brief dialogue and visible actions.';

export function localConfiguration(defaults, providers) {
  return { ...defaults, script_provider: 'anthropic',
    script_model: providers.find(item => item.id === 'anthropic')?.models[0]?.id || 'claude-sonnet-5-5',
    video_provider: 'local_cinematic', video_model: 'ltx-2.5', video_input_mode: 'image',
    content_type: 'short_drama', source_mode: 'topic', topic: '', supplied_script: '',
    duration_seconds: 30, aspect_ratio: '9:16', visual_style: 'cinematic',
    resolution: '720p', fps: 24, narration: 'model_audio', captions: 'timed',
    continuity_mode: 'anchored', cast_package_id: null, scene_image_reference_ids: {},
    max_shots: 6, require_motion: true, paid_generation_confirmed: false,
    character_reference_ids: [], start_image_reference_id: null,
    shot_image_reference_ids: {}, shot_driving_video_ids: {} };
}

export default function LocalVideoStudio({ request }) {
  const [catalog, setCatalog] = useState(null);
  const [configuration, setConfiguration] = useState(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [genre, setGenre] = useState('drama');
  const [completedScript, setCompletedScript] = useState(null);
  useEffect(() => {
    let active = true;
    request('/generation/catalog').then(value => {
      if (active) { setCatalog(value); setConfiguration(localConfiguration(value.defaults, value.script_providers)); setError(''); }
    }).catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [request, reload]);
  function change(values) { setConfiguration(previous => ({ ...previous, ...values })); }
  function setDuration(duration, nextGenre = genre) {
    change({ duration_seconds: duration, ...(duration > 600 ? {max_cost_usd:'25'} : {}), max_shots: Math.ceil(duration / 5),
      content_type: duration >= 120 ? 'long_drama' : nextGenre === 'drama' ? 'short_drama' : 'animated_comedy' });
  }
  if (!configuration) return <div className="generation-message" role={error ? 'alert' : 'status'}>{error || 'Opening Local Video Studio…'}{error && <button type="button" className="button subtle" onClick={() => setReload(value => value + 1)}>Try again</button>}</div>;
  const long = configuration.duration_seconds >= 120;
  const wan = configuration.video_model === 'wan2.2-animate-2';
  const scriptProviders = catalog.script_providers.filter(item => ['anthropic', 'ollama'].includes(item.id));
  const scriptProvider = scriptProviders.find(item => item.id === configuration.script_provider);
  const models = catalog.video_providers.find(item => item.id === 'local_cinematic')?.models || [];
  return <div className="generation-page local-video-studio">
    <div className="local-studio-banner"><span className="local-studio-mark" aria-hidden="true">▶</span><div><strong>Your story. Your local studio.</strong><p>Shorts and longer films with LTX, Wan or hybrid. Enter your own idea, review the screenplay, then generate connected moving shots.</p></div><span className="soft-tag">DGX SPARK</span></div>
    <ol className="local-studio-steps" aria-label="Creation steps"><li>Your idea</li><li>Screenplay</li><li>Characters & shots</li><li>Preview & review</li></ol>
    <section className="generation-section"><div className="generation-section-heading"><span>01</span><div><h2>Tell your story</h2><p>Write your own topic or paste a screenplay. Your story stays under your direction.</p></div></div>
      <div className="generation-grid"><label>Story input<select value={configuration.source_mode} onChange={e => change({ source_mode: e.target.value, topic: '', supplied_script: '' })}><option value="topic">My idea</option><option value="script">My screenplay</option></select></label>
        <label>Style<select value={genre} onChange={e => { const next = e.target.value; setGenre(next); change({ visual_style: next === 'animation' ? '2d_cartoon' : next === 'comedy' ? 'caricature' : 'cinematic' }); setDuration(configuration.duration_seconds, next); }}><option value="drama">Realistic drama</option><option value="animation">Animated story</option><option value="comedy">Comedy / satire</option></select></label></div>
      <label htmlFor="local-story">{configuration.source_mode === 'script' ? 'Your screenplay' : 'Your story idea'}</label><textarea id="local-story" rows={5} maxLength={configuration.source_mode === 'script' ? 20000 : 1000} value={configuration.source_mode === 'script' ? configuration.supplied_script : configuration.topic} onChange={e => change(configuration.source_mode === 'script' ? { supplied_script: e.target.value } : { topic: e.target.value })} placeholder="Who is in the story? Where are they? What happens, and how does it end?" />
      <div className="generation-grid"><label>Video format<select value={long ? 'long' : 'short'} onChange={e => setDuration(e.target.value === 'long' ? 180 : 30)}><option value="short">Short · 30–60 seconds</option><option value="long">Long · 2–30 minutes</option></select></label>
        <label>Target duration<select value={configuration.duration_seconds} onChange={e => setDuration(Number(e.target.value))}>{(long ? [120, 180, 240, 900, 1200, 1800] : [30, 45, 60]).map(seconds => <option key={seconds} value={seconds}>{seconds >= 120 ? `${seconds / 60} minutes` : `${seconds} seconds`}</option>)}</select></label>
        <label>Orientation<select value={configuration.aspect_ratio} onChange={e => change({ aspect_ratio: e.target.value })}><option value="9:16">Portrait · 9:16</option><option value="16:9">Landscape · 16:9</option></select></label>
        <label>Local video model<select value={configuration.video_model} onChange={e => change({ video_model: e.target.value, continuity_mode: e.target.value === 'wan2.2-animate-2' && configuration.continuity_mode === 'connected' ? 'anchored' : configuration.continuity_mode, video_input_mode: e.target.value === 'wan2.2-animate-2' || configuration.continuity_mode !== 'independent' ? 'image' : 'text', narration: e.target.value === 'wan2.2-animate-2' ? 'local_tts' : 'model_audio' })}>{models.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}</select></label></div>
      <label>Character continuity<select value={configuration.continuity_mode} onChange={e => change({continuity_mode:e.target.value, video_input_mode:wan || e.target.value !== 'independent' ? 'image' : 'text'})}><option value="anchored">Same cast with approved scene images</option><option value="connected" disabled={wan}>Same cast with connected action between shots</option><option value="independent">Independent shots · appearance may change</option></select></label>
      <p>{wan && configuration.continuity_mode !== 'independent' ? 'Wan reuses your approved actor image with each driving clip. Connected action carry is available through LTX or the hybrid pipeline.' : configuration.continuity_mode === 'connected' ? 'Continuous-action shots inherit selected frames from the previous shot. Camera cuts start from approved scene images. Replacing an earlier shot marks dependent shots for inspection.' : configuration.continuity_mode === 'anchored' ? 'Every camera cut starts from your approved scene image. A cast portrait helps identity; a scene image also establishes composition, location and clothing.' : 'Each shot starts fresh. Choose a continuity mode above when keeping the same actor matters.'}</p>
      <LocalModelGuide model={configuration.video_model} inputMode={configuration.video_input_mode} continuityMode={configuration.continuity_mode} />
      <details className="local-studio-example"><summary>Example: The Missing Lunch · 30-second comedy</summary><p>{EXAMPLE}</p><p>For consistent characters, upload Maya and Theo images and save a cast package first. Independent mode can run without images but may change faces. For hybrid, upload separate Maya and Theo identity images after drafting, using the screenplay’s exact IDs. Use single-character close-ups for Wan animation.</p><button type="button" className="button subtle" onClick={() => { setConfiguration({ ...localConfiguration(catalog.defaults, catalog.script_providers), topic: EXAMPLE, content_type: 'animated_comedy', visual_style: 'caricature' }); setGenre('comedy'); }}>Load this example</button></details>
      <details className="local-studio-advanced"><summary>Advanced settings</summary><p>Defaults: 720p export, 24 fps, timed captions and roughly five-second shots. Native model dimensions remain at the verified DGX settings; export size does not change inference resolution.</p><div className="generation-grid">
        <label>Script provider<select value={configuration.script_provider} onChange={e => { const provider = scriptProviders.find(item => item.id === e.target.value); change({ script_provider: provider.id, script_model: provider.models[0].id }); }}>{scriptProviders.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
        <label>Export resolution<select value={configuration.resolution} onChange={e => change({ resolution: e.target.value })}><option value="720p">720p</option><option value="1080p">1080p · locally upscaled</option></select></label>
        {!wan && <label>Starting visuals<select value={configuration.video_input_mode} onChange={e => change({ video_input_mode: e.target.value })}><option value="text">Start from text</option><option value="image">Start from images</option></select></label>}
        <SpeechOptions request={request} value={configuration.speech_profile_id} onChange={value=>change({speech_profile_id:value})}/>
        <label>Audio<select value={configuration.narration} onChange={e => change({ narration: e.target.value })}>{!wan && <option value="model_audio">Native model audio</option>}<option value="local_tts">Local narrator</option>{configuration.cast_package_id && <option value="character_tts">Fixed character voices · eSpeak baseline</option>}</select></label>
        <label>Caption timing<select value={configuration.captions} onChange={e=>change({captions:e.target.value})}><option value="timed">Estimated phrases</option><option value="scene_text">Scene text</option><option value="asr">Recognized speech word timing</option><option value="aligned">Forced transcript alignment · scenes up to30s</option></select></label>
        <ResearchDossierPicker request={request} value={configuration.research_dossier_id} onChange={value=>change({research_dossier_id:value})}/>
        <label>Script budget (USD)<input type="number" min="0" max="1000" step="0.01" value={configuration.max_cost_usd} onChange={e => change({ max_cost_usd: e.target.value })} /></label>
      </div></details>
      <p className="generation-human-note">Script: {scriptProvider?.models[0]?.label || configuration.script_model}. {configuration.script_provider === 'anthropic' ? 'Claude receives your story text for paid screenplay planning. Video generation stays local.' : 'Ollama plans the screenplay locally.'}</p>
      {!scriptProvider?.configured && configuration.script_provider === 'anthropic' && <p className="generation-message" role="status">Add ANTHROPIC_API_KEY to your server .env and restart the backend, or choose Local Ollama in Advanced settings.</p>}
    </section>
    {configuration.continuity_mode !== 'independent' && <CinematicCast request={request} value={configuration.cast_package_id} onChange={id => change({cast_package_id:id})} />}
    <ScriptWorkshop disabled={configuration.continuity_mode !== 'independent' && !configuration.cast_package_id} scope="local" configuration={configuration} request={request} category="entertainment" region="US" limit={1} onCompleted={setCompletedScript} />
    <CloudProduction scope="local" scriptAttemptId={completedScript?.attempt_id} request={request} />
  </div>;
}
