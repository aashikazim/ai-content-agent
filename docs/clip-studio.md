# Clip Studio

Clip Studio is a page inside the existing React dashboard and FastAPI app. It uses
`DATABASE_PATH`, the existing administrator session/CSRF checks, private asset
serving, and the existing manual Review and Publishing pages. No separate app or
database is required. Existing Streamlit/local/cloud creation flows are preserved.

## Start with the installed native runtime

1. Set `CLIP_STUDIO_MOCK=false` locally and restart the server. Keep your existing
   `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` private in `.env`.
2. Choose **Clip Studio** in the sidebar. Supply instructions and a YouTube video,
   uploaded video, or up to eight images. Confirm source rights.
3. Select Claude or OpenAI and one of the app's supported planning models. Model
   selection is independent of the video model. Account access is checked by the
   actual provider response; there is no silent provider/model fallback.
4. Select **Existing native LTX / Wan runtime**. This reuses the verified isolated
   runtime, checkpoints, shared GPU lock, memory guards and stage timeouts already
   used by Local Video Studio. No model is replaced or downloaded during rendering.
5. For recurring actors, create/approve a cast package in **Local Video Studio**,
   then select it here. Use native hybrid and connected continuity for connected
   action; see the character guide below.
6. Choose audio and confirm the displayed paid planning estimate. Click Generate.
   Source ingestion, analysis, planning, rendering and assembly run asynchronously.
   Refreshing/reopening polls the persisted project; it never repeats submission.
7. Inspect the outputs. Validated selected initial real exports automatically
   become `pending_review` jobs in the existing **Review** queue. Approve/reject
   there. Then open **Publishing**, select YouTube/Facebook and explicitly upload.
   **Approval never publishes.** Existing uncertain-upload and duplicate guards
   remain in force. Facebook's existing Reels integration rejects videos over60s;
   use YouTube for longer clips.
8. Regenerate individual clips without changing other selected versions. Explicitly
   select a replacement, then use **Send selected version to review**. Each attempt
   is imported at most once; old review/publication records remain immutable.

Mock mode is still available with `CLIP_STUDIO_MOCK=true`. It makes moving CPU
FFmpeg test patterns and a tone, without downloads, hosted planning, or GPU calls.
Mock clips are rejected by the Review import endpoint and cannot be published.

## Settings and limits

```dotenv
CLIP_STUDIO_MOCK=false
CLIP_STUDIO_DATA_DIR=data/clip_studio
CLIP_STUDIO_UPLOAD_MB=256
CLIP_STUDIO_ANALYSIS_PYTHON=data/clip_studio_runtime/venv/bin/python
CLIP_STUDIO_WHISPER_DIR=data/clip_studio_runtime/whisper
CLIP_STUDIO_WORKFLOWS_DIR=workflows/clip_studio
COMFYUI_BASE_URL=http://127.0.0.1:8188
```

Paths may be absolute. Keep private runtime/output paths ignored by Git. Keep
`CLIP_STUDIO_DATA_DIR` stable while projects exist. Source limits: one video up to
one hour, up to8 images,256MiB combined upload/download by default (maximum1024MiB),
128–8192px dimensions, and32 megapixels per uploaded image. Video uploads must be
MP4/MOV/WebM/MKV and pass FFprobe; images are decoded and normalized to PNG.
YouTube URLs must identify a single HTTPS video/Shorts on an allowed YouTube host;
playlists, generic URLs, embedded credentials, redirects supplied by users and
other hosts are rejected. Rights confirmation is required before work starts.

Projects contain1–12 ordered output clips,2–300 seconds each,16:9/9:16/1:1, optional
seed. Long outputs use balanced3–10s native shots (a requested2s clip is generated
as3s then trimmed). The final codec is H.264/AAC at24fps and896×512,512×896 or512².
Wan internally uses the existing384px short-edge profile. There is no new collection
timeline editor; the existing Local Video Studio remains the dedicated15–30-minute
screenplay/long-film interface. Clip Studio retains ordered clip items and can make
long connected collections with the same cast, with separate per-clip reviews.

## Isolated CPU analysis setup

