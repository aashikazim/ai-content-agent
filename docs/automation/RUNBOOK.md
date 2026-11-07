# Automation runbook

This is a single-host DGX Spark system. Defaults are disabled, manual and dry-run.
Director only creates work. The separate opt-in Governor is the only automation
allowed to approve/publish. Cast approval remains human for every series.

## Before changing anything

Inspect Automation → Operations and Publishing Governor. Record the lane, day,
run/job/production/action IDs, current status and last heartbeat. Existing content
creation, review and manual publishing remain available. A generation credential
cannot approve/publish or change Governor settings; use the admin session/CSRF.

Back up SQLite before applying additive automation migrations:

```bash
# Run from the repository; choose a new backup filename.
sqlite3 data/trends.db ".backup 'data/trends-before-automation.db'"
# This command verifies migrations on copies only; source is opened read-only.
.venv/bin/python scripts/check_automation_migration.py \
  --source data/trends.db --output-dir data/automation/validation/my-schema-check
```

Do not delete/reset trends.db, execution claims, quotes, media or provider IDs.
Do not copy a test database over the live file. Keep credentials out of logs/chat.

## Stop automation

Use **Automation → Publishing Governor → Stop Governor now**, or create the file
configured by AUTOMATION_KILL_SWITCH_FILE (default below):

```bash
mkdir -p data/automation
touch data/automation/KILL
```

The file and DB switches are independent. Kill stops further Governor decisions;
it does not cancel an in-flight upload. Pause individual platforms in the panel.
Switch a lane to manual to require normal manual review/publishing. Disable
AUTOMATION_ENABLED for future Director dispatch and AUTOMATION_GOVERNOR_ENABLED
for Governor operation, then restart the owner-managed scheduler as appropriate.
Already submitted work remains tracked; do not kill neural/upload processes blindly.
No service was installed or started during implementation.

## Restart and interrupted generation

The existing app_work_queue claims once, heartbeats, and marks expired running work
interrupted rather than requeueing it. Inspect saved local task/provider IDs first.
Repeated day/episode requests reuse their intent; they cannot reclaim a submission.
A process restart never grants new billing authorization.

For a queued never-claimed item, restore the existing durable worker or explicitly
run the normal worker drain after inspection. For interrupted/uncertain work, use
existing generation reconciliation/continuation controls, preserving original
outputs. A paid request with uncertain billing/completion must never be retried
automatically. Do not reset a lease/status in SQL to force a new attempt.

Keep one application worker consumer and one scheduler process. Existing worker
and GPU locks serialize work across manual and automated creation. A GPU task that
is still active must finish/reconcile before a replacement is submitted.

## Stuck GPU or insufficient capacity

Operations labels historical seeds separately from measured worker stage times.
Only real completed measurements are hardware evidence; mocked soak output adds
none. Check host available RAM, free disk, worker heartbeat, saved native task
state, per-job deadline and the existing local runtime diagnostics. DGX Spark's
memory pool is shared; do not start a second eager video/planner model to debug it.

A forecast hold is not a crash. Director checks the nightly window and remaining
time; services reserve the shared day budget. It may shorten/drop configured
Shorts slots or reuse the episode buffer. Do not shorten an episode silently.
An explicit single-episode manual request still needs the normal capacity checks.
A failed slot has at most one known-failure backup substitution; uncertain work
holds instead. Restore disk/RAM/runtime readiness before authorizing any new work.

## Publishing uncertainty and platform pauses

Inspect the existing Publishing page and publication record. Uploaded, submitted,
processing or private is not verified published. Use the repository's existing
reconciliation flow or the documented reconciliation CLI:

```bash
# This can read live platform status; operator runs it deliberately after setup.
.venv/bin/python scripts/reconcile_publications.py
```

Any needs_reconciliation record or unfinished live Governor action pauses the
platform. Three consecutive failures also pause it. The Governor never consumes
retry_authorized automatically. Existing manual retry authorization remains an
owner action after checking that no duplicate remote upload exists.

