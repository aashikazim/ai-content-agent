import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App, { canPublish } from './App.jsx';
const session = {
  authenticated: true,
  configured: true,
  username: 'director',
  csrf_token: 'test-csrf',
  expires_at: 9999999999
};
const job = {
  job_id: 'review-1',
  title: 'Why the sky changes color',
  topic: 'The sky',
  category: 'science',
  status: 'pending_review',
  image_mode: 'text',
  video_exists: true,
  scene_count: 2,
  duration_seconds: 30,
  created_at: '2026-11-03T12:00:00Z'
};
const approved = {
  ...job,
  job_id: 'approved-1',
  title: 'A story worth sharing',
  status: 'approved'
};
const plan = {
  title: job.title,
  hook: 'Look up. There’s a story in the sky.',
  description: 'An original explanation.',
  hashtags: ['#Science'],
  scenes: [{
    scene_number: 1,
    duration_seconds: 15,
    narration: 'The first part of our story.',
    visual_prompt: 'A blue sky',
    on_screen_text: 'Look up'
  }, {
    scene_number: 2,
    duration_seconds: 15,
    narration: 'The rest of the story.',
    visual_prompt: 'An orange sunset',
    on_screen_text: 'At sunset'
  }]
};
function overview(overrides = {}) {
  return {
    summary: {
      total_jobs: 2,
      pending_review: 1,
      approved: 1,
      active_jobs: 0,
      failed_jobs: 0,
      publication_attempts: 0,
      reported_views: 0
    },
    jobs: [job, approved],
    publications: [],
    analytics: [],
    category_performance: [],
    platform_settings: {
      youtube_privacy: 'private'
    },
    ...overrides
  };
}
function response(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body
  };
}
let state;
let handler;
beforeEach(() => {
  state = overview();
  handler = vi.fn(async (url, options = {}) => {
    if (url.endsWith('/auth/session')) return response(session);
    if (url.endsWith('/dashboard')) return response(state);
    if (url.endsWith('/details')) {
      const id = url.split('/').at(-2);
      const record = state.jobs.find(j => j.job_id === id);
      return response({
        job: record,
        plan: {
          ...plan,
          title: record?.title
        },
        video_exists: record?.video_exists,
        manifest: {
          scenes: []
        },
        publications: state.publications
      });
    }
    if (url.endsWith('/review')) {
      const body = JSON.parse(options.body);
      state = {
        ...state,
        jobs: state.jobs.map(j => j.job_id === 'review-1' ? {
          ...j,
          status: body.decision
        } : j)
      };
      return response({
        ...job,
        status: body.decision
      });
    }
    if (url.endsWith('/publish')) return response({
      status: JSON.parse(options.body).dry_run ? 'dry_run' : 'uploaded'
    });
    if (url.endsWith('/auth/logout')) return response({
      authenticated: false
    });
    throw new Error(`Unexpected request ${url}`);
  });
  vi.stubGlobal('fetch', handler);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
async function openPage(name) {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole('heading', {
    name: 'Your creative command center.'
  });
  if (name) await user.click(within(screen.getByRole('navigation', {
    name: 'Main navigation'
  })).getByRole('button', {
    name
  }));
  return user;
}
const mutations = () => handler.mock.calls.filter(([, options]) => options?.method === 'POST');
describe('private studio', () => {
  it('signs in with a username/password and never asks for an API key', async () => {
    handler.mockImplementation(async url => url.endsWith('/auth/session') ? response({
      authenticated: false,
      configured: true
    }) : url.endsWith('/auth/login') ? response(session) : response(state));
    const user = userEvent.setup();
    render(<App />);
    await user.type(await screen.findByLabelText('Username'), 'director');
    await user.type(screen.getByLabelText('Password'), 'a-long-private-password');
    await user.click(screen.getByRole('button', {
      name: 'Enter your studio'
    }));
    await screen.findByRole('heading', {
      name: 'Your creative command center.'
    });
    const [, options] = handler.mock.calls.find(([url]) => url.endsWith('/auth/login'));
    expect(JSON.parse(options.body)).toEqual({
      username: 'director',
      password: 'a-long-private-password'
    });
    expect(options.credentials).toBe('same-origin');
    expect(screen.queryByLabelText(/api key/i)).not.toBeInTheDocument();
    expect(localStorage.length).toBe(0);
  });
  it('displays actual workspace data and an honest missing-analytics state', async () => {
    await openPage();
    expect(await screen.findByText('Generation jobs')).toBeInTheDocument();
    expect(screen.getByText('Your audience story is next')).toBeInTheDocument();
    expect(mutations()).toHaveLength(0);
  });
  it('requires a human review check and approval never calls publishing', async () => {
    const user = await openPage(/Review queue/);
    const approve = await screen.findByRole('button', {
      name: 'Approve video'
    });
    expect(approve).toBeDisabled();
    await user.click(screen.getByRole('checkbox', {
      name: /I’ve watched the video/
    }));
    await user.click(approve);
    await waitFor(() => expect(handler.mock.calls.some(([url]) => url.endsWith('/review'))).toBe(true));
    const reviews = mutations().filter(([url]) => url.endsWith('/review'));
    expect(reviews).toHaveLength(1);
    expect(JSON.parse(reviews[0][1].body)).toEqual({
      decision: 'approved'
    });
    expect(reviews[0][1].headers['X-CSRF-Token']).toBe('test-csrf');
    expect(handler.mock.calls.some(([url]) => url.endsWith('/publish'))).toBe(false);
  });
  it('shows missing scene visuals and provenance rather than inventing them', async () => {
    const user = await openPage(/Review queue/);
    await user.click(await screen.findByRole('button', {
      name: /Look up/
    }));
    expect(screen.getByText('Scene visual unavailable')).toBeInTheDocument();
    expect(screen.getByText('Source provenance not recorded')).toBeInTheDocument();
    expect(screen.getByText('License information unavailable')).toBeInTheDocument();
  });
  it('uses saved generation settings for the reviewed video format and output metadata', async () => {
    state = overview({ jobs: [{ ...job, generation_config: { aspect_ratio: '16:9', resolution: '720p', fps: 24 } }, approved] });
    await openPage(/Review queue/);
    expect(await screen.findByText('Landscape · 16:9')).toBeInTheDocument();
    expect(screen.getByText('720p · 24 fps')).toBeInTheDocument();
    expect(screen.getByLabelText('Generated video preview').parentElement).toHaveStyle({ aspectRatio: '16 / 9' });
    expect(mutations()).toHaveLength(0);
  });
  it('defaults to a dry run and requires an additional explicit action for a live upload', async () => {
    const user = await openPage('Publishing');
    const preflight = await screen.findByRole('button', {
      name: 'Run preflight check'
    });
    expect(preflight).toBeDisabled();
    await user.click(screen.getByRole('checkbox', {
      name: /I confirm this approved video/
    }));
    await user.click(preflight);
    await screen.findByText('Preflight complete.');
    expect(JSON.parse(mutations().find(([url]) => url.endsWith('/publish'))[1].body)).toEqual({
      platform: 'youtube',
      dry_run: true,
      confirmed: false
    });
    await user.click(screen.getByRole('button', {
      name: 'Live upload'
    }));
    await user.click(screen.getByRole('checkbox', {
      name: /I confirm this approved video/
    }));
    await user.click(screen.getByRole('button', {
      name: 'Review upload'
    }));
    const dialog = screen.getByRole('dialog');
    expect(mutations().filter(([url]) => url.endsWith('/publish'))).toHaveLength(1);
    await user.click(within(dialog).getByRole('button', {
      name: 'Authorize upload'
    }));
    await waitFor(() => expect(mutations().filter(([url]) => url.endsWith('/publish'))).toHaveLength(2));
    expect(JSON.parse(mutations().filter(([url]) => url.endsWith('/publish'))[1][1].body)).toEqual({
      platform: 'youtube',
      dry_run: false,
      confirmed: true
    });
  });
  it('blocks an existing publication attempt even when it needs reconciliation', async () => {
    state = overview({
      publications: [{
        publication_id: 'attempt-1',
        job_id: 'approved-1',
        platform: 'youtube',
        status: 'needs_reconciliation',
        created_at: job.created_at
      }]
    });
    await openPage('Publishing');
    expect(await screen.findByText('An upload attempt already exists.')).toBeInTheDocument();
    expect(screen.getByRole('button', {
      name: 'Run preflight check'
    })).toBeDisabled();
    expect(mutations()).toHaveLength(0);
  });
  it('allows a manually authorized Facebook retry only after separate live confirmation', async () => {
    state=overview({publications:[{publication_id:'attempt-1',job_id:'approved-1',platform:'facebook',status:'retry_authorized',platform_video_id:null,created_at:job.created_at}]});
    const user=await openPage('Publishing');
    await user.click(screen.getByRole('button',{name:/Facebook Page Reels/}));
    await screen.findByText(/A manually inspected Facebook retry is authorized/);
    expect(mutations()).toHaveLength(0);
    await user.click(screen.getByRole('button',{name:'Live upload'}));
    await user.click(screen.getByRole('checkbox',{name:/I confirm this approved video/}));
    await user.click(screen.getByRole('button',{name:'Review upload'}));
    expect(mutations()).toHaveLength(0);
    await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'Authorize upload'}));
    await waitFor(()=>expect(mutations().filter(([url])=>url.endsWith('/publish'))).toHaveLength(1));
    expect(JSON.parse(mutations().find(([url])=>url.endsWith('/publish'))[1].body)).toEqual({platform:'facebook',dry_run:false,confirmed:true});
  });
  it('does not retry an uncertain upload and blocks a second request after navigation', async () => {
    const previous = handler.getMockImplementation();
    handler.mockImplementation(async (url, options) => url.endsWith('/publish') && JSON.parse(options.body).dry_run === false ? response({
      detail: 'Upload outcome uncertain'
    }, 502) : previous(url, options));
    const user = await openPage('Publishing');
    await screen.findByRole('button', {
      name: 'Run preflight check'
    });
    await user.click(screen.getByRole('button', {
      name: 'Live upload'
    }));
    await user.click(screen.getByRole('checkbox', {
      name: /I confirm this approved video/
    }));
    await user.click(screen.getByRole('button', {
      name: 'Review upload'
    }));
    await user.click(within(screen.getByRole('dialog')).getByRole('button', {
      name: 'Authorize upload'
    }));
    await screen.findByText('This attempt needs inspection.');
    expect(mutations().filter(([url]) => url.endsWith('/publish'))).toHaveLength(1);
    await user.click(within(screen.getByRole('navigation')).getByRole('button', {
      name: 'Overview'
    }));
    await user.click(within(screen.getByRole('navigation')).getByRole('button', {
      name: 'Publishing'
    }));
    await screen.findByText('An upload has already been requested.');
    expect(screen.getByRole('button', {
      name: 'Run preflight check'
    })).toBeDisabled();
    expect(screen.queryByRole('checkbox', {
      name: /I confirm this approved video/
    })).not.toBeInTheDocument();
    expect(mutations().filter(([url]) => url.endsWith('/publish'))).toHaveLength(1);
  });
});
describe('publication eligibility', () => {
  it('requires approval, a rendered file, and no reservation for the chosen platform', () => {
    expect(canPublish(job, [], 'youtube')).toBe(false);
    expect(canPublish({
      ...approved,
      video_exists: false
    }, [], 'youtube')).toBe(false);
    expect(canPublish(approved, [{
      job_id: approved.job_id,
      platform: 'youtube',
      status: 'failed'
    }], 'youtube')).toBe(false);
    expect(canPublish(approved, [{
      job_id: approved.job_id,
      platform: 'youtube',
      status: 'uploaded'
    }], 'facebook')).toBe(true);
  });
});
