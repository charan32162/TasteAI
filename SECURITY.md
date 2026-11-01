# TasteAI security notes

**Status:** local prototype; not approved for public deployment
**Updated:** 2026-10-22

## Current controls

- Passwords use salted PBKDF2-HMAC-SHA256. Bearer tokens are signed JWTs with expiry. Personal reads/writes use the authenticated user identity; friend taste transfer accepts only user-entered values and requires consent.
- Request bodies use bounded Pydantic schemas. The app logs request method/path/status/latency and provider operation metadata without logging request bodies, credentials, precise location, or upstream provider bodies.
- The generic API request limiter keeps at most 4,096 client buckets per process. When full, it prunes expired buckets and rejects requests from previously unseen clients with `429` until a bucket expires; existing clients retain their window state. This is a local memory bound, not a distributed rate limiter.
- Google Places is disabled by default and requires a server-side key, an explicit paid-use opt-in, and HTTPS policy URLs. Field masks, timeouts, result limits, sanitized errors, and process-local throttling reduce accidental data/cost exposure.
- Google place results are request-time only. User-provided nearby coordinates and manual area text are not stored. External place candidates cannot inherit fictional dishes.
- Google Maps links are restricted to expected Maps hosts. The structured-data parser validates HTTPS source URLs, rejects embedded credentials and non-standard ports, bounds HTML bytes, JSON-LD script count, graph depth and nodes, and never performs a network request.
- The optional OSM adapter requires an operator-supplied identifying User-Agent, HTTPS endpoints, no redirects/retries, bounded responses and timeouts, and sanitized provider errors. HTTPX informational request logging is suppressed so manual area text in Nominatim request URLs is not written to application logs. OSM attribution is emitted with a fixed copyright URL.
- The JSON-LD parser extracts only explicit `MenuItem` names and `Offer` amounts/currencies. It does not interpret external content as instructions, infer availability, or retain raw HTML.
- Menu photo uploads and review routes require authentication plus an explicit `TASTEAI_MENU_PHOTO_REVIEWER_USER_IDS` account allowlist (empty by default). Upload bodies are streamed with a byte cap; Pillow verifies JPEG/PNG/WebP content and decoding dimensions; generated opaque keys are stored outside static assets with best-effort restrictive POSIX permissions. On Windows, actual isolation depends on the storage directory's inherited/configured ACLs. Raw photos are not served by an HTTP route, paths and filenames are not accepted from callers, and request bodies are not logged. This allowlist is a prototype reviewer boundary, not a full role-management system.
- Menu photos create the existing `MenuSource` and `MenuDocument` records. The source is approved only by an allowlisted reviewer acting as the capture/authorization attestor. Extraction proposals remain pending and are not evidence-searchable. External OCR/Vision is opt-in; only individually accepted proposals enter existing evidence ingestion. Missing capture time remains unknown and makes evidence stale under the evidence freshness rules.
- Optional grounded RAG sends only the bounded user query and structured fields for up to five eligible evidence records when `TASTEAI_ALLOW_LLM=true` and the provider is configured. It excludes source descriptions, reviews, images, and private filesystem/storage identifiers. Model output is limited to typed claim selectors; the server validates evidence IDs and recorded prices, then renders factual wording from the evidence fields. It cannot assert current availability, ingredients, dietary suitability, or review sentiment through this claim interface.
- Grounded generation context is capped at 6,000 characters and omits source descriptions. Query text is JSON-encoded as data, and provider instructions mark both query and evidence as untrusted. This reduces prompt-injection impact but is not a formal security guarantee.
- Existing source approval and evidence ingestion operations remain internal; no arbitrary URL fetch route exists. The read-only menu evidence API excludes private authorization references and only returns evidence for approved sources.
- Menu provenance URLs must be HTTPS, use the standard port, and exclude credentials, query parameters, and fragments to reduce accidental secret retention. Freshness and source conflicts are surfaced rather than silently resolved; menu listing never means available now.

## Important limitations

- No production security posture is claimed. There is no refresh/revocation lifecycle, shared rate limiter, production database/backups, CSRF design for browser-held credentials, or deployment hardening.
- The structured-data parser does not prove a URL is restaurant-owned, fetch pages, follow redirects, inspect robots.txt, or decide legal permission. Do not turn it into an arbitrary URL fetch endpoint. Manual source approval is not automated legal/ownership verification, and no source is approved by default.
- The LLM has no arbitrary tool execution. Query understanding and typed RAG claim selection are optional; they cannot authorize side effects or control deterministic constraints and ranking. The application does not perform free-form natural-language entailment validation.
- PDFs, website fetching, automated identity creation/merging, and public evidence ingestion are not implemented. Photo bytes retain original metadata (including any EXIF fields) in private local storage; host access and retention must be controlled.

## Review status

A read-only review of the TasteAI application, structured-data parser, and optional OSM integration on 2026-10-21 identified no high-confidence security findings. This is not a penetration test, formal assurance, or production approval.

## Release gate

Before deployment, complete a threat model and security review, use a managed secret store, configure HTTPS/CORS/CSRF/session policy, add shared abuse and cost controls, verify database backups and migrations, establish source authorization/retention rules, and test authorization boundaries and external-input handling in the production topology.
