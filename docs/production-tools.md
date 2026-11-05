# Production tools and release operations

The **Production tools** page is part of the existing React/FastAPI app. It uses the
same administrator login, CSRF protection, SQLite database, review queue and
publishing panel. It never approves or uploads content. Original assets, selected
versions, approval states and publication records remain unchanged by these tools.

## Available features

| Tool | Result | Important boundary |
|---|---|---|
| Neural narration | Downloadable Piper WAV; selectable narrator and per-cast profiles | CPU inference; no automatic fallback; not voice cloning |
| Script assessment | Word count, speaking duration, pace/repetition/claim warnings | Heuristics, not factual verification |
| Speech captions | Recognized-word timings or forced intended-transcript alignment, WER report | Forced mode supports clips/scenes up to30s; longer audio uses ASR timing |
| Licensed library | Pexels videos/photos with license, creator, source URL and checksum | Explicit intended-use rights confirmation; configure your own key |
| Research leads | GDELT source links and publication metadata | Leads remain unverified; articles are not scraped |
| Reviewed dossiers | Immutable claim/primary-source notes attached to script planning | Human review recorded; news still labelled unverified |
| Scene asset regeneration | New legacy illustration/narration and derivative video | New pending-review job; original stays intact |
| Standalone SDXL |1024²,768×1344 or1344×768 illustration/reference image | Explicit model selection, own pinned checkpoint, GPU lock; not a video |
| Readiness | Executables, storage/memory, voice/model presence and worker heartbeat | Does not verify account credits or spend money |

Media tools run asynchronously and persist status/results. Reopening the page polls
saved jobs. Downloaded media/voice models, private diagnostics and derivative files
remain under ignored data directories. There is no automatic deletion of originals.

## Neural speech

The isolated CPU environment is `data/speech_runtime/venv`. It contains the official
Piper1.8.0 ARM64 wheel and ONNX Runtime; the application and existing GPU environments
are not modified. The installed `piper-en-us-ljspeech-high` voice uses the LJ Speech
dataset identified as public domain in its model card. Piper's engine is GPL-3.0;
it runs as a separate installed process rather than being bundled into this repo.

```bash
.venv/bin/python scripts/setup_speech.py --download-voice
```

Choose the installed profile under **Neural narrator profile**, then choose local
narration. In Clip Studio use **Local narrator · optional neural voice**. The planner
supplies a bounded `narration_text`; synthesis rejects narration exceeding the clip
budget instead of silently dropping speech. Local Video Studio also supports a
neural narrator. Approved cast packages can select a neural profile per actor;
existing packages without that field retain their fixed eSpeak voices. Saving a new
cast package explicitly approves the new voice selection.

Voice files/configuration/model cards are hash-checked and registered in immutable
manifests. Setup retains an existing profile instead of replacing it. A missing or
changed neural model fails clearly; it never silently switches to eSpeak. Native
LTX audio remains separate and can still vary in identity/words. Neural narration
or fixed speech does not provide automatic lip sync/audio-to-video conditioning.

