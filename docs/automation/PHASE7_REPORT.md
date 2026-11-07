# Phase 7 report — acceptance pending

Implementation and automated verification are complete. The owner explicitly
authorized committing Phase 7 while the actual 24-hour dry-run continues, with
separate bug-fix commits for anything it reveals. **Elapsed-time acceptance remains
pending**; this commit does not claim the soak is complete. All five Shorts and
the real episode entered their private Review queues after owner quality checks.
Phase 5 is11dead8; Phase 6 isd0ba630. No push, installation or live upload occurred.
No stretch work started.

## Built

Read-only Operations dashboard/API, queue/worker/recovery snapshots and honest
GPU seed/measurement labels; Governor filtering avoids repeat decode of completed
or not-yet-due work. Episode buffering can warm before a future start, and missing
publish slots freeze atomically with local-time/DST handling. Existing slots stay.

A development-only recorded/mock harness reuses the existing test factories and
real Director/queue/Governor/dry-run publishing services. It blocks network, GPU,
upload and subprocess paths. The bounded foreground runner records active and
UTC elapsed time, source hashes, heartbeats and resumable state. A smoke run cannot
be relabeled as24-hour acceptance. Interrupted time never counts.

Director is an alias of the existing scheduler unit; Governor check is a status-only
oneshot. Templates were rendered and verified, not installed or started. The
runbook and go-live checklist cover recovery, approvals, caps and reconciliation.

## Verification

| Check | Result |
|---|---|
| Full Python regression |791passed758.04s; includes final scheduling/hardening/35-day cases |
| Frontend |82passed2.26s; production build662ms |
| Copied migration |21automationtables;40legacytables/1771rows preserved;3initializations; integrityok; source read-only |
| Combined35-day simulation |29explicit mock publications/1skip, ordered lifecycle, finale, one rollover awaiting cast;0actual uploads/duplicates |
| Systemd templates | Four rendered; host systemd-analyze --user verify exit0/no output; none installed/started |
| Static/document checks | compileall/diff-check passed; no broken relative automation-doc links |
| Real24-hour dry-run | RUNNING; final start2026-11-07T15:46:33.603316+00:00; earliest2026-11-08T15:46:33.603316+00:00 |
| Five real pilot Shorts | Owner confirmed all five; private pending_review; no content approval/upload |

The earlier five-minute soak preflight was preserved after detecting scheduling
gaps and does not count. The final run uses corrected code and43source hashes.
Its synthetic artifacts are not real video/codec/GPU evidence. See VERIFICATION.md
for distinctions between mocks, the real episode and platform/deployment checks.

## Files added or changed in Phase 7

| File | Change |
|---|---|
| README.md | Current results, operations links, elapsed-time acceptance status |
| app/api/automation_routes.py | Admin-only read-only operations endpoint |
| app/automation/operations.py | Read-only queue/host/metrics/recovery snapshot |
| app/automation/governor.py | Skip already-processed and non-due work before decode |
| app/automation/series/episodes.py | Pre-start buffer lead, frozen local-time episode slots |
| frontend/src/Automation.jsx | Operations navigation within existing Automation page |
| frontend/src/AutomationOperations.jsx | Read-only operations view using existing styles |
| frontend/src/AutomationOperations.test.jsx | Read-only/seed/uninitialized-state tests |
| deploy/systemd/ai-content-agent-scheduler.service | Director alias, same scheduler |
| deploy/systemd/ai-content-agent-governor-check.service | Status-only oneshot template |
| scripts/automation_soak.py | Bounded real-time runner, source hashes, safe resume |
| tests/automation_soak_harness.py | Recorded/mock orchestration fixtures; no live providers |
| tests/test_automation_episodes.py | Pre-start lead, future slots, DST/frozen slots |
| tests/test_automation_governor.py | Reusable fixture support and duplicate-decode regression |
| tests/test_automation_operations.py | Read-only DB/API, stale work, seeds, path refusal |
| tests/test_automation_soak.py | Combined35-day lifecycle, smoke/resume/time labeling |
| docs/automation/ARCHITECTURE.md | Current architecture and historical boundaries |
| docs/automation/DECISIONS.md | Hardening, scheduling and truthful soak decisions |
| docs/automation/EPISODES.md | Prewarming and immutable slot behavior |
| docs/automation/PROGRESS.md | Exact tests, running IDs, recovery/next steps |
| docs/automation/RUNBOOK.md | Operational recovery and validation commands |
| docs/automation/GO_LIVE.md | Owner configuration and opt-in verification checklist |
| docs/automation/VERIFICATION.md | Definition-of-Done matrix with unresolved rows |
| docs/automation/PHASE7_REPORT.md | This report |

