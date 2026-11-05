import React from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {act,cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ClipStudio from './ClipStudio.jsx';
import App from './App.jsx';

afterEach(()=>{cleanup();vi.unstubAllGlobals();window.history.replaceState(null,'','/');});
const projectId='e71045e3-0297-41e6-975b-d22077ef7ad0';
const clipId='8331fa3d-1728-457c-865e-40d2b386df9e';
const attemptId='0c5c36f5-f757-4881-82b0-d45542a242d9';
const catalog={mock:true,notice:'Mock output only.',planner_providers:[
  {id:'anthropic',label:'Claude (Anthropic)',models:[{id:'claude-sonnet-5-5',label:'Claude Sonnet 5.5'}]},
  {id:'openai',label:'OpenAI',models:[{id:'gpt-6.1-sol',label:'GPT-6.1 Sol'},{id:'gpt-6-sol',label:'GPT-6 Sol'}]}]};
const project={project_id:projectId,request:{instruction:'Make cinematic clips',settings:{planner_provider:'openai',planner_model:'gpt-6-sol',video_model:'ltx'}},
  jobs:[{job_id:projectId,status:'completed',stage:'finished',progress:1}],events:[],clips:[{
    clip_id:clipId,selected_attempt_id:attemptId,plan:{title:'Clip 1',generation_prompt:'MOCK cinematic example',source_start:0,source_end:2,duration_seconds:2},
    attempts:[{attempt_id:attemptId,version:1,status:'completed',model:'ltx',seed:42,video_url:'/api/v1/clip-studio/attempts/'+attemptId+'/video'}]}]};
function api({existing=false,lost=false}={}) {
  return vi.fn(async(path,options)=>{
    if(path==='/clip-studio/catalog')return catalog;
    if(path==='/clip-studio/projects' && !options?.method)return {projects:existing?[{project_id:projectId,instruction:'Make cinematic clips',status:'completed'}]:[]};
    if(path==='/clip-studio/projects'){if(lost)throw new Error('Acknowledgement lost');return project;}
    if(path===`/clip-studio/projects/${projectId}`)return project;
    if(path.endsWith('/regenerate'))return {job_id:projectId,status:'queued'};
    throw new Error(path);
  });
}

it('requires rights and saves provider/model selections through multipart without automatic work',async()=>{
  const user=userEvent.setup(),request=api();render(<ClipStudio request={request}/>);
  await screen.findByRole('option',{name:'Claude Sonnet 5.5'});
  const generate=screen.getByRole('button',{name:'Generate mock clips'});
  expect(generate).toBeDisabled();expect(request.mock.calls.some(([,o])=>o?.method==='POST')).toBe(false);
  await user.type(screen.getByLabelText('Instructions'),'Make cinematic clips');
  await user.type(screen.getByLabelText('YouTube URL'),'https://youtu.be/abcdefghijk');
  await user.selectOptions(screen.getByLabelText('Planning provider'),'openai');
  await user.selectOptions(screen.getByLabelText('Planning model'),'gpt-6-sol');
  expect(generate).toBeDisabled();await user.click(screen.getByRole('checkbox',{name:'I have the rights to use this source.'}));
  await user.click(generate);
  expect(await screen.findByRole('link',{name:'Download clip'})).toHaveAttribute('href',expect.stringContaining('?download=true'));
  const calls=request.mock.calls.filter(([,o])=>o?.method==='POST');expect(calls).toHaveLength(1);
  expect(calls[0][1].body).toBeInstanceOf(FormData);
  expect(JSON.parse(calls[0][1].body.get('payload')).settings).toMatchObject({planner_provider:'openai',planner_model:'gpt-6-sol'});
  expect(window.location.hash).toBe('#clip-studio/'+projectId);
  expect(request.mock.calls.some(([path])=>/publish|approve/.test(path))).toBe(false);
});

it('reopens a project after a page refresh using GETs only',async()=>{
  window.history.replaceState(null,'',`/#clip-studio/${projectId}`);
  const request=api({existing:true});const view=render(<ClipStudio request={request}/>);
  await screen.findByLabelText('Clip 1 preview');view.unmount();render(<ClipStudio request={request}/>);
  await screen.findByLabelText('Clip 1 preview');
  expect(request.mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(0);
});

it('does not change another page URL when a submission finishes after leaving Clip Studio',async()=>{
  const user=userEvent.setup(),base=api();let acknowledge;
  const request=vi.fn((path,options)=>path==='/clip-studio/projects' && options?.method==='POST' ? new Promise(resolve=>{acknowledge=resolve;}) : base(path,options));
  const view=render(<ClipStudio request={request}/>);
  await screen.findByRole('option',{name:'Claude Sonnet 5.5'});
  await user.type(screen.getByLabelText('Instructions'),'A new scene');
  await user.type(screen.getByLabelText('YouTube URL'),'https://youtu.be/abcdefghijk');
  await user.click(screen.getByRole('checkbox',{name:'I have the rights to use this source.'}));
  await user.click(screen.getByRole('button',{name:'Generate mock clips'}));
  await waitFor(()=>expect(acknowledge).toBeTypeOf('function'));view.unmount();
  await act(async()=>acknowledge(project));
  expect(window.location.hash).toBe('');
});

it('does not automatically repeat a creation request after acknowledgement loss',async()=>{
  const user=userEvent.setup(),request=api({lost:true});render(<ClipStudio request={request}/>);
  await screen.findByRole('option',{name:'Claude Sonnet 5.5'});
  await user.type(screen.getByLabelText('Instructions'),'A hopeful scene');
  await user.type(screen.getByLabelText('YouTube URL'),'https://youtu.be/abcdefghijk');
  await user.click(screen.getByRole('checkbox',{name:'I have the rights to use this source.'}));
  await user.click(screen.getByRole('button',{name:'Generate mock clips'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Acknowledgement lost');
  const first=request.mock.calls.filter(([,o])=>o?.method==='POST');expect(first).toHaveLength(1);
  await user.click(screen.getByRole('button',{name:'Generate mock clips'}));
  const posts=request.mock.calls.filter(([,o])=>o?.method==='POST');expect(posts).toHaveLength(2);
  expect(JSON.parse(posts[0][1].body.get('payload')).request_id).toBe(JSON.parse(posts[1][1].body.get('payload')).request_id);
});

it('integrates with the existing sidebar, session and CSRF-aware multipart request helper',async()=>{
  const user=userEvent.setup();
  const feature=api();
  const fetch=vi.fn(async(url,options={})=>{
    if(url==='/api/v1/auth/session')return {ok:true,json:async()=>({authenticated:true,username:'admin',csrf_token:'csrf-fixture'})};
    if(url==='/api/v1/dashboard')return new Promise(()=>{});
    const result=await feature(String(url).replace('/api/v1',''),options);
    return {ok:true,json:async()=>result};
  });
  vi.stubGlobal('fetch',fetch);render(<App/>);
  await user.click(await screen.findByRole('button',{name:'Clip Studio'}));
  await screen.findByRole('option',{name:'Claude Sonnet 5.5'});
  await user.type(screen.getByLabelText('Instructions'),'A moving scene');
  await user.type(screen.getByLabelText('YouTube URL'),'https://youtu.be/abcdefghijk');
  await user.click(screen.getByRole('checkbox',{name:'I have the rights to use this source.'}));
  await user.click(screen.getByRole('button',{name:'Generate mock clips'}));
  await waitFor(()=>expect(fetch.mock.calls.some(([url,o])=>url==='/api/v1/clip-studio/projects' && o?.method==='POST')).toBe(true));
  const [,options]=fetch.mock.calls.find(([url,o])=>url==='/api/v1/clip-studio/projects' && o?.method==='POST');
  expect(options.body).toBeInstanceOf(FormData);
  expect(options.headers['Content-Type']).toBeUndefined();
  expect(options.headers['X-CSRF-Token']).toBe('csrf-fixture');
  expect(options.credentials).toBe('same-origin');
});

it('requires separate paid consent in real mode and preserves native/cast/audio choices',async()=>{
  const user=userEvent.setup(),base=api();
  const request=vi.fn(async(path,options)=>{
    if(path==='/clip-studio/catalog')return {...catalog,mock:false,real_generation_available:true,planning_estimate_usd:.4};
    if(path==='/generation/cast-packages')return {packages:[{package_id:projectId,name:'Maya · blue jacket'}]};
    return base(path,options);
  });
  render(<ClipStudio request={request}/>);
  await screen.findByRole('option',{name:'Maya · blue jacket'});
  await user.type(screen.getByLabelText('Instructions'),'Keep Maya in a blue jacket, walking through the cafe.');
  await user.type(screen.getByLabelText('YouTube URL'),'https://youtu.be/abcdefghijk');
  await user.selectOptions(screen.getByLabelText('Local video model'),'hybrid');
  await user.selectOptions(screen.getByLabelText('Approved cast package'),projectId);
  await user.selectOptions(screen.getByLabelText('Continuity'),'connected');
  await user.selectOptions(screen.getByLabelText('Audio'),'character_tts');
  await user.click(screen.getByRole('checkbox',{name:'I have the rights to use this source.'}));
  expect(screen.getByRole('button',{name:'Generate clips'})).toBeDisabled();
  await user.click(screen.getByRole('checkbox',{name:/I authorize the selected planning provider/}));
  await user.click(screen.getByRole('button',{name:'Generate clips'}));
  await screen.findByLabelText('Clip 1 preview');
  const call=request.mock.calls.find(([p,o])=>p==='/clip-studio/projects' && o?.method==='POST');
  expect(JSON.parse(call[1].body.get('payload')).settings).toMatchObject({backend:'native',video_model:'hybrid',cast_package_id:projectId,continuity:'connected',narration:'character_tts',paid_planning_confirmed:true});
  expect(request.mock.calls.some(([p])=>/publish|approve/.test(p))).toBe(false);
});

it('shows the existing Review link for a real export without approving or publishing',async()=>{
  window.history.replaceState(null,'',`/#clip-studio/${projectId}`);
  const go=vi.fn(),base=api();
  const request=vi.fn(async(path,options)=>path===`/clip-studio/projects/${projectId}`?{...project,mode:'real',clips:project.clips.map(c=>({...c,attempts:c.attempts.map(a=>({...a,review_job_id:projectId}))}))}:base(path,options));
  render(<ClipStudio request={request} onNavigate={go}/>);
  await userEvent.click(await screen.findByRole('button',{name:'Open review queue'}));
  expect(go).toHaveBeenCalledWith('review');
  expect(request.mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(0);
});

it('shows a prominent saved analysis error without automatically retrying',async()=>{
  window.history.replaceState(null,'',`/#clip-studio/${projectId}`);
  const base=api(),message='YouTube did not provide a usable video/audio format. Update the isolated yt-dlp package.';
  const request=vi.fn(async(path,options)=>path===`/clip-studio/projects/${projectId}`?{...project,mode:'real',clips:[],jobs:[{job_id:projectId,status:'failed',stage:'analyze',progress:.1,error:message}]}:base(path,options));
  render(<ClipStudio request={request}/>);
  expect(await screen.findByRole('alert')).toHaveTextContent(message);
  expect(screen.getByRole('alert')).toHaveTextContent('Clip workflow failed');
  expect(request.mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(0);
});
