# TasteAI development

TasteAI runs independently from the repository's Express Team Task Manager.

## Local setup

From the repository root in PowerShell:

```powershell
python -m venv tasteai\.venv
.\tasteai\.venv\Scripts\Activate.ps1
python -m pip install -r tasteai\requirements-dev.txt
$env:TASTEAI_SECRET_KEY = (python -c "import secrets; print(secrets.token_urlsafe(48))")
python -m uvicorn tasteai.app:app --reload --port 8000
```

Open `http://127.0.0.1:8000`; API documentation is at `/docs`. SQLite is created under `tasteai/data/`. The fictional catalog is seeded automatically. Development startup applies Alembic migrations when `TASTEAI_AUTO_CREATE_SCHEMA=true`.

## Configuration

See `tasteai/.env.example`. Important variables:

- `TASTEAI_DATABASE_URL`: optional SQLAlchemy URL; SQLite is the default.
- `TASTEAI_SECRET_KEY`: private signing key; production requires at least 32 characters.
- `TASTEAI_ENV=production`: turns on production validation.
- `TASTEAI_AUTO_CREATE_SCHEMA`: local automatic migration switch; disable and migrate explicitly in production.
- `TASTEAI_RANKING_WEIGHTS`, `TASTEAI_INTENT_WEIGHTS`: optional normalized ranking weight JSON.
- `TASTEAI_RESTAURANT_PROVIDER`: `demo` by default; Google requires the explicit paid-use opt-in, server-side key, and HTTPS Terms/Privacy URLs.
- `TASTEAI_MENU_EVIDENCE_TTL_SECONDS`: freshness window for approved menu sources; defaults to 604800 seconds (7 days), bounded to 60 seconds–31536000 seconds (1 year).
- `TASTEAI_MENU_PHOTO_REVIEWER_USER_IDS`: comma-separated trusted reviewer account IDs; empty by default, which disables menu photo routes.
- `TASTEAI_MENU_PHOTO_STORAGE_DIR`: private local photo storage directory; defaults to `~/.tasteai/private-menu-photos`, outside the static web directory.
- `TASTEAI_MENU_PHOTO_MAX_BYTES` and `TASTEAI_MENU_PHOTO_MAX_PIXELS`: upload and decoded-dimension caps; defaults are 10 MiB and 20 million pixels.
- For optional OpenStreetMap lookup, set `TASTEAI_RESTAURANT_PROVIDER=osm` and a descriptive `TASTEAI_OSM_USER_AGENT`; Nominatim and Overpass endpoints can be overridden with `TASTEAI_OSM_NOMINATIM_URL` and `TASTEAI_OSM_OVERPASS_URL`. Public OSM services have strict usage policies; local selection is not approval for production-scale traffic.

Never put real credentials in tracked files. Do not set Google live options unless the operator has explicitly approved the cost and policy obligations.

## Validation

```powershell
python -m unittest discover -s tasteai\tests -v
python -m tasteai.evaluation.run --strict
node --check tasteai\web\app.js
python -m compileall -q tasteai
git diff --check
```

Normal tests use temporary SQLite databases and mocked HTTP transports; they do not call external providers. The live Places smoke test is separately gated by `GOOGLE_PLACES_LIVE_TEST=true` and provider configuration.

The 2026-10-21 verification run completed 131 tests (130 passed, one optional live Places smoke test skipped), the 110-case offline evaluator, Python compilation, JavaScript syntax validation, and `git diff --check`. A separate explicitly bounded Google Places check resolved Chintalapudi, returned five candidates within 5 km, and successfully retrieved Place Details for one candidate. It made no menu or review request. No menu evidence or restaurant identity links were present, so no real dish-level recommendation was returned. A previous bounded OSM discovery request timed out with HTTP 503; it was not retried. Browser-based interaction was blocked by the local browser approval gate; automated API and static UI tests passed, but interactive browser behavior is not claimed as verified. No migration changed in this verification. See `BLOCKERS.md` and `DATA_SOURCES.md` for the current evidence boundary.

## Database migrations

Migration files live in `tasteai/migrations/versions/`. Use Alembic from the repository root with `tasteai\alembic.ini`. Review migrations before applying them to any non-disposable database. Avoid manually changing schema.

## Structured-data parser

`tasteai.structured_data.parse_schema_menu()` parses caller-supplied HTML only. The `tasteai.menu_evidence` service can persist normalized records only for manually approved sources and exposes them through a read-only API; it has no public ingestion route and does not power real-place recommendations. Approval is an operator assertion, not automated verification of rights. Source URLs are provenance, not proof of ownership or authorization. Keep tests offline and bounded; any future fetcher must be designed and reviewed separately for terms/robots compliance, SSRF, redirects, content size/type, timeouts, and retention.

## Menu photo pipeline

Only allowlisted reviewer accounts can upload, review, and commit photos. Upload raw JPEG/PNG/WebP bytes with `X-Restaurant-Identity-Id`; supply a timezone-aware `X-Menu-Captured-At` only when capture time is known. Optional provider identity headers must match a link already stored for that canonical restaurant. Original image bytes are kept in private local storage; the API does not serve them. Reviewers inspect each pending proposal and explicitly accept, reject, or correct it before commit. Commit reuses `ingest_menu_batch`; pending proposals are never evidence. Missing capture dates remain unknown, so their evidence is stale under current search policy. The default extractor is disabled and actual OCR/Vision is not implemented; the deterministic extractor is test-only. Pillow is declared in `tasteai/requirements.txt` for safe image verification and decoding.
