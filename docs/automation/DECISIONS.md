# Automation decisions

## 2026-11-06 — Phase 1

1. **Scope authority and preservation.** Follow `AGENTS_AUTOMATION.md`; the user's
   reference to “AGENTS.md section 14” means section 14 of that automation spec.
   Root `AGENTS.md` is retained. Local context/backlog/progress files are read-only
   handoff history for this task; automation progress lives in this tracked folder.
   Existing APIs, modules, startup, UIs and service templates are unchanged.

2. **Episode duration.** The newest user message asks for 5–10 minutes, superseding
   the spec's 3–5-minute mission/example. Ship validated 300–600s configuration.
   Later scripts must still split into <=180s chapters through the existing long-plan
   mechanism; this phase does not create any screenplay or footage.

3. **Manual default; future explicit Governor amendment.** Existing review and
   publishing remain separate manual actions. The owner has approved a future
   `auto_with_gates` mode only through a separate Governor, never Director or workers.
   Phase 1 parses approval mode/dry-run settings and creates state tables but builds
   no gate/approval/upload path. README describes this as planned, not available.
   Future live automation requires all gates, scoped privileges, explicit opt-in,
   non-dry-run mode and kill/cap/reconciliation behavior from spec section 9.

4. **No hidden startup migration or scheduler.** New modules are not imported by
   existing entry points. Foundation inspection validates settings without creating
   tables. Future automation startup must load/validate config explicitly, then
   initialize storage only after the operator backs up the existing database.
   Development migrations run solely on private SQLite backup copies.

5. **One database, additive schema.** Follow the existing storage-class pattern
   rather than add an ORM, Alembic, database or infrastructure dependency. Use
   `CREATE ... IF NOT EXISTS` in an explicit transaction. Preserve legacy schema,
   rows, `user_version` and publication uniqueness. Add one supplemental
   `automation_idempotency` table for durable request claims; it is not a queue.
   Amounts use decimal text; spend reservation/consumption waits for Phase 6.

6. **Fail-closed idempotency.** A duplicate claim never gets another execution
   token, even after 35 simulated days. Unknown submissions are never auto-retried.
   Immutable fingerprints prevent edits under the same request key. Keep existing
   quotes/leases/submission markers; this layer cannot bypass their checks.

7. **No new dependencies or paid providers.** Reuse installed Pydantic/PyYAML and
   standard-library SQLite/time/hash/typing tools. Until standing authorization is
   implemented, automation config accepts only local providers. Parsing a positive
   budget or Governor flag does not authorize spending or publishing. Later paid
   support must extend validation together with the existing quote/consent flow.

8. **Clock and explicit registry.** Inject a Clock rather than patch global time;
   UTC instants and IANA local dates avoid host-time/DST drift. Use a trusted explicit
   lane-kind registry; configuration cannot import/execute arbitrary Python.

9. **Seeds versus measurements.** Preserve 29/20 seconds-per-video-second as
   historical README seeds only. 5–10-minute episodes need a new capacity forecast;
   no assumption is made that the older 3–5-minute nightly estimate still fits.
   No hardware inference, platform calls, services or schedulers run in Phase 1.

10. **Audit log.** Parameterized append-only events keep IDs, reasons and small
    structured details. No raw provider response, transcript, signed URL or credential
    belongs in this log. SQL update/delete triggers prevent accidental edits through
    this connection; this is application auditing, not tamper-proof storage against
    a privileged host administrator. Existing structured logging/redaction is reused
    by the offline command; no logging configuration is changed for existing apps.

## Deferred decisions

Trend sources and scoring, concrete recipe mapping into current `GenerationConfig`,
GPU forecasting/degradation, cast/scene-reference approval UX, thread/state judges,
standing authorization accounting, Governor gate evidence/visibility reporting and
live deployment are assigned to their specified later phases. They are not blockers
for the foundation and have not been implemented prematurely.


## 2026-11-06 — Phase 2

- Owner explicitly authorized commits after Phases 2/3/4 and progression through
  Phase 4, superseding the earlier wait-after-each-phase instruction for these
  milestones only. No push, publishing, service start or paid provider use.
