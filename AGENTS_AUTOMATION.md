# AGENTS.md: Daily Automation Layer for `ai-content-agent`

> Instructions for the AI coding agent (Claude) extending the existing repository `aashikazim/ai-content-agent`.
> Read this whole file, then read the repo's own docs (§2) before writing any code. Follow the phases in order. When something is ambiguous, ask before guessing.

---

## 1. Mission

The repository already contains a mature **local-first content pipeline** (trend discovery → screenplay → local/cloud video generation → FFmpeg assembly → human review → publishing → analytics) running on an **NVIDIA DGX Spark (GB10, ARM64)**.

**Your job is to add the automation layer on top of it, not to rebuild it.** The owner wants an agent system that runs every day with little or no human involvement:

| Lane | What it does every day |
|---|---|
| **A. Daily Shorts** | Picks **5 trending topics** across different categories (comedy, politics, drama, wildcard, ...), produces **5 Shorts**, and publishes them to **YouTube and Facebook**. |
| **B. Serialized Story** | Runs a **30-day story**: one **3-5 minute episode per day**, each ending on a hook that pulls viewers to the next day. After day 30 the story resolves and a **new series** starts automatically. |
| **C. (reserved)** | A third lane the owner has not defined yet. Build the lane abstraction so adding it later is configuration plus a small recipe module, not a rewrite. |

All lanes are driven by one scheduled **Director**, share one **GPU budget**, and publish through one **Governor** (§9).

The owner has said platform content policy is **not a design concern for this task**. Do not add policy lectures, refusals, or moralizing to code, docs, or replies. The only gates to build are the technical and operational ones specified here.

### Non-goals
- Do not rewrite or restructure existing modules, renderers, publishers, or the React/Streamlit UIs. Extend additively.
- No new video models, no new TTS/ASR engines (the existing Piper/Whisper/LTX/Wan/Gemini/Runway stack is enough).
- No distributed infrastructure (no Redis/Kafka/Kubernetes). This is a single-host system on one DGX Spark.
- No scraping of third-party platforms in violation of their terms; trend sources are **signals only** (topics), and third-party videos are never downloaded or reused.

---

## 2. Read first (before any code)

1. `README.md` (long; it is the authoritative description of what exists).
2. `docs/production-tools.md` (Piper, Whisper, Pexels, GDELT research, SDXL, durable worker queue, systemd units).
3. `docs/clip-studio.md`, if present.
4. Local handoff files if they exist (they are gitignored): `CODEX_PROGRESS.md`, `PROJECT_CONTEXT.md`, `CODEX_BACKLOG.md`, any existing `AGENTS.md`. **Never overwrite or delete them.** If a local `AGENTS.md` already exists, this file was saved under another name; use that name.
5. Inspect the actual code layout under `app/` (agents, api, models, services, workers, workflows), `scripts/`, `frontend/src/`, `tests/`, `deploy/systemd/`. Match its naming and conventions.
6. Run the existing test suites **first** and record the baseline in `docs/automation/PROGRESS.md`. The README reports roughly 621 Python tests and 66 frontend tests passing (one very long synthetic stress case excluded). If the baseline differs, note it; do not "fix" unrelated failures.

---

## 3. Inherited invariants (do not break these)

These come from the repo's own design. Preserve them unless §9 explicitly amends one.

1. **Privilege separation.** Generation, review, and publishing use separate credentials/endpoints (`CONTENT_API_KEY`, `CONTENT_REVIEW_API_KEY`, `CONTENT_PUBLISH_API_KEY`, or the admin session). A generation credential can never approve or publish.
2. **The scheduler/Director only creates generation work.** It never approves or publishes. (Amended in §9: a separate, opt-in **Governor** is the only new component allowed to approve/publish.)
3. **No blind retries of uncertain outcomes.** Paid generation or uploads that time out or are ambiguous go to `needs_reconciliation`. Never auto-retry them. Existing manual reconciliation/continuation flows stay as they are.
4. **Idempotency.** One upload per `(job_id, platform)`; duplicate generation requests are prevented by quotes/leases. Your new work must be idempotent too (§7 unique keys).
5. **Cost control.** Paid providers need explicit quote-bound authorization. The default automation configuration uses **only local providers** (Ollama + local LTX/Wan, $0 provider cost). Any paid use in automation requires an explicit, owner-created, capped **standing authorization** (§8.4) that still goes through the existing quote/consent mechanism. Never bypass quotes.
6. **Additive SQLite migrations only** (idempotent `CREATE TABLE IF NOT EXISTS` / guarded `ALTER`). Back up before changing schema: `sqlite3 data/trends.db ".backup 'data/trends_backup.db'"`. Never delete `data/trends.db`.
7. **GPU discipline.** One neural stage at a time via the existing process lock; memory guards, deadlines, and isolated interpreters stay as they are. Do not run unbounded eager models on the shared-memory host.
8. **Secrets.** Never commit `.env`, `credentials/`, databases, media, or model files. Update `.env.example` with new variables (placeholders only).
9. **Git.** Do **not** commit or push unless the owner explicitly says so. Stage nothing automatically. Propose commit messages at the end of each phase.
10. **No live paid calls or live uploads in tests or verification.** Mock providers and platforms. Real-GPU runs are separate, labeled, and use private validation databases/directories.

