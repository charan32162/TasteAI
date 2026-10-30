# TasteAI Product Specification

**Product:** TasteAI — “Not the best food. The best food for YOU.”
**Status:** Local MVP, optional Google Places/OpenStreetMap place lookup, provider-neutral bounded restaurant discovery with identity-scoped approved evidence attachment, offline structured-menu extraction, and separate approved-menu-evidence search implemented; external lookups remain opt-in
**Last updated:** 2026-10-21

## Product goal

TasteAI helps a person decide what to eat by combining their current craving, learned taste preferences, food history, budget, dietary needs, and (when explicitly provided) location. It ranks dishes for that person rather than sorting restaurants by a generic rating. Its recommendation explanations must be traceable to profile, catalog, and interaction data.

## Principles and trust boundaries

- No restaurant, menu, rating, or review is presented as real unless it comes from an identified, permitted source. The local starter catalog is explicitly fictional demo content; it contains no reviews.
- Location search is optional and starts only after the user submits an area or explicitly chooses “Use my location once.” Browser permission is never requested automatically. Coordinates and area text are used for that lookup only and are not persisted.
- No LLM or external provider is required for food discovery. Natural-language parsing and dish ranking are deterministic and local. Google Places is disabled by default and requires explicit paid-usage opt-in, server-side credentials, and HTTPS Terms of Use and Privacy Policy URLs. OpenStreetMap lookup is also disabled by default and requires an explicit provider selection and descriptive User-Agent; public OSM endpoints remain subject to their current usage policies.
- Structured menu extraction parses caller-supplied HTML offline. A provider-neutral evidence foundation persists normalized facts only for manually approved sources; exact-dish evidence search can rank stored facts, and restaurant discovery may attach evidence only through a confident match to an existing canonical identity. Neither path fetches sources, verifies restaurant ownership/rights, or asserts live availability.
- A match percentage is a heuristic, not a calibrated probability of satisfaction. It must be described as an estimate until evaluated against sufficient real feedback.
- Accounts are isolated by authenticated user ID. Passwords are salted and hashed; tokens and signing keys are not stored in the repository.
- Local development uses SQLite. A PostgreSQL URL is supported through SQLAlchemy; paid API calls, public deployment, and production data are not enabled by default.

## User experience

The responsive web MVP is guest-first: anyone can search or browse the explicitly fictional demo menu without registering. Discovery puts one best match before alternatives, supports quick prompts and budget/exploration controls, and shows the parsed request, heuristic match estimate, and “why this?” evidence. Guests can mark dishes tried or hide them for the current browser session; this feedback is not sent to the server or retained as account history. Signing in unlocks persistent feedback, Taste DNA, saved dishes, memories, and food history. Account holders can say things such as “spicy chicken, not biryani, under ₹300” or “avoid spice today.” Explicit “never recommend” statements become permanent memories; “today” requests are stored as temporary memories with expiry. Users can correct their profile and remove memories through the API.

Onboarding is incremental: a new account receives neutral feature values and learns when the person rates or rejects a dish. Taste features use a 0–10 scale (low to high); ingredients, cuisines, dietary restrictions, and memories are separate profile data. Feedback is evidence about the user, not a claim about the food catalog.

## Local architecture

```text
tasteai/
  app.py             FastAPI routes, startup, request logging
  database.py        SQLAlchemy engine and session dependency
  models.py          Relational user, profile, catalog, memory, and history records
  schemas.py         Validated API request/response contracts
  security.py        PBKDF2 password hashing and signed access tokens
  providers.py       RestaurantDataProvider contract and fictional demo provider
  location_providers.py Provider boundary, optional Google Places adapter, and shared place model
  osm_provider.py     Optional, rate-paced Nominatim/Overpass lookup
  restaurant_discovery.py Provider-neutral bounded discovery and read-only evidence attachment
  structured_data.py Bounded, offline Schema.org MenuItem/Offer extraction
  menu_evidence.py   Source authorization, identity, evidence persistence, freshness/conflict queries
  evidence_discovery.py Exact-dish evidence retrieval, hard constraints, ranking, explanations
  engine.py          Intent parser, deterministic ranking, explanations, review evidence
  evaluation/        Synthetic offline discovery corpus and measurable evaluator
  web/               Same-origin mobile-friendly web client
  tests/             Unit and API tests
```