- Explicit manual collection may run while AUTOMATION_ENABLED=false; no scheduled
  job is registered until Phase 6. Collection intervals are idempotency buckets.
- Preview is strictly local/read-only. Optional Ollama category fallback occurs
  during collection, under the cinematic lock, with keep_alive=0.
- Real YouTube collection uses a private fresh DB; future live migration requires
  backup first. Wikimedia signals are global English rather than falsely regional.
- Quota fallback is explicit when a real source sample lacks requested categories.
  No fabricated diversity or topics. Fixture provenance records remote GDELT
  recording failure and synthetic optional Reddit/contract bodies honestly.


## Phase 4 — local planning and manual cast gate (2026-11-07)

- Reuse the installed Ollama model and cinematic GPU lock, with bounded five-day
  chunks and 16K context; no paid model, new dependency or scheduler.
- Keep bible immutable/hash-bound; compile structural metadata but never fabricate
  creative hooks. Record validator feedback, retry only acknowledged invalid output
  at most twice, and hold uncertain completion for inspection.
- Use 300–600s episode targets per the newer owner request, superseding the original
  specification's 180–300s example. Phase4 plans, not renders, these episodes.
- Require manual cast approval even if legacy config permits series_start_mode=auto.
  Owner's explicit human gate takes precedence. Approval never authorizes publishing.
- Whole-outline optimistic edits permit coordinated hook changes while preserving
  episode uniqueness; any produced episode locks outline editing. Future episode
  memory/state and publishing order are Phase5 work.

- Real qwen3:8b tests exposed copy-forward hooks and invented reference IDs despite
  error feedback. Constrain known per-day IDs/ledger/finale in the output schema;
  retain deterministic whole-outline checks for duplicates and continuity. The
  installed Ollama rejected prefixItems tuples with HTTP400, so use five named
  day fields on the wire and decode to the existing ordered episode list. Record
  known rejections separately from transport uncertainty, and unload loaded weights.

- Runtime validation exposed weak recaps despite stored previous-hook IDs. Compile
  an explicit literal recap from the prior model-written hook and validate its
  presence. This guarantees acknowledgment without claiming semantic story QA.
- Separate structure/ledger and hook-specialist agents, at most3 received outputs
  each (max6 per chunk). Explicit known-failure revisions copy a verified bible
  and accepted prefix atomically; uncertain requests cannot use this path. Original
  rows remain intact; replay returns saved intent even if source state later changes.
- Story-only sampling .9/repeat_penalty1.2 and concise repairs avoid copy-forward
  loops; Shorts defaults remain unchanged. Retain every failed iteration and do
  not advertise this as a first-attempt successful local run.

## Phase 5 — episodes and lifecycle (2026-11-07)

- Preserve the Phase 4 regular 300–600s contract. The requested real 3–4-minute
  pilot uses an explicit private-only 180–240s override, marked validation-only.
- Script/story-state compilation reuses the existing long-plan service; all local
  planner calls use the same receipt/claim discipline. Acknowledged malformed
  output may receive bounded feedback; uncertain requests never gain a new claim.
- Automated episodes use LTX image conditioning. Existing manual Wan/hybrid
  workflows remain available; automated Wan needs explicit driving-footage support
  and therefore fails closed instead of silently replacing a model.
- Native LTX crops portrait inputs to landscape. Preserve original approved cast
  assets and prepare derived scene references with contain-on-blur framing. This
  fixes face removal without claiming new approval of fabricated identity assets
  or full-frame cinematic composition.
- The owner explicitly approved six generated portraits, three cast packages and
  Tidal Echoes as the private pilot cast. Finished-video continuity/captions/rights
  and content approval remain independent human actions. Never substitute technical
  quality results for those manual confirmations.
- Episode order trusts verified published records, never uploaded/private status.
  Skips require owner confirmation/reason and retain actual state; rollover creates
  a fresh planning intent and cannot approve the next cast.


## Phase 6 — explicit Governor amendment (2026-11-07)

- Implement the owner's section9 amendment: manual by default, separate Governor
  opt-in authority. Director/workers remain generation-only. Admin/CSRF protects
  all Governor mutations; generation API keys gain no privilege.
