# Real local series validation — 2026-11-07

Title: **Tidal Echoes**. Final series `8019aa1a-4ad6-5628-90c1-9866a7a691ad`.

Model: installed local **qwen3:8b**, 16K context. No paid calls, video renders, cast approval, service/scheduler starts or publishing.

Final explicit revision wall time: **137.28s** (includes local requests/unload/lock time, not CUDA kernel profiling). It reused 15 accepted days and the frozen bible from a known-failed source, then generated 15 remaining days. All 30 creative outlines/hooks were written by the real local model; structural metadata was compiled deterministically.

Final status **awaiting_cast**; zero automatic approvals. 30 episodes, **450s planned runtime each**; these are plans, not rendered videos. Full validators returned **[]**. Replaying the same request/process made no table/ID changes, and the frozen bible stayed identical. The inherited prefix was verified unchanged before editorial edits. Two hooks were subsequently revised by the local model; recaps were normalized to quote prior hooks through the validated edit service.

The final revision accepted all three structural chunks first attempt; one targeted hook-repair call was needed for days21–25. A separate 8.81s local editorial request revised hooks 15/20; deterministic recaps now explicitly acknowledge each preceding ending. Earlier development iterations failed and are retained below. This is not a claim of first-attempt success.

## The 30 endings

| Day | Phase | Ending hook / finale |
|---:|---|---|
| 1 | setup | The sealed journal hints at a family secret tied to the mayor's disappearance |
| 2 | setup | The map reveals the mayor's last known location and a hidden chamber beneath the village |
| 3 | setup | The warning about the spring tide threatens to flood the village and reveal the sabotage |
| 4 | setup | Lucien's hidden family connection and the mayor's fate are intertwined in a dangerous revelation |
| 5 | setup | The mayor's final location and the full scope of the sabotage are revealed, linking to Lucien's family secret |
| 6 | setup | The ledger suggests the mayor's disappearance is linked to a hidden family connection. |
| 7 | setup | The coded message suggests the mayor's fate is tied to Lucien's own past. |
| 8 | setup | The sealed journal contains a warning about the spring tide and the mayor's final days. |
| 9 | escalation | The Tidal Veil: A mysterious signal disrupts the lighthouse light, hinting at a hidden threat beyond the village walls. |
| 10 | escalation | The Last Light: A collapsing seawall section traps Miri in the lighthouse, leaving Lucien with a choice between saving her or stopping the flood. |
| 11 | escalation | The sealed journal hints at a family secret tied to the mayor's disappearance and the sabotage of the seawall. |
| 12 | escalation | The mayor's final message suggests his disappearance is linked to a hidden family connection and the sabotage of the seawall. |
| 13 | escalation | The mayor's final message hints at a hidden family heirloom and the sabotage of the seawall. |
| 14 | escalation | The mayor's final message hints at a buried artifact and the sabotage of the seawall. |
| 15 | escalation | Lucien finds a sealed envelope in his father's belongings, revealing a hidden meeting place beneath the lighthouse and a cryptic warning about an impending storm. |
| 16 | escalation | A fractured memory shows Lucien as a boy watching his father disappear into the sea, and Miri standing silently by, her expression unreadable. |
| 17 | escalation | A strange symbol etched into the tunnel wall matches one in her family's old records, suggesting an ancient pact between the village and its rulers. |
| 18 | escalation | A warning note slips from her coat pocket—written in the mayor’s hand—and hints at an urgent choice: seal the pact or lose everything. |
| 19 | escalation | The final seal requires both of them—Lucien’s blood and Miri’s memory—to complete it before the spring tide reaches its peak. |
| 20 | escalation | Miri discovers the final clue to resealing the ancient pact is tied to her own lineage and requires an act of self-sacrifice to restore balance. |
| 21 | escalation | The code’s final phrase activates a dormant mechanism, releasing a trapped entity that now seeks Lucien's blood for its release. |
| 22 | escalation | Miri’s ledger shows the mayor planned to sacrifice himself—but only if his son survived, leaving Lucien with an impossible choice. |
| 23 | climax | The map leads to a sealed vault beneath the lighthouse where Miri’s father was last seen, and her own fate is tied to its contents. |
| 24 | climax | As they activate the seal, the water rises faster than expected—revealing an unseen fissure that could collapse the entire tunnel system. |
| 25 | climax | The ritual succeeds but leaves Lucien with a cursed mark and Miri’s memory fragmented, hinting at another pact buried in the village's past. |
| 26 | climax | A hidden chamber beneath the tidal tunnel has begun flooding, revealing a relic tied to both families and the mayor’s disappearance. |
| 27 | climax | The relic begins to glow with a warning: it will not remain dormant unless both their bloodlines are fully sealed together. |
| 28 | climax | As they complete the ritual, the relic begins to dissolve into dust, leaving behind a single line of text: 'The tide has come. The cycle is done.' |
| 29 | climax | As the final memory is retrieved, an ancient mechanism beneath the tidal tunnel activates, preparing to seal away all secrets until the next spring tide arrives. |
| 30 | resolution | As the spring tide reaches its peak without incident, Lucien looks at Miri with newfound understanding. The village is secure, their families' secrets buried once more. |