The TasteAI service runs separately from the existing Express Team Task Manager so its routes, database assumptions, and frontend do not alter that application. FastAPI serves the client and versioned `/api/v1` routes. SQLAlchemy manages a compact relational schema: users and profiles; restaurants and dishes; food memories; feedback/history events; and saved/rejected interactions represented by those events. JSON columns hold flexible taste vectors and dish attributes without creating one table for every feature. Foreign keys and unique constraints protect ownership and catalog relationships. SQLite is the zero-configuration local default; `TASTEAI_DATABASE_URL` can point to PostgreSQL when its driver and server are available.

The `RestaurantDataProvider` boundary keeps dish ranking independent of a catalog vendor. `FictionalDemoProvider` supplies clearly labeled sample dishes and restaurant names only; `DemoMenuProvider` returns its demo menu explicitly; `UnknownMenuProvider` never claims that an external place has a menu. The `RestaurantProvider` protocol normalizes restaurant search, nearby search, and place detail operations into TasteAI's `RestaurantPlace` model. `GooglePlacesProvider` is isolated in `location_providers.py`; `OpenStreetMapProvider` uses Nominatim/Overpass in `osm_provider.py`. Google Places requests use a backend-only key, minimal field masks, bounded timeouts, sanitized errors, and transient response models; live calls require paid-use opt-in, credentials, and operator-supplied HTTPS Terms/Privacy URLs. OSM requires a descriptive User-Agent and is paced according to the Nominatim policy. Both providers are opt-in, are not written to the application database, and return place candidates only. Follow the current [Google Places attribution and content policies](https://developers.google.com/maps/documentation/places/web-service/policies), [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/), and applicable Overpass service policies.

`structured_data.py` parses already-supplied HTML using Python's standard library. It recognizes explicit Schema.org `MenuItem` and `Offer` JSON-LD, bounds input bytes, number of scripts, graph depth, and node count, and returns source URL, capture time, content hash, parser version, and sanitized warnings. It extracts no ingredients, dietary claims, dish attributes, or availability; an absent/invalid price stays absent and a missing currency stays unknown. Its `supported` status means the claim is explicitly present in the supplied structured data only. It does not prove the source is restaurant-owned or authorized, fetch the URL, apply freshness policy, persist evidence, or feed the recommendation engine. This is a parser foundation, not a real menu provider.

External place results are visually separated from the fictional food catalog and show provider attribution in the same result card; map links are restricted to expected Google Maps or OpenStreetMap hosts. Area queries and browser-provided coordinates are sent only after an explicit user action. The nearby button requests low-accuracy browser geolocation only after confirming that a location provider is enabled; if the provider is disabled, no permission prompt occurs. Coordinates are submitted in a request body and are not cached or persisted. Manual OSM area text is necessarily sent to Nominatim, but HTTPX informational request logging is suppressed to prevent its query URL being recorded locally. Google normal search requests omit ratings, price levels, website/hours, reviews, and photos from their field masks to control SKU costs. Place Details uses its own mask. Reviews and photos have separate provider methods and masks; ordinary discovery does not request them. Their models retain source, author, profile/photo attribution, and source-link metadata, but those contents are not exposed by the current UI or persisted.

`POST /api/v1/restaurants/search` is the manual area route and `POST /api/v1/restaurants/nearby` is the coordinate route; both use bounded request bodies (maximum ten results). `POST /api/v1/restaurants/discover` accepts exactly one explicit area query or a complete coordinate pair, bounds radius to 100–50000 meters, supports local cuisine filtering and an optional dish query, and never expands the requested radius. The optional food query is not sent to the place provider. Discovery resolves identities read-only and attaches menu evidence only when the existing identity resolver confidently matches a canonical identity; it does not create identities/provider links. If no approved evidence is found, the result explicitly states that the dish is not verified. TasteAI does not infer menu contents from a restaurant listing, provider search text, or the fictional demo menu. Nearby distance is calculated from request-time coordinates and provider coordinates, not read from provider ratings. The app enforces a process-local limit of 60 place lookups per hour and a two-second client cooldown; OSM outbound requests are additionally paced at one per second. These are local safeguards, not multi-instance production rate limits.

