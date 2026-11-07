# Daily local Shorts

Phase 3 adds explicit generation of an idempotent daily plan. It does not enable a
scheduler, Governor, content approval or publishing. Existing manual and cloud
production pages remain available. Defaults use Ollama + LTX-2.5, Piper narration,
ASR captions, 720p export at 24fps, portrait output and zero provider budget.

## Operator flow

1. Keep existing model runtimes/checkpoints. The shipped recipe selects the already
   installed `piper-en-us-ljspeech-high` profile; on another host install it through
   the existing optional speech setup before selecting that profile. No eSpeak or
   cloud fallback is automatic.
2. Collect signals with Phase 2's explicit CLI, backing up before upgrading an
   existing database. No trend videos are downloaded.
3. Set `AUTOMATION_ENABLED=true` privately when ready and restart the existing app
   yourself. Nothing here starts/installs services or changes your `.env`.
4. Open **Automation**, preview topics, choose a date and confirm local GPU work.
   The date/lane/slot intent and its recipes/topics/backups are frozen atomically.
5. The existing durable SQLite queue processes the bounded day. With durable mode
   disabled, the confirmed action launches a bounded queue drainer; it exits when
   queued work ends. With `DURABLE_WORKERS=true`, your existing supervised worker
   consumes the same queue. A launch failure stays visible with queued intent.
6. Inspect previews and **Inspect production quality**. The existing human
   continuity/caption/rights confirmations are still required to reach pending_review.
   Approval and publishing remain separate manual actions; no uploads occur here.

```bash
# Preview only; works while global automation is disabled.
.venv/bin/python scripts/run_director.py --preview
# Explicit local batch; no scheduler/timer. Reuses the same saved plan on repetition.
AUTOMATION_ENABLED=true .venv/bin/python scripts/run_director.py \
  --run --wait --plan-date 2026-11-06
```

Manual `--run` is an immediate drill, not a claim that the nightly timer ran. The
nightly window supplies its capacity budget. Actual cron wiring is Phase 6 work.
`AUTOMATION_DATA_DIR` chooses ignored report/worker-log/backup paths. Use a separate
validation database plus private media/plan/reference roots for real GPU trials.
The existing application worker's per-database lock serializes consumers; its
existing cinematic process lock serializes neural stages across databases.

## Recipes and scripts

Category recipes define duration 30–60s, style, narrator/caption mode, title/hashtag
templates and local providers. The current automatic daily path deliberately
supports independent text-mode LTX; unsupported image/Wan/cast recipes fail before
reserving jobs, while those models remain available in Local Video Studio. Series
cast-conditioned production comes in later phases.

A category brief requests setup/escalation/punchline, mini-drama/reveal/hook, cartoon
beats, or a cautious unverified explainer. Source bundles are auto_compiled and
reviewed=false. Ollama creates original beats; deterministic timing assigns balanced
3–10s shots with exact coverage and compiles the existing ProductionPlan. Existing
validators enforce narration budgets, factual labels and motion-provider settings.
One received-invalid-output repair is allowed; transport/unload uncertainty holds
the day without retry. Received candidates and errors are saved privately in
`automation_short_receipts`, not returned by dashboard routes.

The existing ScriptGenerationService reserves/stores the draft, CloudVideoService
creates/consumes its zero-cost quote, then ContentWorkflow skips discovery/planning
and generates only the authorized footage. There is no new uploader or renderer.
Only one existing tracked video batch is allowed at once, so five slots are reserved
up front and their productions run serially. Job and generation IDs persist before
polling; a lost controller never grants another execution token.

## Capacity, failures and reports

`automation_gpu_measurements` labels historical rate seeds separately from real
per-job measurements. Completed LTX/Wan stage seconds and GPU queue wait come from
the existing worker usage records. Missing measurements remain missing; synthetic
pytest fixtures are not host benchmarks. Forecasts use measured weighted rates when
available, otherwise seeds, 25% headroom and 120s/job for script/TTS/assembly.

The configured `gpu.degradation_order` defaults to shorten Shorts to 30s, drop
wildcard slots, then drop lowest-priority remaining slots. Forecasts use aware UTC
arithmetic through DST. New submissions also stop if measured elapsed work consumes
the day's capacity. An already submitted neural task is not blindly restarted or
cancelled just to meet a forecast.

A known technical failure may use one frozen ranked backup topic. Two failed attempts
skip a slot; other slots continue. Any uncertain/active failed batch holds the day
for reconciliation and cannot trigger substitution. Completed originals and attempts
remain intact. Reopening/refreshing/re-running a plan never creates duplicate jobs.

`GET /api/v1/automation/plan/{date}`, `/gpu/forecast?day=...`, `/reports/{date}` and
`/lanes` are authenticated reads. `POST /plan/{date}/run` additionally needs CSRF,
confirmed=true and enabled automation; paid provider overrides are rejected.
Reports under `AUTOMATION_DATA_DIR/reports/` include status/reasons/IDs/measurements.
They report technical readiness, not human quality approval.

Real validation results and exact run IDs live in [PROGRESS.md](PROGRESS.md).
