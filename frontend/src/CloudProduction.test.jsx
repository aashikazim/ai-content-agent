import React from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CloudProduction from './CloudProduction.jsx';
afterEach(()=>{cleanup();vi.restoreAllMocks();});
const identifier='ba7fb41f-eafa-493d-9b28-31358dc98848';
const scriptId='ba7fb41f-eafa-493d-9b28-31358dc98849';
const plan={content_plan:{title:'A moving original story'},factual_status:'fiction',characters:[],shots:[{shot_number:1,action:'A bird takes flight'}]};
const production={production_id:identifier,job_id:identifier,status:'technical_ready',production_plan:plan,shots:[{attempt_id:identifier,shot_number:1,version:1,selected:true,status:'completed',operation:'generate',estimate_usd:'1.13',video_url:'/api/v1/fixture.mp4'}],events:[]};
function api({lost=false,existing=false}={}) {
  return vi.fn(async (path,options) => {
    if (path==='/generation/scripts') return {attempts:[{attempt_id:scriptId,status:'completed',production_plan:plan}]};
    if (path==='/generation/references') return {references:[]};
    if (path==='/generation/videos' && !options?.method) return {productions:existing ? [production]:[]};
    if (path==='/generation/video-quotes') return {quote_id:identifier,estimate_usd:'4.52',projected_estimate_usd:'4.73',shot_count:4,notice:'Estimate only; no automatic retries.'};
    if (path==='/generation/videos') {if(lost) throw new Error('Connection interrupted'); return {production_id:identifier,job_id:identifier,status:'queued'};}
    if (path===`/generation/videos/${identifier}`) return {...production,status:'processing',shots:[]};
    if (path===`/generation/videos/${identifier}/review`) return {...production,status:'pending_review'};
    throw new Error(`Unexpected ${path}`);
  });
}
const paid = request=>request.mock.calls.filter(([path,options])=>path==='/generation/videos' && options?.method==='POST');
it('confirms local GPU work without claiming a paid video request',async()=>{
  const user=userEvent.setup(); const base=api();
  const request=vi.fn(async(path,options)=>{
    if(path==='/generation/scripts')return {attempts:[{attempt_id:scriptId,status:'completed',production_plan:plan,configuration:{video_provider:'local_cinematic',video_model:'ltx-2.5',video_input_mode:'text'}}]};
    if(path==='/generation/video-quotes')return {quote_id:identifier,paid_request:false,provider_label:'Local cinematic',model:'ltx-2.5',estimate_usd:'0',projected_estimate_usd:'0',shot_count:1,notice:'Local generation; no provider video fee.'};
    return base(path,options);
  });
  render(<CloudProduction request={request}/>);
  await screen.findByLabelText('Completed script with a shot plan');
  await user.selectOptions(screen.getByLabelText('Completed script with a shot plan'),scriptId);
  await user.click(screen.getByRole('button',{name:'Estimate Local cinematic video cost'}));
  const start=await screen.findByRole('button',{name:'Start local cinematic generation'});
  expect(start).toBeDisabled();
  expect(screen.queryByRole('button',{name:'Confirm paid video generation'})).not.toBeInTheDocument();
  await user.click(screen.getByRole('checkbox',{name:/Confirm local GPU work/}));
  expect(start).toBeEnabled();
  expect(paid(request)).toHaveLength(0);
  await user.click(start);
  expect(paid(request)).toHaveLength(1);
  expect(request.mock.calls.some(([path])=>/publish|\/review$/.test(path))).toBe(false);
});

it('shows local performance and only regeneration for local candidates',async()=>{
  const user=userEvent.setup(); const base=api();
  const request=vi.fn(async(path,options)=>path==='/generation/videos' && !options?.method ? {productions:[{...production,configuration:{video_provider:'local_cinematic'},shots:[{...production.shots[0],provider:'local_cinematic',usage:{stage:'completed',progress:1,elapsed_seconds:120,peak_gpu_memory_mb:50000}}]}]} : base(path,options));
  render(<CloudProduction request={request}/>);
  await screen.findByLabelText('Existing production');
  await user.selectOptions(screen.getByLabelText('Existing production'),identifier);
  expect(screen.getByRole('progressbar',{name:'Local generation progress'})).toHaveAttribute('value','1');
  expect(screen.queryByRole('option',{name:'Edit this shot'})).not.toBeInTheDocument();
  expect(screen.getByText(/50000 MiB/)).toBeVisible();
});

