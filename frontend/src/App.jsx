import React, { useCallback, useEffect, useRef, useState } from 'react';
import GenerationForm from './GenerationForm.jsx';
import LocalVideoStudio from './LocalVideoStudio.jsx';
import ClipStudio from './ClipStudio.jsx';
const API = '/api/v1';
const ACTIVE = new Set(['queued', 'discovering', 'planning', 'rendering', 'processing']);
const labels = {
  pending_review: 'Awaiting review',
  awaiting_quality_review: 'Quality inspection needed',
  awaiting_shot_selection: 'Choose a shot version',
  needs_reconciliation: 'Needs reconciliation',
  retry_authorized: 'Manual retry authorized',
  dry_run: 'Dry run',
  queued: 'Queued',
  discovering: 'Finding trends',
  planning: 'Writing script',
  rendering: 'Rendering',
  approved: 'Approved',
  rejected: 'Rejected',
  failed: 'Failed',
  published: 'Published',
  private: 'Private',
  uploaded: 'Uploaded',
  submitted: 'Submitted',
  processing: 'Processing',
  uploading: 'Uploading',
  pending: 'Reserved'
};
const titles = {
  overview: ['Your creative command center.', 'A clear view of what’s creating, what’s ready, and what comes next.'],
  create: ['Your next video starts here.', 'A format, a story, and the right models. Make every choice intentional.'],
  local: ['Bring your story to life.', 'Plan a screenplay, direct your cast, and create moving video on your DGX Spark.'],
  clips: ['Give your source a new direction.', 'Create, compare and refine individual clips in Clip Studio.'],
  review: ['Good content. Your final call.', 'Watch the video, check the story, and decide what deserves an audience.'],
  publish: ['Ready when you are.', 'Give approved work its next chapter. Every upload starts with you.']
};
export function Icon({
  name,
  size = 20,
  ...props
}) {
  const paths = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
    film: <><rect x="3" y="3" width="18" height="18" rx="3" /><path d="M7 3v18M17 3v18M3 8h4m10 0h4M3 16h4m10 0h4" /></>,
    upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5" /></>,
    arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
    arrowUp: <path d="M7 17 17 7M7 7h10v10" />,
    chevron: <path d="m9 5 7 7-7 7" />,
    check: <path d="m5 12 4 4L19 6" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    play: <path d="m9 5 11 7-11 7V5Z" />,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6.1 6.1a8 8 0 0 1 13.3 3M4.6 14.9a8 8 0 0 0 13.3 3" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    eye: <><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></>,
    shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z" /><path d="m8 12 3 3 5-6" /></>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" /></>,
    logout: <><path d="M9 4H5v16h4m5-12 4 4-4 4M9 12h10" /></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
    spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z" /></>,
    warn: <><path d="m12 3 10 18H2L12 3Z" /><path d="M12 9v5m0 3v.1" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    chart: <><path d="M4 3v18h17M8 16v-4m5 4V8m5 8V5" /></>,
    youtube: <><rect x="2" y="5" width="20" height="14" rx="4" /><path d="m10 9 5 3-5 3V9Z" /></>,
    facebook: <path d="M14 21v-8h3l.5-4H14V7c0-1 .5-2 2-2h2V2h-3c-3 0-5 2-5 5v2H7v4h3v8" />,
    file: <><path d="M14 2H5v20h14V7l-5-5Z" /><path d="M14 2v6h5M8 12h8m-8 4h6" /></>,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    music: <><path d="M9 18V5l12-2v13M9 8l12-2" /><ellipse cx="6" cy="18" rx="3" ry="3" /><ellipse cx="18" cy="16" rx="3" ry="3" /></>
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name] || paths.film}</svg>;
}
export function Status({
  status
}) {
  return <span className={`status status-${status}`}><span />{labels[status] || status?.replaceAll('_', ' ') || 'Unknown'}</span>;
}
export function formatNumber(value) {
  return value == null ? '—' : Intl.NumberFormat('en', {
    notation: Number(value) > 9999 ? 'compact' : 'standard',
    maximumFractionDigits: 1
  }).format(value);
}
function timeAgo(value) {
  if (!value) return 'No date';
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value)) / 60000));
  return minutes < 1 ? 'Just now' : minutes < 60 ? `${minutes}m ago` : minutes < 1440 ? `${Math.floor(minutes / 60)}h ago` : new Date(value).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric'
  });
}
function dateLabel(value) {
  return value ? new Date(value).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit'
  }) : '—';
}
function safeExternal(url) {
  try {
    return ['https:', 'http:'].includes(new URL(url).protocol) ? url : null;
  } catch {
    return null;
  }
}
function Brand({
  light = false
}) {
  return <div className={`brand ${light ? 'brand-light' : ''}`}><span className="brand-mark"><span /><Icon name="play" size={19} /></span><div>content<span>agent</span><small>THE CREATOR’S STUDIO</small></div></div>;
}
function Empty({
  icon = 'film',
  title,
  children,
  action
}) {
  return <div className="empty"><div className="empty-icon"><Icon name={icon} size={30} /></div><h3>{title}</h3><p>{children}</p>{action}</div>;
}
function Notice({
  children,
  type = 'info'
}) {
  return <div className={`notice notice-${type}`} role={type === 'error' ? 'alert' : undefined}><Icon name={type === 'error' || type === 'warning' ? 'warn' : 'shield'} size={18} /><div>{children}</div></div>;
}
export default function App() {
  const [session, setSession] = useState(null);
  const [bootError, setBootError] = useState('');
  const [page, setPage] = useState(() => window.location.hash.startsWith('#clip-studio') ? 'clips' : 'overview');
  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [selected, setSelected] = useState(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [toast, setToast] = useState('');
  const liveAttempts = useRef(new Set());
  const request = useCallback(async (path, options = {}) => {
    let response;
    if (options.method === 'POST' && path.endsWith('/publish') && options.body) {
      const body = JSON.parse(options.body);
      if (body.dry_run === false) {
        const key = `${path}:${body.platform}`;
        if (liveAttempts.current.has(key)) throw new Error('An upload has already been requested in this session. Inspect the publication record before another attempt.');
        liveAttempts.current.add(key);
      }
    }
    try {
      response = await fetch(`${API}${path}`, {
        credentials: 'same-origin',
        ...options,
        headers: {
          ...(options.body && !(options.body instanceof FormData) ? {
            'Content-Type': 'application/json'
          } : {}),
          ...(options.method && options.method !== 'GET' && session?.csrf_token ? {
            'X-CSRF-Token': session.csrf_token
          } : {}),
          ...options.headers
        }
      });
    } catch {
      throw new Error('Cannot reach your studio. Check that the local server is running, then refresh.');
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401 && path !== '/auth/login') {
        setSession({
          authenticated: false,
          configured: true
        });
        setData(null);
      }
      const detail = typeof body.detail === 'string' ? body.detail : 'The request could not be completed. Please check your inputs.';
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return body;
  }, [session?.csrf_token]);
  const boot = useCallback(async () => {
    setBootError('');
    try {
      setSession(await request('/auth/session'));
    } catch (error) {
      setBootError(error.message);
    }
  }, [request]);
  useEffect(() => {
    boot();
  }, []); // Session is restored only on initial load, never by repeating a mutation.
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const result = await request('/dashboard');
      setData(result);
      setLoadError('');
      return result;
    } catch (error) {
      setLoadError(error.message);
      throw error;
    } finally {
      setRefreshing(false);
    }
  }, [request]);
  useEffect(() => {
    if (!session?.authenticated) return;
    refresh().catch(() => {});
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') refresh().catch(() => {});
    }, 15000);
    return () => window.clearInterval(timer);
  }, [session?.authenticated, refresh]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(''), 7000);
    return () => clearTimeout(timer);
  }, [toast]);
  function navigate(next, jobId = null) {
    if (next === 'clips') window.history.replaceState(null, '', '#clip-studio');
    else if (window.location.hash.startsWith('#clip-studio')) window.history.replaceState(null, '', window.location.pathname + window.location.search);
    setPage(next);
    setSelected(jobId);
    setMobileNav(false);
  }
  async function logout() {
    try {
      await request('/auth/logout', {
        method: 'POST'
      });
      setSession({
        authenticated: false,
        configured: true
      });
      setData(null);
      setPage('overview');
      setSelected(null);
    } catch (error) {
      setToast(error.message);
    }
  }
  if (!session) return <div className="boot"><Brand /><div className="spinner" /><p>{bootError || 'Opening your studio…'}</p>{bootError && <button className="button primary" onClick={boot}>Try again</button>}</div>;
  if (!session.authenticated) return <Login configured={session.configured} request={request} onLogin={setSession} />;
  const summary = data?.summary || {};
  return <div className="app-shell">
    {mobileNav && <button className="nav-backdrop" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}
    <aside className={`sidebar ${mobileNav ? 'sidebar-open' : ''}`}>
      <Brand light />
      <div className="workspace-label"><span className="workspace-dot" /> YOUR WORKSPACE <span className="local-tag">LOCAL</span></div>
      <nav aria-label="Main navigation">
        {[['overview', 'grid', 'Overview'], ['create', 'plus', 'Create video'], ['local', 'spark', 'Local Video Studio'], ['clips', 'film', 'Clip Studio'], ['review', 'film', 'Review queue'], ['publish', 'upload', 'Publishing']].map(([id, icon, label]) => <button key={id} className={`nav-item ${page === id ? 'active' : ''}`} onClick={() => navigate(id)} aria-current={page === id ? 'page' : undefined}><Icon name={icon} /><span>{label}</span>{id === 'review' && summary.pending_review > 0 && <span className="nav-count">{summary.pending_review}</span>}{page === id && id !== 'review' && <span className="nav-active-dot" />}</button>)}
      </nav>
      <div className="sidebar-note"><div className="sidebar-note-icon"><Icon name="shield" size={23} /><span className="little-star">✳</span></div><h3>Created by AI.<br />Directed by you.</h3><p>You have the final say.<br />Nothing publishes on its own.</p><span className="sidebar-note-rule" /></div>
      <div className="sidebar-bottom"><div className="connection"><span />Local studio <small>DGX SPARK</small></div><div className="profile"><span className="avatar">{session.username?.slice(0, 1).toUpperCase() || 'A'}</span><div><strong>{session.username}</strong><small>Studio administrator</small></div><button onClick={logout} className="icon-button" title="Sign out" aria-label="Sign out"><Icon name="logout" size={19} /></button></div></div>
    </aside>
    <main className="main-content">
      <header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMobileNav(true)}><Icon name="menu" /></button><span>Workspace</span><Icon name="chevron" size={13} /><strong>{{ overview: 'Overview', create: 'Create video', local: 'Local Video Studio', clips: 'Clip Studio', review: 'Review queue', publish: 'Publishing' }[page]}</strong></div><div className="topbar-right"><span className="private-label"><Icon name="lock" size={13} />Private workspace</span><button className={`icon-button ${refreshing ? 'rotating' : ''}`} aria-label="Refresh workspace" disabled={refreshing} onClick={() => refresh().catch(() => {})}><Icon name="refresh" size={17} /></button><span className="top-avatar">{session.username?.slice(0, 1).toUpperCase() || 'A'}</span></div></header>
      <div className="page-content">
        <div className="page-heading"><div><div className="eyebrow">{{ overview: 'MAKE SOMETHING WORTH WATCHING', create: 'FROM IDEA TO FIRST FRAME', local: 'LOCAL MODELS · YOUR DIRECTION', clips: 'CLIP STUDIO · YOUR SOURCE, NEW DIRECTIONS', review: 'THE HUMAN TOUCH', publish: 'THE FINAL STEP' }[page]}</div><h1>{titles[page][0]}</h1><p>{titles[page][1]}</p></div>{page === 'overview' && <button className="button primary" onClick={() => navigate('create')}><Icon name="plus" size={17} />Create video</button>}</div>
        {loadError && <div className="load-error"><Notice type="error">{loadError} {data && 'Showing the last loaded data.'}</Notice><button className="button subtle" onClick={() => refresh().catch(() => {})}>Refresh</button></div>}
        {page === 'clips' ? <ClipStudio request={request} onNavigate={navigate} /> : page === 'local' ? <LocalVideoStudio request={request} /> : page === 'create' ? <GenerationForm onLocalStudio={() => navigate('local')} request={request} onCreated={async () => {
          setToast('Generation requested. Follow progress in the overview, then review your video.');
          await refresh().catch(() => {});
        }} /> : !data ? <div className="loading-panel"><div className="spinner" /><p>Loading your workspace…</p></div> : page === 'overview' ? <Overview data={data} navigate={navigate} /> : page === 'review' ? <Review data={data} selected={selected} setSelected={setSelected} request={request} refresh={refresh} notify={setToast} /> : <Publishing data={data} selected={selected} setSelected={setSelected} request={request} refresh={refresh} notify={setToast} liveAttempts={liveAttempts.current} />}
        <footer className="page-footer"><span><span className="tiny-mark">✳</span> Content Agent Studio</span><span>Original content. Human judgment.</span></footer>
      </div>
    </main>
    {toast && <div className="toast" role="status"><Icon name="check" size={19} /><span>{toast}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => setToast('')}><Icon name="close" size={16} /></button></div>}
  </div>;
}
function Login({
  configured,
  request,
  onLogin
}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await request('/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          username,
          password
        })
      });
      setPassword('');
      onLogin(result);
    } catch (error) {
      setError(error.message);
    } finally {
      setBusy(false);
    }
  }
  return <div className="login-page"><section className="login-story"><Brand light /><div className="login-story-content"><span className="pill dark-pill"><span />YOUR IDEAS, IN MOTION</span><h1>A little artificial<br />intelligence.<br /><em>A lot of you.</em></h1><p>From the first spark to the final frame.<br />Your private space to create, curate, and share.</p><div className="orbit-art" aria-hidden="true"><div className="orbit-ring ring-one" /><div className="orbit-ring ring-two" /><div className="orbit-center"><Icon name="play" size={43} /></div><span className="orbit-spark">✳</span><div className="orbit-card"><span className="orbit-card-bars"><i /><i /><i /><i /><i /><i /><i /></span><span>Made with intention.</span></div><span className="orbit-note">A STUDIO. NOT AN AUTOPILOT.</span></div></div><div className="login-story-footer"><Icon name="shield" size={16} />Your content. Your approval. Always.</div></section><section className="login-form-side"><div className="login-top-note"><Icon name="lock" size={14} />LOCAL-FIRST · SINGLE ADMIN</div><div className="login-form-wrap"><div className="login-welcome-icon"><Icon name="spark" size={28} /></div><div className="eyebrow">WELCOME TO YOUR STUDIO</div><h2>{configured ? 'Back to creating.' : 'Make it yours.'}</h2><p>{configured ? 'Sign in to pick up where your ideas left off.' : 'Set up your private admin account to open the studio.'}</p>{configured ? <form onSubmit={submit}><label htmlFor="username">Username</label><input id="username" name="username" autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} placeholder="Your admin username" required maxLength={64} /><label htmlFor="password">Password</label><div className="password-input"><input id="password" name="password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Enter your password" required maxLength={1024} /><button type="button" className="icon-button" aria-label={showPassword ? 'Hide password' : 'Show password'} onClick={() => setShowPassword(!showPassword)}><Icon name="eye" size={18} /></button></div>{error && <p className="form-error" role="alert">{error}</p>}<button className="button primary login-submit" disabled={busy || !username.trim() || !password}>{busy ? 'Signing in…' : 'Enter your studio'}<Icon name="arrow" size={18} /></button><div className="login-session-note"><Icon name="shield" size={15} />One secure sign-in. No repeated API keys.</div></form> : <div className="setup-panel"><p>On your DGX Spark, run:</p><code>python scripts/setup_admin.py</code><p>Create your username and password in the terminal, then refresh this page. Registration is intentionally closed.</p><button className="button primary" onClick={() => window.location.reload()}>I’ve created my account<Icon name="arrow" size={17} /></button></div>}<div className="login-help">{configured ? <>Need to reset your password? Run <code>python scripts/setup_admin.py --reset</code> on your machine.</> : 'Credentials stay on your machine. They are never sent to a third-party service.'}</div></div><span className="login-copyright">CONTENT AGENT STUDIO <span>BUILT FOR ORIGINAL THINKING</span></span></section></div>;
}
function Overview({
  data,
  navigate
}) {
  const s = data.summary;
  const jobs = data.jobs || [];
  const analytics = data.analytics || [];
  const pending = jobs.filter(j => j.status === 'pending_review');
  const categoryCounts = Object.entries(jobs.reduce((acc, job) => ({
    ...acc,
    [job.category]: (acc[job.category] || 0) + 1
  }), {})).sort((a, b) => b[1] - a[1]);
  const categoriesMax = Math.max(1, ...categoryCounts.map(([, count]) => count));
  return <>
    <section className="hero-banner"><div className="hero-copy"><span className="hero-kicker"><span className="pulse-dot" />YOUR STUDIO, AT A GLANCE</span><h2>{s.pending_review > 0 ? <>Your next great video<br />is waiting for <em>your eyes.</em></> : s.active_jobs > 0 ? <>A new idea is<br /><em>taking shape.</em></> : <>Big ideas start<br />with a <em>little spark.</em></>}</h2><p>{s.pending_review > 0 ? `${s.pending_review} ${s.pending_review === 1 ? 'video is' : 'videos are'} ready for a closer look. Make the final cut.` : 'Create something original. Review every detail. Share it on your terms.'}</p><button className="button hero-button" onClick={() => navigate('review')}>{s.pending_review > 0 ? 'Open review queue' : 'Visit review queue'}<Icon name="arrow" size={17} /></button></div><div className="hero-art" aria-hidden="true"><span className="hero-art-label">FRAME BY FRAME.<br />IDEA BY IDEA.</span><div className="art-orbit" /><div className="art-frame frame-back" /><div className="art-frame frame-front"><div className="frame-corner" /><span className="frame-time">9:16</span><div className="frame-play"><Icon name="play" size={24} /></div><div className="frame-wave"><i /><i /><i /><i /><i /><i /><i /><i /><i /></div></div><span className="art-star">✳</span><span className="art-plus">+</span></div></section>
    <section className="metric-grid" aria-label="Workspace statistics">{[['film', 'Generation jobs', s.total_jobs, 'Your ideas, brought to life', 'neutral'], ['eye', 'Awaiting review', s.pending_review, 'Ready for your final call', 'amber'], ['check', 'Approved videos', s.approved, 'Curated and ready to share', 'green'], ['chart', 'Reported views', s.reported_views, 'Latest available platform data', 'purple']].map(([icon, label, value, description, tone]) => <article className="metric-card" key={label}><div className="metric-top"><span>{label}</span><span className={`metric-icon ${tone}`}><Icon name={icon} size={18} /></span></div><strong>{formatNumber(value)}</strong><small>{description}</small></article>)}</section>
    <div className="overview-columns"><section className="panel recent-panel"><div className="section-heading"><div><h2>Fresh from the studio <span className="count-label">{jobs.length}</span></h2><p>Your latest content, from first draft to final approval.</p></div><button className="text-button" onClick={() => navigate('review')}>Review queue<Icon name="arrow" size={15} /></button></div>{!jobs.length ? <Empty title="Your first story starts here">Create a video to bring your workspace to life. Finished videos will appear here for your review.</Empty> : <div className="job-table-wrap"><table className="job-table"><thead><tr><th>VIDEO</th><th>STATUS</th><th>CREATED</th><th aria-label="Open video" /></tr></thead><tbody>{jobs.slice(0, 6).map((job, index) => <tr key={job.job_id}><td><button className="table-video" onClick={() => navigate(job.status === 'approved' ? 'publish' : 'review', job.job_id)}><span className={`mini-poster poster-${index % 4}`}><Icon name={ACTIVE.has(job.status) ? 'spark' : 'play'} size={18} /></span><span><strong>{job.title || job.topic || `${capitalize(job.category)} video`}</strong><small>{capitalize(job.category)}<span>·</span>{job.duration_seconds ? `${job.duration_seconds}s` : 'In progress'}</small></span></button></td><td><Status status={job.status} /></td><td className="muted-cell">{timeAgo(job.created_at)}</td><td><button className="icon-button" aria-label={`Open ${job.title || 'video'}`} onClick={() => navigate(job.status === 'approved' ? 'publish' : 'review', job.job_id)}><Icon name="arrowUp" size={16} /></button></td></tr>)}</tbody></table></div>}</section><section className="panel workflow-panel"><div className="section-heading"><div><h2>In the pipeline</h2><p>A little structure. A lot of possibility.</p></div><Icon name="spark" size={19} /></div><div className="pipeline"><PipelineStep number="01" title="Create" description="Ideas become original videos" count={s.active_jobs || 0} label="in progress" /><PipelineStep number="02" title="Curate" description="You decide what makes the cut" count={s.pending_review || 0} label="awaiting you" /><PipelineStep number="03" title="Share" description="One separate, intentional action" count={s.approved || 0} label="approved" last /></div><div className="pipeline-foot"><Icon name="shield" size={16} /><p>Approval never starts an upload.<br /><strong>You’re always in the director’s chair.</strong></p></div>{s.failed_jobs > 0 && <div className="failed-count"><Icon name="warn" size={15} />{s.failed_jobs} generation {s.failed_jobs === 1 ? 'job needs' : 'jobs need'} attention</div>}</section></div>
    <div className="overview-columns bottom-columns"><section className="panel"><div className="section-heading"><div><h2>What you’re creating</h2><p>Topics across your latest content.</p></div><span className="soft-tag">BY CATEGORY</span></div>{categoryCounts.length ? <div className="category-bars">{categoryCounts.slice(0, 5).map(([category, count], i) => <div className="category-row" key={category}><div><span><i style={{
                  background: ['#709e6b', '#a895cf', '#d5ae64', '#70a9b2', '#b78679'][i]
                }} />{capitalize(category)}</span><strong>{count} {count === 1 ? 'video' : 'videos'}</strong></div><div className="bar-track"><span style={{
                width: `${count / categoriesMax * 100}%`,
                background: ['#b8d4a6', '#d6c8ed', '#e9d6a8', '#b7d6d9', '#dbb9b0'][i]
              }} /></div></div>)}</div> : <Empty icon="chart" title="A blank canvas, for now">Your topic mix will grow as you create videos.</Empty>}</section><section className="panel"><div className="section-heading"><div><h2>Out in the world</h2><p>Actual performance from your connected platforms.</p></div><Icon name="arrowUp" size={19} /></div>{analytics.length ? <div className="performance-list">{analytics.slice(0, 4).map(item => {
            const publication = data.publications.find(p => p.publication_id === item.publication_id);
            const job = jobs.find(j => j.job_id === publication?.job_id);
            return <div className="performance-item" key={item.publication_id}><span className={`platform-symbol ${item.platform}`}><Icon name={item.platform} size={18} /></span><div><strong>{job?.title || 'Published video'}</strong><small>{capitalize(item.platform)} · {dateLabel(item.collected_at)}</small></div><strong>{formatNumber(item.views)}<small>views</small></strong></div>;
          })}<p className="data-note">Missing platform metrics are shown as —.</p></div> : <Empty icon="chart" title="Your audience story is next">Once you publish and collect analytics, real performance will appear here.</Empty>}</section></div>
  </>;
}
function capitalize(value) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1).replaceAll('_', ' ') : 'Uncategorized';
}
function PipelineStep({
  number,
  title,
  description,
  count,
  label,
  last
}) {
  return <div className={`pipeline-step ${last ? 'last' : ''}`}><span className="pipeline-number">{number}</span><div><h3>{title}</h3><p>{description}</p><span className="pipeline-count"><strong>{count}</strong> {label}</span></div></div>;
}
function useDetails(selected, request, revision) {
  const [details, setDetails] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let current = true;
    setDetails(null);
    setError('');
    if (!selected) {
      setLoading(false);
      return;
    }
    setLoading(true);
    request(`/jobs/${encodeURIComponent(selected)}/details`).then(result => {
      if (current) setDetails(result);
    }).catch(err => {
      if (current) setError(err.message);
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => {
      current = false;
    };
  }, [selected, request, revision]);
  return {
    details,
    error,
    loading
  };
}
function Review({
  data,
  selected,
  setSelected,
  request,
  refresh,
  notify
}) {
  const [filter, setFilter] = useState('pending_review');
  const [search, setSearch] = useState('');
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState('');
  const [actionError, setActionError] = useState('');
  const [revision, setRevision] = useState(0);
  const jobs = data.jobs || [];
  const visible = jobs.filter(j => (filter === 'all' || j.status === filter) && `${j.title || ''} ${j.category || ''} ${j.topic || ''}`.toLowerCase().includes(search.toLowerCase()));
  useEffect(() => {
    if (selected && !jobs.some(j => j.job_id === selected)) setSelected(null);
  }, [jobs, selected, setSelected]);
  useEffect(() => {
    if (!selected && visible.length) setSelected(visible[0].job_id);
  }, [selected, visible, setSelected]);
  useEffect(() => {
    setChecked(false);
    setActionError('');
  }, [selected]);
  const currentJob = jobs.find(record => record.job_id === selected);
  const detailsRevision = `${revision}:${currentJob?.updated_at}:${currentJob?.status}:${currentJob?.video_exists}`;
  const {
    details,
    error,
    loading
  } = useDetails(selected, request, detailsRevision);
  async function decide(decision) {
    if (!checked || busy || !details) return;
    setBusy(decision);
    setActionError('');
    try {
      await request(`/jobs/${encodeURIComponent(selected)}/review`, {
        method: 'POST',
        body: JSON.stringify({
          decision
        })
      });
      setChecked(false);
      notify(decision === 'approved' ? 'Video approved. It has not been published.' : 'Video rejected. Nothing was published.');
      setSelected(null);
      await refresh().catch(() => {});
      setRevision(v => v + 1);
    } catch (err) {
      setActionError(`${err.message} Refresh the job before making another decision.`);
      setChecked(false);
      await refresh().catch(() => {});
      setRevision(v => v + 1);
    } finally {
      setBusy('');
    }
  }
  return <><div className="queue-toolbar"><div className="segmented" aria-label="Filter videos">{[['pending_review', 'Awaiting review'], ['all', 'All videos']].map(([id, label]) => <button key={id} className={filter === id ? 'selected' : ''} onClick={() => {
          setFilter(id);
          setSelected(null);
        }}>{label}<span>{filter === id || id === 'pending_review' ? id === 'all' ? jobs.length : jobs.filter(j => j.status === id).length : jobs.length}</span></button>)}</div><div className="search-field"><Icon name="search" size={17} /><input aria-label="Search videos" value={search} onChange={e => setSearch(e.target.value)} placeholder="Find a video…" /></div></div><div className="review-layout"><aside className="queue-list" aria-label="Video queue"><div className="queue-list-label">YOUR VIDEOS <span>{visible.length}</span></div>{visible.length ? visible.map((job, index) => <button key={job.job_id} className={`queue-card ${selected === job.job_id ? 'selected' : ''}`} onClick={() => setSelected(job.job_id)}><div className={`queue-poster poster-${index % 4}`}><Icon name="play" size={22} /><span>{job.duration_seconds ? `${job.duration_seconds}s` : '—'}</span></div><div><span className="queue-category">{capitalize(job.category)}</span><h3>{job.title || job.topic || 'Video in progress'}</h3><Status status={job.status} /><small>{timeAgo(job.created_at)}{job.scene_count ? ` · ${job.scene_count} scenes` : ''}</small></div></button>) : <div className="small-empty">{search ? 'No matching videos.' : 'All caught up. Your next video will appear here.'}</div>}</aside><section className="review-workspace">{loading ? <div className="loading-panel"><div className="spinner" /><p>Opening video…</p></div> : error ? <Notice type="error">{error}</Notice> : !details ? <Empty icon="check" title={search ? 'No matching videos' : 'Your queue is clear.'}>{search ? 'Try a different title or category.' : 'A little breathing room. New videos will arrive here when generation finishes.'}</Empty> : <><div className="workspace-section-heading"><div><span className="eyebrow">{capitalize(details.job.category)} · {details.plan?.total_duration || sumDuration(details.plan)} SECOND STORY</span><h2>{details.plan?.title || details.job.title || 'Video in progress'}</h2></div><Status status={details.job.status} /></div><div className="preview-and-meta"><VideoPreview details={details} /><div className="video-info"><div className="section-label"><Icon name="file" size={16} />THE STORY</div><h3>{details.plan?.hook || 'Content plan'}</h3><p>{details.plan?.description || 'The content plan is not available yet.'}</p><div className="hashtags">{details.plan?.hashtags?.map(tag => <span key={tag}>{tag}</span>)}</div><dl className="metadata-grid"><div><dt>FORMAT</dt><dd>{videoFormat(details).label}</dd></div><div><dt>RUN TIME</dt><dd>{sumDuration(details.plan) ? `${sumDuration(details.plan)} seconds` : '—'}</dd></div><div><dt>SCENES</dt><dd>{details.plan?.scenes?.length || '—'}</dd></div>{videoFormat(details).output && <div><dt>OUTPUT</dt><dd>{videoFormat(details).output}</dd></div>}<div><dt>VISUAL MODE</dt><dd>{['google','runway','local_cinematic'].includes(details.job.generation_config?.video_provider) ? `${details.job.generation_config.video_provider === 'local_cinematic' ? details.job.generation_config.video_model : details.job.generation_config.video_provider === 'runway' ? 'Runway' : 'Gemini'} moving footage` : details.job.image_mode === 'text' ? 'Text cards' : details.job.image_mode === 'diffusion' ? 'Generated images' : details.job.image_mode || '—'}</dd></div></dl><div className="video-quality-note"><Icon name="eye" size={17} /><p>{['google','runway','local_cinematic'].includes(details.job.generation_config?.video_provider) ? 'Review moving footage, character and voice continuity, factual labeling and estimated caption timing before approval.' : details.job.image_mode === 'text' ? 'This video uses text-card visuals. Check that the visual style meets your expectations before approval.' : 'Review every scene for visual quality, accuracy, and suitability.'}</p></div>{details.manifest?.warning && <Notice type="warning">{details.manifest.warning}</Notice>}{details.job.error && <Notice type="error">Generation reported an error. Check the server logs for details.</Notice>}</div></div><Storyboard details={details} />{details.job.status === 'pending_review' ? <section className="review-decision"><div><span className="eyebrow">YOUR FINAL CUT</span><h3>Does it make the cut?</h3><p>Approval saves your decision. Publishing is a separate step.</p></div><label className="checkbox-label"><input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)} disabled={!!busy} /><span>I’ve watched the video and checked the narration, factual claims, metadata, and media rights.</span></label>{!details.video_exists && <Notice type="warning">The rendered video is missing. Approval is unavailable.</Notice>}{actionError && <Notice type="error">{actionError}</Notice>}<div className="decision-buttons"><button className="button reject" disabled={!checked || !!busy} onClick={() => decide('rejected')}><Icon name="close" size={17} />{busy === 'rejected' ? 'Saving…' : 'Reject video'}</button><button className="button primary" disabled={!checked || !!busy || !details.video_exists} onClick={() => decide('approved')}><Icon name="check" size={18} />{busy === 'approved' ? 'Saving…' : 'Approve video'}</button></div></section> : <Notice>{details.job.status === 'approved' ? 'This video is approved. Open Publishing when you’re ready to authorize a separate upload.' : `This video is ${labels[details.job.status]?.toLowerCase() || details.job.status}. Only videos awaiting review can receive a decision.`}</Notice>}</>}</section></div></>;
}
function sumDuration(plan) {
  return plan?.scenes?.reduce((sum, scene) => sum + (scene.duration_seconds || 0), 0) || 0;
}
export function videoFormat(details) {
  const config = details.job.generation_config || details.generation_config;
  const aspect = ['9:16', '16:9', '1:1'].includes(config?.aspect_ratio) ? config.aspect_ratio : '9:16';
  const orientation = { '9:16': 'Vertical', '16:9': 'Landscape', '1:1': 'Square' }[aspect];
  return {
    aspect,
    label: `${orientation} · ${aspect}`,
    output: config?.resolution && config?.fps ? `${config.resolution} · ${config.fps} fps` : null
  };
}
function VideoPreview({ details }) {
  const format = videoFormat(details);
  return <div className="video-preview"><div className="video-frame" style={{ aspectRatio: format.aspect.replace(':', ' / ') }}>{details.video_exists ? <video key={details.job.job_id} controls preload="metadata" playsInline src={`${API}/jobs/${encodeURIComponent(details.job.job_id)}/video`} aria-label="Generated video preview" /> : <div className="video-unavailable"><Icon name="film" size={37} /><span>{ACTIVE.has(details.job.status) ? 'Your video is taking shape.' : 'Video unavailable'}</span></div>}<span className="video-format">{format.aspect}</span></div><div className="video-preview-caption"><span className="pulse-dot" />ORIGINAL VIDEO <span>{details.video_exists ? 'Preview before you approve' : 'No rendered file'}</span></div></div>;
}
function Storyboard({
  details
}) {
  const [open, setOpen] = useState(null);
  useEffect(() => setOpen(null), [details.job.job_id]);
  return <section className="storyboard"><div className="section-heading"><div><h3>Frame the story</h3><p>Take a closer look at each scene.</p></div><span className="soft-tag">{details.plan?.scenes?.length || 0} SCENES</span></div><div className="scene-list">{details.plan?.scenes?.map(scene => {
        const asset = details.manifest?.scenes?.find(s => s.scene_number === scene.scene_number);
        const expanded = open === scene.scene_number;
        const sourceUrl = safeExternal(asset?.source_url);
        return <article className="scene-item" key={scene.scene_number}><button className="scene-toggle" onClick={() => setOpen(expanded ? null : scene.scene_number)} aria-expanded={expanded}><span className="scene-number">{String(scene.scene_number).padStart(2, '0')}</span><span><strong>{scene.on_screen_text || `Scene ${scene.scene_number}`}</strong><small>{scene.narration}</small></span><span className="scene-duration"><Icon name="clock" size={13} />{scene.duration_seconds}s</span><Icon name="chevron" size={15} className={expanded ? 'expanded-chevron' : ''} /></button>{expanded && <div className="scene-expanded">{asset?.image_url ? <img src={asset.image_url} alt={`Scene ${scene.scene_number} visual`} loading="lazy" onError={event => {
              event.currentTarget.hidden = true;
              event.currentTarget.nextElementSibling?.removeAttribute('hidden');
            }} /> : <div className="missing-visual"><Icon name="film" size={20} /><span>Scene visual unavailable</span></div>}{asset?.image_url && <div className="missing-visual" hidden><Icon name="film" size={20} /><span>Scene visual unavailable</span></div>}<div><span className="section-label">NARRATION</span><p>{scene.narration}</p><span className="section-label">VISUAL DIRECTION</span><p>{scene.visual_prompt}</p>{scene.on_screen_text && <><span className="section-label">ON-SCREEN TEXT</span><p>{scene.on_screen_text}</p></>}{asset?.audio_url && <audio controls preload="none" src={asset.audio_url} aria-label={`Scene ${scene.scene_number} narration`} />}<div className="provenance">{sourceUrl ? <a href={sourceUrl} target="_blank" rel="noopener noreferrer">View media source <Icon name="arrowUp" size={12} /></a> : <span>Source provenance not recorded</span>}<span>{asset?.license ? `License: ${asset.license}` : 'License information unavailable'}</span>{asset?.attribution && <span>{asset.attribution}</span>}</div></div></div>}</article>;
      })}</div></section>;
}
export function canPublish(job, publications, platform) {
  return job?.status === 'approved' && job.video_exists === true && !publications.some(p => p.job_id === job.job_id && p.platform === platform && !(p.platform === 'facebook' && p.status === 'retry_authorized' && !p.platform_video_id));
}
function Publishing({
  data,
  selected,
  setSelected,
  request,
  refresh,
  notify,
  liveAttempts
}) {
  const jobs = data.jobs.filter(j => j.status === 'approved');
  const [platform, setPlatform] = useState('youtube');
  const [dryRun, setDryRun] = useState(true);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [revision, setRevision] = useState(0);
  const [uncertain, setUncertain] = useState(new Set());
  useEffect(() => {
    if ((!selected || !jobs.some(j => j.job_id === selected)) && jobs.length) setSelected(jobs[0].job_id);
  }, [selected, jobs, setSelected]);
  useEffect(() => {
    setChecked(false);
    setDryRun(true);
    setResult(null);
    setError('');
    setConfirming(false);
  }, [selected, platform]);
  const currentJob = jobs.find(record => record.job_id === selected);
  const detailsRevision = `${revision}:${currentJob?.updated_at}:${currentJob?.status}:${currentJob?.video_exists}`;
  const {
    details,
    error: detailError,
    loading
  } = useDetails(selected, request, detailsRevision);
  const attempts = [...data.publications, ...(details?.publications || [])];
  const existing = attempts.find(p => p.job_id === selected && p.platform === platform);
  const attemptKey = `${selected}:${platform}`;
  const retryAuthorized = existing?.platform === 'facebook' && existing.status === 'retry_authorized' && !existing.platform_video_id;
  const blocked = (!!existing && !retryAuthorized) || uncertain.has(attemptKey) || liveAttempts.has(`/jobs/${encodeURIComponent(selected)}/publish:${platform}`);
  const ready = canPublish(details ? { ...details.job, video_exists: details.video_exists } : null, attempts, platform) && !blocked;
  async function publish(liveConfirmed = false) {
    if (busy || !ready || !checked || !dryRun && !liveConfirmed) return;
    setConfirming(false);
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const response = await request(`/jobs/${encodeURIComponent(selected)}/publish`, {
        method: 'POST',
        body: JSON.stringify({
          platform,
          dry_run: dryRun,
          confirmed: !dryRun && liveConfirmed
        })
      });
      setResult(response);
      setChecked(false);
      notify(dryRun ? 'Preflight complete. No video was uploaded.' : 'Upload request completed. Verify processing and visibility separately.');
    } catch (err) {
      setError(dryRun ? err.message : `${err.message} The upload outcome may be uncertain. Inspect the publication record before any new attempt.`);
      if (!dryRun) setUncertain(previous => new Set([...previous, attemptKey]));
      setChecked(false);
    } finally {
      await refresh().catch(() => {});
      setRevision(v => v + 1);
      setBusy(false);
    }
  }
  return <><div className="publishing-policy"><span className="policy-icon"><Icon name="shield" size={21} /></span><div><strong>A deliberate next step.</strong><p>Only approved videos can be uploaded. You choose the destination and authorize every attempt.</p></div><span className="soft-tag">HUMAN-LED PUBLISHING</span></div>{!jobs.length ? <div className="panel"><Empty icon="upload" title="Good things are worth reviewing first.">Approved videos will appear here. Head to your review queue to make your first selection.</Empty></div> : <div className="publish-layout"><section className="panel publish-content"><div className="section-heading"><div><h2>The video</h2><p>Choose an approved story to share.</p></div><span className="soft-tag">{jobs.length} APPROVED</span></div><label className="field-label" htmlFor="publish-job">APPROVED VIDEO</label><select id="publish-job" value={selected || ''} onChange={e => setSelected(e.target.value)} disabled={busy}>{!selected && <option value="">Select a video</option>}{jobs.map(job => <option key={job.job_id} value={job.job_id}>{job.title || job.topic || `${capitalize(job.category)} video`}</option>)}</select>{loading ? <div className="loading-panel"><div className="spinner" /></div> : detailError ? <Notice type="error">{detailError}</Notice> : details && <><div className="publish-preview"><VideoPreview details={details} /><div><span className="eyebrow">APPROVED CONTENT</span><h2>{details.plan?.title || details.job.title}</h2><Status status={details.job.status} /><dl><dt>DESCRIPTION</dt><dd>{details.plan?.description || 'No description'}</dd><dt>HASHTAGS</dt><dd className="hashtags">{details.plan?.hashtags?.map(tag => <span key={tag}>{tag}</span>)}</dd><dt>RUN TIME</dt><dd>{sumDuration(details.plan)} seconds · {details.plan?.scenes?.length || 0} scenes</dd></dl></div></div>{!details.video_exists && <Notice type="warning">The rendered video is missing. Uploading is unavailable.</Notice>}</>}</section><aside className="panel publish-controls"><div className="section-heading"><div><h2>Make it intentional.</h2><p>Your destination. Your decision.</p></div></div><span className="field-label">DESTINATION</span><div className="platform-options">{['youtube', 'facebook'].map(id => <button key={id} className={`platform-option ${platform === id ? 'selected' : ''}`} onClick={() => setPlatform(id)} disabled={busy || (id === 'facebook' && details?.job.generation_config?.content_type === 'long_drama')} aria-pressed={platform === id}><span className={`platform-symbol ${id}`}><Icon name={id} size={21} /></span><span><strong>{id === 'youtube' ? 'YouTube' : 'Facebook'}</strong><small>{id === 'youtube' ? (details?.job.generation_config?.content_type === 'long_drama' ? 'Video' : 'Shorts') : 'Page Reels'}</small></span><span className="radio-indicator">{platform === id && <span />}</span></button>)}</div><div className="destination-note"><Icon name="lock" size={15} />{platform === 'youtube' ? `YouTube privacy: ${data.platform_settings?.youtube_privacy || 'private'}.` : 'A live upload may publish to your Page’s audience.'}{details?.job.generation_config?.content_type === 'long_drama' && ' Long drama publishing is supported on YouTube; this Facebook Reels integration supports short videos.'}</div><div className="control-divider" /><span className="field-label">UPLOAD MODE</span><div className="upload-mode segmented"><button className={dryRun ? 'selected' : ''} onClick={() => {
            setDryRun(true);
            setChecked(false);
            setResult(null);
          }} disabled={busy}>Dry run<span className="recommended">DEFAULT</span></button><button className={!dryRun ? 'selected' : ''} onClick={() => {
            setDryRun(false);
            setChecked(false);
            setResult(null);
          }} disabled={busy}>Live upload</button></div><p className="mode-description">{dryRun ? 'Check your approved video and publishing metadata. No upload, no reservation, no surprises.' : 'Send this approved video to the selected platform. An additional confirmation is required.'}</p>{blocked ? <Notice type="warning"><strong>{existing ? 'An upload attempt already exists.' : busy ? 'Upload in progress.' : uncertain.has(attemptKey) ? 'This attempt needs inspection.' : 'An upload has already been requested.'}</strong><p>{existing ? `${labels[existing.status] || existing.status}. Duplicate uploads are blocked. Check the history below.` : busy ? 'The request is in progress. Another upload is blocked.' : 'Inspect the publication record before considering any new attempt.'}</p></Notice> : <label className="checkbox-label publish-checkbox"><input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)} disabled={busy || !ready} /><span>I confirm this approved video and its publishing metadata.</span></label>}{error && <Notice type="error">{error}</Notice>}{result && <Notice><strong>{result.status === 'dry_run' ? 'Preflight complete.' : 'Upload request completed.'}</strong><p>{result.status === 'dry_run' ? 'Validation passed. Nothing was uploaded. Select Live upload only when you’re ready.' : 'Check the platform’s processing and visibility before treating the video as published.'}</p></Notice>}<button className={`button publish-action ${dryRun ? 'primary' : 'live-button'}`} disabled={!ready || !checked || busy} onClick={() => dryRun ? publish() : setConfirming(true)}><Icon name={dryRun ? 'check' : 'upload'} size={18} />{busy ? 'Please wait…' : dryRun ? 'Run preflight check' : 'Review upload'}</button>{retryAuthorized && <Notice>A manually inspected Facebook retry is authorized. A live upload still requires your separate confirmation.</Notice>}<span className="controls-footnote"><Icon name="shield" size={13} />No automatic uploads. No automatic retries.</span></aside></div>}<section className="panel publication-history"><div className="section-heading"><div><h2>Publication history</h2><p>Every attempt has a story. Keep an eye on its outcome.</p></div><span className="count-label">{data.publications.length}</span></div>{data.publications.length ? <div className="job-table-wrap"><table className="job-table"><thead><tr><th>VIDEO / PLATFORM</th><th>STATUS</th><th>ATTEMPTED</th><th>DETAILS</th></tr></thead><tbody>{data.publications.slice(0, 30).map(publication => {
              const job = data.jobs.find(j => j.job_id === publication.job_id);
              const link = safeExternal(publication.url);
              return <tr key={publication.publication_id}><td><div className="history-video"><span className={`platform-symbol ${publication.platform}`}><Icon name={publication.platform} size={17} /></span><span><strong>{job?.title || publication.job_id.slice(0, 8)}</strong><small>{capitalize(publication.platform)}</small></span></div></td><td><Status status={publication.status} /></td><td className="muted-cell">{dateLabel(publication.created_at)}</td><td><details className="event-details"><summary>Inspect record</summary><div><code>{publication.publication_id}</code>{publication.platform_video_id && <p>Platform ID: {publication.platform_video_id}</p>}{publication.error && <p className="history-error">An error was recorded. Inspect server-side publication details before another attempt.</p>}{publication.external_status && <p>{publication.external_status}</p>}{publication.events?.map((event, i) => <p key={event.event_id || i}>{dateLabel(event.created_at)} · {event.event_type?.replaceAll('_', ' ')}</p>)}{link && <a href={link} target="_blank" rel="noopener noreferrer">Open platform video <Icon name="arrowUp" size={12} /></a>}</div></details></td></tr>;
            })}</tbody></table></div> : <Empty icon="upload" title="A fresh start. Nothing uploaded yet.">When you authorize an upload, its status and history will be recorded here.</Empty>}</section>{confirming && <Modal title="Ready to send this one out?" onClose={() => setConfirming(false)}><div className="confirm-upload-icon"><Icon name={platform} size={28} /></div><p className="modal-intro">You’re authorizing a real upload to <strong>{platform === 'youtube' ? 'YouTube' : 'Facebook'}</strong>.</p><div className="confirm-video-title">{details?.plan?.title || details?.job.title}</div><Notice type="warning">{platform === 'youtube' ? `The configured YouTube visibility is ${data.platform_settings?.youtube_privacy || 'private'}.` : 'Facebook may make this Reel available to the Page’s audience.'} The upload will not be retried automatically.</Notice><div className="modal-actions"><button className="button subtle" onClick={() => setConfirming(false)}>Keep reviewing</button><button className="button live-button" onClick={() => publish(true)}><Icon name="upload" size={17} />Authorize upload</button></div></Modal>}</>;
}
function Modal({
  title,
  children,
  onClose
}) {
  const dialog = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement;
    const node = dialog.current;
    (node.querySelector('button:not(:disabled), input:not(:disabled), select:not(:disabled)') || node).focus();
    function key(event) {
      if (event.key === 'Escape') closeRef.current();
      if (event.key === 'Tab') {
        const elements = node.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]');
        if (!elements.length) return;
        const first = elements[0];
        const last = elements[elements.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === node)) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener('keydown', key);
    const original = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', key);
      document.body.style.overflow = original;
      previous?.focus();
    };
  }, []);
  return <div className="modal-backdrop" onMouseDown={e => {
    if (e.target === e.currentTarget) onClose();
  }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title" tabIndex={-1} ref={dialog}><div className="modal-heading"><h2 id="modal-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close dialog"><Icon name="close" /></button></div>{children}</section></div>;
}