---

## 4. What already exists (reuse map)

Use these; do not duplicate them. Verify names by reading the code first.

| Capability | Existing pieces (per README) | How you reuse it |
|---|---|---|
| Trend discovery | `app/agents/trend_agent.py`, `ranking_service.py`, `youtube_service.py`, `main.py` | Wrap as one `TrendSource`; reuse the ranking math (recency, engagement, view velocity). |
| Source research | GDELT search + dossiers (`research_service`, `research_dossiers`) | Second `TrendSource` and source bundles for the news/politics-style recipe. |
| Screenplay generation | `content_agent.py`, `script_generation_service.py`, `openai_script_service.py`, `llm_service.py`, models `ContentPlan`, `LongContentPlan`, `generation_config.py`, `production_plan.py` | Generate scripts per recipe. Long plans: 120-1800 s, chapters of at most 180 s with a shared cast bible and carried story-state notes. |
| Workflow | `app/workflows/content_workflow.py` (LangGraph), `job_service.py` | Create jobs with discovery/planning skipped when a draft exists. |
| Local video | `local_cinematic_*`, `app/workers/local_cinematic_worker.py`, `local_production_runner.py`, `local_production_coordinator.py` | LTX-2.5 text/image modes for daily output. |
| Cast continuity | `cinematic_cast_service.py` (immutable cast packages), `cinematic_continuity.py`, `cinematic_adapter_service.py` | One frozen cast per series, reused by all 30 episodes. |
| Voice/captions | Piper profiles, `speech_analysis` (ASR/WER, forced alignment), `character_dialogue_audio.py` | Neural narration, caption timing, WER gate. |
| Stills / stock | SDXL standalone, Pexels library | Optional cast/scene reference generation; optional b-roll. |
| Assembly | `cloud_video_renderer.py` (FFmpeg), quality checks | Unchanged. |
| Review | Review queue, statuses `awaiting_quality_review` → `pending_review` → `approved`/`rejected` | Governor uses these existing transitions. |
| Publishing | `publishing_service.py`, `youtube_publisher.py`, `facebook_publisher.py`, `publish.py`, reconciliation scripts | Governor calls the existing service layer; no new upload code except where §9 says. |
| Analytics | `collect_analytics.py`, `youtube_analytics.py`, `facebook_analytics.py`, `feedback_service.py` | Feed lane/category performance back into topic selection (Phase 6). |
| Scheduling | `scheduler.py` (APScheduler `BlockingScheduler`, single instance), `scheduler_service.py` | Register Director jobs in the same single process, behind `AUTOMATION_ENABLED`. |
| Durable work | `app_work_queue`, `app.workers.application_worker`, `DURABLE_WORKERS=true` | Run generation work through the durable single-host queue. |
| Supervision | `scripts/install_services.py`, `deploy/systemd/*` | Add Director/Governor unit templates in the same style. |

Known limits to respect (from the README): Facebook Reels publishing is **short videos only** (long uploads are rejected); YouTube long-form is supported; uploads default to `private` (`YOUTUBE_PRIVACY_STATUS`); API uploads from unaudited YouTube projects are locked private by YouTube regardless of that setting, so the Governor must detect and report it, never loop on it; Facebook tokens are revocable and need inspection (`scripts/facebook_auth.py`).

---

## 5. Architecture of the new layer

