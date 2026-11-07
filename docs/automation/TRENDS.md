# Trend intelligence and read-only preview

Phase 2 adds topic signals, not source footage or factual verification. Source text
is untrusted; recipes in Phase 3 must create original material. GDELT bundles remain
auto_compiled/reviewed=false and must not be passed off as human-reviewed dossiers.

```bash
# Private explicit collection; no timer/service is installed or started.
.venv/bin/python scripts/run_director.py --collect-trends --sources youtube \
  --database data/automation/my-validation.db
# Read-only: no network, models, jobs, quotes or writes.
.venv/bin/python scripts/run_director.py --preview \
  --database data/automation/validation/phase2-20261106/validation.db
.venv/bin/python -m pytest tests/test_automation_trends.py -q
```

Back up an existing database before invoking mutating collection there. The real
validation uses a new private database. The CLI reuses existing server credentials
privately; no key appears in arguments, result bodies or event messages. API preview
is `POST /api/v1/automation/plan-preview` with JSON `{"lane":"shorts_daily","region":"US"}`
and the existing admin session/CSRF token. No old endpoint contract changes.

Signals are retained per collection tick. Repeating a source/region tick within
AUTOMATION_TREND_INTERVAL_HOURS does not repeat its request, including a recorded
failure. A failed source cannot fail other sources. Missing/disabled sources are
reported; no fake signals are generated. Wikimedia is a global English signal with
a two-day reporting lag; HN is global. Region does not imply country filtering for
those sources. Google Trends and Reddit are optional, disabled by default; Reddit
requires owner-provided OAuth access. No anonymous Reddit scraping is used.

A cluster deduplicates (source, external_id), then uses normalized token overlap or
near-identical sorted title similarity. Score = 3 × additional independent sources
+ 2 × saturated log velocity + exponential 72-hour recency + 0.5 × saturated log
popularity. The metric scales prevent absolute Wikipedia views dominating every
other source. Category keywords take precedence over source hints; unknown topics
become wildcard. Optional AUTOMATION_CLASSIFY_WITH_OLLAMA=true enriches hints only
while collecting under the cinematic GPU lock; it unloads its local model afterward.
Preview itself always remains model/network free.

The selector excludes near-matching topics used during the lane cooldown, including
legacy produced plans. It fills each quota, then substitutes the next-highest-ranked
unused topic when a category lacks candidates. Each fallback is visible in reasons.
Unselected ranked topics become backups. Insufficient fresh signals produce fewer
items plus a warning, never invented topics. Analytics weighting is Phase 6 work.

Tests use saved response snapshots/contract fixtures and forbid network calls.
Fixture provenance distinguishes recorded YouTube/Wikimedia/HN responses from
synthetic GDELT/Reddit contracts; GDELT recording failed remotely. Live source
availability is not established by mocked tests.

Official contracts reviewed:
- [YouTube videos.list](https://developers.google.com/youtube/data/reference/rest/v3/videos/list)
- [Wikimedia pageview API](https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html)
- [Hacker News API](https://github.com/HackerNews/API)
- [GDELT DOC API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)

Optional source references:
- [Google Trends RSS export](https://support.google.com/trends/answer/3076011)
- [Reddit OAuth listings](https://www.reddit.com/dev/api/)