## Retained request outcomes

| Series | Result | Last stage |
|---|---|---|
| `7fc930dd-992a-5d8e-9f69-259d073b620c` | failed | owner_revision |
| `aebac48c-1722-5e6e-b01b-1764af08c2bd` | failed | owner_revision |
| `8c7e1ef7-5583-5886-addd-25e08091f333` | failed | schema_rejected |
| `a5000b86-0284-5d3d-8733-90cecc16cd99` | failed | owner_revision |
| `127ddba8-99b6-5504-919e-2539c3d00870` | failed | owner_revision |
| `0f52652b-c277-5bf8-9cfb-5aa7862ae99e` | failed | owner_revision |
| `8019aa1a-4ad6-5628-90c1-9866a7a691ad` | completed | awaiting_cast |

## Every recorded received-output failure

Known HTTP400 tuple-schema rejection was explicitly reconciled from local Ollama logs (01:12:40 EST); schema-reconciliation.json retains evidence. It was never blindly resubmitted. The final handler classifies acknowledged400/422 rejection separately and unloads weights. Transport/unload uncertainty still stops immediately.

- `7fc930dd-992a-5d8e-9f69-259d073b620c` / bible / attempt1 (invalid): : Value error, Character removal precedes introduction
- `7fc930dd-992a-5d8e-9f69-259d073b620c` / bible / attempt2 (invalid): : Value error, Character removal precedes introduction
- `7fc930dd-992a-5d8e-9f69-259d073b620c` / bible / attempt3 (invalid): : Value error, Character removal precedes introduction
- `aebac48c-1722-5e6e-b01b-1764af08c2bd` / outline_1 / attempt1 (invalid): Day 1: thread advanced before planting or after resolution: the_seawall_sabotage; Day 2: unknown thread; Day 2: missing thread planting: the_seawall_sabotage; Day 4: character absent from bible or outside introduction/removal window: milo; Day 5: character absent from bible or outside introduction/removal window: milo; Day 5: missing thread planting: the_family_connection
- `aebac48c-1722-5e6e-b01b-1764af08c2bd` / outline_1 / attempt2 (invalid): Day 4: character absent from bible or outside introduction/removal window: milo; Day 4: thread advanced before planting or after resolution: the_family_connection; Day 5: character absent from bible or outside introduction/removal window: milo
- `aebac48c-1722-5e6e-b01b-1764af08c2bd` / outline_1 / attempt3 (invalid): Day 4: character absent from bible or outside introduction/removal window: milo; Day 4: thread advanced before planting or after resolution: the_family_connection; Day 5: hook must remain unresolved; Day 5: character absent from bible or outside introduction/removal window: milo
- `8c7e1ef7-5583-5886-addd-25e08091f333` / outline_1 / attempt1 (rejected): Reconciled local HTTP400 schema rejection; original acknowledgment absent from initial handler
- `a5000b86-0284-5d3d-8733-90cecc16cd99` / outline_6 / attempt1 (invalid): Day 6: hook duplicates an earlier ending; Day 7: hook duplicates an earlier ending; Day 8: hook duplicates an earlier ending; Day 9: hook duplicates an earlier ending; Day 10: hook duplicates an earlier ending
- `a5000b86-0284-5d3d-8733-90cecc16cd99` / outline_11 / attempt1 (invalid): Day 12: hook duplicates an earlier ending; Day 13: hook duplicates an earlier ending; Day 14: hook duplicates an earlier ending; Day 15: hook duplicates an earlier ending
- `a5000b86-0284-5d3d-8733-90cecc16cd99` / outline_11 / attempt2 (invalid): Day 12: hook duplicates an earlier ending; Day 13: hook duplicates an earlier ending; Day 14: hook duplicates an earlier ending; Day 15: hook duplicates an earlier ending
- `a5000b86-0284-5d3d-8733-90cecc16cd99` / outline_16 / attempt1 (invalid): Day 19: hook duplicates an earlier ending; Day 20: hook duplicates an earlier ending
- `a5000b86-0284-5d3d-8733-90cecc16cd99` / outline_16 / attempt2 (invalid): Day 19: hook duplicates an earlier ending; Day 20: hook duplicates an earlier ending
- `a5000b86-0284-5d3d-8733-90cecc16cd99` / outline_16 / attempt3 (invalid): Day 19: hook duplicates an earlier ending; Day 20: hook duplicates an earlier ending
- `127ddba8-99b6-5504-919e-2539c3d00870` / outline_6 / attempt1 (invalid): Day 9: hook duplicates an earlier ending; Day 10: hook duplicates an earlier ending
- `127ddba8-99b6-5504-919e-2539c3d00870` / outline_6 / attempt2 (invalid): Day 9: hook duplicates an earlier ending; Day 10: hook duplicates an earlier ending
- `127ddba8-99b6-5504-919e-2539c3d00870` / outline_11 / attempt1 (invalid): Day 13: hook duplicates an earlier ending; Day 14: hook duplicates an earlier ending; Day 15: hook duplicates an earlier ending
- `127ddba8-99b6-5504-919e-2539c3d00870` / outline_11 / attempt2 (invalid): Day 15: hook duplicates an earlier ending
- `127ddba8-99b6-5504-919e-2539c3d00870` / outline_16 / attempt1 (invalid): Day 16: hook duplicates an earlier ending; Day 17: hook duplicates an earlier ending; Day 18: hook duplicates an earlier ending; Day 19: hook duplicates an earlier ending; Day 20: hook duplicates an earlier ending
- `127ddba8-99b6-5504-919e-2539c3d00870` / outline_16 / attempt2 (invalid): Day 16: hook duplicates an earlier ending; Day 17: hook duplicates an earlier ending; Day 18: hook duplicates an earlier ending; Day 19: hook duplicates an earlier ending; Day 20: hook duplicates an earlier ending
- `127ddba8-99b6-5504-919e-2539c3d00870` / outline_16 / attempt3 (invalid): Day 20: hook duplicates an earlier ending
- `0f52652b-c277-5bf8-9cfb-5aa7862ae99e` / hooks_16 / attempt1 (invalid): Day 19: hook duplicates an earlier ending
- `0f52652b-c277-5bf8-9cfb-5aa7862ae99e` / hooks_16 / attempt2 (invalid): Day 19: hook duplicates an earlier ending
- `0f52652b-c277-5bf8-9cfb-5aa7862ae99e` / hooks_16 / attempt3 (invalid): Day 19: hook duplicates an earlier ending

