import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import GenerationForm from './GenerationForm.jsx';
import App from './App.jsx';

const defaults = {
  schema_version: 1, content_type: 'realistic_short', script_provider: 'ollama', script_model: 'qwen3:8b',
  video_provider: 'local', video_model: 'local-pipeline', image_mode: 'text', image_model: 'local-image-model',
  duration_seconds: 45, aspect_ratio: '9:16', visual_style: 'cinematic', resolution: '1080p', fps: 30,
  narration: 'local_tts', source_mode: 'trends', topic: '', supplied_script: '', character_reference_ids: [],
  captions: 'scene_text', max_shots: 12, max_cost_usd: '10', require_motion: false, paid_generation_confirmed: false
};
const catalog = {
  schema_version: 1, phase: 1, defaults,
  content_types: [
    { id: 'realistic_short', label: 'Realistic short', executable: true, phase: 1, description: 'Local illustrated scenes.' },
    { id: 'short_drama', label: 'Short drama', executable: false, phase: 4 },
    { id: 'animated_comedy', label: 'Animated comedy', executable: false, phase: 4 },
    { id: 'long_drama', label: 'Long drama', executable: false },
    { id: 'news', label: 'News', executable: false, phase: 6 }
  ],
  script_providers: [
    { id: 'ollama', label: 'Local Ollama', implemented: true, configured: true, models: [{ id: 'qwen3:8b', label: 'qwen3:8b', implemented: true }] },
    { id: 'openai', label: 'OpenAI', implemented: false, configured: true, models: [{ id: 'gpt-6.1-sol', label: 'GPT-6.1 Sol', implemented: false }] }
  ],
  video_providers: [
    { id: 'local', label: 'Local illustrated preview', implemented: true, configured: true, models: [{ id: 'local-pipeline', label: 'Local pipeline', implemented: true, fps: [24, 30], aspect_ratios: ['9:16', '16:9', '1:1'] }] },
    { id: 'google', label: 'Google Gemini', implemented: false, configured: true, models: [{ id: 'gemini-omni-1.1-flash', label: 'Gemini Omni 1.1 Flash', implemented: false, fps: [24], aspect_ratios: ['9:16', '16:9'], clip_duration_seconds: { min: 3, max: 10 } }] },
    { id: 'kling', label: 'Kling', implemented: false, configured: false, models: [{ id: 'kling-4.0', label: 'Kling 4.0', implemented: false }] }
  ],
  image_models: [{ id: 'local-image-model', label: 'Configured local image model' }],
  styles: [{ id: 'cinematic', label: 'Cinematic' }, { id: 'realistic', label: 'Realistic' }, { id: 'sketch', label: 'Sketch' }],
  aspect_ratios: ['9:16', '16:9', '1:1'], resolutions: ['720p', '1080p'], fps: [24, 30]
};

