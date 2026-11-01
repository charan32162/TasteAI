# TasteAI deployment notes

TasteAI is a local-first FastAPI modular monolith. This guide provides a reproducible container build and an operator-controlled deployment sequence. It does not claim that a public production service has been provisioned or load/security tested.

## Compose with PostgreSQL

The tracked `compose.yaml` runs only the TasteAI app and PostgreSQL. The database is reachable only on the internal Compose network, both services have health checks, and provider gates are disabled by default. No credentials are committed; supply them through the shell or a private Compose environment file that is not checked in.

From the repository root, set a random signing key and a database password for the current shell:

```powershell
$env:TASTEAI_SECRET_KEY = (python -c "import secrets; print(secrets.token_urlsafe(48))")
$env:POSTGRES_PASSWORD = (python -c "import secrets; print(secrets.token_urlsafe(24))")
docker compose up -d db
docker compose run --rm app python -m alembic -c tasteai/alembic.ini upgrade head
docker compose up -d app
docker compose ps
```

Verify the actual API health endpoint and a deterministic offline request:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/v1/health
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/food/search `
  -ContentType 'application/json' `
  -Body '{"query":"spicy chicken food under ₹350","limit":3}'
```

The named `tasteai-postgres-data` volume preserves PostgreSQL state across app restarts and `docker compose down`; remove it only when intentionally resetting the database with `docker compose down -v`. The named menu-photo volume is private to the container user and is not required for the deterministic demo flow. Run `docker compose logs --no-color app db` during diagnosis, and never put secrets or raw prompts in logs or command history.

To apply reviewed schema changes, stop the app if necessary, run `docker compose run --rm app python -m alembic -c tasteai/alembic.ini upgrade head`, then restart the app. The container has automatic schema creation disabled, so startup never silently mutates a PostgreSQL schema.

## Build

Run from the repository root:

```sh
docker build -f Dockerfile.tasteai -t tasteai:local .
```

The image copies only the TasteAI application, migrations, requirements, and static web assets. It runs as a non-root user, uses SQLite under `/data` by default, stores menu photos under the user's private home directory, and has a local health check. Bind persistent storage for `/data` and `/home/tasteai/.tasteai/private-menu-photos`.

## First start

Create a private signing key and a persistent volume for database/photo data. Do not place the key in a tracked file or image layer.

```sh
export TASTEAI_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
docker volume create tasteai-data
docker volume create tasteai-menu-photos
docker run --rm \
  -e TASTEAI_SECRET_KEY \
  -e TASTEAI_RESTAURANT_PROVIDER=demo \
  -v tasteai-data:/data \
  -v tasteai-menu-photos:/home/tasteai/.tasteai/private-menu-photos \
  tasteai:local python -m alembic -c tasteai/alembic.ini upgrade head
```

Keep that same secret in the current shell or inject it from a secret manager. Use the same secret and volumes when starting the service:

```sh
docker run -d --name tasteai \
  -e TASTEAI_SECRET_KEY="$TASTEAI_SECRET_KEY" \
  -e TASTEAI_RESTAURANT_PROVIDER=demo \
  -p 127.0.0.1:8000:8000 \
  -v tasteai-data:/data \
  -v tasteai-menu-photos:/home/tasteai/.tasteai/private-menu-photos \
  tasteai:local
```

The shell variable in this example must be populated from a secret manager or generated securely before use. Do not paste credentials into shell history. For multiple instances, configure a supported shared database and external private object storage; this image's SQLite/local-photo defaults are for a single instance only.

## Required deployment work before public exposure

- Use PostgreSQL with backups, restore testing, TLS, and reviewed migration operations.
- Add shared rate limits and abuse protection; current place lookup limits are process-local.
- Configure a reverse proxy, HTTPS, trusted-host policy, request-size limits, and operational alerting.
- Set and test account deletion/retention procedures, private photo retention, and access controls.
- Configure only approved restaurant/menu providers. Google requires its policy URLs, backend key, and explicit paid-use opt-in. OSM use must comply with current service policies.
- Do not enable menu-photo routes until trusted reviewer IDs are configured and storage is private and durable.
- Perform a deployment-specific security review and test backups, health checks, and migration rollback procedures.

## CI

`.github/workflows/tasteai.yml` runs offline tests, the deterministic evaluator, Python/JavaScript checks, migration upgrade/downgrade/re-upgrade and model parity, and a container build. It uses no provider credentials and does not run the optional live Places smoke test.