Structural-agent receipts are checked for structure/ledger/lifecycle first. Hook rejections before the specialist are separate append-only events:

- `0f52652b-c277-5bf8-9cfb-5aa7862ae99e`: {"errors":["Day 16: hook duplicates an earlier ending","Day 17: hook duplicates an earlier ending","Day 18: hook duplicates an earlier ending","Day 19: hook duplicates an earlier ending","Day 20: hook duplicates an earlier ending"],"start":16}
- `8019aa1a-4ad6-5628-90c1-9866a7a691ad`: {"errors":["Day 21: hook duplicates an earlier ending","Day 22: hook duplicates an earlier ending","Day 23: hook duplicates an earlier ending","Day 24: hook duplicates an earlier ending","Day 25: hook duplicates an earlier ending"],"start":21}

## Resolutions and limits

- Actor lifecycle: precise actor/date feedback and explicit full-series recurring actors.
- Unknown actor/thread IDs and timing: named-day decoder schemas constrain allowed references, planting/payoffs and finale state. Installed Ollama rejected tuple schemas, so compatible named fields replaced them.
- Copied hooks: all earlier endings supplied as banned context; separate bounded hook-only specialist; old hook removed from positive context; short single-sentence repairs and story-only temperature0.9/repeat_penalty1.2. Shorts defaults preserved.
- Bounded calls: at most3 structural outputs and3 hook-only outputs per chunk. Known failure requires a new explicitly confirmed idempotent revision; uncertainty cannot use it. Accepted prefix and frozen bible are copied atomically; no cast approval is inherited.
- Deterministic checks do not certify semantic storytelling or acting. The inherited first15 days may still warrant owner editing for artistic quality. This report is planning evidence, not episode video or platform verification.

Private raw evidence: `data/automation/validation/series-20261107/{validation.db,revision-6-result.json,final-verification.json}`. Model artifacts/databases are ignored by Git.


## Final automated verification

| Check | Result |
|---|---|
| Full Python post-freeze regression | **738 passed,607.10s**, no failures/skips |
| Final automation targeted suite | **115 passed,23.29s** |
| Frontend | **75 passed,3.43s**,11 files |
| Production build | Passed,**1.53s** |
| Source-database migration copy | 16 additive tables,3 initializations; all40 original tables/1762 rows/schema preserved; integrity ok; source read-only |

Original baseline:623 Python/66frontend. No credentials, generated videos,
SQLite databases or checkpoints are committed. No cast/content approval, paid
provider call or publishing was performed. Episode production/lifecycle, Governor
and schedulers remain later phases, not verified features of this milestone.
