# TasteAI architecture

**Status:** local-first modular monolith; describes the code currently in this repository
**Updated:** 2026-10-22

## Runtime and boundaries

TasteAI is a standalone FastAPI application in `tasteai/`. It serves its own static web client and `/api/v1` JSON endpoints. Local development uses SQLite and Alembic migrations. SQLAlchemy supports PostgreSQL URLs, and the repository includes a locally validated PostgreSQL Compose workflow.

```text
Browser (tasteai/web)
        │ same-origin HTTP
        ▼
FastAPI routes (tasteai/app.py)
  ├── deterministic recommendation engine (tasteai/engine.py)
  ├── provider contracts and fictional catalog (tasteai/providers.py)
  ├── optional Google place lookup (tasteai/location_providers.py)
  ├── optional OSM place lookup (tasteai/osm_provider.py)
  ├── provider-neutral restaurant discovery (tasteai/restaurant_discovery.py)
  ├── offline Schema.org extractor (tasteai/structured_data.py)
  ├── provider-neutral menu evidence service (tasteai/menu_evidence.py)
  ├── evidence-only deterministic discovery (tasteai/evidence_discovery.py)
  └── SQLAlchemy models/session (tasteai/models.py, database.py)
                    │
                    ▼
          SQLite by default / PostgreSQL by configuration
```

## Modules

| Module | Responsibility | Current boundary |
| --- | --- | --- |
| `app.py` | Startup validation/migration/seeding, request middleware, REST endpoints, authentication dependencies, and provider orchestration | Modular monolith entry point; some route/domain logic remains colocated |
| `database.py`, `models.py`, `migrations/` | SQLAlchemy engine/session, relational entities, and versioned schema | Includes restaurant identities, provider identity links, source authorization records, captured document metadata, and normalized menu-item evidence |
| `schemas.py` | Pydantic request validation and bounds | API inputs, including location and group requests |
| `security.py` | PBKDF2-HMAC-SHA256 password hashing and signed expiring JWTs | One bearer-token model; no refresh/revocation service |
| `engine.py` | Deterministic food-intent parsing, hard filtering, weighted ranking, explanation, feedback learning, memories, group matching, taste transfer, and caller-submitted review excerpt analysis | Remains authoritative for demo-menu ranking; optional LLM hints never override hard constraints |
| `ai_providers.py` | Structured OpenAI Responses adapter, Pydantic output validation, local hash embeddings, optional OpenAI embeddings, and provider configuration gates | External calls are disabled by default; adapters verified with mocked transports only |
| `canonical_dishes.py` | Exact conservative dish normalization, pending alias proposals, approved alias resolution, and provenance-backed evidence links | No fuzzy automatic merges; alias suggestions require review |
| `rag.py` | Approved-evidence chunking/indexing, hybrid lexical/vector retrieval, hard filters, freshness/conflict checks, bounded context, typed claim validation, and citations | Local hash vectors are lexical features, not semantic embeddings; factual sentences are rendered from validated fields |
| `agent.py` | Fixed four-step query-understanding, evidence-retrieval, deterministic-ranking, and explanation state machine | At most four steps and two tool calls; no arbitrary tool execution |
| `menu_photos.py` | Private upload extraction protocol, deterministic test extractor, optional OpenAI Vision OCR, and pending proposal staging | External OCR is opt-in; extracted candidates need human review before becoming evidence |
| `providers.py` | Restaurant/menu protocols plus clearly fictional and unknown menu implementations | Demo dishes are separate from provider restaurant candidates |
| `location_providers.py` | Normalized restaurant-place contract, optional Google Places adapter, safe Maps URLs, field masks, transient review/photo metadata, provider selection, and in-process request limits | Google is disabled unless operator configuration explicitly enables paid calls; OSM is disabled unless selected with a descriptive User-Agent; results are not persisted |
| `osm_provider.py` | Optional Nominatim area geocoding and Overpass restaurant/fast-food lookups; normalizes OSM tags and emits ODbL attribution | User-triggered only, HTTPS endpoints configurable, one-second process-wide request pacing, bounded timeout/response/result count; no live requests made during tests |
| `restaurant_discovery.py` | Bounded provider-neutral discovery, explicit location resolution, geodesic radius filtering, cuisine filtering, conservative identity matching/deduplication, and optional evidence attachment | Read-only; recalculates distance and excludes missing/out-of-radius coordinates; never widens the radius, creates identities/provider links, persists location, or attaches evidence to unresolved candidates |
| `structured_data.py` | Bounded parser for caller-supplied HTML containing Schema.org JSON-LD `MenuItem`/`Offer` data | Offline extraction only; it does not fetch, verify source ownership, write to the database, or assert dish availability |
| `menu_evidence.py` | Provider-neutral evidence records, conservative restaurant identity matching, manual source approval/revocation, ingestion, freshness/conflict evaluation, and read queries | No network fetcher or public ingestion/approval endpoint; current Schema.org adapter parses caller-supplied HTML |
| `evidence_discovery.py` | Exact normalized dish lookup, fail-closed hard constraints, evidence aggregation, deterministic weighted scoring, and grounded explanations | Separate evidence-only path; does not join external place candidates or fictional demo dishes |
| `evaluation/` | Synthetic corpus and deterministic strict evaluator | Regression coverage for the fictional catalog, not real-world recommendation quality |
| `web/` | Responsive same-origin HTML/CSS/JavaScript UI | Guest discovery and authenticated taste, history, saved, group, transfer, and evidence screens |