- Dry-run projects approved status read-only into the existing publishing validator.
  It never persists approval, continuity acceptance or a publication reservation.
  Live opt-in reuses shared technical-review validation and existing review/publish
  services, with a distinct Governor audit event; it never claims human inspection.
- Reserve job/platform/mode and daily capacity before any live side effect. A crash
  leaves a reconciliation hold and cannot replay. Existing manual retry authorization
  is never consumed by Governor. Closing an inspected intent retains its unique key.
- Keep fifteen-minute slot windows; late approval waits until the next corresponding
  local-day slot. Count manual publications in the cap and preserve private defaults.
  Public/private mismatch is a visibility blocker, not proof of an unaudited project.
- Register automation in the existing scheduler process only. Default flags remain
  false/manual/dry-run. No services, schedulers, paid calls or uploads were run.
- Standing consent reserves conservative quote estimates with Decimal caps and
  expiry; uncertainty keeps the full reservation. Daily recipes remain local-only;
  paid helpers cannot silently change a provider or bypass quote consumption.
- Reuse FeedbackService ranking with >=3 latest published samples per category and
  bounded weights, retaining hard quotas. No fabricated analytics or GPU measurements.


## Phase7 — hardening and truthful elapsed-time acceptance (2026-11-07)

- Keep Operations read-only even for an uninitialized legacy database. Label seeds,
  real measured stages and synthetic output separately; do not start/recover work
  from a health page. Governor skips completed actions/non-due slots before costly
  decode checks, retaining atomic publication guards and reconciliation holds.
- Render Director as an alias of the existing scheduler; add a status-only Governor
  oneshot. Four rendered templates verified offline; no installation/start and no
  second APScheduler process.
- Freeze missing episode slots in existing columns, with local-time/DST conversion,
  preserving existing values. Allow pre-start buffering within the configured lead;
  early generation never makes future publication eligible. This closes gaps found
  while hardening and adds no schema/endpoint contract change.
- Use the installed pytest fixture factories for a development-only foreground
  validation harness. Its marked private DBs, synthetic media/cast/planning and
  explicit mock35-day publication history cannot be confused with the real pilot.
  No new production/test dependency was installed.
- The owner selected recorded sources + mocked generation for24h. Count actual
  active monotonic and UTC elapsed time, not fake days or downtime. Preserve the
  interrupted five-minute preflight; restart final acceptance after scheduling
  fixes with source hashes. Never label smoke/preflight as24h completion.
- Keep Phase7 uncommitted until the final elapsed-time evidence is checked. The
  owner authorized milestone commits, not fabricated acceptance or stretch work.


## Owner update — commit implementation while soak continues (2026-11-07)

The owner explicitly superseded the previous wait-to-commit boundary: commit
through Phase7 now, then address any dry-run findings with separate bug-fix commits.
Commit the verified implementation with the elapsed-time matrix still pending.
Keep the existing foreground run and its source hashes unchanged; do not restart
it, enable live uploads, start services or begin stretch work.


## Unattended mode — owner request (2026-11-10)

- The owner asked for no dashboard intervention. Add an opt-in overlay file
  (config/unattended.yaml, read only when AUTOMATION_UNATTENDED_FILE names it) instead of weakening defaults.
  Env flags remain the outer grant; the file never enables the Governor itself.
- This supersedes the Phase 4 "cast approval stays human" rule only when the
  overlay sets series_start_mode=auto: local SDXL portraits of fictional bible
  characters are frozen through the existing cast package and approve_cast path,
  with an automation actor and human_inspected=false in the audit log.
- Recipes may name openai/anthropic. Spending still requires the existing quote,
  an owner standing authorization and a positive global daily budget; otherwise
  local Ollama (or skip). Series planning/episode scripts remain local.
- Episodes may also go to Facebook as Page videos through a separate publisher
  that only the Governor enables; the Reels path and manual default are unchanged.
- A scheduled read-only status check supplies verified "published" states; it
  never uploads. Uncertain uploads still pause the platform for the owner.
- A stuck head episode is skipped through the existing audited skip, never
  resubmitted, and never while YouTube is paused or an upload attempt exists.
- Wan/hybrid stays out of unattended episodes: measured rates cannot fit a
  daily 5–10 minute episode in the nightly window.
