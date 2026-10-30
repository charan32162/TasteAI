# Team Task Manager

This is my full-stack Team Task Manager assignment project. Users can create projects, add members, assign tasks, update task status, and check dashboard counts. The project also includes Admin/Member role-based access.

Live app: https://team-task-manager-production-e47d.up.railway.app

GitHub repo: https://github.com/charan32162/team-task-manager

## Features

- Signup and login with JWT authentication
- Password hashing using bcrypt
- Project creation, deletion, and team members
- Admin and Member roles
- Task creation, assignment, priority, due date, and status
- Dashboard for total tasks, status counts, tasks per user, and overdue tasks
- PostgreSQL database

## Run Locally

```bash
npm install
cp .env.example .env
npm start
```

Update `.env` with a valid PostgreSQL `DATABASE_URL` and `JWT_SECRET`, then open `http://localhost:3000`.

## Main Tables

- `users`
- `projects`
- `project_members`
- `tasks`

## Deploy On Railway

1. Push this repository to GitHub.
2. Create a Railway project from the GitHub repo.
3. Add Railway PostgreSQL.
4. Set `DATABASE_URL`, `JWT_SECRET`, and `NODE_ENV=production`.
5. Railway installs dependencies and runs `npm start`.

The server creates required tables automatically at startup.

## Notes

- The project creator is automatically added as Admin.
- A user needs to sign up first before an Admin can add them to a project.
- Members can update only the tasks assigned to them.

## TasteAI (standalone local prototype)

TasteAI is being developed as a separate FastAPI application so the existing task manager remains unchanged. See [`TASTEAI_SPEC.md`](TASTEAI_SPEC.md), [`TASTEAI_ARCHITECTURE.md`](TASTEAI_ARCHITECTURE.md), [`DATA_SOURCES.md`](DATA_SOURCES.md), and [`tasteai/README.md`](tasteai/README.md) for scope, source status, architecture, setup, and tests. API, security, evaluation, and development notes are in [`API.md`](API.md), [`SECURITY.md`](SECURITY.md), [`EVALUATION.md`](EVALUATION.md), and [`DEVELOPMENT.md`](DEVELOPMENT.md). Deployment constraints, local demo steps, and operator procedures are in [`DEPLOYMENT.md`](DEPLOYMENT.md), [`DEMO.md`](DEMO.md), and [`RUNBOOK.md`](RUNBOOK.md); the TasteAI-only image uses [`Dockerfile.tasteai`](Dockerfile.tasteai), and offline checks are defined in [`.github/workflows/tasteai.yml`](.github/workflows/tasteai.yml). Run TasteAI with `python -m uvicorn tasteai.app:app --reload --port 8000`; the default catalog is fictional demo content. Optional Google Places and OpenStreetMap lookup are disabled in the tracked example configuration. This worktree's FastAPI app loads its ignored local `tasteai/.env`; the current app status is Google Places enabled. Google requires explicit paid-use opt-in, server-side credentials, and policy URLs, and calls may incur charges. The separate exact-dish menu-evidence search API and provider-neutral `POST /api/v1/restaurants/discover` endpoint are implemented. Discovery resolves an area to a point, then locally enforces the requested radius on provider results and reports exclusions; it can attach approved evidence only to confidently matched existing identities. No real menu source is approved or configured by default, and unresolved candidates do not receive evidence.