it('binds Wan driving motion assignments to the generation request',async()=>{
  const user=userEvent.setup(); const base=api(); const motionId='ba7fb41f-eafa-493d-9b28-31358dc98850';
  const request=vi.fn(async(path,options)=>{
    if(path==='/generation/scripts')return {attempts:[{attempt_id:scriptId,status:'completed',production_plan:plan,configuration:{video_provider:'local_cinematic',video_model:'wan2.2-animate-2',video_input_mode:'image'}}]};
    if(path==='/generation/driving-references')return {references:[{reference_id:motionId,name:'My permitted performance'}]};
    return base(path,options);
  });
  render(<CloudProduction request={request}/>);
  await screen.findByLabelText('Completed script with a shot plan');
  await user.selectOptions(screen.getByLabelText('Completed script with a shot plan'),scriptId);
  await screen.findByRole('option',{name:'My permitted performance'});
  await user.selectOptions(screen.getByLabelText('Driving video for shot 1'),motionId);
  await user.click(screen.getByRole('button',{name:'Estimate Local cinematic video cost'}));
  const [,options]=request.mock.calls.find(([path])=>path==='/generation/video-quotes');
  expect(JSON.parse(options.body).shot_driving_video_ids).toEqual({1:motionId});
  expect(paid(request)).toHaveLength(0);
});
it('uses Runway start frames, credits and fresh paid consent without a Gemini key', async()=>{
  const user=userEvent.setup();
  const base=api();
  const frameId='ba7fb41f-eafa-493d-9b28-31358dc98850';
  const request=vi.fn(async(path,options)=>{
    if(path==='/generation/scripts') return {attempts:[{attempt_id:scriptId,status:'completed',production_plan:plan,configuration:{video_provider:'runway',video_model:'gen4_turbo',video_input_mode:'image'}}]};
    if(path==='/generation/references') return {references:[{reference_id:frameId,name:'A scene start frame',character_id:'scene_a'}]};
    if(path==='/generation/runway/credits') return {available_credits:1000};
    if(path==='/generation/video-quotes') return {quote_id:identifier,provider_label:'Runway',estimated_credits:'225',available_credits:1000,estimate_usd:'2.25',projected_estimate_usd:'2.25',shot_count:1,notice:'Explicit Runway authorization required.'};
    return base(path,options);
  });
  render(<CloudProduction request={request}/>);
  await screen.findByRole('option',{name:/A moving original story/});
  await user.selectOptions(screen.getByLabelText('Completed script with a shot plan'),scriptId);
  await user.selectOptions(screen.getByLabelText('Start frame for shot 1'),frameId);
  await user.click(screen.getByRole('button',{name:'Check Runway credits'}));
  await screen.findByText(/Runway available credits: 1000/);
  await user.click(screen.getByRole('button',{name:'Estimate Runway video cost'}));
  const quoteCall=request.mock.calls.find(([path])=>path==='/generation/video-quotes');
  expect(JSON.parse(quoteCall[1].body)).toMatchObject({script_attempt_id:scriptId,start_image_reference_id:null,shot_image_reference_ids:{1:frameId}});
  const submit=await screen.findByRole('button',{name:'Confirm paid video generation'});
  expect(submit).toBeDisabled();
  await user.click(screen.getByRole('checkbox',{name:/Authorize Runway video cost/}));
  expect(submit).toBeEnabled();
  await user.selectOptions(screen.getByLabelText('Start frame for shot 1'),'');
  expect(screen.queryByRole('button',{name:'Confirm paid video generation'})).not.toBeInTheDocument();
  expect(paid(request)).toHaveLength(0);
  expect(request.mock.calls.some(([path])=>/publish|\/review$/.test(path))).toBe(false);
});

it('enables explicitly selected Runway visual edits and final-frame continuation', async()=>{
  const user=userEvent.setup();
  const base=api();
  const request=vi.fn(async(path,options)=>path==='/generation/videos' && !options?.method ? {productions:[{...production,configuration:{video_provider:'runway'}}]} : base(path,options));
  render(<CloudProduction request={request}/>);
  await screen.findByLabelText('Existing production');
  await user.selectOptions(screen.getByLabelText('Existing production'),identifier);
  expect(screen.getByRole('option',{name:'Edit this shot'})).toBeEnabled();
  expect(screen.getByRole('option',{name:'Extend this shot'})).toBeEnabled();
  expect(screen.getByRole('option',{name:'Regenerate this shot'})).toBeEnabled();
  await user.click(screen.getByText('Create another candidate'));
  await user.selectOptions(screen.getByLabelText('Shot operation'),'edit');
  expect(screen.getByLabelText(/Runway edit model/)).toHaveValue('aleph2');
  expect(request.mock.calls.some(([,options])=>options?.method==='POST')).toBe(false);
  await user.selectOptions(screen.getByLabelText('Shot operation'),'extend');
  expect(screen.getByText(/Continue from the parent’s final frame/)).toBeVisible();
});

