# TasteAI local MVP

TasteAI is a standalone FastAPI application; it does not replace the repository's Team Task Manager. The browser UI and JSON API run from one local process, with SQLite and an explicitly fictional demo menu by default. No paid API calls, real restaurant data, or restaurant reviews are enabled by default.

## AI and evidence capabilities (verified locally)

TasteAI has optional OpenAI Responses adapters for structured query hints and menu-photo OCR, plus an optional OpenAI embeddings adapter. All external AI paths are disabled by default and require separate `TASTEAI_ALLOW_LLM`, `TASTEAI_ALLOW_EXTERNAL_OCR`, or `TASTEAI_ALLOW_EXTERNAL_EMBEDDINGS` opt-ins and a server-side `OPENAI_API_KEY`. They were tested with mocked HTTP transports only; no provider request was made. OCR sends the uploaded image to the configured provider only when explicitly enabled, and every extracted row remains pending human review.

Approved menu evidence can be indexed through reviewer-only `POST /api/v1/rag/index` and queried through `POST /api/v1/rag/query`. Local vectors use deterministic lexical feature hashing; the optional OpenAI adapter creates semantic vectors only when explicitly enabled. Retrieval is hybrid and transparent: each result reports lexical/vector method, evidence quality, freshness, and hard filters for source approval, state, conflict, staleness, and budget. Optional grounded RAG generation selects typed evidence claims; TasteAI independently validates their IDs and fields, then renders factual text from those fields. If generation is off, fails, or returns no supported claims, the deterministic cited answer remains available. The four-step agent path exposes typed read-only tool contracts, execution limits, and audit metadata; deterministic intent constraints and ranking remain authoritative. AI status reports whether LLM/embedding providers are optional external capabilities or deterministic offline fallbacks, without making network calls. The normal `/api/v1/food/search` and `/api/v1/food/recommend` path now prefers matching approved menu evidence and falls back to the explicitly labeled fictional catalog; each result includes a structured grounded explanation and ranking trace.

The web client exposes an approved-menu evidence query and a visible “Grounded AI pipeline” panel. Search responses show whether candidates came from approved evidence or the fictional catalog, plus the deterministic intent → evidence → constraints → personalization → ranking → explanation stages. A fresh install with no approved menu evidence correctly returns insufficient evidence. The OCR, query-hint and grounded-answer LLM, external embedding, Google Places, and OSM providers have not been live-tested.

## Run

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r tasteai\requirements.txt
$env:TASTEAI_SECRET_KEY = (python -c "import secrets; print(secrets.token_urlsafe(48))")
python -m uvicorn tasteai.app:app --reload --port 8000
```

Open `http://127.0.0.1:8000`. Search and browse the explicitly fictional demo menu without an account. Sign in or create an account to save taste preferences, feedback, and food history; guest feedback is temporary to the current browser session. API documentation is at `/docs`. `npm start` remains the existing Team Task Manager command.

The local database is created at `tasteai/data/tasteai.db`; it is ignored by Git. Set `TASTEAI_DATABASE_URL` to a PostgreSQL SQLAlchemy URL to use PostgreSQL (`postgresql://...` is normalized to the psycopg 3 driver). For durable local JWT sessions, set `TASTEAI_SECRET_KEY` to a private random string of at least 32 characters. Production mode refuses to start without it.

Optional restaurant lookup defaults to demo/disabled in the tracked `.env.example`. The ignored local `tasteai/.env` in this worktree selects Google Places and enables paid lookups; do not commit that file. Importing `tasteai.app` loads this file, while importing only `tasteai.location_providers` does not, so provider status should be checked through the app or its API. Google calls may incur charges. Google configuration requires a backend-only key, explicit paid-use opt-in, and valid HTTPS Terms and Privacy URLs; the readiness check validates URL shape, not public reachability or policy compliance. The app will not request browser location or send area text while the selected provider is disabled. To use OpenStreetMap instead, select `TASTEAI_RESTAURANT_PROVIDER=osm` and configure a descriptive `TASTEAI_OSM_USER_AGENT`; this contacts Nominatim/Overpass public endpoints, so check their current policies before enabling. Provider readiness is visible at `/api/v1/providers/status`; this endpoint never returns credentials. Incomplete provider configuration leaves the app running without making requests.