let request;
let onCreated;
let saved;
beforeEach(() => {
  saved = [];
  onCreated = vi.fn();
  request = vi.fn(async (path, options = {}) => {
    if (path === '/generation/catalog') return structuredClone(catalog);
    if (path === '/generation/configurations' && !options.method) return { configurations: saved };
    if (path === '/generation/configurations') {
      const config = JSON.parse(options.body).configuration;
      const draft = { config_id: `draft-${saved.length + 1}`, config, created_at: '2026-11-03T12:00:00Z' };
      saved.push(draft);
      return draft;
    }
    if (path === '/generation/preflight') {
      const configuration = JSON.parse(options.body).configuration;
      const executable = configuration.video_provider === 'local' && configuration.script_provider === 'ollama';
      return { executable, configuration, errors: executable ? [] : ['Cloud integration is planned.'], warnings: [], paid_request: false, estimated_cost_usd: null };
    }
    if (path === '/jobs') return { job_id: 'job-created', status: 'queued' };
    throw new Error(`Unexpected request: ${path}`);
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function open() {
  const user = userEvent.setup();
  render(<GenerationForm request={request} onCreated={onCreated} />);
  await screen.findByLabelText('Script provider');
  return user;
}
const posts = () => request.mock.calls.filter(([, options]) => options?.method === 'POST');
const jobs = () => posts().filter(([path]) => path === '/jobs');

describe('Create video settings', () => {
  it('supports a twenty-minute draft and 240 shots only in long-drama mode',async()=>{
    const user=await open();
    await user.click(screen.getByRole('radio',{name:/Long drama/}));
    const duration=screen.getByLabelText('Duration (seconds)');
    const shots=screen.getByLabelText('Maximum scenes');
    expect(shots).toHaveAttribute('max','240');
    await user.clear(duration);await user.type(duration,'1200');
    await user.clear(shots);await user.type(shots,'240');
    await user.click(screen.getByRole('button',{name:'Save settings'}));
    await waitFor(()=>expect(saved).toHaveLength(1));
    expect(saved[0].config).toMatchObject({content_type:'long_drama',duration_seconds:1200,max_shots:240});
    expect(jobs()).toHaveLength(0);
  });
  it('removes hidden source text when switching back to trends', async () => {
    const user = await open();
    await user.selectOptions(screen.getByLabelText('Story source'), 'topic');
    await user.type(screen.getByLabelText('Your topic'), 'A temporary idea');
    await user.selectOptions(screen.getByLabelText('Story source'), 'trends');
    await user.click(screen.getByRole('button', { name: 'Start local generation' }));
    await waitFor(() => expect(jobs()).toHaveLength(1));
    expect(JSON.parse(jobs()[0][1].body).generation_config).toMatchObject({ source_mode: 'trends', topic: '', supplied_script: '' });
  });
  it('loads only metadata, exposes all formats honestly, and never requests an API key', async () => {
    await open();
    expect(screen.getAllByRole('radio')).toHaveLength(5);
    expect(screen.getByRole('radio', { name: /Realistic short/ })).toBeChecked();
    expect(screen.getByText(/Text cards are explicit baseline output/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/api key/i)).not.toBeInTheDocument();
    expect(posts()).toHaveLength(0);
    await waitFor(()=>expect(request.mock.calls.map(([path]) => path)).toEqual(['/generation/catalog', '/generation/configurations', '/tools/speech-profiles', '/tools/research-dossiers']));
  });

  it('keeps script and video selection independent without a provider fallback', async () => {
    const user = await open();
    await user.selectOptions(screen.getByLabelText('Script provider'), 'openai');
    expect(screen.getByLabelText('Script model')).toHaveValue('gpt-6.1-sol');
    expect(screen.getByLabelText('Video provider')).toHaveValue('local');
    await user.selectOptions(screen.getByLabelText('Video provider'), 'kling');
    expect(screen.getByLabelText('Video model')).toHaveValue('kling-4.0');
    expect(screen.getByLabelText('Script model')).toHaveValue('gpt-6.1-sol');
    await user.selectOptions(screen.getByLabelText('Script provider'), 'ollama');
    expect(screen.getByLabelText('Video provider')).toHaveValue('kling');
    expect(screen.getByLabelText('Video model')).toHaveValue('kling-4.0');
    expect(posts()).toHaveLength(0);
  });

  it('shows configured cloud credentials as planned, honors native capabilities, and cannot start a paid job', async () => {
    const user = await open();
    await user.selectOptions(screen.getByLabelText('Video provider'), 'google');
    expect(screen.getByText('Credential configured · integration planned')).toBeInTheDocument();
    expect(screen.getByText(/Credential presence does not confirm model access/)).toBeInTheDocument();
    expect(screen.getByLabelText('Frame rate')).toHaveValue('24');
    expect(within(screen.getByLabelText('Frame rate')).getByRole('option', { name: '30 fps' })).toBeDisabled();
    expect(within(screen.getByLabelText('Aspect ratio')).getByRole('option', { name: /1:1/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Start local generation' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Check configuration' }));
    await screen.findByText('This configuration cannot run yet.');
    expect(jobs()).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    await screen.findByText('Settings saved locally. No generation has started.');
    expect(saved[0].config.video_provider).toBe('google');
    expect(saved[0].config.paid_generation_confirmed).toBe(false);
    expect(jobs()).toHaveLength(0);
  });

  it('round-trips a future production configuration without starting generation', async () => {
    const user = await open();
    await user.click(screen.getByRole('radio', { name: /Animated comedy/ }));
    await user.selectOptions(screen.getByLabelText('Script provider'), 'openai');
    await user.selectOptions(screen.getByLabelText('Video provider'), 'kling');
    await user.clear(screen.getByLabelText('Duration (seconds)'));
    await user.type(screen.getByLabelText('Duration (seconds)'), '120');
    await user.selectOptions(screen.getByLabelText('Aspect ratio'), '16:9');
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    await screen.findByText('Settings saved locally. No generation has started.');
    expect(saved[0].config).toMatchObject({ content_type: 'animated_comedy', script_provider: 'openai', video_provider: 'kling', duration_seconds: 120, aspect_ratio: '16:9' });
    await user.selectOptions(screen.getByLabelText('Video provider'), 'local');
    await user.selectOptions(screen.getByLabelText('Aspect ratio'), '9:16');
    await user.click(screen.getByRole('button', { name: 'Load settings' }));
    expect(screen.getByLabelText('Video provider')).toHaveValue('kling');
    expect(screen.getByLabelText('Script provider')).toHaveValue('openai');
    expect(screen.getByLabelText('Duration (seconds)')).toHaveValue(120);
    expect(screen.getByLabelText('Aspect ratio')).toHaveValue('16:9');
    expect(screen.getByRole('radio', { name: /Animated comedy/ })).toBeChecked();
    expect(jobs()).toHaveLength(0);
  });

  it('checks the latest local settings and sends them in the actual job request', async () => {
    const user = await open();
    await user.clear(screen.getByLabelText('Duration (seconds)'));
    await user.type(screen.getByLabelText('Duration (seconds)'), '60');
    await user.selectOptions(screen.getByLabelText('Aspect ratio'), '16:9');
    await user.selectOptions(screen.getByLabelText('Resolution'), '720p');
    await user.selectOptions(screen.getByLabelText('Frame rate'), '24');
    await user.selectOptions(screen.getByLabelText('Visual style'), 'sketch');
    await user.selectOptions(screen.getByLabelText('Local visual assets'), 'diffusion');
    expect(screen.getByLabelText('Local image model')).toHaveValue('local-image-model');
    await user.click(screen.getByRole('button', { name: 'Start local generation' }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ job_id: 'job-created', status: 'queued' }));
    expect(posts().map(([path]) => path)).toEqual(['/generation/preflight', '/jobs']);
    expect(JSON.parse(jobs()[0][1].body)).toMatchObject({
      category: 'science', region: 'US', limit: 5, image_mode: 'diffusion',
      generation_config: { duration_seconds: 60, aspect_ratio: '16:9', resolution: '720p', fps: 24, visual_style: 'sketch', image_mode: 'diffusion', image_model: 'local-image-model' }
    });
    expect(screen.getByRole('button', { name: 'Request submitted' })).toBeDisabled();
    expect(request.mock.calls.some(([path]) => /review|publish/.test(path))).toBe(false);
  });

  it('respects a failed server preflight even when the selected local settings look executable', async () => {
    const previous = request.getMockImplementation();
    request.mockImplementation(async (path, options) => path === '/generation/preflight' ? { executable: false, errors: ['Runtime settings are not supported.'], warnings: [], paid_request: false } : previous(path, options));
    const user = await open();
    await user.click(screen.getByRole('button', { name: 'Start local generation' }));
    await screen.findByText('Runtime settings are not supported.');
    expect(jobs()).toHaveLength(0);
    expect(onCreated).not.toHaveBeenCalled();
  });

  it('does not repeat a job request with an uncertain response, including after settings change', async () => {
    const previous = request.getMockImplementation();
    request.mockImplementation(async (path, options) => {
      if (path === '/jobs') throw new Error('Connection interrupted.');
      return previous(path, options);
    });
    const user = await open();
    await user.click(screen.getByRole('button', { name: 'Start local generation' }));
    await screen.findByText(/Check the overview before requesting another video/);
    expect(jobs()).toHaveLength(1);
    await user.selectOptions(screen.getByLabelText('Aspect ratio'), '1:1');
    const submit = screen.getByRole('button', { name: 'Request submitted' });
    expect(submit).toBeDisabled();
    await user.click(submit);
    expect(jobs()).toHaveLength(1);
  });

  it('uses the existing admin session and CSRF protection when launched from the studio', async () => {
    const fetcher = vi.fn(async (url, options = {}) => {
      const path = url.replace('/api/v1', '');
      let body;
      if (path === '/auth/session') body = { authenticated: true, configured: true, username: 'director', csrf_token: 'generation-csrf' };
      else if (path === '/dashboard') body = { summary: {}, jobs: [], publications: [], analytics: [], category_performance: [] };
      else body = await request(path, options);
      return { ok: true, status: 200, json: async () => body };
    });
    vi.stubGlobal('fetch', fetcher);
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole('heading', { name: 'Your creative command center.' });
    expect(request).not.toHaveBeenCalled();
    await user.click(within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('button', { name: 'Create video' }));
    await screen.findByLabelText('Script provider');
    await user.click(screen.getByRole('button', { name: 'Start local generation' }));
    await waitFor(() => expect(fetcher.mock.calls.some(([url]) => url === '/api/v1/jobs')).toBe(true));
    const mutations = fetcher.mock.calls.filter(([, options]) => options?.method === 'POST');
    expect(mutations.map(([url]) => url)).toEqual(['/api/v1/generation/preflight', '/api/v1/jobs']);
    for (const [, options] of mutations) {
      expect(options.credentials).toBe('same-origin');
      expect(options.headers['X-CSRF-Token']).toBe('generation-csrf');
      expect(options.headers['X-API-Key']).toBeUndefined();
    }
    expect(screen.queryByLabelText(/api key/i)).not.toBeInTheDocument();
  });
});
