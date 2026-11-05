import React,{useEffect,useState} from 'react';
export default function ResearchDossierPicker({request,value,onChange}){
 const [items,setItems]=useState([]);
 useEffect(()=>{let live=true;request('/tools/research-dossiers').then(data=>live&&setItems(data.dossiers||[])).catch(()=>{});return()=>{live=false;};},[request]);
 return <label>Reviewed research dossier<select value={value||''} onChange={e=>onChange(e.target.value||null)}><option value="">No attached dossier</option>{items.map(d=><option key={d.dossier_id} value={d.dossier_id}>{d.topic} · {d.claims.length} supported claims</option>)}</select><small>Prepare claim/source notes in Production Tools. News still requires human factual review.</small></label>;
}
