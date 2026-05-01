# Team Task Manager

A full-stack project management app with JWT authentication, PostgreSQL persistence, project teams, task assignment, status tracking, dashboards, and Admin/Member role-based access.

## Run Locally

```bash
npm install
cp .env.example .env
npm start
```

Update `.env` with a valid PostgreSQL `DATABASE_URL` and `JWT_SECRET`, then open `http://localhost:3000`.

## Deploy On Railway

1. Push this repository to GitHub.
2. Create a Railway project from the GitHub repo.
3. Add Railway PostgreSQL.
4. Set `DATABASE_URL`, `JWT_SECRET`, and `NODE_ENV=production`.
5. Railway installs dependencies and runs `npm start`.

The server creates required tables automatically at startup.
