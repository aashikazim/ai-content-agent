# Go-live checklist

Do not switch a lane to live auto_with_gates until the verification matrix and
owner checks below are complete. No live upload or unattended deployment was
performed by implementation tests. Manual operation remains available throughout.

## Verify the installation

- [ ] Review main-branch changes and keep .env, credentials, databases, model files
  and generated videos ignored. Back up trends.db and confirm restore procedures.
- [ ] Read VERIFICATION.md. Require a completed real24h run with matching source
  hashes, >=86400active seconds, >=86400UTC seconds, zero forbidden calls/uploads,
  zero duplicate validation calls and zero publication rows. A short smoke or
  fake-clock35-day simulation does not meet this condition.
- [ ] Run regression suites/build in the intended environment. Confirm isolated
  Ollama/LTX/Wan/Piper/Whisper environments, checkpoints, FFmpeg and free disk/RAM.
  Test generation using the existing manual page before unattended operation.
- [ ] Inspect real generated footage, speech, caption timing, continuity and rights.
  The real240s episode passed owner quality inspection; its private Review job is
  still not content-approved. All five earlier pilot Shorts also passed owner quality inspection and are
  pending_review in their private validation database.
- [ ] Review measured model/mode rates in Operations. Historical seeds and mocked
  soak output are not new GPU benchmarks. Confirm five Shorts plus the chosen
  episode duration fit the configured window; otherwise adjust the window/recipe
  or accept recorded skipped slots. Do not silently shorten a story episode.

## Configure generation

- [ ] Set config/lanes.yaml timezone, categories/quotas, durations, enabled lanes,
  GPU nightly window, publish slots, caps, cooldown and owner hold rules.
  Leave lane_3 disabled. Daily generation remains local Ollama + local LTX.
- [ ] Provision trend-discovery credentials locally and collect a current sample;
  verify five topics and fallback reasons with plan-preview. YouTube footage is
  never reused by trend automation. Optional sources remain best-effort.
- [ ] Inspect the30hooks and immutable bible; approve cast images/package manually.
  Choose a series start date with enough buffer lead. The engine can prewarm
  inside buffer_ahead_episodes days while keeping publish slots in the future.
  Check each episode's prior-script state and actual runtime/quality.
- [ ] Set AUTOMATION_ENABLED=true and DURABLE_WORKERS=true only after testing the
  existing queue worker and recovery flow. Keep Governor disabled or dry-run.
  Verify one application worker and one scheduler process, not parallel copies.

## Verify platform access manually

- [ ] Configure YouTube OAuth/project/channel access locally. Confirm actual upload
  visibility and any project audit restriction manually; private uploads are not
  public delivery. Keep private defaults until deliberately changed by the owner.
- [ ] Configure Facebook Page permissions/token locally and inspect current access.
  Tokens can be revoked. Confirm Reels limits and the intended Page/account.
  Episodes map only to YouTube; long Facebook uploads remain out of scope.
- [ ] Inspect existing publication attempts and reconcile every uncertain outcome.
  Test duplicate prevention, platform pause and kill switch using mocks/private
  verification before enabling live work. Never reset statuses to force an upload.

## Enable Governor deliberately

- [ ] Start with AUTOMATION_GOVERNOR_ENABLED=true and
  AUTOMATION_GOVERNOR_DRY_RUN=true. Confirm reports show appropriate technical,
  WER/caption, duplicate, owner-rule, series-order, slot and cap decisions.
- [ ] Set each chosen lane to auto_with_gates in the admin panel or YAML. A saved
  dashboard override takes precedence over YAML mode. Other lanes remain manual.
  Check that generation API credentials cannot change Governor controls.
- [ ] Inspect publish slots,15minute grace, daily caps and existing manual uploads.
  Verify missed slots defer, episodes remain in order, and uploaded/submitted/
  private records are not shown as verified public publication.
- [ ] Clear both file and DB kill switches only when ready. Confirm the dashboard
  stop button remains usable during an outstanding evaluation. Pausing cannot
  cancel or retry an in-flight upload.
- [ ] Only after all checks, set AUTOMATION_GOVERNOR_DRY_RUN=false locally and
  restart the existing owner-managed scheduler. This is an explicit grant for the
  separate Governor to approve/publish eligible content at slots. It is not granted
  merely by running the application or enabling generation.
- [ ] Watch initial outcomes manually, reconcile using existing platform status
  tools and inspect the daily report. Stop/return to manual if outcomes are unclear.

## Deployment and paid providers

- [ ] Render/inspect/verify units before installing. The Director alias refers to
  the same scheduler unit. The Governor check unit is status-only; do not create a
  second scheduling daemon. Installation/start remain owner actions.
- [ ] Keep AUTOMATION_DAILY_BUDGET_USD=0 for local-only use. If later using paid
  orchestration helpers, create an expiring owner standing authorization with
  provider/daily/total caps and matching actor-bound quotes. Never bypass consent,
  refund an unknown reservation automatically, or retry uncertain billing.
- [ ] Monitor disk, RAM, queue heartbeats, held work, measured rates and platform
  pauses. Keep the runbook and backups accessible. Every next-series cast still
  requires human approval. Choose any stretch item explicitly after acceptance.