If no platform attempt was reserved or its outcome is known, close the inspected
Governor action with a recovery note through
POST /api/v1/automation/governor/actions/{id}/close. Its permanent unique key
remains, so closure cannot restart an automatic upload. Then resume the platform
explicitly. Do not clear a publication's uncertain status merely to unpause it.
A restarted Governor observes reconciliation results; it never infers success.

When public YouTube visibility remains private, inspect project audit/visibility
and the video's actual status. A private result alone does not prove the cause.
Do not repeatedly upload the same video. YOUTUBE_PRIVACY_STATUS defaults private.
For Facebook token/permission problems, use existing scripts/facebook_auth.py
inspection/provisioning instructions; tokens remain revocable. Never paste tokens.

## Series and buffer

A series waits for owner-approved cast images/package before activation. The bible
and package remain frozen. Next script inputs include three previous summaries,
actual-script state and the next outline. Inspect episode errors in Series planning.
A broken previous script prevents blindly generating later state.

Use explicit skip with a meaningful reason when appropriate. Produced canon is
preserved; missing scripted events are not invented. Publishing N+1 requires all
earlier episodes published or deliberately skipped. A dry buffer means skip a day
and resume in order. Day30 completion creates new planning after the configured
gap; the new series still waits for cast approval. Do not mark cast-approved in SQL.

## Slots, approvals and paid limits

Changing approval mode does not publish immediately: a configured local-time slot
must be due. The execution grace is15minutes; missed slots defer to the next day.
The atomic daily cap includes manual uploads and retained action reservations.
Use the report to distinguish held/skipped/uploaded/private/verified-published.

Standing authorization is provider-scoped and expiring, with daily/total Decimal
caps plus AUTOMATION_DAILY_BUDGET_USD. Zero global budget forbids paid automation.
Existing quotes must match the standing actor and request; no quote is bypassed.
Unknown spend retains the full conservative reservation. Revocation prevents new
spend but does not refund/cancel submitted work. Daily recipes currently stay local.

## Services: render first, one scheduler

```bash
# Preview only. This neither installs nor starts anything.
.venv/bin/python scripts/install_services.py --output data/service-preview
systemd-analyze --user verify data/service-preview/*.service
```

The existing scheduler unit runs scheduler.py. Its Director name is an alias of the
same unit, not a second daemon. The optional governor-check unit is status-only,
oneshot; actual timed Governor execution belongs to that same scheduler process.
Installation, enabling and service starts remain deliberate operator deployment
steps. Do not run two copies under different unit names or shell sessions.
DURABLE_WORKERS=true uses the existing supervised queue consumer. Verify working
directory, isolated model runtimes and local environment before deployment.

## Recorded/mock validation and resuming the24h check

The validation script requires the repository's existing pytest development
environment and reuses fixture factories. It is not an application service.
It confines data to a marked private directory, blocks network/GPU/upload/process
launches, and uses recorded YouTube parsing, real durable queue/Director/Governor,
mock planning/media/identity and PublishingService dry-run. Its byte artifacts are
not playable videos or real technical/GPU evidence. It never approves real content.

```bash
# A short smoke check is explicitly NOT24h acceptance.
.venv/bin/python scripts/automation_soak.py \
  --root data/automation/validation/my-smoke --smoke-seconds 2
# Bounded foreground24h validation; no scheduler or systemd activation.
.venv/bin/python scripts/automation_soak.py \
  --root data/automation/validation/my-real24h
```

Do not launch the same root twice while its foreground lock is held. progress.json
and heartbeats.jsonl record actual active monotonic time and UTC elapsed time.
Resume an interrupted run with the exact same command/root; downtime is excluded
and continuity is labeled false. Completion requires both active and wall time
>=86400seconds. A smoke run cannot be relabeled24h on resume. Do not count the
35-day fake-clock simulation as real elapsed time. Actual upload count and24h
publication rows must stay zero. Mock35-day lifecycle records are separate and
explicitly simulated; they never appear in the real24h publication table.

Reports: data/automation/reports/YYYY-MM-DD.md; private validation reports live
inside their fixture roots. See PROGRESS.md and VERIFICATION.md for current evidence.
No stretch item is implemented until the owner chooses it.
