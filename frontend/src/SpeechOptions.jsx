import React,{useEffect,useState} from 'react';

export default function SpeechOptions({request,value,onChange}){
  const [profiles,setProfiles]=useState([]);
  useEffect(()=>{let live=true;request('/tools/speech-profiles').then(data=>live&&setProfiles(data.profiles||[])).catch(()=>{});return()=>{live=false;};},[request]);
  return <label>Neural narrator profile<select value={value||''} onChange={e=>onChange(e.target.value||null)}><option value="">Existing eSpeak narrator</option>{profiles.map(p=><option value={p.id} key={p.id}>{p.label}</option>)}</select><small>Applies to local narration. Fixed character voices are chosen in the cast package.</small></label>;
}