## API contract

Health, catalog reads, and food search/recommendation are public. A valid optional bearer token personalizes discovery; an invalid supplied token is rejected. Account profiles, feedback, saved/history data, memories, group planning, taste transfer, and review analysis require authentication.

All personal endpoints require an `Authorization: Bearer <access_token>` header. Request bodies are validated and errors use JSON `detail` responses. Current routes:

| Route | Purpose |
| --- | --- |
| `GET /api/v1/health`, `GET /api/v1/providers/status` | Liveness and safe provider readiness; no credentials are returned |
| `POST /api/v1/auth/register`, `POST /api/v1/auth/login` | Account creation and access token |
| `GET /api/v1/users/me`, `GET /api/v1/users/me/taste-profile` | Current account and Taste DNA |
| `PATCH /api/v1/users/me/taste-profile` | Update taste features and dietary/cuisine preferences |
| `GET /api/v1/food/catalog`, `POST /api/v1/food/search`, `POST /api/v1/food/recommend` | Catalog and ranked results |
| `POST /api/v1/groups/recommend` | Group restaurant suggestions with an eligible listed dish for every attendee |
| `POST /api/v1/food/taste-transfer` | Compare a friend's explicitly shared taste features with the signed-in user's Taste DNA |
| `POST /api/v1/food/feedback`, `GET /api/v1/food/history`, `GET /api/v1/food/saved` | Learning signals and food memory |
| `GET /api/v1/dishes/{dish_id}`, `GET /api/v1/restaurants/{restaurant_id}`, `GET /api/v1/restaurants/nearby` | Demo catalog details and the explicitly fictional restaurant list |
| `POST /api/v1/restaurants/search` | Explicit manual area search; returns transient, attributed restaurant candidates only |
| `POST /api/v1/restaurants/nearby` | Explicit one-time nearby search from a coordinate pair; returns transient, attributed restaurant candidates only |
| `POST /api/v1/restaurants/discover` | Bounded provider-neutral discovery; may attach approved menu evidence only to confidently matched existing identities |
| `GET /api/v1/restaurants/providers/{place_id}` | Explicit provider detail lookup; never persists the result |
| `POST /api/v1/food-memory`, `GET /api/v1/food-memory`, `DELETE /api/v1/food-memory/{memory_id}` | Explicit/inferred/temporary preferences |
| `POST /api/v1/agent/chat` | Local intent parsing, memory extraction, recommendation response |
| `POST /api/v1/reviews/analyze` | Evidence-only aspect extraction from review text supplied by the caller; excerpts are not persisted |

The API does not expose provider keys, request automatic geolocation, provide image recognition, or include refresh tokens or an LLM endpoint in this MVP. Location input is validated; partial/invalid coordinates, mixed area-plus-coordinate requests, invalid result limits, and malformed provider payloads are rejected or handled without fabricated fallbacks. Missing Google credentials keep the application running in a disabled state and are reported without sending a request. Places searches are process-limited, deduplicated, capped at ten results, and are not automatically repeated.

The JSON-LD menu parser is an internal Python API (`tasteai.structured_data.parse_schema_menu`), not an HTTP endpoint. It does not retrieve a page or store raw HTML. A separate service persists normalized evidence for approved sources and exposes it through a read-only route; source approval is a manual operator assertion, not rights verification. See `DATA_SOURCES.md` for source/rights boundaries.

Group recommendations accept 2–8 attendees with per-person food intent, ingredient exclusions, optional budgets, and an optional use of the signed-in user's Taste DNA. A venue is returned only when its listed demo menu has an eligible dish for each attendee. Group scores are heuristic blends, not predictions of consensus. Vegetarian/vegan eligibility is checked only against the demo vegetarian flag and listed ingredients; unsupported dietary restrictions fail closed with no venue suggestions. The menu cannot verify allergens, cross-contact, or preparation practices.