FFmpeg/FFprobe and eSpeak NG are system binaries already used by the project. To
install the additional source-analysis packages without changing the app or GPU
environment:

```bash
.venv/bin/python scripts/setup_clip_studio.py --whisper-model base
```

The script creates `data/clip_studio_runtime/venv`, references the existing GPU
venv's packages read-only to reuse PyTorch, and installs yt-dlp, PySceneDetect,
OpenCV and openai-whisper into the **new** environment. It never installs into the
existing environments. Specify `--gpu-python` if the existing local runtime is in a
custom location. Supply `--whisper-model tiny|base|small|medium` to pre-download
another checkpoint. No Whisper download occurs during a job. Choose its matching
model in Clip Studio. Analysis hides CUDA devices, uses CPU/fp32 speech recognition
and limits Torch/OpenMP/Numba CPU threads. Silent sources skip Whisper.

YouTube download uses yt-dlp's YouTube extractor with bounded size, duration and
socket timeouts, no playlists, cookies, config files, or automatic download retries.
Availability still depends on YouTube access/extractor compatibility. Analysis uses
PySceneDetect ContentDetector, up to600 scenes, three thumbnail frames per scene,
and timestamped Whisper segments. Source hashes and cached analysis are retained.
Partial download sidecars and transient concat/normalization files are cleaned up.
Private sources, reference/carry frames, completed chunks and selected exports are
retained for audit and resumption; there is no automatic retention/deletion policy.

## Character and voice continuity

The existing immutable cast-package service owns approved character IDs, face
images/multiple views, descriptions, wardrobe, location notes, fixed eSpeak voices,
and optional approved LTX adapters. Assets are hash-checked on every run. Clip
Studio never trains an adapter or silently changes actors/models.

- **LTX:** source frames supply image-to-video scene context. A single approved
  actor's primary reference anchors a cut. Connected action conditions on the
  previous selected output using the same17-frame carry protocol as Local Video
  Studio. Multi-actor identity is weaker without approved scene images/adapters
  and still requires human inspection.
- **Wan:** one approved actor plus a source video for driving motion. Image-only
  Wan jobs are rejected because there is no driving motion. Anchored continuity
  only; choose source audio or fixed character voices because native Wan is silent.
- **Native hybrid:** Wan on one-actor cuts, then LTX for continuous action. Multi-
  actor/establishing shots use LTX. This reuses the committed route policy and avoids
  resetting the pose with a second Wan pass. Cached chunk requests freeze routes,
  seeds, reference hashes, carry frames, external IDs and approved cast digest.
- **Cuts versus continuations:** the planner preserves canonical cast IDs and
  marks true continuous action. A cast change requires a cut. Continuing clips wait
  for the preceding selected completed clip and pin its ID/hash. Selecting a new
  upstream version marks dependents stale in Clip Studio; regenerate and review
  them deliberately. No downstream clip is automatically rerendered.
- **Audio:** model audio preserves LTX/hybrid sound but does not guarantee speech,
  matching voices, or lip sync. Source audio is extracted from the source range,
  padded with silence if shorter, and trimmed to the export. Fixed character speech
  reuses eSpeak voice IDs per cast member, preserving dialogue order. Overlong
  dialogue is rejected rather than silently truncating speech. Fixed speech is not
  automatic lip sync; review every export. Missing model audio becomes silence.

Example: upload a cafe scene, select an approved Maya/blue-jacket cast package,
native hybrid, connected continuity,3 clips×10s, and fixed cast speech. Instruction:
“Keep Maya's face, blue jacket and calm voice throughout. She enters the cafe,
continues walking to the counter, then orders tea. Use short dialogue and moving
camera/actors; mark cuts only when action or location changes.”

## Claude/OpenAI planning and costs

Hosted planning sends the instruction, selected settings, timestamped transcript,
scene ranges and canonical cast descriptions. It does **not** upload source videos
or cast images to the hosted planner. The planner returns a strict versioned JSON
schema. Local validation enforces count, duration, source bounds, actor IDs,
dialogue speakers, selected model and valid continuation transitions. Native
LTX/hybrid plan image-to-video conditioning, even for video sources; arbitrary
video-to-video editing is not claimed for those models. Wan uses driving video.

