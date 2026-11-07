# Automation implementation progress

## CURRENT RESUME STATE

Owner authorized Phases5–7 (2026-11-07), commits after each phase, no uploads or
stretch work. Main HEAD75745c5; clean initial tree. Exact same source as last
verified baseline738 Python607.10s /75 frontend3.43s/build1.53s; no claim of a new
baseline run. Earlier full logs remain evidence. Existing handoff files untouched.

Phase5 WIP, uncommitted. Owner approved ALL SIX generated cast reference images
and selected Tidal Echoes for the private240s episode. MainDB now has three newly
owner-approved packages (backed up first):
- Tidal Echoes:6cf9b34d-ffcb-4751-bbcb-6a9c5c13fe7b
- Everyday Comedy:8ddfc36e-9760-4c38-aab1-7c78a278d00b
- Coastal Adventure:ccddbef1-3abd-4186-813c-8541a5fb49ad
Images/provenance/approval record:
`data/automation/validation/cast-options-20261107/registered-packages.json`.
No human approval of finished videos has been given.

**Real corrected episode COMPLETE**, private root
`data/automation/validation/episode-20261107-v2`, DB `validation/episode.db`.
Run774fc942-fb20-50e8-b922-f50e6d69ca40, production84adf95f-d492-42ed-bbf1-45449f1f52e4,
jobb512edc1-2f34-437e-aaa7-85c736901f42. DO NOT RELAUNCH/RESET CLAIMS.
24/24 shots,240.086s,H2641280x720/24fps,AAC. Measured LTX stage4828.8s,
render wall5248.59s; measurement stored privately. Replay changed no production,
job,script,shot,run or measurement rows. All technical checks passed. Finished
video remains awaiting_quality_review; owner continuity/captions/rights acceptance
is pending. No approval/publication was fabricated. Exact MP4/hash and last30s:
[EPISODE_VALIDATION.md](EPISODE_VALIDATION.md). verification.json stores evidence.

Initial probe preserved2 completed shots after detecting portrait center-cropping;
22 NEVER-submitted reservations were cancelled. Corrected run reused the actual
completed screenplay/state and original cast images with geometry-only landscape
scene references. Blurred side-fill remains visible. This is not artistic QA.
Known probe/recovery evidence remains under the private validation roots.

Latest automation tests134passed80.41s; frontend77passed2.31s/build646ms.
Final full Python regression: **757 passed in688.12s**, log
`/tmp/automation-phase5-final-python.log`, frozen Phase5 backend. Compared with
Phase4:738passed607.10s, +19 tests, zero regressions. Earlier749passed705.44s
predates final geometry/lifecycle fixes and is not the final claim.
Final copied migration:19 automation tables,3 initializations,40 legacy tables/
1771 rows preserved,integrity ok. MainDB opened read-only, not automation-migrated.
Copy: `data/automation/validation/phase5-schema-final-20261107`.
Phase5 regression complete; commit, then Phase6. Acceptance item remains
pending unless owner responds to the existing quality-inspection question.

Owner chose RECORDED SOURCES + MOCKED GENERATION for the real24h Phase7 soak.
Must still measure24 actual hours using bounded foreground harness, no service/
APScheduler starts, no live uploads or paid calls. No Phase6/7 code started yet.

Historical phase checkpoints follow; later entries supersede older STOP/pending notes.

## Phase 1 — orientation and foundation (2026-11-06)

Status: **Phase 1 complete and verified. STOPPED awaiting owner approval.**
No Phase 2 work has been started.

### Scope and safeguards

- Source of scope: root `AGENTS_AUTOMATION.md`, sections 3, 7, 14 and 15.
- Read README, production-tools, Clip Studio and the existing local handoff files.
  Those handoff files are historical evidence and have not been overwritten.
- Initial branch `main`; initial working tree contains only owner-supplied untracked
  `AGENTS_AUTOMATION.md`. Do not stage, commit, push, install or start services.
- Add foundation only. No trend collection, LLM calls, GPU inference, generation
  submission, review, uploads, scheduler registration or Governor implementation.
- Use the newer user requirement of 300–600-second series episodes; the supplied
  specification's older 180–300-second example is superseded for configuration.
- Existing database is never migrated in place in this phase. Use SQLite backup to
  copy `data/trends.db` into a private validation directory, then migrate the copy.

### Baseline (before implementation)

| Suite | Command | Result |
|---|---|---|
| Python, all existing tests including long synthetic assembly | `.venv/bin/python -m pytest tests/ -q` | **623 passed**, **587.45s**; log `/tmp/automation-baseline-python.log` |
| React | `cd frontend && npm test` | **66 passed**, 9 files, **2.07s** |

Python TestClient checks run outside the execution sandbox because its local
async/event-loop behavior was a known constraint in earlier handoffs. Providers,
platforms and neural calls are mocked by the existing suite; CPU media tests are
real. The historical 621-test count excluded one long test; this baseline includes it.
The actual current baseline has 623 tests; no pre-existing failures were observed.

### Initial execution plan (completed; final results below)

1. Record Python baseline outcome and any pre-existing failures without fixing them.
2. Add architecture/decisions, YAML/Pydantic validation, Clock, additive storage,
   event log, idempotency helpers, Lane protocol and inert Director skeleton.
3. Run targeted tests, migrate a private live-database copy twice, then rerun both
   complete suites and frontend build. Compare baseline and final results.
4. Record files/results/limitations and stop for owner approval before Phase 2.

## Foundation milestone

- Added typed lane/config models, safe YAML loader and explicit settings, Clock,
  ten additive tables (nine specified domain/audit/state tables plus an idempotency
  claim ledger), append-only events and single-winner reservations/claims.
- Added pure Lane protocol/Director skeleton and offline inspection command.
  Concrete recipes, job dispatch, scheduler registration, APIs, UI and Governor
  execution remain unimplemented and unreachable from existing entry points.
