import React from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import LocalVideoStudio from './LocalVideoStudio.jsx';
import CloudProduction from './CloudProduction.jsx';
import App from './App.jsx';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const scriptId = 'da7fb41f-eafa-493d-9b28-31358dc98849';
const cloudId = 'da7fb41f-eafa-493d-9b28-31358dc98850';
const productionId = 'da7fb41f-eafa-493d-9b28-31358dc98851';
const plan = { content_plan: { title: 'The Missing Lunch', scenes: [] }, factual_status: 'fiction', outline: ['Lunch goes missing'], characters: [{ character_id: 'maya', name: 'Maya', description: 'Mustard cardigan', voice_notes: 'Warm voice' }], shots: [{ shot_number: 1, scene_number: 1, duration_seconds: 5, action: 'Maya opens the fridge', character_ids: ['maya'] }] };
const local = { script_provider: 'anthropic', script_model: 'claude-sonnet-5-5', video_provider: 'local_cinematic', video_model: 'ltx-2.5', video_input_mode: 'text' };
const catalog = { defaults: { max_cost_usd: '10', image_model: 'local-image' },
  script_providers: [{ id: 'anthropic', label: 'Claude (Anthropic)', configured: true, models: [{ id: 'claude-sonnet-5-5', label: 'Claude Sonnet 5.5' }] }, { id: 'ollama', label: 'Local Ollama', configured: true, models: [{ id: 'qwen3:8b' }] }],
  video_providers: [{ id: 'local_cinematic', models: [{ id: 'ltx-2.5', label: 'LTX' }, { id: 'wan2.2-animate-2', label: 'Wan' }, { id: 'ltx-wan-hybrid', label: 'Hybrid' }] }] };
function api() {
  let generated = false;
  let config = local;
  const draft = () => ({ attempt_id: scriptId, status: 'completed', configuration: config, production_plan: plan });
  return vi.fn(async (path, options) => {
    if (path === '/generation/catalog') return catalog;
    if (path === '/generation/cast-packages') return {packages:[]};
    if (path.startsWith('/generation/cast-packages/')) return {characters:[]};
    if (path === '/generation/script-quotes') return { paid: true, quote_id: scriptId, maximum_cost_usd: '0.21' };
    if (path === '/generation/scripts' && options?.method === 'POST') { generated = true; config = JSON.parse(options.body).configuration; return draft(); }
    if (path === '/generation/scripts') return { attempts: [{ ...draft(), attempt_id: cloudId, configuration: { video_provider: 'runway' }, production_plan: { ...plan, content_plan: { title: 'Cloud-only story' } } }, ...(generated ? [draft()] : [])] };
    if (path === '/generation/references' || path === '/generation/driving-references') return { references: [] };
    if (path === '/generation/videos' && !options?.method) return { productions: [] };
    if (path === '/generation/video-quotes') return { quote_id: scriptId, paid_request: false, provider_label: 'Local cinematic', estimate_usd: '0', projected_estimate_usd: '0', shot_count: 6 };
    if (path === '/generation/videos' && options?.method === 'POST') return { production_id: productionId };
    if (path === `/generation/videos/${productionId}`) return { production_id: productionId, job_id: productionId, configuration: config, status: 'awaiting_shot_selection', production_plan: plan, shots: [], events: [] };
    throw new Error(`Unexpected ${path}`);
  });
}
const mutations = request => request.mock.calls.filter(([, options]) => options?.method === 'POST');

it('explains Wan-on-cuts and LTX continuation before connected hybrid work is authorized',async()=>{
  const user=userEvent.setup(),request=api();render(<LocalVideoStudio request={request}/>);
  await screen.findByLabelText('Local video model');
  await user.selectOptions(screen.getByLabelText('Local video model'),'ltx-wan-hybrid');
  await user.selectOptions(screen.getByLabelText('Character continuity'),'connected');
  expect(screen.getByText(/In connected mode, Wan establishes identity on camera cuts/)).toBeVisible();
  expect(mutations(request)).toHaveLength(0);
});

it('opens with simple local defaults, manual input and no automatic mutations', async () => {
  const request = api(); render(<LocalVideoStudio request={request} />);
  await screen.findByLabelText('Your story idea');
  expect(screen.getByLabelText('Local video model')).toHaveValue('ltx-2.5');
  expect(screen.getByLabelText('Target duration')).toHaveValue('30');
  expect(screen.queryByText('Discover trends')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Check Runway credits' })).not.toBeInTheDocument();
  expect(screen.queryByText('Cloud-only story')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Check access & estimate/ })).toBeDisabled();
  expect(mutations(request)).toHaveLength(0);
});