Taste transfer accepts only feature values and a rating submitted by the signed-in user, and requires a consent confirmation. It does not look up or modify a friend's account. Similarity and dish-fit values are heuristic estimates; the friend's rating is reported as user-supplied and is not independently verified. Submitted transfer data is not persisted.

## Intent and ranking

Natural-language parsing recognizes common food terms, English and selected Hindi craving/exclusion phrases, vegetarian intent, protein requirements/exclusions, taste attributes, filling requests, adventurousness, restaurant/location intent, and budget amounts. “Under/below/up to” amounts are hard ceilings, while “around/about/roughly” amounts are soft price targets. Unsupported allergy or dietary constraints fail closed instead of being treated as safe. Uncatalogued signals (for example protein quantity or flavor intensity) are returned as unavailable rather than silently treated as measured data. Parsing is deterministic and its result is returned so users can inspect what was understood; it does not claim general language understanding.

The ranking layer applies hard dietary, requested-protein, ingredient-exclusion, and budget-ceiling filters before calculating a weighted score. Default configurable weights are taste fit 0.28, current-intent fit 0.42, personal history 0.13, price fit 0.10, and novelty 0.07. The service normalizes weights from optional `TASTEAI_RANKING_WEIGHTS` JSON. Requested taste attributes, protein, adventurousness, cuisine, and liked-ingredient signals are combined using separately configurable, normalized `TASTEAI_INTENT_WEIGHTS`. Supported keys and defaults are provided in `tasteai/.env.example`. Requested attributes are ranking signals, not guarantees about preparation; explanations cite only attributes actually present in the catalog, budget, dietary label, or account history. Missing or invalid catalog attributes are omitted from component scores and explanations; final match scores normalize only across available components and do not invent neutral component evidence. Taste DNA records explicit and feedback-inferred feature values; a migration preserves non-default values from existing profiles as `legacy` rather than discarding them. Rating updates adjust inferred/legacy dimensions and preserve explicit values. No popularity, review quality, distance, availability, or weather signal is scored without source data. Exploration shifts weight toward novelty but does not bypass dietary exclusions. Guest recommendations use an ephemeral neutral profile and do not create a user or persist search/feedback.

Ratings and meal feedback update taste dimensions gradually. Explicit and permanent exclusions take precedence over inferred preferences. Temporary memories expire. Rejections are recorded and suppress repeated recommendations without being confused with a permanent exclusion.

## Review intelligence

The MVP accepts review excerpts supplied in the request and extracts aspect mentions with deterministic phrase matching. The authenticated web view accepts one excerpt per line, displays exact supporting excerpts and mention counts, and makes no claim when no supported phrase is found. The request is not persisted. No demo review rows are seeded; review summaries, sentiment, restaurant ratings, and hygiene signals are unavailable until licensed, attributable text is provided. The Places adapter can map transient provider reviews/photos with author/source attribution, but the app currently does not fetch or display them. A future LLM-based analyzer must cite retrieved source excerpts and must not add unsupported claims.

## Security, privacy, and operations

Passwords use PBKDF2-HMAC-SHA256 with random salts. Access tokens are signed JWTs and expire; production startup requires `TASTEAI_SECRET_KEY`. Development may generate an ephemeral key when none is configured, which invalidates tokens after restart. `.env` files and SQLite databases are ignored by Git. Request logs record method, route, status, and latency without request bodies, tokens, precise location, or personal profile data. Provider logs contain provider, operation, outcome, duration, result count, and sanitized error category only. `GOOGLE_PLACES_API_KEY` is backend-only and is never included in status responses or logs. The current Alembic migration chain includes Taste DNA provenance; no location-provider migration is needed because provider content is request-time only. The local service is not hardened for public production deployment; distributed rate limiting, refresh/revocation, CSRF policy for browser sessions, backups, and operational metrics must be completed before that use.

## MVP scope and roadmap