- Added architecture and decisions, current README direction/security note, safe
  env examples, private automation data ignore rule and migration-copy checker.
- Targeted verification: **52 passed in 0.55s**. Python compilation and whitespace
  checks passed. No dependencies added or installed.

### Existing-database copy verification

Command:

```bash
.venv/bin/python scripts/check_automation_migration.py --source data/trends.db \
  --output-dir data/automation/validation/phase1-20261106
```

- Source opened using SQLite `mode=ro`; online backup includes committed WAL data.
- Private original backup: `data/automation/validation/phase1-20261106/trends_backup.db`.
- Migrated copy: same directory, `trends_migration.db`; **three** initializations.
- **40 original tables / 1,762 original rows preserved** with original schema and
  user_version unchanged. All ten new tables present; integrity check **ok**.
- Private proof: same directory, `report.json`. Copies are ignored, owner-readable
  only; validation refuses to overwrite an existing output directory.
- No migration was applied to the live database. No real GPU generation, platform
  request, paid call, approval, service/scheduler start, credential edit or commit.

### Final regression — completed

- Full Python command remains `.venv/bin/python -m pytest tests/ -q` including the
  long synthetic media case. **675 passed in 604.61s**, zero failures/skips;
  log `/tmp/automation-phase1-python.log`.
- React command `cd frontend && npm test && npm run build`; log
  `/tmp/automation-phase1-frontend.log`: **66 passed**, 9 files, **1.87s**;
  Vite production build passed in **619ms**.
- Both test/build sessions exited zero. No rerun is needed to resume the next phase.

## Files in this phase

Existing tracked files modified: `.env.example`, `.gitignore`, `README.md`.

Added:

- `app/automation/__init__.py`, `__main__.py`, `clock.py`, `config.py`,
  `director.py`, `events.py`, `idempotency.py`, `schema.py`, `storage.py`,
  `lanes/__init__.py`.
- `config/lanes.yaml`.
- `scripts/check_automation_migration.py`.
- `tests/test_automation_foundation.py`.
- `docs/automation/ARCHITECTURE.md`, `DECISIONS.md`, `PROGRESS.md`.

The owner-supplied `AGENTS_AUTOMATION.md` remains unchanged and untracked.
Existing handoff files, application modules, frontend components, APIs, service
units, `.env`, credentials and the live database were not edited by this phase.

## Safe commands to try

```bash
# Inspect configuration and inert lane status without opening SQLite.
.venv/bin/python -m app.automation --config config/lanes.yaml --describe

# Foundation unit/integration tests use temporary databases and fakes.
.venv/bin/python -m pytest tests/test_automation_foundation.py -q

# Repeat migration verification only with a NEW private output directory.
.venv/bin/python scripts/check_automation_migration.py --source data/trends.db \
  --output-dir data/automation/validation/phase1-owner-check
```

Do not set automation flags expecting daily output in Phase 1. No concrete lane or
Governor is implemented. Offline inspection reports disabled/unimplemented lanes;
Director `run_day` fails closed. Nothing has been installed or activated.

## Deferred work and risks

- Phase 2 only after owner approval: YouTube/GDELT/Wikipedia/Hacker News fixture-backed
  signal adapters, isolated collection failures, category clustering, scoring,
  quotas/backups, cooldown/dedupe and a genuine topic plan-preview CLI/API.
- Later phases own local-generation service integration, GPU forecasting, serial
  story planning/cast approval/state/buffer/lifecycle, Governor gates and standing
  authorization, scheduler/UI/reporting and operations/soak checks.
- Positive budgets or auto-mode flags cannot grant any current execution authority.
  Decimal accounting/authorization row shapes are not a spend-control implementation.
- No new hardware/platform measurement was taken. Existing rate seeds are historical;
  5–10-minute episodes will need a new GPU forecast and later measured production.
- Actual provider permissions, platform visibility and owner cast approval remain
  future operational inputs. No credentials are needed for foundation verification.

Suggested commit message (not committed):

```text
feat: add opt-in automation foundation and lane configuration

- Add validated local-only lane settings and injected clocks
- Add additive SQLite schema, audit events and idempotent claims
- Add Lane protocol and inert Director inspection
- Verify migrations on a private copy and document phase boundaries
```

## End-of-phase report — 2026-11-06

| Verification | Baseline | Phase 1 final | Comparison |
|---|---|---|---|
| All Python tests, including the long synthetic case | 623 passed / 587.45s | 675 passed / 604.61s | +52 tests; zero failures/skips in either run |
| React | 66 passed / 2.07s | 66 passed / 1.87s | Same coverage; no frontend changes |
| React production build | Not separately run for baseline | Passed / 619ms | Existing bundle builds |
| Foundation targeted | Not present | 52 passed / 0.55s | Includes config, Clock, schema, events, claims, Director, import/CLI and copy safety |
| Database migration copy | Not present | 3 initializations; 40 original tables / 1,762 rows preserved; integrity ok | Live DB never migrated |
| Compile / git whitespace | — | Passed | New Python modules parse/compile |

Full Python duration increased 17.16s; the suite contains long CPU media operations
and this is not a controlled performance benchmark. No pre-existing failures were
found or unrelated application fixes made. Real GPU/platform measurements: **none**;
all model and publication tests remain mocked, CPU FFmpeg regressions are real.
Historical timing seeds have not been promoted to new measurements.

Branch `main`; nothing staged, committed or pushed. No services/schedulers started,
no `.env`/credential edits, no live source migration and no existing handoff edits.
Generated validation DBs/report are private and ignored by Git. There are 19 added
or modified implementation/config/docs/test files, excluding the unchanged
owner-supplied `AGENTS_AUTOMATION.md`; the inventory is above.

