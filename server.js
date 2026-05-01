require("dotenv").config();

const path = require("path");
const express = require("express");
const cors = require("cors");
const bcrypt = require("bcryptjs");
const jwt = require("jsonwebtoken");
const { Pool } = require("pg");

const app = express();
const PORT = process.env.PORT || 3000;
const JWT_SECRET = process.env.JWT_SECRET || "dev-secret-change-before-deploy";
const DATABASE_URL = process.env.DATABASE_URL;

if (!DATABASE_URL) {
  console.warn("DATABASE_URL is missing. Add PostgreSQL connection details before running the app.");
}

const pool = new Pool({
  connectionString: DATABASE_URL,
  ssl: /sslmode=require/i.test(DATABASE_URL || "") || process.env.PGSSLMODE === "require"
    ? { rejectUnauthorized: false }
    : false
});

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, "public")));

function required(value, field) {
  if (!value || String(value).trim() === "") {
    const error = new Error(`${field} is required`);
    error.status = 400;
    throw error;
  }
  return String(value).trim();
}

function validEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function normalizeStatus(status) {
  const allowed = ["To Do", "In Progress", "Done"];
  if (!allowed.includes(status)) {
    const error = new Error("Status must be To Do, In Progress, or Done");
    error.status = 400;
    throw error;
  }
  return status;
}

function normalizePriority(priority) {
  const allowed = ["Low", "Medium", "High"];
  if (!allowed.includes(priority)) {
    const error = new Error("Priority must be Low, Medium, or High");
    error.status = 400;
    throw error;
  }
  return priority;
}

async function query(sql, params = []) {
  const result = await pool.query(sql, params);
  return result;
}