**Implemented MVP scope:** guest-first local demo catalog search and browse, authenticated personal profiles with feature provenance, deterministic intent parsing and explainable ranking, offline corpus evaluation, budget/exploration inputs, session-only guest feedback, account-persisted feedback and history, memory capture, caller-supplied review excerpt analysis, responsive web UI, group dining recommendations, consent-confirmed taste transfer, and optional explicitly gated/attributed Google Places and OpenStreetMap place lookup.

**Implemented foundation and API:** offline Schema.org JSON-LD extraction, a provider-neutral evidence store, a separate exact-normalized-dish search API, and a provider-neutral restaurant-discovery API. The evidence store includes restaurant identity candidates, manual source approval, source/document/item provenance, freshness, conflict detection, variant/range-price semantics, and a read-only API. The search and discovery APIs use only approved, supported/verified evidence, apply hard constraints before deterministic scoring, and return provenance-grounded explanations; discovery attaches evidence only for confidently matched existing identities. No data source is configured or approved; no public ingestion/approval route, fetcher, PDF parser, or evidence-search UI exists.

**Next product increments:** select and legally review an approved menu-data provider; safe, policy-compliant source retrieval and operator ingestion; provide an explicit confirmation workflow for unresolved candidate identities; add a user-facing evidence-search experience; policy-compliant display of externally sourced review/photo evidence; evidence-grounded review ingestion; photo recognition with confidence; travel/Taste Passport; background analysis; refresh-token lifecycle and app-wide/distributed rate limiting; and a React Native client. These features are not represented as working functionality until implemented and tested.

## Acceptance criteria

1. A new user can register, log in, edit Taste DNA, search and rank the clearly labeled demo catalog, save/reject/rate a dish, and see persisted history.
2. Hard exclusions, dietary restrictions, and a requested budget are applied before recommendation ranking.
3. Each match exposes its heuristic component scores and explanations based only on available profile/catalog/history data.
4. No restaurant/review data is fabricated. External place content is shown only when a provider is explicitly enabled and is accompanied by its required attribution.
5. The standalone service starts locally without credentials or external API calls; the existing Team Task Manager remains available through its existing entry point.
6. Tests cover intent parsing, filters, score/explanation behavior, feedback learning, memory expiry, and authenticated API flows.
7. Group recommendations exclude any venue without an eligible listed dish for every attendee and fail closed for unsupported dietary restrictions.
8. Taste transfer requires consent confirmation, compares only caller-supplied friend features, never reads another account's profile, and does not persist the friend snapshot.
9. Review insights display only caller-supplied excerpts and do not persist them or create evidence-free claims.
10. A guest can browse/search without registering, see an evidence-grounded best match before alternatives, and use temporary session feedback; account-only data remains inaccessible without authentication.
11. Provider readiness is reported without exposing credentials; the default provider is fictional demo and makes no external request.
12. Area/nearby lookup requires an explicit user action, never requests permission in demo mode, does not persist the input/results, and presents the selected provider's content with attribution when enabled.
13. Offline evaluation uses only a labeled synthetic corpus and fails if expected parser, hard-filter, top-match-attribute, or explanation-grounding checks regress.
14. Places search and details normalize provider data without persistence, request bounded minimal fields, and report provenance for known versus unknown restaurant signals.
15. Real restaurant candidates never inherit dishes or prices from the fictional demo catalog; without approved menu evidence, no dish recommendation is produced.
16. The optional Google live smoke test is skipped unless explicitly enabled alongside provider credentials, paid-use opt-in, and policy URLs; ordinary tests make no network calls.
17. The internal structured-data parser extracts explicit menu fields from bounded caller-supplied HTML, preserves source/capture/hash/parser provenance, never fetches or stores raw source documents, and does not invent availability.
18. Optional OSM lookup requires explicit provider selection and a descriptive User-Agent, is paced and bounded, carries OSM attribution, and returns no inferred menu/review facts.
19. Menu-source evidence is accepted only from a manually approved source; pending/revoked sources are neither ingested nor returned, and the API exposes no public ingestion or approval operation.
20. Restaurant identity resolution preserves name-only ambiguity, while fresh conflicting price or availability claims are surfaced and excluded from the future dish-evidence lookup.
21. Missing prices, currency, and current availability remain unknown; named variants and explicit price ranges remain distinct, and stale evidence is labeled and excluded from dish lookup.
22. The menu-evidence migration upgrades and downgrades cleanly; evidence remains isolated from live-place recommendations unless a compliant source and explicit identity integration are available.
23. The separate evidence-search endpoint uses only approved, supported/verified stored records and exact normalized dish-name matching; it never falls back to fictional demo dishes, provider search text, or external requests.
24. Evidence search applies budget, dietary, stale, availability, and conflict constraints before ranking; unknown price/currency or unverified dietary/ingredient suitability cannot satisfy a hard constraint.
25. Evidence-search scores are deterministic, normalize only over available signals, preserve variants, and include explanations/provenance traceable to stored claims; unverified availability is never presented as current.
26. Restaurant discovery accepts only an explicit area query or complete coordinate pair, respects the requested radius without fallback expansion, applies optional cuisine filtering locally, and does not persist location/results.
27. Restaurant discovery preserves unresolved/conflicting identities and attaches approved menu evidence only to confidently resolved existing identities; it never creates identity links or fabricates menu facts.