Live OSM verification on 2026-10-21: one bounded Chintalapudi discovery request (5 km, up to 10 results) timed out and returned HTTP 503; it produced no restaurant results. Do not treat this as live discovery verification. The app was not retried against the public service in that run.

The current adapter uses the official [Places API (New) Text Search](https://developers.google.com/maps/documentation/places/web-service/text-search), [Nearby Search](https://developers.google.com/maps/documentation/places/web-service/nearby-search), and [Place Details](https://developers.google.com/maps/documentation/places/web-service/place-details) endpoints. Search requests use a small explicit field mask for place ID, display name, address, location, and Google Maps URI; ratings, price level, website, opening hours, reviews, and photos are not requested as part of normal search. Google bills at the highest applicable field SKU in a mask; ratings/price and reviews/photo fields can raise the request to higher-priced tiers. See [current billing and pricing](https://developers.google.com/maps/documentation/places/web-service/usage-and-billing) and [Places policies](https://developers.google.com/maps/documentation/places/web-service/policies) before enabling calls.

`POST /api/v1/restaurants/search` accepts a manual `city`/area and `POST /api/v1/restaurants/nearby` accepts a one-time coordinate pair. Both support a bounded result limit (1–10) and optional `food_query` context. The food query is parsed locally and is not sent to the place provider. Explicit provider calls are capped to 60 per process per hour and one request per client every two seconds; OSM requests are additionally paced to at most one per second per process. These in-memory limits are local safeguards, not distributed production rate limits. Results and details are transient, not cached or persisted; place IDs are not stored either. The response includes signal provenance, but no restaurant is promoted as a food recommendation without menu evidence. Dish availability, item price, dietary suitability, ratings not requested, and review evidence remain `unknown`. No provider rating is used in ranking.

Cards separate provider place data visually and show same-card attribution (“Google Maps” or linked “© OpenStreetMap contributors”); map links are restricted to the corresponding provider hosts. Google attribution follows its text display requirements when used. Place-detail review/photo methods are isolated, separately field-masked, and not called by ordinary lookup or displayed in this MVP. They retain review/photo author attribution metadata for a future separately reviewed UI, but the review/photo content is never stored.

The discovery screen offers manual area entry and a one-time nearby lookup. Browser geolocation is requested only after the user presses the nearby button and only when a live place provider is enabled. The browser uses low-accuracy location and sends coordinates in the request body. The app does not persist location or provider results; HTTPX informational logs are suppressed so the manual area in a Nominatim query URL is not written to application logs. If the provider is disabled, the UI explains why and does not prompt for location. Provider errors, denied permission, timeouts, and no-results responses are surfaced without substituting fictional places. A location result is explicitly a restaurant candidate only; it cannot be combined with the fictional demo menu or mistaken for a verified dish match.

The Group table view suggests a fictional demo venue only when each attendee has at least one eligible listed dish; unsupported dietary restrictions produce no suggestions. The Taste transfer view compares only friend-provided taste values after consent is confirmed, without accessing another account or saving that snapshot. Neither feature verifies allergens, food preparation, real-world availability, nor the friend-provided rating.

The Review evidence view analyzes excerpts pasted by the signed-in user, shows exact supporting text, and does not store excerpts or retrieve external reviews.

Discovery uses deterministic local intent parsing and ranking. Budget phrases such as “under ₹300” are hard price ceilings, while “around ₹200” is a soft target. Recognized dietary/protein requirements and exclusions are filtered before ranking; unsupported allergy or dietary constraints fail closed. Spice, crunch, filling, and adventurousness requests influence ranking and are described only when supported by demo-catalog attributes. Missing attributes are omitted, and final scores normalize across available components instead of inventing neutral evidence. Taste DNA provenance distinguishes explicit settings from rating-inferred values; non-default values in older profiles are preserved as legacy preferences. Match percentages are estimates, not satisfaction probabilities. Optional `TASTEAI_RANKING_WEIGHTS` and `TASTEAI_INTENT_WEIGHTS` JSON environment variables override the normalized ranking and intent weights.

`POST /api/v1/food/search` and `/api/v1/food/recommend` are guest-accessible. A valid optional bearer token enables use of the signed-in user's profile and history; invalid supplied tokens are rejected. Catalog reads are public, while saved dishes, personal history, taste profiles, feedback, group planning, taste transfer, and review analysis remain authenticated.

`POST /api/v1/restaurants/discover` is the explicit-action endpoint used by the browser's optional restaurant discovery form. It accepts a location query or coordinate pair, radius 100–50000 m, optional cuisine filter, and optional exact dish/budget query; it does not persist location or expand the requested radius. The browser displays candidates separately from approved menu evidence, including source, freshness, evidence ranking estimate, price, and availability uncertainty. It resolves identities read-only and attaches approved menu evidence only to existing canonical identities that confidently match. Unresolved candidates never inherit evidence, and a discovered restaurant is not represented as serving a dish without approved supporting evidence. Provider calls remain disabled by default; OSM/Google results and all location input are transient.

The internal `tasteai.structured_data.parse_schema_menu()` parser extracts explicit Schema.org menu fields from caller-supplied HTML without fetching or retaining raw documents. `tasteai.menu_evidence` adds normalized persistence, source authorization state, conservative restaurant identity resolution, provenance, freshness and conflict handling, and a read-only API. `POST /api/v1/food/evidence-search` is a separate exact-normalized-dish search over approved stored evidence, with approved canonical dish aliases resolved conservatively; it does not call providers, use demo dishes, or join transient place candidates. The offline evaluator also includes a reusable synthetic multi-restaurant fixture covering overlapping dishes, aliases, stale/revoked sources, unknown signals, dietary filtering, and citations. Hard budget and dietary constraints fail closed when price, currency, or explicit suitability is unknown. `TASTEAI_EVIDENCE_STALE_POLICY` defaults to `exclude` and may be set to `downgrade`; `TASTEAI_EVIDENCE_RANKING_WEIGHTS` configures normalized score weights. No source is approved by default; parser output is not proof of availability or authorization. See the repository-root `API.md`, `DATA_SOURCES.md`, and `SECURITY.md` before configuring any source.

Local development applies Alembic revisions automatically when `TASTEAI_AUTO_CREATE_SCHEMA=true`. A recognized existing pre-Alembic schema is stamped at the initial revision and upgraded additively; an unrecognized schema stops startup instead of being overwritten. For production, disable automatic migration and run the reviewed Alembic revisions first:

```powershell
$env:TASTEAI_ENV = "production"
$env:TASTEAI_AUTO_CREATE_SCHEMA = "false"
python -m alembic -c tasteai\alembic.ini upgrade head
python -m uvicorn tasteai.app:app --host 127.0.0.1 --port 8000
```

## Validate

Install optional test client dependencies with `python -m pip install -r tasteai\requirements-dev.txt`, then run:

```powershell
python -m unittest discover -s tasteai\tests -v
```

The tests use a temporary SQLite database and mocked provider transport; they do not contact external services. The deterministic recommendation regression corpus is synthetic and contains 110 requests. Run it with `python -m tasteai.evaluation.run --strict`; it checks parsed intent, hard constraints, requested attributes in the best match, and explanation grounding. Its scores measure regression coverage against the fictional seven-dish catalog, not real-world recommendation quality.

Architecture, source rights/status, API, security limitations, evaluation, and development setup are documented in the repository-root `TASTEAI_ARCHITECTURE.md`, `DATA_SOURCES.md`, `API.md`, `SECURITY.md`, `EVALUATION.md`, and `DEVELOPMENT.md`.

An optional, potentially billable live smoke test is skipped by default. It makes one Text Search request capped at one result and runs only when `GOOGLE_PLACES_LIVE_TEST=true`, the Google provider, the separate paid-use opt-in, a backend key, and HTTPS policy URLs are all configured. Normal test commands do not make Google or OSM requests. Place-provider data remains request-time only; the independent menu-evidence foundation has an additive Alembic migration.