## Data and request flow

Guest or signed-in food search is validated by Pydantic, parsed locally, filtered for hard constraints, then ranked against the seven-item fictional catalog and, for authenticated users, their Taste DNA/history. Explanations cite only known catalog/profile signals. Feedback and personal records are scoped to the authenticated user. Demo catalog content is seeded as fictional data.

Restaurant place search is a separate explicit user action. Manual area text or one-time coordinates are sent only when the configured restaurant provider is enabled. `POST /api/v1/restaurants/discover` resolves an explicit area before nearby search: Google uses a bounded Places Text Search for an area result, while OSM uses Nominatim. The resolved point is a search center, not an area boundary. TasteAI applies a local geodesic radius check to provider results, removes results with missing/invalid coordinates, computes displayed distance, and orders candidates nearest first; it reports exclusion counts and does not widen the radius. Coordinate inputs are request-only. Cuisine filtering and canonical identity resolution are local and read-only. Google and OSM place results are transient candidates and cannot inherit a demo dish. Optional dish evidence is attached only for confident matches to already-existing canonical identities and comes exclusively from approved menu evidence; without such evidence, the response explicitly says the dish is not verified. Discovery never creates identities/provider links, and menu, dish price, dietary suitability, and review signals remain unknown unless supported by approved stored evidence.

The structured-data parser accepts HTML already supplied by its caller, extracts only explicit menu-item fields and offer prices, and returns a content hash, source URL, UTC capture time, parser version, and warnings. The evidence service can persist its normalized output only for a manually approved source, retaining provenance and freshness/conflict status but not raw HTML. Provider imports cannot mark evidence verified or inferred; a separate trusted manual review operation can verify a captured document. Menu listings do not imply current availability.

`POST /api/v1/food/evidence-search` is an exact-dish discovery route over approved stored evidence. `POST /api/v1/restaurants/discover` may reuse that service with an explicit identity allowlist, so only confidently matched candidates can receive evidence. Both apply budget/dietary/exclusion gates before ranking, keep variants separate, exclude conflicts, and return only score components supported by stored facts. The existing fictional `/food/search` and `/food/recommend` flow is unchanged. No approved source is configured by default, so a fresh installation returns no evidence candidates.

Restaurant identity matching never merges on name alone: exact provider IDs or corroborating identity signals can resolve an identity; name-only results remain possible matches and contradictory signals are returned as conflicts. Source approval is a local operator assertion backed by an approver and authorization reference, not automated verification of legal rights. The read-only `GET /api/v1/menu-evidence/restaurants/{identity_id}` route returns only evidence from approved sources and supports bounded pagination. No source is actually approved/configured by default.

## AI stack status and remaining limits

- Structured query understanding and grounded RAG claim selection are optional behind `TASTEAI_ALLOW_LLM`; external embeddings require the separate `TASTEAI_ALLOW_EXTERNAL_EMBEDDINGS` gate. Provider adapters are verified with mocked HTTP transports only. Live provider calls were not made during this milestone.
- Optional Vision OCR is implemented behind an explicit environment gate. It was verified with mocked transport and never called a live provider. Extracted rows stay pending until reviewer acceptance.
- The vector table stores embeddings and supports hybrid retrieval, but local feature hashing is lexical rather than semantic. PostgreSQL/pgvector and other external vector stores are not configured.
- RAG can ask the optional LLM to select typed `menu_listing` or `listed_price` claims. A deterministic validator rejects unknown evidence IDs and prices not present in evidence; the application renders factual sentences from the validated fields and returns citation IDs. It does not accept free-form LLM factual prose, and it cannot validate arbitrary natural-language entailment.
- Grounded context is bounded to five result records and 6,000 characters and omits source descriptions. Provider failure, invalid selectors, or no supported claims use deterministic evidence-backed wording.
- The agent is a fixed bounded workflow, not autonomous multi-step tool planning. No shell, filesystem, arbitrary URL, or arbitrary SQL tool is exposed.
- The browser has a grounded approved-menu lookup. A clean installation has no approved real menu evidence, so it reports insufficient evidence.
- No Redis, background worker, distributed rate limiter, production observability, production data source, payment, ordering, delivery, or public deployment is configured.

These boundaries describe implemented code and verified local behavior; mock-provider tests do not establish live service compatibility or production readiness.