## 27. Local MVP delivery and release gates

The missing closing section is completed as a delivery and readiness contract, rather than assuming an unprovided feature or data source. The current deliverable is a local-first web MVP, not a production launch. A release candidate is acceptable when the acceptance criteria above pass, the service starts with the documented setup, feedback persists between restarts, mobile layouts remain usable, and an unauthenticated user cannot access another account's data. The offline evaluation corpus currently contains 110 synthetic requests; latest results are 165/165 parser expectations, 179/179 hard-constraint checks, 8/8 top-match attribute checks, and 494/494 explanation-grounding checks for the seven-item fictional catalog. These are regression metrics, not evidence of real-world recommendation quality. The task-manager service remains independently runnable.

Do not publish or deploy this service until its operator has configured private signing keys, selected a real database, run reviewed schema migrations, added backups and rate limiting, completed a privacy/security review, published Terms of Use and a Privacy Policy, and connected only an approved catalog provider. Paid Places requests remain disabled unless the operator explicitly opts in; do not enable them without the user's explicit approval. Follow Google's current content-retention, attribution, and display policies and the current OSM licensing/endpoint policies before selecting those providers. Do not call a fictional sample dish, restaurant, rating, review, distance, or availability a real-world fact. Any feature listed as a roadmap item remains explicitly unavailable until its implementation and tests are delivered.

## Phase 2 — real restaurant discovery foundation