```
            APScheduler (existing scheduler.py, one process)
                         │ cron jobs (AUTOMATION_ENABLED=true)
                         ▼
┌──────────────────────── Director (new) ─────────────────────────┐
│ collect_trends ─► topic_selector ─► daily_plan                  │
│ series_tick ─► story_engine (bible, outlines, state, buffer)    │
│ gpu_planner (schedules all lanes into the nightly window)       │
└───────────┬─────────────────────────────────────────────────────┘
            │ creates jobs only (existing generation endpoints/services)
            ▼
   EXISTING pipeline: LangGraph workflow → durable queue → local cinematic
   worker (GPU lock) → FFmpeg assembly → quality checks → Review queue
            │
            ▼
┌──────────────────── Governor (new, opt-in) ─────────────────────┐
│ per-lane approval_mode: manual | auto_with_gates                │
│ gates → approve → publish slots → reconcile → report            │
│ kill switch, daily caps, dry-run default                        │
└───────────┬─────────────────────────────────────────────────────┘
            ▼
   EXISTING publishing (YouTube / Facebook) → analytics → feedback
```

New package: `app/automation/` with submodules `trends/`, `selector.py`, `lanes/`, `series/`, `director.py`, `gpu_planner.py`, `governor.py`, `storage.py`, `reports.py`, plus `app/api/automation_routes.py`, `scripts/run_director.py` (manual trigger), `frontend/src/Automation*.jsx`, and `docs/automation/`. Follow the repo's existing naming and structure after inspecting it.

---

## 6. Lane specifications

### 6.1 Lane A: Daily Shorts

**Trend intelligence (`app/automation/trends/`)**
- `TrendSource` interface: `collect(region) -> list[TrendSignal]` (title, source, external id, category hint, raw score, velocity, timestamp, url, extra).
- Adapters: (1) existing YouTube most-popular discovery; (2) GDELT leads; (3) Wikipedia top pageviews; (4) Hacker News front page; (5) optional Google Trends "Trending Now" and Reddit, each behind its own flag and treated as best-effort (they can break; a failing source must never fail the run). Each adapter has recorded-fixture tests; no live network in tests.
- Collect every few hours (`AUTOMATION_TREND_INTERVAL_HOURS`, default 3) into `trend_signals`.

**Topic selector (`selector.py`)**
- Normalize and cluster signals into `topic_candidates` (normalized title + n-gram similarity; optionally local-LLM embeddings later). Classify category (keyword rules first, local LLM fallback) into the lane's categories.
- Score: cross-source agreement + velocity + recency, minus recent-use penalty. **Cooldown** (default 14 days) and **dedupe** against previously produced topics. Apply category weights from `feedback_service` once analytics exist (Phase 6).
- Select by **quotas** from `config/lanes.yaml` (default 5/day: comedy 2, politics 1, drama 1, wildcard 1). If a category has too few candidates, fill from the next-best category and record why. Keep ranked **backup topics** for substitution when a job fails.
- `plan-preview` (CLI + API): shows what would be picked and why, with no side effects.

**Recipes (`lanes/shorts.py` + YAML)**
- A recipe maps category → generation settings using the repo's existing config model: content type (Short), style (e.g. comedy/satire, realistic drama, animation), duration 30-60 s (default 45), aspect 9:16, video provider `local_cinematic` model `ltx-2.5`, input mode, script provider `ollama` by default, narration mode, caption mode (ASR/forced), cast mode, hashtag/title templates.
- Category-specific script prompts: comedy (setup/escalation/punchline in under 45 s), drama (mini-scene with a hook and a turn), politics/news-style (explainer built from a **source bundle**; use GDELT leads and mark the bundle `auto_compiled`, `reviewed=false`; do not mark it human-reviewed; the existing "unverified" labeling stays).
- Topic → brief → script uses the existing workflow with discovery/planning skipped (the draft already exists). Prefer a new service method and a new route under `/api/v1/automation`; do **not** change existing endpoint contracts.

**Daily plan**
- `daily_plans` rows `(plan_date, lane, slot)` are unique; each links topic → job → publication. The Director creates all five jobs, ordered so the GPU planner can finish them inside the nightly window.
- **Failure policy:** if a job fails technically, substitute the next backup topic once (bounded). Never auto-retry paid/uncertain work. A slot that still fails is marked `skipped` with a reason in the daily report.

### 6.2 Lane B: Serialized Story (30-day arcs)

**Entities (`app/automation/series/`)**
- `series`: id, title, genre, premise, status (`planning` → `awaiting_cast` → `active` → `completed`), start date, total episodes (30), cast package ids, bible JSON + hash.
- `series_bible`: logline, tone and visual style (frozen once), 2-4 main characters (appearance, wardrobe, voice, motivation, secrets), locations, world rules, **mystery/thread ledger** (planted question → expected payoff day).
- `series_episodes`: `(series_id, ep_no)` unique; outline JSON, script/plan id, job id, status (`outlined` → `scripted` → `rendering` → `rendered` → `in_review` → `approved` → `scheduled` → `published` | `skipped` | `failed`), planned and actual publish times, `summary_text`, `state_after_json`.