it('quotes the explicitly selected edit model and still requires cost consent',async()=>{
  const user=userEvent.setup();const base=api();
  const request=vi.fn(async(path,options)=>{
    if(path==='/generation/videos' && !options?.method) return {productions:[{...production,configuration:{video_provider:'runway'}}]};
    if(path.endsWith('/shot-quotes')) return {quote_id:identifier,model:'klingO3_pro',provider_label:'Runway',estimate_usd:'1.36',projected_estimate_usd:'6.76',shot_count:1,notice:'Visual edit; source audio preserved.'};
    return base(path,options);
  });
  render(<CloudProduction request={request} editModels={[{id:'aleph2',label:'Aleph 2'},{id:'klingO3_pro',label:'Kling O3 Pro'}]}/>);
  await screen.findByLabelText('Existing production');
  await user.selectOptions(screen.getByLabelText('Existing production'),identifier);
  await user.click(screen.getByText('Create another candidate'));
  await user.selectOptions(screen.getByLabelText('Shot operation'),'edit');
  await user.selectOptions(screen.getByLabelText(/Runway edit model/),'klingO3_pro');
  await user.type(screen.getByLabelText('Shot change instructions'),'Change the lighting.');
  await user.click(screen.getByRole('button',{name:'Estimate this shot change'}));
  const quote=request.mock.calls.find(([path])=>path.endsWith('/shot-quotes'));
  expect(JSON.parse(quote[1].body)).toMatchObject({operation:'edit',edit_model:'klingO3_pro',instructions:'Change the lighting.'});
  expect(await screen.findByText('klingO3_pro')).toBeVisible();
  expect(screen.getByRole('button',{name:'Confirm paid video generation'})).toBeDisabled();
  expect(request.mock.calls.some(([path])=>path.endsWith('/shots') || /publish|\/review$/.test(path))).toBe(false);
});
it('requires separate quote and explicit confirmation for video and never publishes',async()=>{
  const user=userEvent.setup();const request=api();render(<CloudProduction request={request}/>);
  await screen.findByRole('option',{name:/A moving original story/});
  expect(paid(request)).toHaveLength(0);
  await user.selectOptions(screen.getByLabelText('Completed script with a shot plan'),scriptId);
  await user.click(screen.getByRole('button',{name:'Estimate Gemini video cost'}));
  const submit=await screen.findByRole('button',{name:'Confirm paid video generation'});
  expect(submit).toBeDisabled();expect(paid(request)).toHaveLength(0);
  expect(submit).toHaveAccessibleDescription(/Check “Authorize Gemini video cost” above/);
  expect(screen.getByText('Authorize Gemini video cost')).toBeVisible();
  await user.click(screen.getByRole('checkbox',{name:/I authorize these Gemini requests/}));
  expect(submit).toBeEnabled();
  expect(submit).toHaveAccessibleDescription(/Video cost authorized/);
  await user.click(submit);
  await waitFor(()=>expect(paid(request)).toHaveLength(1));
  expect(JSON.parse(paid(request)[0][1].body)).toEqual({quote_id:identifier,confirmed:true});
  expect(request.mock.calls.some(([path])=>/publish|\/review$/.test(path))).toBe(false);
});
it('invalidates a video quote when the selected script changes',async()=>{
  const user=userEvent.setup();const request=api();render(<CloudProduction request={request}/>);
  await screen.findByRole('option',{name:/A moving original story/});
  await user.selectOptions(screen.getByLabelText('Completed script with a shot plan'),scriptId);
  await user.click(screen.getByRole('button',{name:'Estimate Gemini video cost'}));
  await screen.findByRole('button',{name:'Confirm paid video generation'});
  await user.selectOptions(screen.getByLabelText('Completed script with a shot plan'),'');
  expect(screen.queryByRole('button',{name:'Confirm paid video generation'})).not.toBeInTheDocument();
  expect(paid(request)).toHaveLength(0);
});
it('blocks another paid request after a lost acknowledgement',async()=>{
  const user=userEvent.setup();const request=api({lost:true});render(<CloudProduction request={request}/>);
  await screen.findByRole('option',{name:/A moving original story/});
  await user.selectOptions(screen.getByLabelText('Completed script with a shot plan'),scriptId);
  await user.click(screen.getByRole('button',{name:'Estimate Gemini video cost'}));
  await user.click(await screen.findByRole('checkbox',{name:/I authorize these Gemini requests/}));
  await user.click(screen.getByRole('button',{name:'Confirm paid video generation'}));
  await screen.findByText(/Submission outcome is unknown/);
  expect(screen.getByRole('button',{name:'Confirm paid video generation'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'Confirm paid video generation'})).toHaveAccessibleDescription(/previous submission has an unknown outcome/);
  await user.click(screen.getByRole('button',{name:'Refresh production history'}));
  expect(paid(request)).toHaveLength(1);
  expect(screen.getByRole('button',{name:'Allow a separately quoted request'})).toBeDisabled();
});