This phase extends the existing local-first architecture rather than replacing it. The Google adapter uses the official Places API (New) Text Search, Nearby Search, and Place Details endpoints. Current documentation was checked on 2026-10-21: [Text Search](https://developers.google.com/maps/documentation/places/web-service/text-search), [Nearby Search](https://developers.google.com/maps/documentation/places/web-service/nearby-search), [Place Details](https://developers.google.com/maps/documentation/places/web-service/place-details), [usage and billing](https://developers.google.com/maps/documentation/places/web-service/usage-and-billing), and [Places policies](https://developers.google.com/maps/documentation/places/web-service/policies). Field masks determine the highest billable SKU among requested fields, so normal search asks only for ID, display name, address, location, and Google Maps URI. Ratings/price fields and review/photo fields are omitted from that path; review and photo methods use separate masks and are not called by the UI.

The configuration remains default-demo and supports both the existing `TASTEAI_RESTAURANT_PROVIDER` name and the requested `RESTAURANT_PROVIDER` alias. Selecting Google without a key, paid-use opt-in, and HTTPS policy URLs leaves the service running but disables external calls. No key or live credentials are configured in this worktree. The optional `GOOGLE_PLACES_LIVE_TEST=true` test also requires those other settings and is skipped in normal test runs.

Area and coordinate searches are explicit POST actions; geolocation remains one-time and browser permission is not requested in demo/disabled mode. Places responses are transient and not cached or persisted. A process-local guard permits at most 60 provider requests per hour and enforces a two-second cooldown per client; production deployments would need a shared limiter. Search/detail limits, response schemas, Maps links, provider failures, and output logs are constrained. Logs include operation, provider, outcome, duration, count, and sanitized error category; they exclude search text, coordinates, credentials, and upstream response bodies.

Restaurant existence does not establish menu availability. `DemoMenuProvider` contains only fictional demo dishes, while `UnknownMenuProvider` returns `unknown` for external places. Restaurant candidate results include locally parsed food intent and field-level provenance, but return no dish recommendations without legitimate menu evidence. Menu-item price, dietary suitability, and review evidence remain unknown. TasteAI does not mix the demo menu into real-place results, infer a dish from Google's search text, use unrequested ratings for ranking, or imply a real restaurant serves a dish.

Google Maps content appears in a visually separate card with same-card “Google Maps” text attribution (`translate="no"` and minimum 12px text) and links only to Google Maps hosts. OSM cards show linked “© OpenStreetMap contributors” attribution and use OpenStreetMap map links. OSM tags are community-maintained place metadata, not verified menu/service claims. Google review and photo evidence remains unavailable in the UI. The Google adapter retains available reviewer/photographer name, profile/avatar URL, relative-time/original-text, and source-link metadata if those explicit provider methods are called, but no evidence is stored. Public Terms/Privacy URLs must satisfy Google's current requirements before enabling live calls.

No database migration was added because provider content and locations are not persisted. No real Google Places or OSM request, paid API call, scraping, deployment, or real restaurant/menu/review data fabrication occurred. Current verification and any external credential/menu-source blockers are tracked in `BLOCKERS.md`.

## Phase 3 — offline structured menu evidence foundation

`tasteai/structured_data.py` provides a dependency-free bounded parser for caller-supplied HTML containing Schema.org JSON-LD. It returns explicit `MenuItem` names and `Offer` amounts/currencies only, along with a source URL, UTC capture time, SHA-256 content hash, parser version, and sanitized parse warnings. Unsafe source URL forms (non-HTTPS, embedded credentials, nonstandard ports, localhost/private literal IPs) are rejected as provenance inputs. Input bytes, script count, nesting depth, and graph node count are bounded. The parser does not make network requests, follow redirects, store raw documents, verify source ownership/licensing, infer food attributes, or treat a menu listing as current availability. The later Phase 6 search path consumes only persisted evidence and does not connect the parser to external-place discovery.

The source integration blocker remains: no menu provider or permission to retrieve real restaurant data is configured. The next implementation phase should define a reviewed, authorized source and a persistent evidence/identity/freshness contract before joining parsed menu items to external restaurant places. Details are in `DATA_SOURCES.md`, `TASTEAI_ARCHITECTURE.md`, and `BLOCKERS.md`.

## Phase 4 — optional OpenStreetMap place lookup

`tasteai/osm_provider.py` adds an opt-in Nominatim/Overpass provider behind the existing `RestaurantProvider` contract. Manual area search uses Nominatim for one geocode, then issues a bounded Overpass query; nearby lookup sends request-time coordinates only to Overpass. Results normalize mapped OSM place tags and preserve linked “© OpenStreetMap contributors” attribution. Restaurant detail identifiers are constrained to OSM node/way/relation IDs. This provider does not produce review, photo, rating, dish, item-price, or availability evidence.

Selection remains `TASTEAI_RESTAURANT_PROVIDER=osm` with an operator-configured descriptive `TASTEAI_OSM_USER_AGENT`; default configuration remains fictional demo. Endpoints are HTTPS-validated and configurable. Requests are paced at least one second apart per process, bounded by a five-second timeout, 1 MB response, ten result maximum, and no redirect/retry. Provider output and location input are transient and are not persisted. HTTPX informational request logs are suppressed so Nominatim query text is not logged. Tests use mocked transports and do not call public endpoints. Public OSM use is not production-approved; operators must recheck current Nominatim/Overpass terms and attribution requirements before enabling it.

## Phase 5 — trustworthy real-menu evidence foundation

This additive phase introduces provider-neutral restaurant identity and menu-evidence records without changing the fictional demo catalog or enabling real-world discovery. Alembic revision `a418e1bd3107` adds restaurant identities/provider links, menu sources with pending/approved/revoked authorization state, captured-document provenance, and normalized item facts. The service persists normalized data only after an internal operator approval records an approver and authorization reference. This is an auditable local assertion, not automated proof of source rights; no source is approved or configured in a fresh installation.

The existing caller-supplied Schema.org HTML adapter now feeds this evidence contract without making network requests or retaining raw HTML. Explicit fields remain distinct from unknowns; prices support currency, named variants and ranges; menu listing does not imply current availability. Source TTL is configurable with `TASTEAI_MENU_EVIDENCE_TTL_SECONDS` (default 7 days; 60 seconds to 1 year). Reads surface stale evidence and conflicts between fresh approved sources. Exact provider IDs or corroborating identity signals may resolve an identity; a name-only match remains possible and is never merged automatically. Unicode dish/restaurant names are retained during normalization.

`GET /api/v1/menu-evidence/restaurants/{identity_id}` is read-only and paginated. There is deliberately no public identity creation, source approval, upload, arbitrary URL retrieval, or ingestion route. Provenance URLs require HTTPS and exclude credentials, query parameters, and fragments. No real restaurant/menu/review data was ingested, no external request or paid service was used, and the ordinary fictional-demo flow remains unchanged.

## Phase 6 — evidence-driven dish discovery

`tasteai/evidence_discovery.py` and `POST /api/v1/food/evidence-search` add a separate provider-agnostic path for exact dish-name lookup over approved persisted menu evidence. It does not alter the fictional `/food/search` or `/food/recommend` paths and does not merge evidence with transient Google/OSM places. Candidate variants remain separate; fresh price or explicit availability conflicts exclude the affected variant; only an explicit fresh INR price can satisfy a hard INR budget. Dietary requests require explicit dietary labels, and ingredient/protein exclusions fail closed because the current schema does not contain a complete verified ingredient list.

Ranking is deterministic and centrally configurable through `TASTEAI_EVIDENCE_RANKING_WEIGHTS`; available components are exact match, evidence confidence, freshness, price fit, preferred-cuisine fit when authenticated profile use is requested, and explicit availability confidence. Missing taste/price/availability signals are omitted. Freshness defaults to exclusion; the optional `TASTEAI_EVIDENCE_STALE_POLICY=downgrade` permits clearly marked stale results only without a budget ceiling and never upgrades stale availability. The endpoint is API-only, the evidence store remains empty by default, and no real source, paid service, external request, or fabricated restaurant data was introduced.

## Phase 7 — provider-neutral restaurant discovery

`POST /api/v1/restaurants/discover` layers bounded discovery over the existing restaurant provider contract and OSM location resolver. Inputs are limited to one explicit text query or a full coordinate pair, a 100–50000 meter radius, up to ten results, and optional cuisine, dish, budget, and authenticated taste-profile filters. OSM queries are resolved with Nominatim; location-resolution metadata includes a caveat that provider importance is not an accuracy probability. The provider-neutral path retains text search for adapters without a separate geocoder. Search results are filtered locally for cuisine and deduplicated only when the existing canonical identity resolver returns a confident identity; provider IDs and original provider records remain available in the response.

Identity matching and evidence access are read-only. Discovery never creates canonical identities/provider links, never merges name-only or contradictory candidates, does not persist location/results, and never broadens the requested radius. Existing exact-dish evidence search receives an allowlist of confidently resolved identities only. Attached evidence must come from approved sources and still does not imply current availability; unresolved candidates receive an explicit identity-unresolved result, and candidates without matching approved evidence state that the dish could not be verified. The fictional demo search/recommendation flow is unchanged, and discovery does not fabricate menu, review, price, or availability facts.

API, OSM, identity, evidence, and no-fabrication tests use deterministic fixtures and mocked transports. The live Google test remains separately opt-in and may be billable; no live OSM or Google request was made for this phase. No UI, migration, paid service, public deployment, or real restaurant/menu/review dataset was added.
