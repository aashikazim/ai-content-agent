# Unattended mode

Opt-in mode with no review clicks, no cast upload and automatic publishing.
Edit one file, `config/unattended.yaml`; it overlays `config/lanes.yaml`.
The manual review, cast and publishing workflows are unchanged and still available.

## Turn it on

1. Set `enabled: true` in `config/unattended.yaml` and choose the values below.
2. `.env` must already contain `AUTOMATION_ENABLED=true`, `DURABLE_WORKERS=true`,
   `AUTOMATION_GOVERNOR_ENABLED=true`, `AUTOMATION_GOVERNOR_DRY_RUN=false` and
   `AUTOMATION_UNATTENDED_FILE=config/unattended.yaml`.
3. Renew platform access if needed (`python scripts/youtube_auth.py`,
   `python scripts/facebook_auth.py`) and reconcile any open publication.
4. Run `./scripts/go_live.sh` and leave it open; Ctrl+C stops the scheduler and
   worker and sets the kill switch. It checks the flags and YouTube credentials,
   offers to close upload attempts that failed before reaching the platform
   (credential refresh errors only; never a retry), then follows the logs.

| Setting | Effect |
|---|---|
| `scripts.provider` | `ollama`, `openai` or `claude` for Shorts scripts |
| `scripts.if_unavailable` | `ollama` falls back to local; `skip` drops the Short |
| `shorts.per_day` | 5–10; category mix and publish slots are scaled automatically |
| `shorts.platforms`, `series.platforms` | YouTube and/or Facebook (series need YouTube) |
| `series.auto_cast` | local SDXL portraits become a frozen cast package |
| `series.auto_skip_stuck_after_hours` | skip a failed/stuck head episode and continue |
| `publication_check_minutes` | read-only status checks (uploaded → published) |

## What it does

- Shorts: Director plans/renders nightly; Governor approves and uploads at slots.
- Series: with no usable series, one is planned; its cast is generated once from
  the frozen bible (one seeded portrait per character) and frozen as a normal cast
  package; every episode is image-conditioned on those same portraits with
  connected LTX-2.5. The series starts the next day and rolls over after day 30.
- Episodes upload to YouTube and, as a Page video (not a Reel), to Facebook.
- A scheduled status check marks uploads published so episode order can advance.

## Paid scripts

OpenAI/Claude are used only when `AUTOMATION_DAILY_BUDGET_USD` is above zero and
an active standing authorization lists the provider. Each script is quoted and
reserved against those caps (about $0.21 conservative bound per Short). Series
planning and episode scripts stay on local Ollama.

## Safeguards kept

One upload per job/platform, publish slots, daily caps, gates (technical, WER,
captions, duplicates, hold rules, episode order), platform pause after an
uncertain upload or three failures, kill switch file/DB flag, no automatic retry
of an uncertain upload or paid request.

## Still needs the owner

- Revoked/expired YouTube or Facebook credentials, and any `needs_reconciliation`
  upload: the platform stays paused until inspected and resumed.
- YouTube keeping "public" uploads private (unaudited API project).
- An uncertain paid script request (provider usage must be checked).

## Limits

- Wan/hybrid is not used unattended: at the measured 15–18 min per 5 s shot a
  daily 5–10 minute episode cannot fit the GPU window.
- Auto cast portraits are not human-inspected; identity is conditioned, not
  guaranteed. The Facebook long-video upload and Facebook status mapping are
  covered by mocked tests only, not yet by a live upload.
- If the night's work does not fit the GPU window the planner shortens or drops
  Shorts; an episode is never shortened.

## Stop / roll back

`touch data/automation/KILL` stops publishing decisions immediately. Setting
`enabled: false` and restarting the services restores the previous behavior;
no schema change was made.
