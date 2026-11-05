import React, { useEffect, useState } from 'react';
import { CharacterRegistry } from './CloudProduction.jsx';

export default function CinematicCast({ request, value, onChange }) {
  const [packages, setPackages] = useState([]);
  const [references, setReferences] = useState([]);
  const [selected, setSelected] = useState([]);
  const [primary, setPrimary] = useState({});
  const [wardrobe, setWardrobe] = useState({});
  const [voices, setVoices] = useState({});
  const [adapters, setAdapters] = useState([]);
  const [actorAdapters, setActorAdapters] = useState({});
  const [name, setName] = useState('My film cast');
  const [location, setLocation] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function load() {
    const [cast, refs] = await Promise.all([request('/generation/cast-packages'), request('/generation/references')]);
    setPackages(cast.packages || []); setReferences(refs.references || []);
  }
  useEffect(() => { load().catch(err => setError(err.message)); request('/generation/ltx-adapters').then(data=>setAdapters(data.adapters || [])).catch(()=>{}); }, [request]);
  useEffect(() => setConfirmed(false), [JSON.stringify([selected, primary, wardrobe, voices, actorAdapters, name, location])]);
  const grouped = Object.groupBy(references.filter(ref => selected.includes(ref.reference_id)), ref => ref.character_id);
  async function save() {
    if (!confirmed || busy) return;
    setBusy(true); setError('');
    try {
      const characters = Object.entries(grouped).map(([id, refs]) => {
        const ref = refs.find(item => item.reference_id === primary[id]) || refs[0];
        return {character_id:id, name:ref.name, description:ref.description, wardrobe:wardrobe[id] || ref.wardrobe,
          voice_notes:ref.voice_notes || '', tts_voice:voices[id] || 'en-us', ltx_adapter_id:actorAdapters[id] || null, reference_ids:refs.map(item => item.reference_id), primary_reference_id:ref.reference_id};
      });
      const result = await request('/generation/cast-packages', {method:'POST', body:JSON.stringify({name, location_notes:location, characters, confirmed:true})});
      await load(); onChange(result.package_id); setConfirmed(false);
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  const cast = packages.find(item => item.package_id === value);
  return <section className="generation-section cinematic-cast"><div className="generation-section-heading"><span>02</span><div><h2>Keep your cast consistent</h2><p>Choose the same actors, outfits and location before drafting. A cast package stays fixed; create a new package for deliberate changes.</p></div></div>
    <label>Approved cast package<select value={value || ''} onChange={e => onChange(e.target.value || null)}><option value="">Choose a cast package</option>{packages.map(item => <option disabled={item.unavailable} key={item.package_id} value={item.package_id}>{item.name} · {item.characters.map(actor => actor.name).join(', ')}</option>)}</select></label>
    {cast && <div className="cloud-reference-grid">{cast.characters.map(actor => <figure key={actor.character_id}><img src={`/api/v1/generation/references/${actor.primary_reference_id}/image`} alt={actor.name}/><figcaption><strong>{actor.name}</strong> · {actor.character_id}<small>{actor.wardrobe}</small><small>Voice description: {actor.voice_notes || 'Not supplied'} · {actor.reference_ids.length} reference view(s)</small></figcaption></figure>)}</div>}
    <details><summary>Create a cast package</summary><p>Upload clear face, three-quarter and full-body images with the same character ID, such as maya. Select up to six views per actor and at most three actors. Drama needs two or three actors. Keep scene compositions separate; they are assigned after drafting. Voice descriptions guide native audio but do not lock a voice.</p>
      <CharacterRegistry local request={request} references={references} reload={load} />
      <label>Cast package name<input value={name} maxLength={100} onChange={e => setName(e.target.value)}/></label>
      <label>Shared location and lighting<textarea value={location} maxLength={2000} onChange={e => setLocation(e.target.value)} placeholder="Office kitchen: white cupboards, wooden counter, daylight from camera left."/></label>
      <fieldset className="generation-fieldset"><legend>Identity images belonging to this cast</legend>{references.map(ref => <label className="checkbox-label" key={ref.reference_id}><input type="checkbox" checked={selected.includes(ref.reference_id)} onChange={e => setSelected(previous => e.target.checked ? [...previous,ref.reference_id] : previous.filter(id => id !== ref.reference_id))}/><span>{ref.name} · {ref.character_id} · {ref.reference_id.slice(0,8)}</span></label>)}</fieldset>
      {Object.entries(grouped).map(([id, refs]) => <div className="generation-grid" key={id}><label>Primary view for {id}<select value={primary[id] || refs[0].reference_id} onChange={e => setPrimary(previous => ({...previous,[id]:e.target.value}))}>{refs.map(ref => <option key={ref.reference_id} value={ref.reference_id}>{ref.name} · {ref.reference_id.slice(0,8)}</option>)}</select></label><label>Fixed outfit for {id}<input maxLength={500} value={wardrobe[id] ?? refs[0].wardrobe ?? ''} onChange={e => setWardrobe(previous => ({...previous,[id]:e.target.value}))}/></label><label>Fixed local voice for {id}<select value={voices[id] || 'en-us'} onChange={e=>setVoices(previous=>({...previous,[id]:e.target.value}))}><option value="en-us">Neutral</option><option value="en-us+m1">Male 1</option><option value="en-us+m2">Male 2</option><option value="en-us+f1">Female 1</option><option value="en-us+f2">Female 2</option></select></label>{adapters.length > 0 && <label>Optional tested LTX adapter for {id}<select value={actorAdapters[id] || ''} onChange={e=>setActorAdapters(previous=>({...previous,[id]:e.target.value}))}><option value="">No adapter</option>{adapters.map(item=><option key={item.adapter_id} value={item.adapter_id}>{item.name}</option>)}</select></label>}</div>)}
      <p>These fixed voices use the existing eSpeak baseline. They are consistent but synthetic sounding, and do not promise lip sync. Native LTX audio remains available separately. Optional trained character LoRAs can be registered locally after compatibility testing; none are downloaded or trained automatically.</p>
      <label className="checkbox-label"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/><span>I inspected the actors, outfits, views and location and want to freeze this cast package.</span></label><button type="button" className="button primary" disabled={busy || !confirmed || !selected.length || !name.trim() || Object.keys(grouped).length > 3 || Object.values(grouped).some(refs => refs.length > 6)} onClick={save}>Save approved cast package</button>
    </details>{error && <p role="alert">{error}</p>}
  </section>;
}
