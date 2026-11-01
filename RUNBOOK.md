# TasteAI operator runbook

## Local health

- `GET /api/v1/health` reports application health and selected place-provider readiness; it does not make a provider request.
- `GET /api/v1/providers/status` explains whether the selected place provider is configured without returning credentials.
- `/docs` exposes the local OpenAPI interface.

## Routine offline checks

```sh
python -m unittest discover -s tasteai/tests -v
python -m tasteai.evaluation.run --strict
python -m compileall -q tasteai
node --check tasteai/web/app.js
git diff --check
```

## Database changes

1. Back up the database and verify the backup can be restored.
2. Review the Alembic revision and its downgrade.
3. Apply `python -m alembic -c tasteai/alembic.ini upgrade head` during a controlled maintenance window.
4. Check `python -m alembic -c tasteai/alembic.ini current` and `python -m alembic -c tasteai/alembic.ini check`.
5. Never use `TASTEAI_AUTO_CREATE_SCHEMA=true` as a production migration strategy.

## Provider incidents

- Leave providers disabled when configuration is incomplete. Do not retry quota or timeout failures in loops.
- Google calls may be billable. Inspect provider status and operator configuration before enabling a live call; never print credentials.
- OSM endpoints are public shared services. Respect current usage policies and the configured descriptive User-Agent.
- Provider candidates are not menu evidence. Do not substitute the fictional demo catalog for a real restaurant's menu.

## Menu-photo review

- Configure only trusted account IDs in `TASTEAI_MENU_PHOTO_REVIEWER_USER_IDS`.
- Keep the private photo directory backed up and access-restricted. The API intentionally has no image-serving route.
- OCR is not configured. Uploads are stored without fabricated extraction. Manually entered proposals remain pending until reviewed; commit only accepted proposals after checking the source image.
- If an upload or extraction fails, preserve the source file and inspect sanitized application logs; do not copy image contents or secrets into logs.

## Recovery and privacy

- Stop writes before restoring a database backup.
- Keep database and photo backups access-controlled and encrypted at rest by the hosting platform.
- Use the existing account-scoped deletion endpoints only where supported; there is not yet a complete self-service account deletion/retention workflow.
- After key exposure, rotate the signing key and require users to sign in again; JWTs have a 12-hour lifetime.