**Next action:** wait for owner approval to begin Phase 2. Re-read this progress and
phase decisions; use the saved baseline and completed migration evidence. Do not
repeat baseline/GPU trials or migrate the source merely to recover context.


## Phase 2 — trend intelligence (2026-11-06, verified)

Owner authorized completing/committing Phases 2, 3 and 4 sequentially, including
one real YouTube collection, one five-Short local validation batch, and one local
series outline. No publishing or service/scheduler activation is authorized.
Phase 1 owner commit is `8d87e51`; initial working tree was clean.

Implemented typed TrendSource/TrendSignal; YouTube discovery/ranking wrapper; GDELT
research wrapper; Wikimedia global English pageviews; bounded HN item collection;
optional flagged Google Trends RSS and OAuth Reddit sources. Added one explicit
collection tick with interval-bound idempotency and isolated/sanitized failures.
Optional local classification runs only during collection under the existing
cinematic GPU lock with Ollama keep_alive=0; preview never invokes models.

Selector normalizes/clusters similar titles, counts distinct sources instead of
repeated observations, scores consensus/log-scaled velocity/recency/popularity,
excludes recent topics including legacy jobs, fills category quotas with recorded
fallbacks, and retains ranked backups. Stored provenance links actual signal IDs.
Preview opens SQLite read-only and does not collect, reserve slots, or initialize
missing databases. New POST /api/v1/automation/plan-preview uses existing admin
session and CSRF; generator keys cannot access it. Existing endpoints unchanged.

