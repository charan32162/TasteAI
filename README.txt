Team Task Manager

Live URL:
Add your Railway public URL here after deployment.

GitHub Repository:
Add your GitHub repository URL here after pushing the code.

Overview:
This is a full-stack task management application built for the assignment. It allows users to sign up, create projects, add team members, assign tasks, and track the progress of work inside each project.

I kept the app simple and focused on the required features: authentication, project/team management, task assignment, dashboard counts, database relationships, and role-based access.

Features:
- User signup and login.
- Passwords are hashed before saving.
- JWT is used for protected API routes.
- A user who creates a project becomes the Admin of that project.
- Admin can add/remove project members.
- Admin can create, assign, update, and delete tasks.
- Member can view projects they are part of.
- Member can update only their own assigned tasks.
- Dashboard shows total tasks, tasks by status, tasks per user, and overdue tasks.

Tech Stack:
- Frontend: HTML, CSS, JavaScript
- Backend: Node.js, Express.js
- Database: PostgreSQL
- Authentication: JWT and bcrypt password hashing
- Deployment target: Railway

Database Tables:
- users
- projects
- project_members
- tasks

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
- DELETE /api/projects/:projectId
- GET /api/projects/:projectId/members
- POST /api/projects/:projectId/members
- DELETE /api/projects/:projectId/members/:userId
- GET /api/projects/:projectId/tasks
- POST /api/projects/:projectId/tasks
- PATCH /api/projects/:projectId/tasks/:taskId
- DELETE /api/projects/:projectId/tasks/:taskId
- GET /api/projects/:projectId/dashboard

Demo Video Checklist:
In the demo video I will show:
1. Signup and login.
2. Creating a project.
3. Adding a second user as a project member.
4. Creating a task with due date and priority.
5. Assigning the task to a user.
6. Updating task status.
7. Dashboard values.
8. Difference between Admin and Member access.

Notes:
- The database tables are created automatically on server startup.
- A user must sign up before an Admin can add that user's email to a project.
- Members only see and update tasks assigned to them.
- I used a single Express server to serve both the REST APIs and the frontend so deployment on Railway is simpler.

Possible Improvements:
- Add project invite links.
- Add task comments or attachments.
- Add email notifications for overdue tasks.
- Add edit forms for project details.