**Story planning (once per series; this is the highest-leverage quality step)**
1. **Premise**: generate 3 candidate premises seeded by current trend themes and dedupe against past series; pick one (owner can override or approve in a UI step).
2. **Bible**: characters, world, tone, thread ledger.
3. **30-episode outline** with a structure the validators enforce:
   - Days 1-8 setup, 9-22 escalation and reversals, 23-29 convergence/climax, day 30 resolution.
   - Every episode has: a one-line goal, conflict, a **reveal or turn**, an **ending hook** (cliffhanger) that is a concrete unresolved question or danger, and the threads it advances.
   - Episodes 1-29 **must** end on an unresolved hook; episode 30 must resolve every open thread in the ledger.
   - Each episode opens by paying off or acknowledging yesterday's hook (cold open or 10-second recap line).
   - Each episode also has a small self-contained mini-arc so a new viewer can follow it.
4. **Validators** (deterministic code plus an optional LLM judge): structural coverage, no dangling threads, hook present on 1-29, finale resolves ledger, no character appears before introduction or after removal, runtime within limits. Failing outlines are regenerated with the validator errors fed back (bounded attempts), then escalated to the owner.
5. Planning is a one-time-per-30-days job, so it may use a stronger planner (Claude or a larger Ollama model) through the **standing authorization** mechanism (§8.4). Default remains local Ollama if no authorization exists.

**Cast setup (one-time per series, human approval by design)**
- Frozen via the existing cast package service: 2-3 actors, up to six reference views each, wardrobe/voice descriptions. References can be created with the standalone SDXL tool, but **a cast package is only created after the owner approves the images** (the repo does not auto-approve cast packages). Everything after that is automatic for 30 days.