it('shows model-specific assets and automatically uses local narration for Wan', async () => {
  const user = userEvent.setup(); const request = api(); render(<LocalVideoStudio request={request} />);
  await user.selectOptions(await screen.findByLabelText('Local video model'), 'wan2.2-animate-2');
  expect(screen.getByText(/Required: one character image and a driving-motion MP4/)).toBeVisible();
  await user.click(screen.getByText('Advanced settings'));
  expect(screen.getByLabelText('Audio')).toHaveValue('local_tts');
  expect(screen.queryByRole('option', { name: 'Native model audio' })).not.toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('Local video model'), 'ltx-wan-hybrid');
  expect(screen.getByText(/No driving-video upload is needed/)).toBeVisible();
  expect(screen.getByLabelText('Audio')).toHaveValue('model_audio');
  expect(mutations(request)).toHaveLength(0);
});

it('loads the example, authorizes Claude separately, and hands the completed script to local production', async () => {
  const user = userEvent.setup(); const request = api(); render(<LocalVideoStudio request={request} />);
  await user.click(await screen.findByText(/Example: The Missing Lunch/));
  await user.click(screen.getByRole('button', { name: 'Load this example' }));
  await user.selectOptions(screen.getByLabelText('Character continuity'),'independent');
  expect(screen.getByLabelText('Your story idea').value).toContain('Maya');
  expect(screen.getByRole('button', { name: 'Generate script draft' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: /Check access & estimate/ }));
  await user.click(await screen.findByRole('checkbox', { name: /I authorize one claude-sonnet/ }));
  await user.click(screen.getByRole('button', { name: 'Generate script draft' }));
  await waitFor(() => expect(screen.getByLabelText('Completed script with a shot plan')).toHaveValue(scriptId));
  const script = mutations(request).find(([path]) => path === '/generation/scripts');
  expect(JSON.parse(script[1].body).configuration).toMatchObject({ ...local, source_mode: 'topic', resolution: '720p', fps: 24, max_shots: 6, require_motion: true });
  await user.click(screen.getByRole('button', { name: 'Prepare local generation' }));
  expect(screen.getByRole('button', { name: 'Start local cinematic generation' })).toBeDisabled();
  expect(mutations(request).some(([path]) => path === '/generation/videos')).toBe(false);
  await user.click(screen.getByRole('checkbox', { name: /Confirm local GPU work/ }));
  await user.click(screen.getByRole('button', { name: 'Start local cinematic generation' }));
  expect(mutations(request).filter(([path]) => path === '/generation/videos')).toHaveLength(1);
  expect(request.mock.calls.some(([path]) => /publish|\/review$/.test(path))).toBe(false);
});

it('plans a four-minute screenplay with enough shots and keeps Ollama independent', async () => {
  const user = userEvent.setup(); const request = api(); render(<LocalVideoStudio request={request} />);
  await user.selectOptions(await screen.findByLabelText('Video format'), 'long');
  await user.selectOptions(screen.getByLabelText('Target duration'), '240');
  await user.selectOptions(screen.getByLabelText('Character continuity'),'independent');
  await user.selectOptions(screen.getByLabelText('Story input'), 'script');
  await user.type(screen.getByLabelText('Your screenplay'), 'Maya and Theo resolve a misunderstanding.');
  await user.click(screen.getByText('Advanced settings'));
  await user.selectOptions(screen.getByLabelText('Script provider'), 'ollama');
  await user.click(screen.getByRole('button', { name: 'Generate script draft' }));
  const body = JSON.parse(mutations(request).find(([path]) => path === '/generation/scripts')[1].body);
  expect(body.configuration).toMatchObject({ script_provider: 'ollama', video_model: 'ltx-2.5', content_type: 'long_drama', duration_seconds: 240, max_shots: 48, source_mode: 'script', topic: '', supplied_script: 'Maya and Theo resolve a misunderstanding.' });
});

it('filters completed local drafts out of the cloud production panel', async () => {
  const request = api(); const base = request.getMockImplementation();
  request.mockImplementation(async (path, options) => path === '/generation/scripts' && !options?.method ? { attempts: [{ attempt_id: scriptId, status: 'completed', configuration: local, production_plan: plan }, { attempt_id: cloudId, status: 'completed', configuration: { video_provider: 'runway' }, production_plan: { ...plan, content_plan: { title: 'Cloud-only story' } } }] } : base(path, options));
  render(<CloudProduction scope="cloud" request={request} />);
  await screen.findByRole('option', { name: /Cloud-only story/ });
  expect(screen.queryByRole('option', { name: /The Missing Lunch/ })).not.toBeInTheDocument();
});