New files: app/automation/trends/{__init__,sources,collector,classifier}.py,
app/automation/selector.py, app/api/automation_routes.py, scripts/run_director.py,
tests/test_automation_trends.py, tests/fixtures/automation/*, docs/automation/TRENDS.md.
Existing server.py registers only the new router; .env.example adds disabled flags.

Focused foundation+trend tests: **62 passed in 1.38s** after final provenance/local
classifier corrections. Full regression log /tmp/automation-phase2-full.log still
running at this checkpoint; frontend tests/build log /tmp/automation-phase2-frontend.log.

### Exactly one real YouTube collection

- Private root: data/automation/validation/phase2-20261106.
- validation.db retains idempotency marker, 50 signals and source event.
- youtube-response.json / collection.json / preview.json retain the original result.
- Status collected, count **50**, region US. No third-party media downloaded.
- Wikipedia and HN fixtures recorded from public official APIs, reduced to consumed
  metadata. GDELT live fixture acquisition returned HTTPError; its successful-body
  contract fixture is explicitly synthetic, as is optional Reddit without OAuth.
  tests/fixtures/automation/provenance.json records these boundaries honestly.

| Quota | Selected topic | Reason |
|---|---|---|
| drama | Final Life: Episode 2 - CUSHION BRIDGE! | quota drama; age 6.1h; velocity 0.968; recency 0.919 |
| wildcard | ONE PIECE IS IN FORTNITE! 🤯🏴‍☠️ + Speed Vs. Dele Alli ⚽🏴󠁧󠁢󠁥󠁮󠁧󠁿 | quota wildcard; age 5.4h; velocity 1.000; recency 0.927 |
| comedy | THE FURTHER MIS-ADVENTURES OF CLIFF BOOTH / Official Trailer #2 / Netflix | quota fallback: comedy unavailable; selected drama; age 12.7h; velocity 1.000; recency 0.839 |
| comedy | Here In Your Dreams ☁✨ (ft. Liam Pearce) / Dandy's World Official Song | quota fallback: comedy unavailable; selected drama; age 6.2h; velocity 0.936; recency 0.917 |
| politics | 🎮PLAYING EVERY GTA UNTIL I BEAT IT: Grand Theft Auto 3🎮 | quota fallback: politics unavailable; selected wildcard; age 16.8h; velocity 1.000; recency 0.792 |

The single mostPopular sample lacked matching comedy/politics candidates. These
are explicit ranked substitutions, not a claim of five originally balanced source
categories. Tests verify exact category balance when sufficient candidates exist.
Do not repeat this collection after a context reset. Use --preview on the saved DB.


Phase 3 integration findings (read-only audit while Phase 2 regressions run):
CloudVideoService._consume deliberately allows only one active tracked batch, so
five slots must be reserved up front and their video productions created serially.
Do not weaken that global guard. WorkQueue can add one generation-only automation
handler; do not add approval/publish handlers. Existing ContentWorkflow already
skips discovery/planning for preplanned moving productions. Actual local usage
JSON exposes ltx_seconds/wan_seconds/queue_wait_seconds for measured GPU budgeting.
Finished footage must remain awaiting_quality_review, with human continuity/caption/
rights checks preserved before pending_review. Daily automation page should surface
those quality-pending jobs; never fake the three human confirmations.

### Phase 2 completed verification and handoff

- Full regression: **684 passed in 608.49s**, including long synthetic media.
- Final foundation/trend suite: **63 passed in 1.64s**, including two later-added
  classifier/scoring/scarcity cases and the final stored-signal provenance check.
  Counts overlap; do not describe this as 747 unique tests or one 686-test full run.
- The first scarcity assertion used six near-identical titles that correctly
  clustered into one topic. Corrected the fixture to six distinct wildcard topics
  so it tests category scarcity rather than total-topic scarcity; final checks pass.
- Frontend: **66 passed in 1.89s**; production build **655ms**. Compile/diff checks pass.
- Exactly one YouTube collection: 50 signals. Read-only preview-v2.json links actual
  stored signal IDs; original preview/response remain preserved. No second fetch.
- No GPU, paid-provider call, media ingestion, publishing, live DB migration or
  service/scheduler activation. GDELT availability remains best-effort/unverified.
- Owner authorized committing this milestone to main, then Phase 3. Next work is
  local Shorts recipes/queue/forecast/report/UI and private five-Short validation.


## Phase 3 — Daily Shorts (2026-11-06, implementation verified; real run pending)

- Phase 2 committed as **7f0c1d9**. Owner authorized continuing through Phase 4.
- Added category briefs, bounded local JSON beat planning/compilation, private
  received-output diagnostics, existing script/quote/LangGraph production reuse,
  atomic daily intents and immutable attempts, serial generation-only durable queue
  dispatch, one-backup substitution and uncertain-outcome holds. No human quality
  confirmations or uploads are called. Unsupported image/Wan daily recipes fail
  closed; existing Local Video Studio functionality is preserved.
- Added seeded-versus-measured GPU rates, DST-aware capacity forecasts, configurable
  degradation, remaining-capacity checks, per-job stage/wait measurements and reports.
- New Automation React page previews/queues/polls days, exposes forecasts/previews and
  reuses existing CloudProduction for the human quality handoff. API additions do not
  alter existing endpoint contracts. Read-only day/report/forecast routes do not
  initialize a missing database. Existing worker gains only a generation handler.
- Latest focused verification: **88 passed in 18.18s**, including seven distinct
  successful simulated days/35 fake productions, duplicate replay, bounded failure
  substitution/uncertain holds, auth/CSRF, real 30s CPU FFmpeg assembly with mocked
  neural inference, and proof no automatic human-quality handoff occurred.
- Frontend **69 tests passed**, build passed (636ms at latest checkpoint).
- Full Python regression running: /tmp/automation-phase3-full.log, session92561.
  Wait for it before starting the real five-Short batch. No real model inference has
  occurred in Phase 3 yet; only read-only Ollama metadata confirmed qwen3:8b installed.
- Final schema-copy verification: private phase3-schema-final-20261106 directory,
  14 new tables, 3 initializations, original40tables/1762rows preserved, integrity ok.
  Never migrate the live DB as part of validation.

### Planned single hardware pilot (recorded before launch)

- New private root data/automation/validation/shorts-20261106. Copy Phase 2 validation
  DB using SQLite backup; do not fetch YouTube again or change the live application DB.
- Private pilot.yaml freezes all five recipes at **30s** (a permitted first pilot;
  shipped normal defaults remain45/30s), LTX text mode, Piper and ASR. Provider cost0.
- Explicit manual immediate/overnight-style run, not a timed scheduler. Bounded
  application_worker --drain consumes one generation-only day request and exits.
- Preserve actual day/run/job/script/production/generation IDs and all original files.
  Technical successes remain awaiting_quality_review, pending owner inspection.
- After completion: measure neural stage/wait/wall seconds from saved worker records,
  verify final video/audio/decode, replay the same plan without executing new work,
  compare job/script/shot/generation counts, record exact evidence, then commit Phase3.
- If interrupted, inspect current process/queue/day/attempt IDs and private worker
  logs first. Never rerun the hardware batch or reset a claim blindly.

Phase 4 next (only after Phase3 verified/committed): local 3-premise planning, bible
with 2–3 actors and thread ledger, 30-episode structured outlines with bounded
validation feedback, immutable planning receipts, manual cast activation gate,
outline-edit API and React hook grid. Generate one real local outline, display all
30 hooks and actual repair diagnostics, commit, then stop. No episode rendering,
Governor, new-series lifecycle or publishing in Phase4.


Phase 3 final pre-hardware checkpoint:
- Final focused combined suite88passed18.18s; final Shorts-only10passed13.94s after
  exact request beat-count schema binding. Full suite still running in session92561;
  log /tmp/automation-phase3-full.log. Frontend69passed1.92s and final build636ms.
- Private pilot prepared at data/automation/validation/shorts-20261106; validation.db
  is a SQLite backup of Phase2's cached signals, pilot.yaml freezes five30s recipes,
  preflight.json confirms installed LTX runtime and Piper profile without inference.
  prepared.json says prepared_not_started. DO NOT recreate this directory or fetch
  YouTube again. No generation IDs exist yet at this checkpoint.
- Actual launch must set only per-process env DATABASE_PATH, AUTOMATION_ENABLED, AUTOMATION_LANES_FILE and AUTOMATION_DATA_DIR
  plus CONTENT_PLANS_DIR/CLOUD_VIDEOS_DIR/CHARACTER_REFERENCES_DIR/DRIVING_REFERENCES_DIR
  to this pilot's private roots; keep shared LOCAL_CINEMATIC_ROOT/checkpoints intact.
  DURABLE_WORKERS=false launches one bounded drainer, not a systemd service.

Phase4 design notes for resume (not implemented): use existing localJSONPlanner and
common durable automation handler. Premise agent returns3 original candidates,
bible agent defines2–3 actors/locations/thread payoff ledger, outline agent writes
30 episodes in bounded chunks with structural validation feedback and private
receipts. Validate hooks1–29, finale closure, act days1–8/9–22/23–29/30, previous-hook
acknowledgment, actor intro/removal, exact thread payoff days and planned300–600s
runtimes. Cast linking must use a hash-verified existing owner-approved package
matching bible IDs; only an admin-confirmed action activates the series. Episode
edits validate the entire outline and use expected revision timestamps. API/React
provide30-hook grid, validation feedback, cast selection and outline editing. A real
local outline stays awaiting_cast, never auto-approved; no episodes rendered inPhase4.


## Phase 3 hardware batch launched (single run)
- Full Python regression passed696tests619.24s; frontend69tests/build passed.
- Real pilot launched via /tmp/launch_auto_pilot.py; log /tmp/automation-phase3-real.log.
- Private root data/automation/validation/shorts-20261106. prepared.json now says
  launched. NEVER rerun the launcher; it refuses if already launched. Inspect
  validation.db / launch.json / automation/worker-logs and existing saved task IDs.
- CLI --run --wait enqueues one bounded day request; child worker consumes existing
  durable queue, serializing5Shorts and neural stages. No scheduler/services started.
- Initial execution session will be recorded with actual run/job/generation IDs below.

Hardware observation1:
- CLI execution session40449; dayrun269492ad-7eaa-58da-bf74-aaae5c96b432 is running.
- Slot1 scriptbfb71e79-5296-4a6b-9477-52ba303dbe19 is running; no production/job ID yet.
- Slots2–5 remain planned, no models submitted for them. Preserve this database and
  all IDs; do not re-run the launcher or submit a new day to inspect progress.

Hardware observation2 (existing IDs only):
- Slot1 scriptbfb71e79-5296-4a6b-9477-52ba303dbe19 completed; production
  df26c15f-9f60-4079-8ef7-2f0677db9820, jobb1111575-4b10-436e-ac2b-1c28463b8554.
- Three10s LTX IDs persisted: 0f44c602-5c27-4cb0-907c-a3febd19e512 (ltx_generation),
  77aeaa45-b4e2-40fb-9f90-197968db270c and e9fa085b-a040-41f3-88c7-fea726131b98
  (waiting_for_gpu). Slot2–5 still planned/unsubmitted. No retry/restart.
- Stage timing is actual model worker data. Summed queue wait overlaps between
  queued shots and must not be added to neural time to infer wall time.

Hardware observation3:
- Slot1 technical-ready and awaiting_quality_review; finalduration30.046s,
  measured721.33s LTX stages,777.19s wall, aggregate shotwait821.94s (overlapping,
  not additive to wall), peakCUDA7928.47MiB per shot. No approval/publication.
- Third generatione9fa085b-a040-41f3-88c7-fea726131b98 completed206.43s LTX;
  first/second288.87s/226.03s. Visual diagnostic first-two-shots.jpg inspected: real
  outdoor rider/water footage at1s/7s, not textcard fallback; not full-film quality certification.
- Slot2 script768f2084-32c0-4621-b202-2a719cf61f48; production
  1740ce90-e289-4e6e-b00f-130f048a15e1; joba057f744-7299-4cd7-a306-4fdb149a4eeb.
  Generation IDs7db716d4-c596-4b7d-936b-188e01a26389,
  b593ea40-7c66-4734-9207-7b612ba3ea11,ceb08915-c657-4b43-87d7-fa32f4f3df8a.
- Latest full IDs/status snapshots: pilot/latest-observation.json. No retry/substitution yet.

Phase3 UI refinement while GPU batch runs (no backend generation changes):
- Matched actual generation-section/grid/action/notice classes after checking CSS,
  rather than relying on undefined form-grid/panel-heading classes. Existing styles
  untouched. Added optional CloudProduction.productionId for direct archived
  inspection; all old callers retain default behavior. Quality checkboxes reset
  when changing production; ASR/aligned caption descriptions reflect saved mode.
- Latest frontend suite70passed and build944ms; new regression proves archive fetch
  opens quality preview without generation/approval/publishing requests.
- Guarded Shorts tests10passed14.85s with real Ollama/HTTP/local GPU submissions
  explicitly forbidden in pytest. Existing full696passed remains recorded.

Hardware observation4: two completed Shorts, exact measured rows:
- {"job_id": "b1111575-4b10-436e-ac2b-1c28463b8554", "video_seconds": 30.046, "neural_seconds": 721.33, "queue_seconds": 821.94, "wall_seconds": 777.1905377559597}
- {"job_id": "a057f744-7299-4cd7-a306-4fdb149a4eeb", "video_seconds": 30.046, "neural_seconds": 579.79, "queue_seconds": 679.11, "wall_seconds": 641.423900244059}
- Slot3 currently linked: {"daily_plan_id": "0ff40104-8091-5ef9-aa37-9d634be225be", "attempt_no": 0, "script_attempt_id": "3f80b135-5f1b-4fb4-8660-49e10386c2a6", "production_id": "925af1f3-fcc5-41be-9086-b1b31b5f63fa", "job_id": "0add5110-17ea-4598-b358-df2722ff35d5", "status": "starting", "error": null}
- Model pipeline source unchanged during the batch; only frontend inspection/CSS fixes were made.

## Phase 3 final validation — 2026-11-07

The single real five-Short batch completed using Ollama qwen3:8b, LTX-2.5,
Piper narration, ASR captions and FFmpeg. Private evidence is under
`data/automation/validation/shorts-20261106` (ignored), database `validation.db`.
No live application DB migration, services, schedulers, paid calls or uploads.

| Slot | Job | Video seconds | LTX seconds | End-to-end seconds |
|---|---|---:|---:|---:|
| 1 | b1111575-4b10-436e-ac2b-1c28463b8554 | 30.046 | 721.33 | 777.19 |
| 2 | a057f744-7299-4cd7-a306-4fdb149a4eeb | 30.046 | 579.79 | 641.42 |
| 3 | 0add5110-17ea-4598-b358-df2722ff35d5 | 30.046 | 598.33 | 655.61 |
| 4 | 8e360dae-d667-4236-8a12-d02de45fbf53 | 30.046 | 558.88 | 615.50 |
| 5 | 67cef8c7-63da-4390-911a-c7e4ba25729e | 30.046 | 465.34 | 528.31 |

All 15 original shots completed; no backup substitution. All five MP4 files have
H.264 720x1280 24fps video, AAC audio and technical_passed=true. Human quality
confirmations remain unset: actual jobs are `awaiting_quality_review`, not approved
or automatically forwarded past that gate. The private batch is not injected into
the main application's Review database. Open the private outputs for inspection;
normal app runs expose the existing quality-inspection action in Automation.

Measured aggregate LTX rate **19.4613 seconds per video second**, used by GPUPlanner
instead of its labelled seed. Shot queue waits overlap and must not be added to
neural time as an elapsed-time estimate. The pilot used 30s recipes for all slots;
shipping defaults remain 45s/30s. This was immediate bounded overnight-style work,
not an installed or tested nightly scheduler.

Replaying enqueue_day for the same date/config made no changes: five slots,
five script attempts, five productions, fifteen shots, five jobs, one queue intent;
all row contents and generation IDs identical. Proof: idempotency-verification.json.
Final file probes/hashes: final-verification.json; measurements: validation.db.

Verification: full Python **696 passed in 619.24s**, versus original623; guarded
Shorts tests **10 passed in 14.85s** (real HTTP/Ollama/submit forbidden); combined
automation/release targeted **88 passed in 18.18s**. Latest frontend **70 passed**,
production build **690ms**, versus original66. No dependencies installed.
Phase 4 is next; episode rendering/lifecycle, Governor and scheduling remain deferred.

## Phase 4 implementation checkpoint — before local inference

- New app/automation/series models, validators, durable service and read-only reads.
- Frozen hashed bible, three premises with past-series dedupe, six five-episode
  chunks, max three acknowledged received-output attempts per stage. Uncertain
  completion stops; execution claims are never reclaimed automatically.
- Additive tables automation_series_requests and automation_series_receipts.
- Existing queue dispatch extended; new session/CSRF series APIs; React Automation
  series page, 30-hook grid, full-outline validated CAS edits and explicit cast gate.
- Cast IDs/name/appearance/wardrobe/voice must match approved immutable references;
  activation never creates jobs, auto-approves content or publishes. Even configured
  series_start_mode=auto cannot bypass the owner's explicit manual-cast requirement.
- Targeted Python: **88 passed in 16.28s** (series/foundation/Shorts combined).
  Initial fake hooks accidentally repeated; fixture corrected, gate retained.
- Frontend: **73 passed**, production build **652ms**. No dependencies added.
- Real request started once, existing qwen3:8b with 16K context, shared GPU lock and
  explicit unload acknowledgment. Recorded feedback will be reported honestly.

### Real local planning recovery checkpoint

Initial series `7fc930dd-992a-5d8e-9f69-259d073b620c` ended known-failed after
three received bible drafts repeated an actor introduction/removal contradiction
(elira_morgan introduced5, removed_after3). Wall99.54s. Its receipts and result.json
are retained. No uncertainty or automatic fourth attempt.

Engineering correction: precise actor/date validator feedback, final feedback
instruction placed after the invalid candidate, and explicit two full-series
recurring actors in the bible prompt. Failed/incomplete planning requests are not
past produced series for premise dedupe. One explicit validation revision started,
series `aebac48c-1722-5e6e-b01b-1764af08c2bd`, execution29076, log
`/tmp/automation-phase4-revision.log`. Marker revision-1.json in the same private
root. DO NOT rerun the launcher. Inspect receipts; final revision output will be
revision-1-result.json. Initial pre-import launch had a SQL quoting syntax error,
fixed/compiled before any model call; no request was made by that failed launch.

Additional series tampering/actor/feedback tests: **29 passed in 3.47s**.
Migration copy phase4-schema-20261107:16 automation tables, three initializations,
all40 original tables/1762 rows/schema preserved, integrity ok, source read-only.
Full regression active in execution3196, log /tmp/automation-phase4-full.log.

### Structured-output compatibility checkpoint

Revision1 `aebac48c-1722-5e6e-b01b-1764af08c2bd` acknowledged/failed at outline1
in139.08s: wrong actor IDs, early/missing thread advances and unresolved-hook flags.
Three attempts exhausted; retained, not automatically retried.

Revision2 `8c7e1ef7-5583-5886-addd-25e08091f333` hit installed Ollama's tuple
schema incompatibility. Initially held needs_reconciliation. Read-only local
journal confirmed **HTTP400 at2026-11-07T01:12:40-05:00** before output. Explicit
engineering reconciliation is saved in schema-reconciliation.json and event log;
request/receipt marked failed/rejected, never resubmitted under the old ID.

Named day fields now carry per-day JSON decoder constraints (known actor/location
IDs, actor lifecycle, ledger planting/payoff and unresolved/finale flags), alongside
the deterministic validators. No creative text fabricated or validation bypassed.
Acknowledged400/422 rejections now fail clearly without retries and unload weights;
transport/unload uncertainty still holds. Shared tests **46 passed in19.26s**.

A new explicit compatibility validation revision is running (revision-3.json,
log /tmp/automation-phase4-revision3.log). This is one final-outline validation
project with retained failed engineering attempts; do not claim first-attempt success
or hide the extra local requests. No paid providers, rendering or publishing.

### Current local evidence

Named-day revision series **a5000b86-0284-5d3d-8733-90cecc16cd99** is progressing.
Premises/bible passed first attempt. Days1–5 passed; days6–10 initially repeated
five earlier hooks, deterministic duplicate checks rejected them, second attempt
passed. Stage outline11 active at this checkpoint. Exact live state is in private
validation.db; await revision-3-result.json. Frontend final **74 passed**, build1.49s;
final shared series/Shorts tests **46 passed in19.26s**. No publishing/episode renders.

### Hook-specific repair checkpoint

Named-day revision a5000b86-0284-5d3d-8733-90cecc16cd99 stopped known-failed
at outline16 after repeated days19–20 hooks; wall326.75s. First15 completed outlines
and every invalid receipt remain intact. No acceptance gate weakened.

Added a narrow hook-only repair when all failures are duplicate/generic endings:
keep goals/conflicts/reveals/cast/ledger unchanged, ask only for the rejected day
strings and provide ALL banned earlier/current endings. Still at most three total
acknowledged outputs per stage; no fourth automatic attempt. Model-generated
repairs merge into the received candidate and the whole prefix is revalidated.
Unit/integration series tests **38 passed in6.08s**.

One explicit engineering validation revision4 is being launched with the corrected
repair behavior (revision-4.json, /tmp/automation-phase4-revision4.log). Do not rerun
its launcher. Earlier iterations remain as honest failure evidence, not lost work.

### Resumable known-failure revision and bounded specialist agents

Revision4 127ddba8-99b6-5504-919e-2539c3d00870 again stopped known-failed at
outline16 with a single repeated hook after three outputs. Existing bible and
first15 accepted days were preserved. Structure and hook repair are now separate
bounded local agents: max3 structural drafts and, if needed, max3 hook-only
repairs per chunk (max6 total, never unbounded; uncertainty stops immediately).

An explicit `source_series_id` revision creates a NEW idempotent request/record,
atomically copies a verified bible/prefix and queues only the remainder. Requires
same actor, known-failed source, unproduced valid prefix and explicit consent;
uncertain sources are rejected. Original rows and receipts remain intact. React
exposes this only with local-planning authorization. No cast approval inherited.

Revision5 reused15 days, confirmed exact prefix equality and bible hash before
local inference. Current ID/log above. New tests:40 series passed9.00s;
frontend75passed/build1.53s. Full732passed709.90s before these final new cases;
release full regression starts after final backend freeze. No new dependencies.

### Story repetition-control evidence

Revision5 stopped known-failed at hooks16 in72.75s; first15 reused intact.
Repair output exposed copying from the old hook embedded in positive context and
500-character paragraphs. Fixed repair context to use goal/conflict/reveal only,
request one10–25-word sentence (max180chars), and give only story planning higher
sampling diversity/repetition penalty (.9/1.2). Existing Shorts .5/default penalty
unchanged and tested.51 combined series/Shorts tests passed22.34s.

Explicit known-failure revision6 reuses the same15-day prefix/bible; days16–20 now
pass FIRST draft with no hook repair. Active exact state above. Earlier failed/
rejected requests remain retained; do not claim a single-attempt successful run.

### Full real outline and editorial validation

Real local final series8019aa1a-4ad6-5628-90c1-9866a7a691ad completed30 days,
awaiting_cast, zero automatic approvals. Revision6 wall137.28s after reusing15 days;
remaining15 structural drafts passed first attempt, one hooks21 repair succeeded.
Original result in revision-6-result.json. Replay snapshot initially proved no new
rows/IDs/receipts/intents. Editorial inspection found a truncated day15 and weak
day20 ending that structural checks missed. One extra local editorial request
(8.81s) generated shorter hooks; saved via the real validated edit method.

Recap continuity strengthened: compiler uses a literal Previous hook recap for
all episodes2–30, validator rejects unrelated openings; original model creative
text is reused without inventing events. Applied to private final-outline.json
through the edit method; no cast approval, rendering or publishing. Previous
snapshots and raw editorial response retained. Immutable request replay now returns
saved output before reevaluating changed source dependencies (test added to the
existing revision test). This also preserves acknowledgments across later source
state changes. No backend changes planned after this checkpoint.

Full736passed660.48s before final sampling/recap/replay changes. Final all-automation
tests pending in /tmp/automation-phase4-automation-release.log; then one complete
post-freeze738-test run will verify the final source. Earlier partial-source proof
remains historical; final report must use final-outline.json after editorial edits.

### Final private replay and migration proof

After editorial hook/recap edits, all30 deterministic validators pass again.
Same request/process replay leaves all series/episode/receipt/queue/event rows
unchanged; bible hash identical. Before editorial edits the inherited15-day prefix
was byte-identical. Current proof distinguishes that historical clone guarantee
from subsequent explicit draft edits. final-verification.json records literal
recaps, validators[], seven retained request versions and75 episode rows (intentional
revision copies, only one full final30-day outline). No duplicate on replay.

Final migrationcopy phase4-schema-final-20261107:16 automation tables across three
initializations, all40 oldtables/1762 rows/schema preserved, integrity ok. Live
source opened read-only and never upgraded. Backend frozen; final full test running.

## Phase 4 end-of-phase report — complete

Built: local premise/bible/30-day planning agents, named-day structured decoding,
bounded structural and hook feedback loops, explicit known-failure revisions,
immutable bible/cast binding, literal previous-hook recaps, validated optimistic
outline edits, durable generation-only dispatch, session/CSRF API and React grid.

Changed files (Phase4 only):

- `app/automation/series/{__init__,models,validators,repairs,service}.py`
- `app/automation/{schema,local_planner,shorts_service}.py`
- `app/api/automation_routes.py`
- `frontend/src/{Automation,AutomationSeries,AutomationSeries.test}.jsx`
- `tests/test_automation_series.py`
- `README.md`, `docs/automation/{ARCHITECTURE,DECISIONS,PROGRESS,SERIES,SERIES_VALIDATION}.md`

No dependencies installed or downloaded. No new environment variables needed;
existing OLLAMA_MODEL (or `story_series.planner.model`) selects the installed local
model. `.env` and credentials untouched; no live database migration or uploads.
All automated API/provider/neural paths mocked; actual local validation is reported
separately, with every failed iteration retained. No GPU video result claimed for
Phase4. Actual five-Short LTX measurements belong to the Phase3 table above.

Try the feature in the existing React Studio: **Automation → Series planning**,
authorize local planning, inspect the hook grid and saved validator feedback.
For known failure, inspect the saved output then explicitly revise the remaining
outline. In Local Studio create an inspected cast matching the displayed frozen
IDs/name/appearance/wardrobe/voice, refresh packages and approve it explicitly.
Activation does not create episodes, approve content or publish.

Verification commands:

```bash
.venv/bin/python -m pytest tests/test_automation_series.py -q
.venv/bin/python -m pytest tests/ -q
npm --prefix frontend test
npm --prefix frontend run build
```

Final real evidence: **Tidal Echoes**, series8019aa1a-4ad6-5628-90c1-9866a7a691ad,
30 planned450s episodes; model-written hook table and all failure/repair evidence
in SERIES_VALIDATION.md. Final local revision137.28s after reusing15 days, separate
editorial request8.81s; no CUDA-kernel profiling or episode rendering claim. Full
validators[], literal recaps verified, frozen bible unchanged, replay no duplicates,
awaiting_cast. Seven retained engineering request versions are intentional history.

Deferred: semantic/acting quality certification, actual cast images/approval,
episode scripts/memory/rendering/lifecycle (Phase5), Governor/publishing/scheduling
(Phase6), operations/soak/stretch (Phase7). No Phase5 work started. The five private
Shorts still need human quality confirmation before entering content Review; no
automatic human confirmations were fabricated to satisfy a queue label.

Suggested milestone commit: `feat: add local series planning and manual cast approval`.
The owner authorized committing this milestone on main; no push is authorized.
Final verification results follow; the milestone commit is identified by title in Git history.


### Final post-freeze regression — completed 2026-11-07

- `.venv/bin/python -m pytest tests/ -q`: **738 passed in607.10s (10:07)**,
  zero failures/skips, execution51008 exit0, /tmp/automation-phase4-postfreeze.log.
- `npm --prefix frontend test`: **75 passed**,11 files, **3.43s**.
- `npm --prefix frontend run build`: passed **1.53s**.
- Targeted all-automation **115 passed in23.29s**; includes42 series cases,
  adversarial outlines, literal recaps, bounded JSON/hook repairs, frozen
  sampling defaults, revision ownership/replay, cast gating and optimistic edits.
- Baseline623 Python/66frontend → final738/75; **+115 Python, +9 frontend**.
  No unrelated regression or baseline failure was hidden or repaired.
- Compilation, whitespace checks and final16-table migration-copy proof passed;
  all40 oldtables/1762 rows/schema retained, integrity ok, source opened read-only.
- Exact local reality and limits are in the end-of-phase report and
  SERIES_VALIDATION.md. Backend frozen after final fixes; no need to rerun models.
- Owner-authorized milestone commit to main, no push. **STOP. Phase5 not started.**


## Phase5 checkpoint — local cast options requested

Owner asked to create a few approved cast packages and chose recorded sources/
mock generation for the real24h Phase7 soak. Actual elapsed24h must still be
measured; no fake24h claim. We will prepare image candidates first, then obtain
owner image approval before creating approved packages (§6.2). No auto-approval.

Using existing installed pinned local SDXL, not a cloud/image API (local-only rule
supersedes imagegen skill's default provider). Prompt guidance from imagegen skill
was read/applied. Six images for three two-actor cast options: Tidal Echoes,
Everyday Comedy, Coastal Adventure. Generation session30492, log
/tmp/automation-cast-options.log, private manifest
`data/automation/validation/cast-options-20261107/manifest.json`. Exclusive root
creation prevents relaunch; DO NOT rerun /tmp/prepare_automation_casts.py.
Lucien/Miri were generated64.23s/57.35s and visually inspected; others ongoing.
No refs registered or cast packages approved yet. Images pending owner review.

Concrete lifecycle observation: frozen TidalEchoes bible marks lucien1–30,
miri1–3. Preserve it, do not silently extend Miri. Episode planner now constrains
on-screen actor IDs to the frozen window/outline and carries inactive actors'
prior state unchanged. UI shows each actor's active episode range.

Phase5 first mocked tests4passed4.80s including35-day simulation. Shared56passed
24.38s before extra cases; latest nine cases8.13s then lifecycle test added (new
result pending). Frontend77passed/build649ms before final metadata changes.
No actual video generation yet. Episode1 pilot240s awaits owner approval of images.

## Phase 5 — production and lifecycle checkpoint (2026-11-07)

Built `series/episode_planner.py`, `series/episodes.py` and
`series/reference_frames.py`; extended the existing automation routes, durable
worker dispatch, additive schema and GPU planner. New AutomationEpisodes React
panel sits inside the existing Series page, using existing production inspection.
Documentation: EPISODES.md, EPISODE_VALIDATION.md, README and DECISIONS.

Verification covers 35 fake-clock days, buffer/skip/finale/rollover and strict
publication order, bounded response feedback, hash-bound actual-script state,
actor lifetime, concurrent stable reservations, copied migrations, and a
240-second CPU FFmpeg end-to-end fixture with mocked models. No live publishing,
paid calls, new dependency, scheduler start or systemd installation.

Actual hardware: owner-approved Tidal Echoes references, two120s Ollama chapters,
24 connected LTX image shots, fixed cast voices/ASR captions and FFmpeg assembly.
Final duration240.086s, neural stage4828.80s, render wall5248.59s, repeat request
unchanged. See EPISODE_VALIDATION.md for path/hash/tail and known framing limits.
Manual quality acceptance is still pending, so Review-queue completion is NOT
claimed. This does not block independent Governor implementation; it must never
be silently converted to an automated approval during Phase6 verification.

Try the existing Automation → Series planning page with an approved series, then
confirm the local episode request. CLI targeted verification:
`.venv/bin/python -m pytest tests/test_automation_episodes.py -q`.
Next: opt-in Governor gates/slots/kill switch, standing authorizations, single
scheduler-process wiring and seven simulated dry-run days, without live uploads.

Final Phase5 regression: **757 Python passed688.12s** (+19 vs738),
**77 frontend passed2.31s** (+2 vs75); build646ms. `git diff --check` and
compileall passed. Manual real-video acceptance remains the sole Phase5 pending
verification item; no human confirmations were invented.
