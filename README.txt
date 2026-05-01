Team Task Manager

Live URL:
Add your Railway public URL here after deployment.

GitHub Repository:
Add your GitHub repository URL here after pushing the code.

Overview:
Team Task Manager is a full-stack web application for creating projects, adding team members, assigning tasks, and tracking progress with Admin and Member access levels.

Features:
- Signup and login with JWT authentication.
- Project creation, where the creator automatically becomes Admin.
- Admins can add or remove project members.
- Admins can create, assign, update, and delete tasks.
- Members can view assigned projects and update only their assigned tasks.
- Dashboard metrics for total tasks, status counts, tasks per user, and overdue tasks.
- PostgreSQL database with relationships between users, projects, memberships, and tasks.

Tech Stack:
- Frontend: HTML, CSS, JavaScript
- Backend: Node.js, Express.js
- Database: PostgreSQL
- Authentication: JWT and bcrypt password hashing
- Deployment target: Railway

Local Setup:
1. Install Node.js 18 or newer.
2. Create a PostgreSQL database.
3. Copy .env.example to .env.
4. Update DATABASE_URL and JWT_SECRET in .env.
5. Install dependencies:
   npm install
6. Start the application:
   npm start
7. Open:
   http://localhost:3000

Environment Variables:
PORT=3000
DATABASE_URL=postgresql://postgres:password@localhost:5432/team_task_manager
JWT_SECRET=replace-this-with-a-long-random-secret

Railway Deployment:
1. Push this project to GitHub.
2. Create a new Railway project from the GitHub repository.
3. Add a Railway PostgreSQL database service.
4. In the web service variables, add:
   DATABASE_URL from the Railway PostgreSQL service
   JWT_SECRET as a long random secret value
   NODE_ENV=production
5. Railway will run npm install during build and npm start during deployment.
6. Open the generated Railway domain and test signup, project creation, member addition, task creation, and status updates.

API Summary:
- POST /api/auth/signup
- POST /api/auth/login
- GET /api/projects
- POST /api/projects
- GET /api/projects/:projectId/members
- POST /api/projects/:projectId/members
- DELETE /api/projects/:projectId/members/:userId
- GET /api/projects/:projectId/tasks
- POST /api/projects/:projectId/tasks
- PATCH /api/projects/:projectId/tasks/:taskId
- DELETE /api/projects/:projectId/tasks/:taskId
- GET /api/projects/:projectId/dashboard

Demo Video Checklist:
1. Show signup and login.
2. Create a project.
3. Add another registered user as Member.
4. Create and assign tasks with due date and priority.
5. Update task status.
6. Show dashboard counts and overdue tasks.
7. Briefly explain Admin versus Member permissions.

Notes:
- The database tables are created automatically on server startup.
- A user must sign up before an Admin can add that user's email to a project.
- Members only see and update tasks assigned to them.