async function initDb() {
  await query(`
    CREATE TABLE IF NOT EXISTS users (
      id SERIAL PRIMARY KEY,
      name TEXT NOT NULL,
      email TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS projects (
      id SERIAL PRIMARY KEY,
      name TEXT NOT NULL,
      description TEXT DEFAULT '',
      created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS project_members (
      project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      role TEXT NOT NULL CHECK (role IN ('Admin', 'Member')),
      joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      PRIMARY KEY (project_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS tasks (
      id SERIAL PRIMARY KEY,
      project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      title TEXT NOT NULL,
      description TEXT DEFAULT '',
      due_date DATE NOT NULL,
      priority TEXT NOT NULL CHECK (priority IN ('Low', 'Medium', 'High')),
      status TEXT NOT NULL CHECK (status IN ('To Do', 'In Progress', 'Done')) DEFAULT 'To Do',
      assigned_to INTEGER REFERENCES users(id) ON DELETE SET NULL,
      created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);
}

function auth(req, res, next) {
  const header = req.headers.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : null;

  if (!token) {
    return res.status(401).json({ message: "Authentication required" });
  }

  try {
    req.user = jwt.verify(token, JWT_SECRET);
    next();
  } catch {
    res.status(401).json({ message: "Invalid or expired token" });
  }
}

async function getMembership(projectId, userId) {
  const result = await query(
    "SELECT role FROM project_members WHERE project_id = $1 AND user_id = $2",
    [projectId, userId]
  );
  return result.rows[0] || null;
}

async function requireProjectMember(req, res, next) {
  const projectId = Number(req.params.projectId || req.body.projectId);
  if (!Number.isInteger(projectId)) {
    return res.status(400).json({ message: "Valid projectId is required" });
  }

  const membership = await getMembership(projectId, req.user.id);
  if (!membership) {
    return res.status(403).json({ message: "You are not a member of this project" });
  }

  req.projectId = projectId;
  req.membership = membership;
  next();
}

function requireAdmin(req, res, next) {
  if (req.membership.role !== "Admin") {
    return res.status(403).json({ message: "Admin access required" });
  }
  next();
}

app.post("/api/auth/signup", async (req, res, next) => {
  try {
    const name = required(req.body.name, "Name");
    const email = required(req.body.email, "Email").toLowerCase();
    const password = required(req.body.password, "Password");

    if (!validEmail(email)) {
      return res.status(400).json({ message: "Enter a valid email address" });
    }
    if (password.length < 6) {
      return res.status(400).json({ message: "Password must be at least 6 characters" });
    }

    const passwordHash = await bcrypt.hash(password, 10);
    const result = await query(
      "INSERT INTO users (name, email, password_hash) VALUES ($1, $2, $3) RETURNING id, name, email",
      [name, email, passwordHash]
    );
    const user = result.rows[0];
    const token = jwt.sign(user, JWT_SECRET, { expiresIn: "7d" });
    res.status(201).json({ token, user });
  } catch (error) {
    if (error.code === "23505") {
      return res.status(409).json({ message: "Email is already registered" });
    }
    next(error);
  }
});

app.post("/api/auth/login", async (req, res, next) => {
  try {
    const email = required(req.body.email, "Email").toLowerCase();
    const password = required(req.body.password, "Password");
    const result = await query("SELECT * FROM users WHERE email = $1", [email]);
    const user = result.rows[0];

    if (!user || !(await bcrypt.compare(password, user.password_hash))) {
      return res.status(401).json({ message: "Invalid email or password" });
    }

    const safeUser = { id: user.id, name: user.name, email: user.email };
    const token = jwt.sign(safeUser, JWT_SECRET, { expiresIn: "7d" });
    res.json({ token, user: safeUser });
  } catch (error) {
    next(error);
  }
});

app.get("/api/me", auth, async (req, res) => {
  res.json({ user: req.user });
});

app.get("/api/users", auth, async (req, res, next) => {
  try {
    const result = await query("SELECT id, name, email FROM users ORDER BY name");
    res.json(result.rows);
  } catch (error) {
    next(error);
  }
});

app.get("/api/projects", auth, async (req, res, next) => {
  try {
    const result = await query(
      `SELECT p.id, p.name, p.description, pm.role,
        COUNT(DISTINCT t.id)::INT AS task_count,
        COUNT(DISTINCT m.user_id)::INT AS member_count
       FROM projects p
       JOIN project_members pm ON pm.project_id = p.id
       LEFT JOIN project_members m ON m.project_id = p.id
       LEFT JOIN tasks t ON t.project_id = p.id
       WHERE pm.user_id = $1
       GROUP BY p.id, pm.role
       ORDER BY p.created_at DESC`,
      [req.user.id]
    );
    res.json(result.rows);
  } catch (error) {
    next(error);
  }
});

app.post("/api/projects", auth, async (req, res, next) => {
  const client = await pool.connect();
  try {
    const name = required(req.body.name, "Project name");
    const description = (req.body.description || "").trim();
    await client.query("BEGIN");
    const project = await client.query(
      "INSERT INTO projects (name, description, created_by) VALUES ($1, $2, $3) RETURNING *",
      [name, description, req.user.id]
    );
    await client.query(
      "INSERT INTO project_members (project_id, user_id, role) VALUES ($1, $2, 'Admin')",
      [project.rows[0].id, req.user.id]
    );
    await client.query("COMMIT");
    res.status(201).json({ ...project.rows[0], role: "Admin" });
  } catch (error) {
    await client.query("ROLLBACK");
    next(error);
  } finally {
    client.release();
  }
});

app.get("/api/projects/:projectId/members", auth, requireProjectMember, async (req, res, next) => {
  try {
    const result = await query(
      `SELECT u.id, u.name, u.email, pm.role
       FROM project_members pm
       JOIN users u ON u.id = pm.user_id
       WHERE pm.project_id = $1
       ORDER BY pm.role, u.name`,
      [req.projectId]
    );
    res.json(result.rows);
  } catch (error) {
    next(error);
  }
});

app.post("/api/projects/:projectId/members", auth, requireProjectMember, requireAdmin, async (req, res, next) => {
  try {
    const email = required(req.body.email, "Member email").toLowerCase();
    const role = req.body.role === "Admin" ? "Admin" : "Member";
    const user = await query("SELECT id, name, email FROM users WHERE email = $1", [email]);

    if (!user.rows[0]) {
      return res.status(404).json({ message: "User must sign up before being added to a project" });
    }

    const result = await query(
      `INSERT INTO project_members (project_id, user_id, role)
       VALUES ($1, $2, $3)
       ON CONFLICT (project_id, user_id) DO UPDATE SET role = EXCLUDED.role
       RETURNING role`,
      [req.projectId, user.rows[0].id, role]
    );
    res.status(201).json({ ...user.rows[0], role: result.rows[0].role });
  } catch (error) {
    next(error);
  }
});

app.delete("/api/projects/:projectId/members/:userId", auth, requireProjectMember, requireAdmin, async (req, res, next) => {
  try {
    const userId = Number(req.params.userId);
    if (userId === req.user.id) {
      return res.status(400).json({ message: "Admins cannot remove themselves" });
    }
    await query("DELETE FROM project_members WHERE project_id = $1 AND user_id = $2", [req.projectId, userId]);
    await query(
      "UPDATE tasks SET assigned_to = NULL WHERE project_id = $1 AND assigned_to = $2",
      [req.projectId, userId]
    );
    res.status(204).end();
  } catch (error) {
    next(error);
  }
});

app.get("/api/projects/:projectId/tasks", auth, requireProjectMember, async (req, res, next) => {
  try {
    const params = [req.projectId];
    let visibility = "";
    if (req.membership.role !== "Admin") {
      params.push(req.user.id);
      visibility = "AND t.assigned_to = $2";
    }
    const result = await query(
      `SELECT t.*, assignee.name AS assignee_name, creator.name AS creator_name
       FROM tasks t
       LEFT JOIN users assignee ON assignee.id = t.assigned_to
       JOIN users creator ON creator.id = t.created_by
       WHERE t.project_id = $1 ${visibility}
       ORDER BY t.due_date ASC, t.created_at DESC`,
      params
    );
    res.json(result.rows);
  } catch (error) {
    next(error);
  }
});

app.post("/api/projects/:projectId/tasks", auth, requireProjectMember, requireAdmin, async (req, res, next) => {
  try {
    const title = required(req.body.title, "Task title");
    const description = (req.body.description || "").trim();
    const dueDate = required(req.body.dueDate, "Due date");
    const priority = normalizePriority(req.body.priority || "Medium");
    const assignedTo = req.body.assignedTo ? Number(req.body.assignedTo) : null;

    if (Number.isNaN(Date.parse(dueDate))) {
      return res.status(400).json({ message: "Enter a valid due date" });
    }

    if (assignedTo) {
      const member = await getMembership(req.projectId, assignedTo);
      if (!member) {
        return res.status(400).json({ message: "Assigned user must be a project member" });
      }
    }

    const result = await query(
      `INSERT INTO tasks (project_id, title, description, due_date, priority, assigned_to, created_by)
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       RETURNING *`,
      [req.projectId, title, description, dueDate, priority, assignedTo, req.user.id]
    );
    res.status(201).json(result.rows[0]);
  } catch (error) {
    next(error);
  }
});

app.patch("/api/projects/:projectId/tasks/:taskId", auth, requireProjectMember, async (req, res, next) => {
  try {
    const taskId = Number(req.params.taskId);
    const existing = await query("SELECT * FROM tasks WHERE id = $1 AND project_id = $2", [taskId, req.projectId]);
    const task = existing.rows[0];

    if (!task) {
      return res.status(404).json({ message: "Task not found" });
    }

    if (req.membership.role !== "Admin" && task.assigned_to !== req.user.id) {
      return res.status(403).json({ message: "Members can update only assigned tasks" });
    }

    const status = req.body.status ? normalizeStatus(req.body.status) : task.status;
    const title = req.membership.role === "Admin" && req.body.title ? required(req.body.title, "Task title") : task.title;
    const description = req.membership.role === "Admin" && req.body.description !== undefined ? String(req.body.description).trim() : task.description;
    const dueDate = req.membership.role === "Admin" && req.body.dueDate ? required(req.body.dueDate, "Due date") : task.due_date;
    const priority = req.membership.role === "Admin" && req.body.priority ? normalizePriority(req.body.priority) : task.priority;
    const assignedTo = req.membership.role === "Admin" && req.body.assignedTo !== undefined
      ? (req.body.assignedTo ? Number(req.body.assignedTo) : null)
      : task.assigned_to;

    if (assignedTo) {
      const member = await getMembership(req.projectId, assignedTo);
      if (!member) {
        return res.status(400).json({ message: "Assigned user must be a project member" });
      }
    }

    const result = await query(
      `UPDATE tasks
       SET title = $1, description = $2, due_date = $3, priority = $4, assigned_to = $5, status = $6, updated_at = NOW()
       WHERE id = $7 AND project_id = $8
       RETURNING *`,
      [title, description, dueDate, priority, assignedTo, status, taskId, req.projectId]
    );
    res.json(result.rows[0]);
  } catch (error) {
    next(error);
  }
});

app.delete("/api/projects/:projectId/tasks/:taskId", auth, requireProjectMember, requireAdmin, async (req, res, next) => {
  try {
    await query("DELETE FROM tasks WHERE id = $1 AND project_id = $2", [req.params.taskId, req.projectId]);
    res.status(204).end();
  } catch (error) {
    next(error);
  }
});

app.get("/api/projects/:projectId/dashboard", auth, requireProjectMember, async (req, res, next) => {
  try {
    const params = [req.projectId];
    let visibility = "";
    if (req.membership.role !== "Admin") {
      params.push(req.user.id);
      visibility = "AND t.assigned_to = $2";
    }

    const total = await query(`SELECT COUNT(*)::INT AS count FROM tasks t WHERE t.project_id = $1 ${visibility}`, params);
    const byStatus = await query(
      `SELECT t.status, COUNT(*)::INT AS count FROM tasks t WHERE t.project_id = $1 ${visibility} GROUP BY t.status`,
      params
    );
    const perUser = await query(
      `SELECT COALESCE(u.name, 'Unassigned') AS name, COUNT(t.id)::INT AS count
       FROM tasks t
       LEFT JOIN users u ON u.id = t.assigned_to
       WHERE t.project_id = $1 ${visibility}
       GROUP BY u.name
       ORDER BY count DESC`,
      params
    );
    const overdue = await query(
      `SELECT COUNT(*)::INT AS count
       FROM tasks t
       WHERE t.project_id = $1 ${visibility} AND t.due_date < CURRENT_DATE AND t.status != 'Done'`,
      params
    );

    res.json({
      totalTasks: total.rows[0].count,
      tasksByStatus: byStatus.rows,
      tasksPerUser: perUser.rows,
      overdueTasks: overdue.rows[0].count
    });
  } catch (error) {
    next(error);
  }
});

app.get("*", (req, res) => {
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

app.use((error, req, res, next) => {
  console.error(error);
  res.status(error.status || 500).json({ message: error.message || "Something went wrong" });
});

initDb()
  .then(() => {
    app.listen(PORT, () => {
      console.log(`Team Task Manager running on port ${PORT}`);
    });
  })
  .catch((error) => {
    console.error("Database initialization failed", error);
    process.exit(1);
  });
