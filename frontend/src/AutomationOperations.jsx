import React,{useCallback,useEffect,useState} from 'react';
export default function AutomationOperations({request}){
 const [data,setData]=useState(null),[error,setError]=useState('');
 const refresh=useCallback(async()=>setData(await request('/automation/operations')),[request]);
 useEffect(()=>{refresh().catch(e=>setError(e.message));const timer=setInterval(()=>refresh().catch(e=>setError(e.message)),15000);return()=>clearInterval(timer);},[refresh]);
 return <div className="generation-section"><h2>Automation operations</h2><p>Read-only queue, host memory, measured GPU timing and recovery information. Refreshing does not dispatch, retry, approve or publish.</p>
 {error&&<p role="alert" className="notice notice-error">{error}</p>}
 {data&&!data.initialized&&<p className="notice">Automation storage has not been initialized. Back up the database before explicitly enabling automation.</p>}
 {data&&<><p>Snapshot: {data.at} · available host RAM: {data.host_available_ram_gib??'unavailable'} GiB · disk free: {data.disk_free_gib??'unavailable'} GiB</p>
 <div className="generation-grid"><article className="generation-section"><h3>Durable queue</h3>{data.queue.length?data.queue.map(q=><p key={q.kind+q.status}>{q.kind} · {q.status}: {q.count}</p>):<p>No queued work.</p>}</article><article className="generation-section"><h3>Workers</h3>{data.workers.length?data.workers.map(w=><p key={w.worker_id}>{w.fresh?'Fresh':'Stale'} · heartbeat {w.age_seconds}s ago · {w.worker_id}</p>):<p>No worker heartbeat. This page never starts one.</p>}</article></div>
 <h3>GPU measurements and historical seeds</h3><p>Measured stage times are worker observations, not CUDA kernel profiling. Seeds are estimates. Synthetic soak output adds no measurements.</p>
 {data.measurements.map((m,i)=><p key={i}>{m.model} / {m.mode} · {m.is_seed?'Historical seed':'Measured'} · {m.samples} samples · {Number(m.seconds_per_video_second).toFixed(2)} neural seconds per video second · {(m.neural_seconds/60).toFixed(1)} summed neural minutes</p>)}
 <h3>Needs attention</h3><p>Inspect retained IDs before recovery. Interrupted or uncertain work must not be requeued blindly. Use the existing generation or Publishing reconciliation flow.</p>{data.attention.length?data.attention.map((a,i)=><p className="notice" key={i}>{a.target_id} · {a.status} · {a.error||'Inspect saved request and provider IDs.'}</p>):<p>No recorded recovery holds.</p>}
 <h3>Series lifecycle</h3>{data.series.map(s=><p key={s.series_id}>{s.title} · {s.status} · {s.start_date||'Start date not set'}</p>)}
 <details><summary>Recent Director and Governor events</summary>{data.events.map(e=><p key={e.event_id}>{e.timestamp} · {e.actor} · {e.action} · {e.subject_id} · {e.detail_json}</p>)}</details></>}
 </div>;
}