**Per-episode production (daily)**
- Inputs to the script request: bible, cast, the episode outline, **previous 3 episode summaries**, the running `story_state` (who knows what, locations, wardrobe, open threads), and the next episode's outline (so the hook sets it up).
- Target 180-300 s per episode, split into chapters of at most 180 s using the repo's long-plan chapter mechanism (shared cast bible plus carried state notes).
- Render with local cinematic (default: LTX-2.5, image-conditioned with anchored or connected continuity, per the README's guidance). Avoid Wan/hybrid in daily automation unless configured: README measurements show 15-18 min per 5 s shot.
- After scripting, run a **state updater** that derives `state_after` and `summary_text` from the script itself (not from the video), stores them, and feeds the next episode.
- **Hook check**: verify the final 20-30 s of the script contains the planned cliffhanger, using a deterministic field check plus an optional LLM judge; fail the episode (regenerate script) if absent.
- **Buffer-ahead**: generate episodes ahead of the publish date (`buffer_ahead_episodes`, default 3) so one failed night does not break the daily cadence. Publishing is decoupled from generation.
- **Ordering rule:** episode N+1 is never published before episode N. If the buffer runs dry, the lane skips the day, logs it, and resumes in order; never post out of sequence.
- **Packaging:** title format `{Series} | Ep {N}: {hook phrase}`; description with a two-line recap, "Tomorrow: {teaser}", series name, and a consistent tag set. Add an end-card line "Part {N+1} tomorrow" in the final seconds. Use a per-series YouTube playlist link in the description if the owner provides one (playlist API calls are stretch).

**Lifecycle**
- Day 30 published → `completed` → after `next_series_gap_days` the Director starts a new series (premise → bible → outline → cast approval → active). `series_start_mode`: `manual_cast_approval` (default) or `auto` (auto-generates references and approves its own cast only when the owner sets this).

### 6.3 Lane C (reserved)

Implement lanes through a `Lane` protocol (`plan_day`, `produce`, `publishable`, `describe`) and a YAML recipe so a third lane can be added without touching the Director or Governor. Ship a disabled `lane_3` placeholder in `config/lanes.yaml`. When the owner describes it, only a recipe plus a small module should be needed.

---

## 7. Data model additions (additive, in the existing SQLite DB)

All tables are created idempotently at initialization, following the repo's storage-class pattern. Include `created_at`/`updated_at`.

| Table | Purpose and key constraints |
|---|---|
| `trend_signals` | Raw signals: source, external_id, title, category_hint, score, velocity, region, collected_at, raw_json. Unique `(source, external_id, collected_at)`. |
| `topic_candidates` | Clustered topics: normalized_topic, category, rank_score, signal_ids_json, status (`new`/`selected`/`used`/`rejected`/`cooldown`), last_used_at. |
| `daily_plans` | Unique `(plan_date, lane, slot)`; topic_id, job_id, recipe, status, planned_publish_at, notes. |
| `series`, `series_episodes` | As in §6.2. Unique `(series_id, ep_no)`. |
| `lane_settings` | Runtime overrides (approval_mode, enabled) so the UI can flip them without editing files. |
| `automation_events` | Append-only audit log: actor (director/governor/user), action, subject ids, detail JSON, timestamp. |
| `standing_authorizations` | Owner-created: provider(s), max USD per day/total, expiry, created_by, consumed total. |
| `governor_state` | Kill switch, per-platform pause flags, consecutive-failure counters. |

Idempotency rules: re-running `plan_day` for the same date must not create duplicate jobs; re-running `series_tick` must not regenerate an episode that already has a job; a publish attempt for a `(job_id, platform)` that exists is a no-op, never a duplicate upload.

---

## 8. GPU budget, scheduling, and cost

### 8.1 Measured baselines (from the README; **re-measure and store, do not assume**)
- LTX 5 s text-to-video + audio: about 144 s total (7.9 GiB peak CUDA allocation).
- LTX 10 s image-conditioned shot: about 203 s; 180 s film of eighteen 10 s shots: about 3,444 s summed neural time (roughly 20 GPU-seconds per video second).
- Wan / hybrid 5 s: roughly 15-18 min. Exclude from daily lanes by default.
- Piper/Whisper run on CPU; SDXL still about 58 s.

Planning estimate for one day: 5 Shorts at 45 s plus one 4-minute episode is roughly 5 x 45 x ~29 + 240 x ~20, about 3.1 GPU-hours, which fits an overnight window with headroom. Extra work (script LLM, TTS, assembly, retries, other episodes in the buffer) must be accounted for by the planner.

### 8.2 `gpu_planner.py`
- Maintains measured seconds-per-video-second per (model, mode) from real runs stored in the DB (seeded with the README numbers, flagged `seed`).
- Given the day's work items and `gpu.nightly_window` (e.g. 00:30-08:30), produces an ordered schedule; flags when work will not fit and applies the configured degradation order (e.g., shorten Shorts to 30 s, drop the wildcard slot, reuse buffer) rather than overrunning.
- Only submits work through the existing durable queue/coordinator, which already serializes GPU stages. Never bypass the GPU lock.

### 8.3 Ollama and memory
The 8B planner model and the video models share unified memory. Sequence script generation and video generation so they never overlap on memory beyond the existing guards.

### 8.4 Standing authorization (only if a paid provider is ever used in automation)
An owner-created record with a daily and total USD cap and an expiry. The Director may then submit work that still goes through the existing quote/consent flow, passing the standing authorization reference. Every consumption is logged in `automation_events`. No cap or expired authorization means the Director falls back to local providers or skips; it never spends. Existing script-cost bounds from the README (for example about $1.06 per long script call, conservative) should inform estimates.

---

## 9. Publishing Governor (the one deliberate amendment to the repo's rules)

The repo's README says manual review and manual publishing are mandatory. The owner now wants daily posting. Therefore:

- **Default behavior is unchanged**: every lane starts with `approval_mode: manual`; the Governor does nothing and videos wait in the Review queue exactly as today.
- Setting a lane to `auto_with_gates` makes the **Governor** the only automation component permitted to approve and publish for that lane, using the existing review/publish service paths and separate credentials. The Director and generation workers still never approve or publish.
- Update the README's "Security and content policy" and "Project direction" sections to describe this opt-in mode accurately, and record the decision in `docs/automation/DECISIONS.md`.

**Gates (all must pass; any failure → leave in Review queue and log why)**
1. Existing technical checks passed (decode, duration, motion, audio, dimensions, FPS).
2. Speech gate: ASR WER vs script below a configurable threshold when speech is present.
3. Caption gate: captions present and aligned per the chosen caption mode.
4. Duplicate gate: not a near-duplicate of anything published in the last N days (title + script similarity).
5. Hold rules (configurable in YAML, empty by default except what the owner adds): keyword/regex or LLM-judge conditions that force a job back to manual review.
6. Series gate: episode N-1 is `published` (or explicitly `skipped`).
7. Platform gate: the platform is not paused, no publication for this job/platform is `needs_reconciliation`, and daily caps are not exceeded.

**Operation**
- Publish at configured slots (e.g. Shorts `09:00, 12:30, 15:00, 18:00, 20:30`; episode `19:00`), not immediately after approval.
- **Dry-run first**: `AUTOMATION_GOVERNOR_DRY_RUN=true` by default. In dry-run it evaluates gates and logs what it would do, calling the existing dry-run publishing path only. Live mode requires the env flag to be false **and** the lane in `auto_with_gates`.
- **Kill switch**: file flag (`AUTOMATION_KILL_SWITCH_FILE`) + DB flag + API/UI button. When set, the Governor stops immediately; in-flight uploads are never cancelled blindly but their state is recorded.
- **Auto-trip**: after 3 consecutive publish failures on a platform, or any `needs_reconciliation`, pause that platform and surface it in the report. Never retry an uncertain upload automatically; recovery uses the repo's existing reconciliation and `authorize_publication_retry.py` flow.
- **Platform mapping**: Shorts → YouTube (Shorts-eligible vertical video) and Facebook Reels; Episodes → YouTube only (Facebook Reels rejects long videos). A Facebook long-video path and an episode teaser Short (a 45-60 s cliffhanger cut posted to both platforms) are stretch items (§14, Phase 7); verify any new API behavior against current docs before building.
- **YouTube visibility**: respects `YOUTUBE_PRIVACY_STATUS`. Detect the "locked private because project is unaudited" condition and report it as a blocker with instructions; do not loop on it.
- Daily **report** (`data/automation/reports/YYYY-MM-DD.md` + dashboard): what was planned, produced, held, published, skipped, why, GPU time used, and anything needing the owner.

---

## 10. Configuration

`config/lanes.yaml` (validated with Pydantic at startup; invalid config fails loudly). Illustrative:

```yaml
timezone: America/New_York
gpu:
  nightly_window: {start: "00:30", end: "08:30"}
  seed_seconds_per_video_second: {ltx_t2v: 29, ltx_i2v: 20}   # seeds from README; re-measured at runtime
lanes:
  shorts_daily:
    kind: topic_shorts
    enabled: true
    daily_count: 5
    quotas: {comedy: 2, politics: 1, drama: 1, wildcard: 1}
    approval_mode: manual            # manual | auto_with_gates
    platforms: [youtube, facebook]
    publish_slots: ["09:00", "12:30", "15:00", "18:00", "20:30"]
    cooldown_days: 14
    recipes:
      comedy:   {content_type: short, style: comedy_satire, duration_s: 45, aspect: "9:16", provider: local_cinematic, model: ltx-2.5, input_mode: text, script_provider: ollama}
      politics: {content_type: short, style: news_explainer, duration_s: 45, aspect: "9:16", provider: local_cinematic, model: ltx-2.5, input_mode: text, script_provider: ollama, source_bundle: auto}
      drama:    {content_type: short, style: realistic_drama, duration_s: 45, aspect: "9:16", provider: local_cinematic, model: ltx-2.5, input_mode: text, script_provider: ollama}
      wildcard: {content_type: short, style: comedy_satire, duration_s: 30, aspect: "9:16", provider: local_cinematic, model: ltx-2.5, input_mode: text, script_provider: ollama}
  story_series:
    kind: serialized_story
    enabled: true
    episodes_per_series: 30
    episode_duration_s: {min: 180, max: 300}
    buffer_ahead_episodes: 3
    approval_mode: manual
    platforms: [youtube]
    publish_slot: "19:00"
    next_series_gap_days: 1
    series_start_mode: manual_cast_approval
    planner: {provider: ollama}       # may be claude with a standing authorization
    episode_render: {provider: local_cinematic, model: ltx-2.5, input_mode: image, continuity: connected}
  lane_3:
    kind: custom
    enabled: false                    # owner will define
governor:
  hold_rules: []                      # owner-defined regex/keyword/LLM-judge rules
  wer_max: 0.25
  duplicate_window_days: 30
  daily_publish_cap: {youtube: 10, facebook: 8}
```

New env vars (add to `.env.example`, placeholders only; defaults safe): `AUTOMATION_ENABLED=false`, `AUTOMATION_LANES_FILE=config/lanes.yaml`, `AUTOMATION_TIMEZONE`, `AUTOMATION_PLAN_HOUR=0`, `AUTOMATION_PLAN_MINUTE=5`, `AUTOMATION_TREND_INTERVAL_HOURS=3`, `AUTOMATION_GOVERNOR_ENABLED=false`, `AUTOMATION_GOVERNOR_DRY_RUN=true`, `AUTOMATION_KILL_SWITCH_FILE=data/automation/KILL`, `AUTOMATION_DAILY_BUDGET_USD=0`, `AUTOMATION_ALERT_WEBHOOK_URL=` (optional).

---

## 11. Interfaces

- **API** (`/api/v1/automation`, admin session + CSRF, or scoped keys consistent with the repo): `GET plan/{date}`, `POST plan-preview`, `POST plan/{date}/run`, `GET lanes`, `PUT lanes/{lane}` (approval mode, enabled), `GET series`, `POST series` (start), `GET series/{id}`, `POST series/{id}/approve-cast`, `PUT series/{id}/episodes/{n}` (edit outline), `POST series/{id}/episodes/{n}/skip`, `GET governor`, `POST governor/kill`, `POST governor/resume`, `GET reports/{date}`, `GET gpu/forecast`, `POST authorizations`.
- **React** (new page in the existing sidebar; reuse existing components and styling): *Today* (the 5 slots with status), *Series* (30-day grid with hook text per episode), *Governor* (approval modes, kill switch, pause flags, gate results), *GPU* (timeline and forecast), *Reports*.
- **CLI**: `scripts/run_director.py --plan-date YYYY-MM-DD [--preview]`, `--series-tick`, `--collect-trends`; `scripts/run_governor.py --once [--live]`. All idempotent, all print what they did.
- **systemd**: templates `ai-content-agent-director` (or extend the scheduler unit) and `ai-content-agent-governor` in the existing `deploy/systemd/` style; render-only by default as the existing installer does. Do not install or start anything.

---

## 12. Code conventions

Python 3.12, type hints throughout, Pydantic models for config and DB rows where the repo does, the repo's existing storage-class pattern for SQLite, structured logging via the repo's logging helpers (JSON redaction preserved), no `print`. Inject a `Clock` (and a `now()` provider) into Director/Governor/series logic so tests can run **simulated days**. Keep modules small and typed; document non-obvious logic (selector scoring, validators, gate order, planner fitting). Follow the frontend's existing patterns, tests, and build (`npm test`, `npm run build`).

---

## 13. Testing and verification

- **Unit**: selector scoring/quota/cooldown/dedupe; each `TrendSource` against recorded fixtures; story validators with adversarial outlines (missing hook, dangling thread, early-resolved mystery, character resurrected, wrong day-30 ending); gate evaluation; GPU planner fitting and degradation; idempotency of plan and tick; standing-authorization accounting.
- **Integration (mocked)**: fake LLM provider returning canned JSON, fake renderer producing a small valid MP4 via the repo's FFmpeg synthetic-media approach, fake publishers. Simulate **35 days** with a fake clock: series completes on day 30, a new series starts, buffer behavior, a failed night, out-of-order protection, kill switch, auto-trip, no duplicate uploads, daily report content.
- **Frontend**: component tests for the new pages; production build passes.
- **Real-hardware checks (separate, labeled, private validation DB/dirs)**: one real overnight Shorts run (5 jobs through to the Review queue, measured GPU time); one real 3-4 minute episode. Record measured times; update the planner's baselines. These never publish.
- Always run the full existing suites before declaring a phase done; no regressions versus the recorded baseline.

---

## 14. Phased delivery plan (7 phases)

At the end of each phase: run all tests, update `docs/automation/PROGRESS.md` (built, measured, deferred, risks), update `.env.example`/docs, and give a report. Do not start the next phase until the owner says so.

### Phase 1: Orientation and foundation
Read the repo (§2), record the test baseline, write `docs/automation/ARCHITECTURE.md` (what exists, what you will add, diagrams) and `DECISIONS.md`. Implement config loader/validation (`config/lanes.yaml`), additive schema (§7), `Clock`, event log, idempotency helpers, and the empty `Lane` protocol and Director skeleton.
**DoD:** baseline recorded; migrations idempotent on a copy of the existing DB; config validation tests pass; nothing existing changes behavior.

### Phase 2: Trend intelligence and topic selector
`TrendSource` interface plus adapters (YouTube wrapper, GDELT, Wikipedia, Hacker News; optional Google Trends/Reddit flagged), collection job, clustering/classification/scoring, cooldown/dedupe, quotas, backups, `plan-preview` CLI/API.
**DoD:** from fixtures, preview returns 5 diverse topics with reasons; live collection works for the real YouTube source on the Spark; source failures are isolated.

### Phase 3: Daily Shorts lane
Recipes, topic→brief→script→job creation via existing workflow, GPU planner, durable queue integration, substitution policy, daily plan page and report.
**DoD:** a real overnight run produces 5 Shorts in the Review queue with measured GPU times; simulated-days tests pass; re-running the plan creates no duplicates.

### Phase 4: Serialized story engine (planning)
Series entities, premise/bible/30-episode outline agents, validators with feedback loop, cast-approval flow, series API and React pages (grid with hook per day, outline editing).
**DoD:** a full 30-episode outline passes all validators (with a real local model, and with a fake model in tests); cast approval blocks activation until done; adversarial validator tests pass.

### Phase 5: Episode production and lifecycle
Per-episode script with memory (summaries, state, next-outline), chapters, rendering through local cinematic, state updater, hook check, buffer-ahead, ordering rule, packaging, finale and next-series automation.
**DoD:** one real 3-4 minute episode rendered into the Review queue with measured GPU time; 35-day simulation covers completion, rollover, failures, skip, and ordering.

### Phase 6: Governor and Director wiring
Approval modes, gates, publish slots, dry-run, kill switch, auto-trip, platform mapping, reconciliation integration, scheduler wiring (single APScheduler process), standing authorization, report generation, analytics feedback into category weights.
**DoD:** simulated 7 days in dry-run with correct gate decisions and zero duplicate publish calls; live path unit-tested with mocked publishers; README and DECISIONS updated for the opt-in mode.

### Phase 7: Hardening, operations, and stretch
Automation dashboard polish, systemd templates, runbook (`docs/automation/RUNBOOK.md`: restart recovery, stuck jobs, GPU hang, quota/token problems, switching lanes to auto, killing everything), soak test (simulated 35 days plus one real 24-hour dry-run of the whole system on the Spark), final verification matrix.
**Stretch (only after the above passes):** episode teaser Short per episode posted to both platforms; Facebook long-video publisher (verify against current Meta docs); YouTube playlist automation and scheduled `publishAt`; thumbnail generation; the owner's lane 3.
**DoD:** the checklist in §16.

---

## 15. Working agreement

1. **Plan first**: at the start of each phase list 5-8 bullets of what you will do and which existing files you will touch, then proceed.
2. **Extend, don't rewrite.** Minimal, additive edits to existing files; prefer new modules/routes. If an existing contract must change, stop and ask.
3. **Run everything yourself** (tests, builds, previews, real-hardware checks where specified) and fix failures. If you cannot run something, say so and record it in `PROGRESS.md`. Never claim a real-GPU, live-platform, or paid-provider result you did not obtain.
4. **Measure, then record.** GPU times and capacity numbers come from real runs; label seeds vs measurements.
5. **Ask before** adding dependencies, changing schema semantics, changing any inherited invariant beyond §9, or spending money.
6. **Never** commit/push without explicit permission; touch `.env`/credentials; delete the database or media; start systemd services or schedulers on the host; run live uploads.
7. **Resumability:** the host or editor may restart. Keep `docs/automation/PROGRESS.md` current with exact state, IDs of any real runs, and resume steps, in the same spirit as the repo's `CODEX_PROGRESS.md`.
8. **End-of-phase report**: what was built, test results versus baseline, real-hardware measurements, exact commands to try it, files changed, deferred items, and what the next phase will do. Then stop.

---

## 16. Definition of Done (whole project)

- [ ] Baseline Python and frontend suites still pass; new tests added for every module
- [ ] `plan-preview` returns 5 category-balanced, deduped topics from real sources
- [ ] A real overnight run produced 5 Shorts through to the Review queue; GPU time measured and used by the planner
- [ ] A full 30-episode series outline passes validators; cast approval gate works
- [ ] A real 3-4 minute episode rendered and reviewed; 35-day simulation proves ordering, buffer, finale, rollover, and skip behavior
- [ ] Governor dry-run gates verified; live path covered by mocked-publisher tests; kill switch and auto-trip verified; no duplicate uploads possible
- [ ] Every new table, endpoint, and CLI is idempotent and documented; `.env.example`, README, and `docs/automation/*` updated
- [ ] Systemd templates render and verify (not installed); runbook written
- [ ] Nothing committed, installed, or published without the owner's explicit action
