# Governor and Director operations

The default remains manual review and separate manual publishing. Generation
credentials cannot use the new administrator-only Governor API. The Director
creates generation work only; a separate opt-in Governor can approve and publish.
No service, scheduler or live upload was started during implementation.

## Explicit opt-in

`AUTOMATION_ENABLED=false`, `AUTOMATION_GOVERNOR_ENABLED=false`,
`AUTOMATION_GOVERNOR_DRY_RUN=true` and manual lane modes are safe defaults.
An owner may set a lane to `auto_with_gates` in config/lanes.yaml or the
Automation → Publishing Governor panel. A persisted dashboard override wins over
YAML approval mode. Environment flags still control master enable and dry-run.
Returning a lane to manual immediately prevents further automated approval/upload.
In-flight operations are retained for inspection, never blindly cancelled.

Use the existing admin session/CSRF protections. The new controls cannot be used
with a generation API key. Administrator actions and Governor decisions have
separate audit actors; automated checks never assert a human inspected a video.
Cast package creation and series activation still require human approval.

## Gates and slots

The Governor reads existing production/job records and validates decoded media,
duration, dimensions, FPS, motion, audio, hashes of final and selected clips, and
unchanged cast/reference/continuation dependencies. Speech requires per-scene ASR
WER <= governor.wer_max; ASR/aligned captions require timestamp evidence and the
caption artifact. Missing evidence holds the job. Owner keyword/regex rules hold
matching scripts; a configured judge without an available evaluator fails closed.
No paid judge is created automatically.

Near duplicates compare title and screenplay with recently verified published
content. Episodes cannot precede any earlier episode that is neither published
nor explicitly skipped. Shorts map to YouTube + Facebook Reels, episodes only to
YouTube. Private validation runs cannot be auto-approved/published.

A configured publish slot has a fifteen-minute execution window. A video that
misses it waits for the next day's corresponding slot. Approval does not trigger
an immediate upload. Daily caps reserve atomically and count manual publications
as well as Governor actions. Retained failed/unknown reservations remain counted;
there is no automatic refund or retry. One action exists per job/platform/mode,
so repeated ticks cannot duplicate a publishing call. Dry/live ledgers are separate.
A nonblocking per-database Governor lock also serializes API and scheduler ticks
across the shared approval transition; a concurrent tick returns busy.

Dry-run calls PublishingService.publish(dry_run=True) with a read-only projection
of approved status. It leaves real approval, human continuity confirmations,
publication rows and platform credentials untouched. Live mode uses the shared
technical review transition, existing ReviewDashboardService and PublishingService;
the existing job/platform uniqueness is preserved. Human review endpoints retain
their confirmation requirements and event wording.

## Stop and recover

Set `AUTOMATION_KILL_SWITCH_FILE` (default data/automation/KILL), or use Stop
Governor now in the dashboard. File and DB switches are independent. Clearing
the DB flag cannot override a remaining file. Check again before mutations/upload.
Existing in-flight uploads are never cancelled or retried blindly.

Three consecutive publish failures pause the platform; any uncertain publication
or unfinished live Governor action pauses immediately. The report shows the
reason. First use existing Publishing reconciliation/manual retry authorization.
The Governor never consumes a manually authorized retry. A known remote ID/outcome
can reconcile its action; an owner can close an inspected unresolved action with
notes, retaining uniqueness forever. Closing never resets an upload claim. Resume
only after unresolved publication records have been reconciled.

Requested public YouTube visibility that remains private is a blocker: inspect
project audit/visibility in YouTube. The Governor does not assume the cause or
retry until public. Existing default YOUTUBE_PRIVACY_STATUS=private is respected.
No live visibility condition was exercised in this implementation.

## Director and the single scheduler

scheduler.py retains one BlockingScheduler. Legacy SCHEDULER_ENABLED controls its
old generation job. AUTOMATION_ENABLED registers trend collection, generation-only
Director ticks, the configured daily-plan tick and report; the Governor tick is
registered only when separately enabled. Jobs coalesce and have max_instances=1.
Nothing starts on import. Never run a second scheduler process for these lanes.

Automatic generation respects the GPU window and remaining-time forecast before
queuing; buffer/day services also reserve shared capacity. The existing durable
queue, process GPU lock, memory guards and isolated native workers remain in use.
Manual generation controls remain explicit. Retained uncertain claims never expire
into a new execution. New series rollover queues planning and waits for manual cast.

## Paid authorizations

The dashboard/API creates provider-scoped daily/total caps and an expiry. An
additional global AUTOMATION_DAILY_BUDGET_USD=0 disables paid automation spend.
StandingAuthorizations.submit_script/create_video require an existing unused quote
bound to actor `automation:standing:{authorization_id}`. They reserve its conservative
estimate atomically before using the existing confirmed quote-consumption methods.
Expiry, revocation, provider, actor, quote and all caps must pass. A crash/uncertain
outcome retains its reservation and cannot resubmit. Budget amounts use Decimal.

Daily recipe/planner configuration intentionally stays local-only; creating an
authorization does not switch providers. Paid orchestration helpers are available
for explicit future provider recipes, without bypassing existing quotes. No paid
provider or new dependency was used to verify this phase.

## API and CLI

- GET `/api/v1/automation/governor`: flags, effective lane modes, platform state,
  recent immutable action identities.
- POST `/governor/state`: confirmed scope/global kill switch or platform pause.
- PUT `/lanes/{lane}/approval`: confirmed manual/auto_with_gates mode.
- POST `/governor/tick`: explicit confirmed configured evaluation.
- POST `/governor/actions/{id}/close`: confirmed inspection note; never reopens claim.
- GET `/events?after_id=0&limit=100`: bounded append-only audit pagination.
- GET/POST `/authorizations`, POST `/authorizations/{id}/revoke`: confirmed caps.

Paths above share `/api/v1/automation`. Examples:

```bash
# State inspection initializes additive tables only after backing up if needed.
.venv/bin/python scripts/run_governor.py --database /path/to/private.db
# One tick honors disabled/manual/dry-run settings; never starts a daemon.
.venv/bin/python scripts/run_governor.py --database /path/to/private.db --tick
.venv/bin/python -m pytest tests/test_automation_governor.py -q
```

Reports combine lanes, held decisions, platform pauses and measured-only timings.
Latest published analytics reuse FeedbackService's category ranking, with at least
three samples and bounded 0.5–2 weights. Hard category quotas remain unchanged.
