# TasteAI

TasteAI is a local-first FastAPI food-intelligence application for personalized, explainable recommendations. It combines deterministic intent parsing, hard dietary and budget constraints, Taste DNA personalization, approved menu evidence, bounded RAG retrieval, grounded explanations, and a typed read-only agent flow.

The default catalog is explicitly fictional and all external providers are disabled unless an operator opts in through private environment configuration. The project does not claim live restaurant menus, real OCR ingestion, or production-scale infrastructure by default.

## What is implemented

- FastAPI JSON API under `/api/v1` with a browser client served from `tasteai/web`.
- Deterministic recommendation modes: automatic, My Usual, Something Different, and Surprise Me.
- Taste DNA with explicit-preference protection, bounded feedback learning, food memories, and history.
- Reviewer-gated restaurant identity and approved menu-evidence workflows with provenance, freshness, conflicts, canonical dish matching, and fail-closed constraints.
- Optional structured OpenAI LLM and embedding adapters, each separately gated, bounded, validated, and subordinate to deterministic authority.
- Approved-evidence-only RAG with lexical/vector fallback metadata, citations, prompt-injection handling, and grounded claim validation.
- Bounded internal agent orchestration with typed allowlisted tools, time/call/result limits, duplicate prevention, sanitized errors, and audit metadata.
- Request correlation, safe structured logging, process-local rate limiting, `429` responses, and abuse-size limits.

## Local development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r tasteai\requirements-dev.txt
$env:TASTEAI_SECRET_KEY = (python -c "import secrets; print(secrets.token_urlsafe(48))")
python -m uvicorn tasteai.app:app --reload --port 8000
```

Open `http://127.0.0.1:8000`; OpenAPI documentation is available at `/docs`. SQLite is the zero-configuration local database. Use `tasteai/.env.example` as the configuration reference and never commit private environment files or credentials.

## PostgreSQL containers

The repository includes a maintained non-root `Dockerfile.tasteai` and a minimal app-plus-PostgreSQL `compose.yaml`. Compose keeps PostgreSQL on the internal service network, uses named volumes, disables external provider gates, and requires secrets from the shell or another private environment source. Apply reviewed migrations explicitly before starting the app:

```powershell
$env:TASTEAI_SECRET_KEY = (python -c "import secrets; print(secrets.token_urlsafe(48))")
$env:POSTGRES_PASSWORD = (python -c "import secrets; print(secrets.token_urlsafe(24))")
docker compose up -d db
docker compose run --rm app python -m alembic -c tasteai/alembic.ini upgrade head
docker compose up -d app
```

See `DEPLOYMENT.md` for health checks, deterministic smoke requests, persistence, and operational limitations.

## Verification

```powershell
python -m unittest discover -s tasteai\tests -v
python -m tasteai.evaluation.run --strict
python -m compileall -q tasteai
node --check tasteai\web\app.js
python -m alembic -c tasteai\alembic.ini upgrade head
python -m alembic -c tasteai\alembic.ini check
git diff --check
```

GitHub Actions runs the offline suite, strict evaluator, syntax checks, SQLite and PostgreSQL migration checks, Compose validation, and the Docker build. The workflow performs CI verification only; it is not a deployment pipeline.

## Evidence and provider boundaries

No source is approved by default. Provider candidates are not menu evidence, fictional catalog data is labeled, and unknown or stale prices, dietary suitability, availability, conflicts, and identity matches fail closed where required. OpenAI, Google Places, OpenStreetMap, OCR, and other paid or external calls remain disabled unless explicitly configured and authorized.

Read `tasteai/README.md`, `API.md`, `TASTEAI_ARCHITECTURE.md`, `TASTEAI_SPEC.md`, `DATA_SOURCES.md`, `SECURITY.md`, `EVALUATION.md`, `DEVELOPMENT.md`, `DEMO.md`, `RUNBOOK.md`, and `BLOCKERS.md` for detailed behavior, limitations, and operator procedures.