Sources: [Piper](https://github.com/OHF-Voice/piper1-gpl),
[LJ Speech model card](https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/ljspeech/high/MODEL_CARD).
Review the voice/engine licenses before redistribution or deployment outside your
private environment.

## Speech analysis and captions

The existing isolated Whisper analysis runtime runs on CPU with CUDA hidden.
Whisper base is installed; other selectable sizes must be downloaded explicitly:

```bash
.venv/bin/python scripts/setup_clip_studio.py --whisper-model base
```

In Local Video Studio's advanced settings, choose **Recognized speech word timing**
or **Forced transcript alignment**. Old estimated/scene-text caption defaults remain
available. Recognized captions use spoken words rather than pretending the planned
script was spoken. Forced mode uses Whisper's DTW alignment on the supplied words,
with separate ASR recognition/WER comparison. It requires clips/scenes up to30s;
choose ASR for longer scenes or split them before alignment.

For existing videos, select a source job in Production Tools and create a caption
export. The derivative receives a new `pending_review` job. It does not replace the
approved/published source. WER reports substitutions, insertions and omissions
against the supplied transcript; proper names/numbers can produce false alarms.
An empty reference cannot be marked a script match. Silence and low-quality speech
still need human inspection. ASR/DTW is not automatic acting, identity, factual or
lip-sync certification. No caption operation automatically approves content.

## Licensed stock footage and source research

Add this locally, then restart the server:

```dotenv
PEXELS_API_KEY=
```

Search Pexels, inspect its source page and [license](https://www.pexels.com/license/),
then confirm intended-use rights before downloading. The API requires a separate
rights flag as well as action confirmation. Transfers only use trusted Pexels CDN
hosts, never arbitrary URLs or redirects, and are bounded to256MiB. Source/license,
creator attribution, dimensions and SHA256 are saved with each result. Repeated
confirmed downloads of the same unchanged asset reuse a private checksum-verified
cache. Pexels is prominently credited in the UI. Upload the downloaded media into
Clip Studio to regenerate it with your local models. Stock-download tools do not
publish unmodified source files.

Research searches a fixed public GDELT endpoint and stores links, not copied full
articles. Read primary sources yourself. Enter a claim and HTTPS supporting URLs,
confirm personal review, and save a dossier. Select it in the screenplay's advanced
settings. The immutable source bundle is included in the exact script request and
paid-quote fingerprint. Source notes cannot override the script contract, and news
never becomes automatically “verified.” Tampered dossiers are rejected. Keep a
bundle below16KB to preserve the script input budget.

Sources: [Pexels API](https://www.pexels.com/api/documentation/),
[GDELT DOC API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/).
Account/rate-limit availability still depends on those services; no blind retry is
made. A successful live Pexels call is not claimed before your key is configured.

## Stronger standalone illustrations

SDXL is an additional optional image model; existing illustrated defaults and
LTX/Wan models are unchanged. The optional FP16 checkpoint is downloaded into
`data/still_image_runtime/model`, with pinned revision and per-file SHA256 manifest:

```bash
.venv/bin/python scripts/setup_still_images.py --download-model
```

This uses the existing verified isolated CUDA Python without installing packages
into it. Inference runs offline in its own process, shares the cinematic GPU lock,
uses the configured allocator fraction/host-memory reserve, and has a deadline.
Private worker diagnostics are retained for failures. Inputs are argument arrays and
JSON data; prompts never become shell commands. Nothing silently falls back to a
text card or another model.

Model: [stabilityai/stable-diffusion-xl-base-1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0),
CreativeML Open RAIL++-M license, pinned revision
`462165984030d82259a11f4367a4eed129e94a7b`.
Generated illustrations may be downloaded and manually uploaded as scene/cast
references. They do not automatically approve a character package. They are not
proof of real events, identities or rights to depict real people/brands.

## Durable single-host generation and supervision

The optional `app_work_queue` lives in `DATABASE_PATH`; it is not another database
or a distributed Redis system. API defaults retain the existing in-process behavior.
When `DURABLE_WORKERS=true`, generation jobs and script drafts are queued and
consumed by:

```bash
.venv/bin/python -m app.workers.application_worker
```

Never-claimed queued work survives API restart. Running work has a lease/heartbeat;
a stale lease becomes interrupted and is **not** automatically requeued. Inspect
provider generation IDs, billing and existing output before using the existing
manual reconciliation/continuation actions. Script attempts retain their atomic
queued-state claim, and accepted chapter continuation can dispatch the same draft
without losing the prior queue record. No publishing handler exists. Clip Studio
and local long-film coordinators retain their existing specialized persisted
workers and manual recovery actions.

Without a supervised consumer, Production Tools launches a bounded queue-draining
process. Worker-launch failures are recorded instead of leaving a hidden queued
request. Private logs are in that tool job's directory. Production readiness checks
require a recent worker heartbeat when durable mode is enabled.

Render service files for review first:

```bash
.venv/bin/python scripts/install_services.py
```

Install user service files explicitly when ready:

```bash
.venv/bin/python scripts/install_services.py --install
systemctl --user daemon-reload
systemctl --user enable --now ai-content-agent-worker ai-content-agent-api
```

The API/worker units explicitly enable durable dispatch and JSON logs, use private
file permissions and restart only their own service process on failure. Do not
start a second API on port8000; stop your existing manual server after active work
has finished before enabling these units. The installer does not start/stop services.
User services may need your administrator to enable user lingering for unattended
operation after logout. GPU/network/account availability remains a deployment check.

The scheduler unit is separate and optional. Review `.env` schedule settings, set
`SCHEDULER_ENABLED=true` only when wanted, then explicitly enable that unit. It only
requests generation; approval and publishing remain manual. Keep a single scheduler.

```bash
journalctl --user -u ai-content-agent-api -u ai-content-agent-worker -f
```

`LOG_FORMAT_JSON=true` enables structured logs with environment credential/bearer
and URL-query redaction. Exception type is logged without raw tracebacks in that
format. `/health` stays compatible; authenticated `/api/v1/tools/readiness` provides
detailed setup status without contacting paid providers or exposing credentials.

## Release boundaries

Implemented code is distinct from account and model-quality acceptance. Remaining
operator/research inputs are: current paid-provider/Meta/YouTube permissions and
billing checks; Pexels key/live access; your exported ComfyUI graphs if using that
optional backend; approved actor datasets for any future character-LoRA training;
licensed identity/semantic evaluators and neural A2V/lip-sync experiments; and human
inspection of a newly generated 15–30-minute story. Existing reference continuity,
manual per-shot checks and tested long-form assembly remain available. Longer neural
quality is not proved by a synthetic-media stress test, and no automatic semantic
or factual quality guarantee is introduced.

No deployment switch, live upload, paid generation or content approval was performed
as part of implementation. Neural speech/alignment was measured on CPU and a single
SDXL still was tested on the DGX. No new video-generation GPU stress run was needed.


## Verification and changed areas

The full regression run passed 621 Python tests in 343.49 seconds, excluding only the
previously verified 30-minute synthetic assembly/cache case. The final production-tool
suite passed 15 tests, including rights confirmation, changed-model detection,
long-film transcript input, ownership, checksum checks, worker-launch failure,
immutable narration/caption derivatives and pending-review separation. Streamlit
compatibility/new-selector checks passed 38 tests together with the tool suite. These
counts overlap and are not a combined unique total. All 66 frontend tests passed;
the Vite production build succeeded. Python compilation and git whitespace checks
passed. The changed-file credential scan found no credentials or generated binaries.

Actual checks: Piper generated 3.54 seconds of audio; CPU Whisper recognition/forced alignment
returned 12 aligned words and 0.1 WER for the supplied sample (names can differ). The
detached application worker generated a private 75,820-byte Piper WAV. A pinned SDXL
1024² cafe illustration passed on GB10 in 57.73 seconds and was visually inspected. User
service templates passed `systemd-analyze --user verify`; none were installed or
started. Existing moving-video/provider tests use mocks; paid accounts/uploads were
not exercised.

Added services: `work_queue`, `operations_service`, `production_tools`,
`research_service`, `research_dossiers`, `speech_profiles`, `speech_analysis`,
`stock_service`, `still_images`, `structured_logging`. Added authenticated
`tools_routes`; application/speech/speech-analysis/still-image workers; setup and
service-rendering scripts; three systemd user units; React ProductionTools and shared
speech/research pickers; a Streamlit link page; regression tests. Existing config,
TTS, cast, script-request, renderer, dispatch and dashboard files were updated only
for optional feature integration. Database additions are in the existing SQLite
file; model/profile manifests and private outputs remain in ignored data paths.

Clip Studio now explains invalid connected-action/cast combinations and disables
submission before any paid request; known validation errors no longer receive the
misleading generic lost-acknowledgment sentence. Network/uncertain outcomes retain
the history/idempotency guidance.