Generated media/databases/logs/fixtures stay under ignored private validation paths.
Local handoff files and credentials were not overwritten. No new dependencies.

## Earlier pilot Shorts — owner quality confirmed

These are real private pilot outputs, separate from synthetic soak artifacts.
The owner confirmed continuity/voice, captions and rights; all five entered the
private Review queue. This is not content approval or publishing.

| Short | Job | Video | Status |
|---|---|---|---|
| 1 | b1111575-4b10-436e-ac2b-1c28463b8554 | [Open video](/home/aashik/Github/ai-content-agent/data/automation/validation/shorts-20261106/videos/df26c15f-9f60-4079-8ef7-2f0677db9820/assembly_a6a3c8c0-ce02-4d48-917d-fa57a5f94724/final_video.mp4) | pending_review |
| 2 | a057f744-7299-4cd7-a306-4fdb149a4eeb | [Open video](/home/aashik/Github/ai-content-agent/data/automation/validation/shorts-20261106/videos/1740ce90-e289-4e6e-b00f-130f048a15e1/assembly_15bfb041-1516-4367-a571-80006e457480/final_video.mp4) | pending_review |
| 3 | 0add5110-17ea-4598-b358-df2722ff35d5 | [Open video](/home/aashik/Github/ai-content-agent/data/automation/validation/shorts-20261106/videos/925af1f3-fcc5-41be-9086-b1b31b5f63fa/assembly_9a17564f-57bf-48e9-9a21-842c344e7b77/final_video.mp4) | pending_review |
| 4 | 8e360dae-d667-4236-8a12-d02de45fbf53 | [Open video](/home/aashik/Github/ai-content-agent/data/automation/validation/shorts-20261106/videos/bb28efb1-7eee-4c3d-a9a2-68663971d0b4/assembly_26f27260-248e-4962-a316-c12e03a835a8/final_video.mp4) | pending_review |
| 5 | 67cef8c7-63da-4390-911a-c7e4ba25729e | [Open video](/home/aashik/Github/ai-content-agent/data/automation/validation/shorts-20261106/videos/f08d23e7-eb64-4588-adf2-d468c4df7410/assembly_f0e76a44-b310-42dd-adaa-50d33a163dde/final_video.mp4) | pending_review |

## Try and resume

Open Automation → Operations; existing generation/review/publishing remain intact.
Read RUNBOOK.md before deployment. Inspect the existing final24h root's
progress.json before doing anything; never launch another process while its
foreground lock is held. If genuinely interrupted, resume the identical command:

```bash
.venv/bin/python scripts/automation_soak.py \
  --root data/automation/validation/phase7-real24h-final-20261107
```

After actual completion, verify >=86400active and UTC elapsed seconds, mode and
source hashes, zero forbidden calls/duplicates/publication rows and no interrupted
queue work. The manual Shorts quality check has been resolved without content approval.
Update the matrix/report after elapsed acceptance. Commit any required fixes separately
on main, as the owner requested, then stop.
Suggested commit: `feat(automation): harden operations and verify dry-run lifecycle`.
No stretch implementation or live upload follows automatically.
