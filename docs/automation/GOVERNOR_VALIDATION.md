# Governor seven-day dry-run verification

Recorded-fixture simulation using the actual PublishingService dry-run validator.
External requests and upload methods raise assertions in the tests. No GPU/provider generation.

| Simulated local day | YouTube | Facebook | Distinct validations |
|---|---|---|---|
| 2026-11-08 | dry_run allowed | dry_run allowed | 2 |
| 2026-11-09 | dry_run allowed | dry_run allowed | 2 |
| 2026-11-10 | dry_run allowed | dry_run allowed | 2 |
| 2026-11-11 | dry_run allowed | dry_run allowed | 2 |
| 2026-11-12 | dry_run allowed | dry_run allowed | 2 |
| 2026-11-13 | dry_run allowed | dry_run allowed | 2 |
| 2026-11-14 | dry_run allowed | dry_run allowed | 2 |

Total: **14 distinct validation calls**, **0 duplicate calls**, **0 actual uploads**,
**0 publication rows**, **0 persisted approvals**. Every tick was repeated three
times. Stable job/platform/mode actions prevented repeat publishing validation.

Private evidence: `data/automation/validation/phase6-governor-20261107/seven-day-decisions.json`.
Fixture databases/media stay ignored; this summary contains no credentials.

Additional tests exercise manual mode, file/DB kills, missed slots, speech/caption
and owner holds, changed media, atomic caps, earlier-episode order, private-validation
holds, three-failure trip, uncertainty, permanent claims after manual closure,
quote-bound conservative spend, expiry and analytics/report integration. The live
Governor path is covered with the actual review and publishing services and mocked
YouTube/Facebook upload objects. Uploaded/submitted are never claimed published.

No live upload, public visibility or platform access was verified.
