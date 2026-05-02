# Team Task Manager

This is my full-stack Team Task Manager assignment project. Users can create projects, add members, assign tasks, update task status, and check dashboard counts. The project also includes Admin/Member role-based access.

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