it('keeps video generation disabled when cost authorization is unchecked again',async()=>{
  const user=userEvent.setup();const request=api();render(<CloudProduction request={request}/>);
  await screen.findByRole('option',{name:/A moving original story/});
  await user.selectOptions(screen.getByLabelText('Completed script with a shot plan'),scriptId);
  await user.click(screen.getByRole('button',{name:'Estimate Gemini video cost'}));
  const authorization=await screen.findByRole('checkbox',{name:/Authorize Gemini video cost/});
  const submit=screen.getByRole('button',{name:'Confirm paid video generation'});
  expect(submit).toBeDisabled();
  await user.click(authorization);
  expect(submit).toBeEnabled();
  await user.click(authorization);
  expect(submit).toBeDisabled();
  expect(submit).toHaveAccessibleDescription(/Script-cost authorization is separate/);
  expect(paid(request)).toHaveLength(0);
});
it('requires all human quality checks and sends to review without approving',async()=>{
  const user=userEvent.setup();const request=api({existing:true});render(<CloudProduction request={request}/>);
  await screen.findByLabelText('Existing production');
  await user.selectOptions(screen.getByLabelText('Existing production'),identifier);
  const submit=screen.getByRole('button',{name:'Send video to manual review'});
  expect(submit).toBeDisabled();
  for(const pattern of [/I checked character appearance/,/I checked spoken words/,/I checked factual claims/]) await user.click(screen.getByRole('checkbox',{name:pattern}));
  await user.click(submit);
  await screen.findByText('pending review');
  const mutations=request.mock.calls.filter(([,options])=>options?.method==='POST');
  expect(mutations.map(([path])=>path)).toEqual([`/generation/videos/${identifier}/review`]);
  expect(paid(request)).toHaveLength(0);
});

it('keeps retrieving status while all clips are complete but local assembly is running',async()=>{
  const originalInterval=globalThis.setInterval;
  vi.spyOn(globalThis,'setInterval').mockImplementation(callback=>originalInterval(callback,10));
  const user=userEvent.setup(); const request=api({existing:true}); const original=request.getMockImplementation();
  request.mockImplementation(async(path,options)=>{
    if(path==='/generation/videos' && !options?.method) return {productions:[{...production,status:'assembling'}]};
    if(path===`/generation/videos/${identifier}/refresh`) return {status:'retrieval_requested'};
    if(path===`/generation/videos/${identifier}`) return production;
    return original(path,options);
  });
  render(<CloudProduction request={request}/>);
  await screen.findByLabelText('Existing production');
  await user.selectOptions(screen.getByLabelText('Existing production'),identifier);
  await screen.findByRole('button',{name:'Send video to manual review'});
  expect(paid(request)).toHaveLength(0);
  expect(request.mock.calls.some(([path])=>path.endsWith('/refresh'))).toBe(true);
});

it('shows a failed production diagnostic and safely refreshes without paid generation',async()=>{
  const user=userEvent.setup();const request=api({existing:true});const original=request.getMockImplementation();
  const failed={...production,status:'failed',error:'Shot 1 (v2): Google reported RESOURCE_EXHAUSTED. Check Gemini project billing, credits and model rate limits.',shots:[]};
  request.mockImplementation(async(path,options)=>{
    if(path==='/generation/videos' && !options?.method) return {productions:[failed]};
    if(path===`/generation/videos/${identifier}`) return failed;
    if(path===`/generation/videos/${identifier}/refresh`) return {status:'retrieval_requested'};
    return original(path,options);
  });
  render(<CloudProduction request={request}/>);
  await screen.findByLabelText('Existing production');
  await user.selectOptions(screen.getByLabelText('Existing production'),identifier);
  expect(screen.getByRole('alert')).toHaveTextContent('RESOURCE_EXHAUSTED');
  expect(screen.getByRole('alert')).toHaveTextContent('model rate limits');
  await user.click(screen.getByRole('button',{name:'Retrieve existing interactions & refresh'}));
  await waitFor(()=>expect(request.mock.calls.some(([path])=>path.endsWith('/refresh'))).toBe(true));
  expect(paid(request)).toHaveLength(0);
  expect(request.mock.calls.some(([path])=>path.endsWith('/review') || path.includes('publish'))).toBe(false);
});
