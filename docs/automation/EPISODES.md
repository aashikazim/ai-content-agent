# Episode production and lifecycle

Automation → Series planning → an active series shows the episode production
panel. Approve the cast separately before requesting any episode. Confirm local
rendering to queue an episode, or fill the buffer. These controls never approve
content or publish it. Defaults remain disabled/manual/local-only.

Each request freezes the outline, approved cast and bible hash, previous three
summaries, running story state and next outline. Scripts use Ollama through the
existing ScriptGenerationService. Chapters are at most 180 seconds; the final
30 seconds contain the planned hook and end-card line. The state updater reads
the actual screenplay and stores a hash-bound summary/knowledge/location/thread
ledger. Titles, recap, Tomorrow teaser and tags use the frozen series context.

Production reuses the existing native LTX image-conditioned connected workflow,
fixed cast voices, ASR captions, FFmpeg assembly, technical checks and Review
queue. Geometry-only landscape references preserve approved portrait framing;
the original approved images and cast package remain unchanged. Blurred side-fill
can remain visible. Human inspection is still necessary for acting, continuity,
caption timing and rights. Existing manual Wan/hybrid workflows are unaffected;
automated episodes currently reject Wan/hybrid because driving footage is not
part of their daily recipe.

## API

All routes require the existing administrator session and CSRF protection for
mutations. Existing endpoints retain their contracts.

- `GET /api/v1/automation/series/{id}/episodes`: ordered outline/job/run history.
- `POST /api/v1/automation/series/{id}/episodes/{n}/run` with
  `{"confirmed":true}`: reserve and dispatch once, returning the durable run.
- `GET /api/v1/automation/episode-runs/{id}`: frozen inputs, status and received
  planner-response metadata.
- `POST /api/v1/automation/series-tick` with `{"confirmed":true}`: fill eligible
  buffer work within the persisted daily GPU forecast.
- `POST /api/v1/automation/series/{id}/episodes/{n}/skip` with
  `{"confirmed":true,"notes":"Owner's explicit reason"}`: skip deliberately.

One intent per series/episode and an execution claim prevent duplicate scripts,
render jobs and measurements. Received responses are saved before validation;
known-invalid responses have bounded feedback attempts. Uncertain requests remain
held for reconciliation. Repeating a form cannot reclaim a failed/uncertain
execution. Inspect retained IDs before taking any recovery action.

## Ordering and rollover

The next script requires the previous script's summary/state. Buffering is
independent of publication. A future series prewarms within its configured buffer
lead; missing local-time publish slots are frozen for all30episodes. Existing
slots are preserved, including the correct UTC offset through daylight-saving
changes. Early generation cannot make a future slot due. Only a verified `published` publication counts for
ordering; uploaded/private/submitted do not. Episode N requires every earlier
episode to be published or explicitly skipped. Skips preserve actual scripted
state where available and otherwise carry prior state plus deferred thread
obligations; missing screenplay events are never asserted as facts.

After episode 30 is published and all predecessors are published/skipped, the
series completes. A durable unique rollover intent waits the configured gap
from the actual publication date, then queues new series planning. The next
series waits for human cast approval. No self-approval is implemented.

Regular episode duration remains 300–600 seconds as established in Phase 4.
A 180–240 second validation override is restricted to private databases under
`AUTOMATION_DATA_DIR/validation` and flagged validation-only. It is never an
ordinary daily episode or permission to publish.

## Verification

Run `.venv/bin/python -m pytest tests/test_automation_episodes.py -q`.
Tests cover 35 simulated days, ordering, failure/skip/rollover, memory and hook
validation, concurrency/idempotency, frozen actor lifetimes, capacity, authenticated
routes and a 240-second CPU FFmpeg end-to-end flow with mocked generation.
These are not GPU benchmarks. See [real episode evidence](EPISODE_VALIDATION.md)
and [progress](PROGRESS.md) for measured hardware results and pending acceptance.
