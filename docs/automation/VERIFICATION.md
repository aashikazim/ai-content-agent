# Automation verification matrix

Status as of2026-11-07. **Phase7 acceptance is pending the actual24-hour soak.**
No live upload, paid request or scheduler/service activation was used for verification.
See PROGRESS.md for commands, logs and exact resume state. Source code is on main.

| Definition of Done | Evidence | Status / limit |
|---|---|---|
| Baseline Python and frontend regressions | Phase5:757passed688.12s; Phase6 full775passed686.54s plus final targeted21passed8.02s; Phase7 full791passed758.04s | Final regression passed; frontend82passed2.26s/build662ms |
| Five real-source topics, quotas/dedupe | Phase2 real YouTube50signals and five previews with honest category fallbacks, TRENDS.md/PROGRESS.md | Verified collection; refresh sources for actual go-live |
| Five real Shorts and measured capacity | Phase3 private outputs, measured neural times721.33/579.79/598.33/558.88/465.34s; repeat created no duplicate | Owner confirmed all five; private jobs pending_review;0publications |
| Thirty-episode outline and cast gate | SERIES_VALIDATION.md; real local outline with recorded repairs; owner approved Tidal Echoes images/package | Verified; semantic quality remains an owner judgment |
| Real3–4minute episode and review | EPISODE_VALIDATION.md;240.086s,24shots, LTX4828.80s/render wall5248.59s; owner confirmed all3quality checks | Private validation job pending_review; not content-approved or published |
|35-day order/buffer/finale/rollover/skip | Existing Phase5 lifecycle test plus Phase7 combined Director/queue/Governor/series test:2passed59.27s | Simulated29published/1skip, next series awaits cast; no actual upload; final full791case regression includes expanded cases |
| Governor gates/dry-run/live mocks/kill/auto-trip/dedupe | GOVERNOR_VALIDATION.md; seven days14distinct validations,0duplicate calls/0uploads/0approvals; actual review/publish services with mock upload objects | Verified with fixtures; no live visibility/access claim |
| Every additive table/idempotency path | Phase6 copy:21automationtables,40legacytables/1771rows preserved,3initializations/integrityok | Verified on copies; live DB not automation-migrated during development |
| API/CLI/config/docs | Admin/CSRF tests, read-only operations tests, EPISODES/GOVERNOR/RUNBOOK/GO_LIVE and .env.example defaults | Implemented; final full regression passed |
| systemd render/verify | Four templates rendered, read-only host systemd-analyze --user verify exit0/no output | Verified, not installed or started; Director alias is same scheduler |
| Real24-hour foreground whole-system dry-run | FINAL root data/automation/validation/phase7-real24h-final-20261107; started2026-11-07T15:46:33.603316+00:00 | RUNNING; earliest2026-11-08T15:46:33.603316+00:00; not complete |
| Owner authorization and secrets | Commits11dead8/d0ba630 explicitly authorized; no push; no .env/credential/media/DB staging; no services/uploads | Owner authorized Phase7 commit before soak completion; acceptance pending; stretch untouched |

The actual24h run uses SystemClock plus monotonic active-time accounting, recorded
YouTube parsing and mocked generation/media/planning/identity. It exercises real
queue/Director/Governor/dry-run publishing methods in a marked private DB. Explicit
bootstrap fixtures cover today's slots; subsequent generation respects the real
nightly window. Its fake byte artifacts are not real MP4/GPU/codec evidence. The
separate real episode is the hardware evidence. This validation does not test a
running web service, APScheduler deployment, live source freshness or platform APIs.

The initial five-minute preflight was deliberately stopped after finding scheduling
gaps. Its files are retained under phase7-real24h-20261107 and do not count toward
acceptance. The final run includes pre-start buffering and persisted local-time
slots. Source hashes bind its resumed version. Downtime is excluded; a resumed
run is labeled discontinuous. Both active and wall time must reach86400seconds.
Do not turn this row green early. The owner authorized committing the implementation
while the soak continues; any discovered bugs receive separate bug-fix commits.

Deferred stretch: episode teasers, Facebook long-video publishing, playlist/scheduled
publishAt, thumbnail generation, and lane3. None started; owner must choose first.
