# Local serialized-story planning (Phase 4)

Open **Automation → Series planning** in the existing React Studio. Sign in with
its existing single-admin account. This is local planning only: no episode render,
scheduler, content approval or upload is triggered by this page or cast activation.
The daily Shorts workflow remains independently available.

## Plan and inspect

1. Choose a genre. Optionally enter a 40–2000-character original premise. Leave it
   blank to select the first of three distinct, past-series-deduped local candidates.
   With an owner premise, candidate one's title is used with that exact premise.
2. Confirm local planning and click **Plan a new 30-episode series**. This works as
   an explicit manual action even while unattended automation is disabled. Refresh
   or reopen history to inspect the same durable request; no new request is made.
3. The installed Ollama model (`story_series.planner.model`, or the existing
   `OLLAMA_MODEL`) produces three premises, a bible, and six five-episode chunks.
   Local requests share the cinematic GPU file lock and use a 16K context. The
   model is explicitly unloaded before releasing the lock. No paid fallback exists.
4. Inspect the 30 hooks and the received-output validator feedback. Stage changes
   to an individual day's JSON, then save the entire validated outline. When an
   ending hook changes, update the next day's `previous_hook` and set its
   `opening_ack` to `Previously: ` plus that exact new hook (or include that recap
   before additional opening text).
   Concurrent/stale versions are rejected. Any produced/scripted episode locks
   outline editing to avoid rewriting established continuity.

The metadata compiler assigns consecutive episode numbers, phases, previous-hook
references, a literal preceding-hook recap for days2–30, and runtime (midpoint
of configured 300–600s limits). The model supplies
all creative text and actor/location/thread references, constrained to known IDs
and per-day ledger/lifecycle rules. The wire schema uses five named day fields
(the installed Ollama rejected tuple/prefixItems schemas). Metadata compilation does
not invent hooks or silently repair failed creative output. Setup is days1–8,
escalation9–22, climax23–29 and resolution30. Prefix checks run on every chunk;
the final check requires all30 days and all thread payoffs.

Each agent has at most **three acknowledged output attempts**, with validator
errors and the previous output fed back. A chunk has a structural agent and,
when necessary, a separate hook-only specialist (at most six received outputs
total per chunk). The specialist preserves valid goals/conflicts/reveals and
receives every banned earlier ending. No gate is weakened to accept duplicates. Invalid JSON may be repaired only after
an acknowledged local completion/unload. Acknowledged HTTP400/422 schema rejections fail without retry and unload weights.
Transport/unload uncertainty stops;
interrupted claims are never automatically reclaimed. A failed request remains
visible with its receipts and partial outline. Inspect it before creating a new
explicit request. For a known-failed outline with a valid bible, authorize
**Revise remaining outline** to create a new record preserving the bible and accepted
days. Only unfinished chunks are generated. The source stays unchanged. Uncertain
requests cannot use this path until reconciled. No scheduler/service is installed or started by configuration.

## Manual cast approval

The frozen bible uses **2–3 actors**, matching existing cast-package limits. In
Local Studio, upload and inspect 1–6 approved reference images for each actor and
create a cast package. Copy the exact character IDs, names, descriptions, wardrobe
and voice notes shown by the series page. The existing cast service verifies the
package, every asset hash, optional adapter and speech profile. Unrelated packages
are rejected; the frozen hashed bible is never rewritten to fit them.

Refresh cast packages, select the correct package and explicitly confirm the
reference images. Only this admin/CSRF-protected action moves `awaiting_cast` to
`active`. One active series per lane is enforced. Even `series_start_mode: auto`
cannot bypass this manual gate. Activating a series **does not create episode
jobs, approve content or publish**. Those later-phase production/lifecycle and
Governor capabilities remain deferred.

## API and storage

All routes use the existing admin session; mutations require its CSRF header.
No existing endpoint contract changes.

| Method / route (`/api/v1/automation`) | Purpose |
|---|---|
| GET `/series` | Read existing series without schema initialization |
| POST `/series` | Queue local planning; `request_id` UUID, `genre`, optional `premise`, `lane: story_series`, `confirmed: true`; optional `source_series_id` for explicit known-failure revision |
| GET `/series/{id}` | Status, bible, episodes and receipt validation feedback |
| PUT `/series/{id}/outline` | All30 `episodes` and `expected_updated_at`; deterministic validation and optimistic version check |
| POST `/series/{id}/approve-cast` | `package_id`, `expected_updated_at`, `confirmed: true`, optional `start_date` |

A request UUID is actor/payload-bound. Repeating it returns the saved series, with
no second queue intent or model call. New additive tables
`automation_series_requests` and `automation_series_receipts` join the existing
`series` and `series_episodes` tables. The existing durable worker dispatches
`automation / series:{id}`. With DURABLE_WORKERS=false, an explicit API submission
launches the existing bounded `--drain` process, not a service or scheduler.
Acknowledged model bodies are stored privately in SQLite before validation;
automation events contain metadata only. GET requests are read-only. Mutating
entry points back up an older DB before initializing missing automation tables.

## Verification and limits

```bash
.venv/bin/python -m pytest tests/test_automation_series.py -q
npm --prefix frontend test
.venv/bin/python scripts/check_automation_migration.py --source data/trends.db \
  --output-dir data/automation/validation/my-new-series-migration-check
```

Automated tests forbid real network/inference/render calls. Deterministic gates
cover schema, runtime, phase/order, non-generic/repeated hooks, previous-hook
references and explicit recaps, mini-arcs, actor lifecycle, locations, planted/payoff timing and dangling
threads. They do **not** certify narrative coherence, performance, lip sync or
semantic truth of an opening acknowledgment; the owner should inspect the text.
The actual local validation project, including retained failed engineering
iterations, resumed prefixes and draft edits, is recorded in PROGRESS.md and
[SERIES_VALIDATION.md](SERIES_VALIDATION.md). GPU video quality/episode performance is not a
Phase4 result.
