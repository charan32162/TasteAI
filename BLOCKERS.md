# TasteAI blockers and limitations

Updated: 2026-10-21

These are explicit data/configuration boundaries, not failures of the local demo.

| Status | Blocker or limitation | Effect | Safe next step |
| --- | --- | --- | --- |
| Menu source and identity coverage unavailable | Google Places is configured and enabled in the local FastAPI app, but the database contains no canonical restaurant identities, provider links, menu sources, menu documents, or menu evidence. | Restaurant discovery and one Place Details lookup can return transient provider facts, but no real dish or price recommendation can be supported. | Select an authorized menu source and establish reviewable restaurant identity links before importing any menu data. Do not scrape Google Maps or infer menu facts from place results. |
| Waiting for an approved data source and identity link | No licensed/approved real menu provider is configured and no menu source has been approved; discovery does not create canonical identities or provider links. | Restaurant discovery works with explicitly enabled providers, but the evidence store is empty by default and most newly discovered candidates remain unresolved until an existing identity is confidently matched. No dish recommendation is made without approved evidence. | Select and review a legitimate menu source; manually approve only after recording authorization, attribution, retention, and freshness terms. Add a separately governed identity-linking workflow only when needed. Do not infer menu contents from Places search text or demo data. |
| Not implemented in this phase | Review/photo evidence is not rendered in the UI. | No external review/photo text or images are shown or used for ranking. | Before display, implement Google attribution, author/avatar/source links, time/translation/ordering notices, reporting, and retention restrictions required by current policy. |
| Foundation implemented; source connection blocked | Provider-neutral identity, authorization records, evidence persistence, freshness/conflict handling, a read-only endpoint, separate exact-dish evidence search, and identity-scoped evidence attachment in restaurant discovery exist; the JSON-LD parser still does not fetch pages and no approved official source is configured. | No real menu claims exist in the database. Discovery may attach approved evidence only to a confidently matched existing canonical identity; unresolved candidates receive none. | Establish a legitimate source, then independently review safe retrieval and operational ingestion. Do not add a public source approval or arbitrary ingestion route. |
| OSM live result remains unverified | OSM/Nominatim and Overpass adapters exist. A previous bounded Chintalapudi request on 2026-10-21 returned a sanitized HTTP 503 timeout. The current local app selects Google, not OSM. | This verification made no OSM request. OSM results remain transient candidates and cannot prove menu contents, prices, or availability. | Do not repeat the timed-out request as part of this verification. A later explicit attempt should follow current OSM policies and remain bounded. |
| Not implemented | Website retrieval, PDF extraction, automatic identity creation/provider linking, and evidence-search UI are absent. | Discovery can attach existing approved evidence only when a candidate confidently matches an already-existing canonical identity; no real menu source or identity links are configured by default. | Add only after selecting compliant sources and tests; keep external services disabled by default and preserve ambiguity rather than merging on name alone. |
| Production-only limitation | Place lookup throttling is process-local (60 requests/hour globally per process and a two-second client cooldown); OSM additionally paces each outbound request to at least one second apart per process. | It limits accidental local duplicate/cost calls but is not shared across multiple workers or instances; it is not suitable as a public-service quota system. | Use a shared rate limiter and centrally enforced quota before any multi-instance deployment. |
| Not approved for release | TasteAI remains a local prototype; public deployment is out of scope. | No production database, shared rate limit, backups, app-wide abuse controls, or full operational security posture is claimed. | Complete the broader production-readiness and privacy/security review before deployment. |

The default fictional demo catalog, guest discovery, and test suite continue to work without Google credentials or any external API.

## Verification record — 2026-10-21

- The current local FastAPI app loaded provider configuration as Google Places enabled. Configuration inspection printed no credential values.
- One bounded live discovery for the explicit Chintalapudi query (5 km radius, limit 5) returned five candidates. One Place Details lookup also returned HTTP 200. TasteAI excluded no results outside radius and found no menu evidence; zero recommendations were returned. No review or menu source was requested, and no retry was made.
- The default SQLite database contained zero restaurant identities, provider links, menu sources, menu documents, and menu items when inspected read-only.
- The automated suite reported 131 tests: 130 passed and one optional live Places smoke test was skipped. The 110-case offline evaluator passed. Python compilation, JavaScript syntax, and `git diff --check` passed. No migration was changed in this verification.
- Interactive browser testing was blocked by the local browser approval gate. API/static UI tests passed, but the browser workflow is not claimed as interactively verified.
- Real dish-level recommendation remains blocked on an approved menu source and confident identity matches.

## Verification record — 2026-10-22

- The TasteAI offline suite completed **144 tests: 143 passed and one optional live Places test skipped**. The deterministic 110-case evaluator passed every intent, hard-constraint, top-match-attribute, and explanation-grounding check.
- Python compilation, browser JavaScript syntax, and `git diff --check` passed.
- Alembic upgraded a fresh temporary SQLite database to `d2ab61cafe15`, downgraded one revision, re-upgraded to head, and reported no model/schema differences. The configured local application database was not used for this check.
- No Google, OSM, OCR, LLM, or other external provider requests were made. The secret-pattern scan found no key-shaped literals in scanned project files; local environment files and database files were excluded and were not read by the scan.
- Added an offline GitHub Actions workflow, a TasteAI-only Dockerfile, and deployment/demo/runbook documentation. The workflow itself was not run on GitHub, and a local container build could not be run because Docker is not installed in this environment.
- The project remains a local prototype. Real OCR/Vision, LLM generation, embeddings/vector retrieval, a generated RAG answer layer, and production shared rate limits/database/operations are not implemented or verified. No real restaurant/menu evidence was added.