it('reopens only local productions and preserves the manual quality gate', async () => {
  const user = userEvent.setup(); const request = api(); const base = request.getMockImplementation();
  request.mockImplementation(async (path, options) => path === '/generation/videos' && !options?.method ? { productions: [
    { production_id: productionId, configuration: local, job_id: productionId, production_plan: plan, shots: [], events: [], status: 'technical_ready' },
    { production_id: cloudId, configuration: { video_provider: 'runway' }, production_plan: { ...plan, content_plan: { title: 'Cloud-only video' } }, shots: [], status: 'technical_ready' }
  ] } : base(path, options));
  render(<CloudProduction scope="local" request={request} />);
  await screen.findByRole('option', { name: /The Missing Lunch/ });
  expect(screen.queryByRole('option', { name: /Cloud-only video/ })).not.toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('Existing production'), productionId);
  expect(screen.getByRole('button', { name: 'Send video to manual review' })).toBeDisabled();
  expect(mutations(request)).toHaveLength(0);
});

it('opens from the admin navigation without asking for another API key', async () => {
  const request = api(); const user = userEvent.setup();
  const fetcher = vi.fn(async (url, options = {}) => {
    const path = url.replace('/api/v1', '');
    const body = path === '/auth/session' ? { authenticated: true, username: 'director', csrf_token: 'studio-csrf' }
      : path === '/dashboard' ? { summary: {}, jobs: [], publications: [], analytics: [], category_performance: [] }
      : await request(path, options);
    return { ok: true, status: 200, json: async () => body };
  });
  vi.stubGlobal('fetch', fetcher); render(<App />);
  await user.click(await screen.findByRole('button', { name: 'Local Video Studio' }));
  await screen.findByLabelText('Your story idea');
  expect(screen.getByRole('heading', { name: 'Bring your story to life.' })).toBeVisible();
  expect(screen.queryByLabelText(/api key/i)).not.toBeInTheDocument();
  expect(within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('button', { name: 'Local Video Studio' })).toHaveAttribute('aria-current', 'page');
  expect(mutations(request)).toHaveLength(0);
});

it('requires an approved cast and an explicit chapter budget for a thirty-minute film',async()=>{
  const user=userEvent.setup(),request=api(),base=request.getMockImplementation();
  const castId='da7fb41f-eafa-493d-9b28-31358dc98854';
  request.mockImplementation(async(path,options)=>{
    if(path==='/generation/cast-packages')return {packages:[{package_id:castId,name:'Lunch cast',characters:[{character_id:'maya',name:'Maya',reference_ids:[scriptId],primary_reference_id:scriptId,wardrobe:'Mustard cardigan'}]}]};
    if(path==='/generation/script-quotes')return {paid:true,quote_id:scriptId,maximum_cost_usd:'10.60',max_requests:10};
    return base(path,options);
  });
  render(<LocalVideoStudio request={request}/>);
  await user.type(await screen.findByLabelText('Your story idea'),'Maya and Theo solve the lunchbox mystery.');
  expect(screen.getByRole('button',{name:/Check access & estimate/})).toBeDisabled();
  await screen.findByRole('option',{name:/Lunch cast/});
  await user.selectOptions(screen.getByLabelText('Approved cast package'),castId);
  await user.selectOptions(screen.getByLabelText('Video format'),'long');
  await user.selectOptions(screen.getByLabelText('Target duration'),'1800');
  await user.click(screen.getByRole('button',{name:/Check access & estimate/}));
  expect(screen.getByRole('button',{name:'Generate script draft'})).toBeDisabled();
  await user.click(await screen.findByRole('checkbox',{name:/I authorize up to 10 chapter requests/}));
  await user.click(screen.getByRole('button',{name:'Generate script draft'}));
  const submission=mutations(request).find(([path])=>path==='/generation/scripts');
  expect(JSON.parse(submission[1].body).configuration).toMatchObject({duration_seconds:1800,max_shots:360,cast_package_id:castId,continuity_mode:'anchored',max_cost_usd:'25'});
  expect(mutations(request).some(([path])=>path==='/generation/videos' || /publish/.test(path))).toBe(false);
});