The UI currently estimates up to**$0.40 per planning operation** from the repository's
supported-model input/output pricing assumption ($2/$10 per million tokens), a
60KB request bound,8000 output-token cap and at most2 calls. This is an estimate,
not a provider/account billing cap. Check your selected model's actual account
pricing. There is no credit/balance endpoint used here. An additional call is
allowed **only** to repair received invalid JSON. Refusal, incomplete output,
timeout,5xx or uncertain acknowledgment never triggers an automatic retry. A
manual planning retry requires **new paid consent** and authorizes at most2 new
calls. No hosted or GPU fallback is chosen automatically.

Calls are durably marked before transmission; received response IDs/usage are
saved. Unexpected/uncertain outcomes are shown with saved IDs. Real worker processes
receive only the hosted keys they need; the separate GPU and analysis workers never
receive those keys. Exception responses never expose arbitrary provider output.

Official contracts checked during implementation:
[OpenAI structured output](https://developers.openai.com/api/docs/guides/structured-outputs),
[Claude structured output](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
[ComfyUI routes](https://docs.comfy.org/development/comfyui-server/comms_routes),
[yt-dlp](https://github.com/yt-dlp/yt-dlp),
[PySceneDetect](https://www.scenedetect.com/docs/latest/api.html),
[Whisper](https://github.com/openai/whisper).

## Optional ComfyUI workflows

ComfyUI is an explicit alternative backend. It is **not** required when using the
installed native runtime. Templates in `workflows/clip_studio/ltx.json` and `wan.json`
are deliberate fail-closed placeholders; they cannot submit generation until you
supply your real exported workflows. No ComfyUI server/custom nodes are installed
or replaced by this feature. Native connected hybrid remains available; ComfyUI
accepts explicit LTX or Wan workflows only.

1. Enable ComfyUI development/API export and export your tested workflow in **API
   format**, not the frontend graph format.
2. Put its node dictionary inside the template's `workflow` field.
3. Map every `bindings` entry to `["actual_node_id", "actual_input_key"]`; one value
   may target multiple distinct node inputs via a list of pairs. Map prompt,
   negative_prompt, reference, duration, width, height and seed. Bindings cannot
   overwrite the same input with different semantic values.
4. Add actual actor-image conditioning to `character_reference`, actual starting-
   frame conditioning to `continuation_reference`, and a video loader input to
   `source_video` when using those features. Missing requested bindings fail closed.
   Set `supports_multiple_characters=true` only for a workflow actually supporting
   that conditioning; otherwise multi-actor clips are rejected.
5. Duration is in **seconds**; perform model frame-grid conversion inside the
   workflow, including first/last-frame rules. Set `max_duration_seconds` between3
   and10 for chunking. Output exactly the requested chunk duration at24fps.
6. Set `output_node_id` to the save node yielding exactly one MP4/WebM/MOV in its
   `videos`, `gifs` or `images` output list. Map extension/codec settings accordingly.
   Remove optional bindings for conditioning your workflow does not support; all
   retained bindings must resolve to actual node inputs. Templates carry detailed mapping comments as `_instructions` JSON fields.
7. Set `configured=true`, configure trusted `COMFYUI_BASE_URL`, select ComfyUI in
   Clip Studio. A workflow export is server configuration, not a client-supplied
   graph. Only a trusted local/private ComfyUI server should be used.

The adapter uploads private assets, submits `/prompt` once, saves `prompt_id`, polls
`/history/{id}`, downloads `/view`, validates video motion/decode, and assembles
chunks with FFmpeg. Completed chunks are reused. Both adapters use the existing
DGX GPU lock; ComfyUI model memory is explicitly freed after successful completion.
Other ComfyUI clients do not honor this application's lock. If a ComfyUI task becomes
uncertain, inspect/stop it before abandoning it or starting other GPU work; an
external server can continue running after its HTTP client disconnects. ComfyUI's
custom-node memory settings are controlled by that server, not the native worker's
memory guards.

## Persistence, retries and extension points

All tables are additive in the existing SQLite database:

- `clip_studio_projects`: owner, idempotent request and immutable settings.
- `clip_studio_sources`: ordered private inputs, metadata and SHA256.
- `clip_studio_analyses`: scene/transcript cache.
- `clip_studio_plans`: provider/model, version, strict JSON, response ID/usage.
- `clip_studio_clips`: ordered item, source ID/range, track ID, timeline start,
  plan/prompt/model, selected immutable render version. Future editing can build on
  these items without adding another database.
- `clip_studio_render_attempts`: version, seed, status, quality, files and errors.
- `clip_studio_chunks`: immutable chunk request/dependencies, state, external ID,
  cached media and hash. Native IDs identify existing local task directories;
  Comfy IDs identify server history records.
- `clip_studio_calls`: pre-transmission markers, planning response IDs and usage.
- `clip_studio_actions`: idempotent manual resume/reconciliation acknowledgments.
- `clip_studio_jobs` / `clip_studio_events`: async queue, lease/heartbeat, stage
  progress and sanitized errors.
- `clip_studio_review_links`: unique attempt→existing job link; no new publishing
  subsystem. Imported clip plans preserve actual2–300s duration without changing
  existing30–60s/long-drama validation.

A bounded queue serializes Clip Studio projects; existing native workers keep their
shared GPU coordination. Individual clip failures preserve other successful outputs.
Normal explicit retries create new versions for failed items; originals remain.
For interrupted generations with saved IDs, **Resume saved generation IDs** reuses
the same attempts and completed chunks and polls existing work before continuing.
If a server restart interrupted worker launch before a claim, **Wake queued worker**
restarts the queue consumer for already-authorized queued work. Atomic claims prevent
duplicate submission; this action cannot restart running/uncertain requests.
Missing acknowledgments never trigger automatic submission. **Mark outcomes
reconciled** requires the operator to check runtime/provider usage and stop abandoned
work; it does not generate, discard IDs, or retry. A later retry is a separate human
action. Stale heartbeat recovery only marks interruption, never resubmits.

## Files and verification

Added feature files:

- `app/api/clip_studio_routes.py`
- `app/services/clip_studio/{__init__,schemas,storage,service,planner,generators,media,native,comfy,pipeline,review}.py`
- `app/workers/clip_studio_worker.py`, `app/workers/clip_studio_analyze.py`
- `scripts/setup_clip_studio.py`
- `workflows/clip_studio/{ltx,wan}.json`
- `frontend/src/ClipStudio.{jsx,css}`, `frontend/src/ClipStudio.test.jsx`
- `tests/test_clip_studio.py`, `tests/test_clip_studio_real.py`
- `docs/clip-studio.md`

Existing files changed: `server.py` router registration; `frontend/src/App.jsx`
sidebar/route/form-data integration; `app/models/long_content_plan.py` marked
import-plan dispatch; `.env.example`, `.gitignore`, `README.md`, `CODEX_PROGRESS.md`.
Existing generation services/components/styles and publishing logic were reused
without edits. Generated data, dependencies and media remain ignored. No commit or
push was made.

Run tests without paid/GPU generation:

```bash
.venv/bin/python -m pytest tests/test_clip_studio.py tests/test_clip_studio_real.py -q
cd frontend
npm test
npm run build
```

Tests cover strict JSON/repair/uncertainty, auth/CSRF/rights/limits/idempotency,
real orchestration with mocked hosted/GPU calls, ComfyUI injection/poll/download,
chunk caching and Wan→LTX continuation, unchanged selected versions, review import,
manual approval/dry-run publishing and mock-publication rejection. CPU FFmpeg
fixtures verify actual output, and an isolated CPU smoke test verified scenes,
Whisper transcription and thumbnails. Real hosted planning, ComfyUI custom workflows
and new real neural renders are intentionally not exercised by automated tests.

Verified on2026-11-04: broad Python run **579 passed,1 deselected** in316s;
the deselected existing30-minute synthetic assembly/cache case passed in the
separate205-test continuity/regression run. After the final feature adjustments,
**30 Clip Studio tests passed** in27s. **60 frontend tests passed** and the production
build passed. Counts overlap; they are not a combined unique-test total. Syntax
compilation and `git diff --check` also passed. No paid API call or GPU render was
used for these checks. The operator still needs real ComfyUI exports for that
optional backend and must manually inspect real model outputs for identity/voice
quality.


## YouTube ingestion fix (2026-11-04)

The original combined-stream-only selector failed for YouTube sources whose audio
and video are separate. Setup now installs `yt-dlp[default]`, including its matching
EJS package, and the worker explicitly enables installed Node/Deno runtimes. Node
must be22+ and available on the backend process PATH (not just a frontend terminal);
see [yt-dlp's official EJS setup](https://github.com/yt-dlp/yt-dlp/wiki/EJS).

The downloader prefers known-size streams within a reserved video/audio budget,
merges them with FFmpeg, and uses the final merged filename. Unknown-size HLS no
longer outranks smaller known-size streams; unknown-size fallback is capped at480p.
A combined byte-progress cap remains enforced and failed component files are cleaned
up. A fixed enum diagnostic is persisted in `analysis-error.json`; provider responses,
signed URLs, cookies and raw exceptions are never copied into public errors. Saved
failures now appear prominently in the page and in backend logs when polled.

Verified the reported project's1347-second YouTube source downloads as a156.8MiB
merged file, within the default256MiB cap. This cached source stays private in the
project's existing analysis directory. No job was automatically retried, no hosted
planning was called, and no GPU render was started. Focused39-test regression run
passed; the final8 downloader-specific tests passed after known-size prioritization.
The8 Clip Studio frontend tests and production build also passed.

Restart the backend to load the worker/API changes, refresh/reopen the project, then
explicitly retry failed work. A retry that can reach paid planning still requires
fresh displayed cost confirmation. Uploaded videos remain an alternative for
sources YouTube restricts or requires authentication to access.


## Planner contract recovery (2026-11-05)

The earlier tests used idealized empty-cast responses and missed incompatibilities
between the provider schema and local validation. The previous generic “invalid
JSON twice” message also covered semantic validation errors. The two reported Claude
calls completed and recorded usage; the old implementation discarded both bodies
and validation reasons, so their exact rejected fields cannot be recovered.

The provider schema now binds the selected video model, duration and reference mode,
requires numeric timestamps for video sources and null timestamps for images, and
excludes actor/voice registry fields when no approved cast exists. Anchored plans
request cuts. Source-audio plans do not request new dialogue. Source actors can
still be described in visual prompts without inventing registry IDs. Canonical
cast IDs are constrained when a package is selected.

Provider-only normalization accepts equivalent enum capitalization and whole-number
JSON spelling (e.g. `LTX`→`ltx`, `15.0`→`15`). It does not replace a selected model,
round fractional durations, accept numeric strings, or invent/rename characters.
Claude documents enum casing exceptions in its
[structured-output contract](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

Completed provider bodies and validation diagnostics are retained privately in the
additive `clip_studio_plan_responses` table, linked by call ID to existing billing
attempt records. They are not returned by the public project/asset endpoints. The
one permitted repair now receives field-specific errors and a bounded rejected
candidate. Unknown response envelopes/timeouts never trigger a repair. A second
rejection reports the failed fields rather than falsely claiming all failures are
JSON syntax errors. No extra paid request was made during this fix.

Transcript input is compacted into timestamped arrays. Oversized input is sampled
evenly over the complete source timeline and explicitly marked as sampled; cached
full analysis is retained. This removes the previous22-minute-source budget failure
without adding summary API calls or silently dropping the end of the source.

The regression suite now covers the reported Claude configuration: native LTX, no
cast,5 clips×15s and original source audio, through actual CPU source/audio/chunk
assembly and existing pending_review import with mocked hosted/neural APIs. This
exposed and fixed odd-width H.264 reference-driver scaling via FFmpeg's
`force_divisible_by=2`. Both saved project contracts were also checked offline against
their actual request/analysis data, without changing the failed jobs or calling an
API. Their planning inputs now fit the authorized input bound.

Final verification of this repair: **54 focused tests passed** in52s. No hosted
request, real neural generation, content approval or upload was made. The failed
production jobs were not reset or retried automatically. Restart the backend to
load the changes before choosing whether to explicitly authorize another attempt.
