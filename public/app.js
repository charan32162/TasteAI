const state = {
  token: localStorage.getItem("token"),
  user: JSON.parse(localStorage.getItem("user") || "null"),
  mode: "login",
  projects: [],
  activeProject: null,
  members: [],
  tasks: [],
  dashboard: null
};

const $ = (selector) => document.querySelector(selector);

const elements = {
  authView: $("#authView"),
  appView: $("#appView"),
  loginTab: $("#loginTab"),
  signupTab: $("#signupTab"),
  authForm: $("#authForm"),
  authSubmit: $("#authSubmit"),
  authMessage: $("#authMessage"),
  nameInput: $("#nameInput"),
  emailInput: $("#emailInput"),
  passwordInput: $("#passwordInput"),
  userLabel: $("#userLabel"),
  projectForm: $("#projectForm"),
  projectList: $("#projectList"),
  emptyState: $("#emptyState"),
  projectWork: $("#projectWork"),
  activeProjectName: $("#activeProjectName"),
  activeProjectDescription: $("#activeProjectDescription"),
  roleLabel: $("#roleLabel"),
  deleteProjectButton: $("#deleteProjectButton"),
  metrics: $("#metrics"),
  memberForm: $("#memberForm"),
  memberEmail: $("#memberEmail"),
  memberRole: $("#memberRole"),
  memberList: $("#memberList"),
  taskForm: $("#taskForm"),
  taskAssignee: $("#taskAssignee"),
  taskBoard: $("#taskBoard"),
  statusFilter: $("#statusFilter"),
  toast: $("#toast")
};

function setAuthMode(mode) {
  state.mode = mode;
  elements.loginTab.classList.toggle("active", mode === "login");
  elements.signupTab.classList.toggle("active", mode === "signup");
  document.querySelectorAll(".signup-only").forEach((node) => node.classList.toggle("hidden", mode !== "signup"));
  elements.authSubmit.textContent = mode === "signup" ? "Create Account" : "Login";
  elements.authMessage.textContent = "";
}

function toast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => elements.toast.classList.remove("show"), 2600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
      ...(options.headers || {})
    }
  });

  if (response.status === 204) {
    return null;
  }

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.message || "Request failed");
  }
  return data;
}

function saveSession(payload) {
  state.token = payload.token;
  state.user = payload.user;
  localStorage.setItem("token", state.token);
  localStorage.setItem("user", JSON.stringify(state.user));
}

function clearSession() {
  state.token = null;
  state.user = null;
  state.projects = [];
  state.activeProject = null;
  localStorage.removeItem("token");
  localStorage.removeItem("user");
}

async function loadProjects() {
  state.projects = await api("/api/projects");
  if (state.activeProject) {
    state.activeProject = state.projects.find((project) => project.id === state.activeProject.id) || null;
  }
  renderProjects();
  renderWorkspace();
}

async function loadProjectDetails() {
  if (!state.activeProject) return;
  const id = state.activeProject.id;
  const [members, tasks, dashboard] = await Promise.all([
    api(`/api/projects/${id}/members`),
    api(`/api/projects/${id}/tasks`),
    api(`/api/projects/${id}/dashboard`)
  ]);
  state.members = members;
  state.tasks = tasks;
  state.dashboard = dashboard;
  renderWorkspace();
}

function showApp() {
  elements.authView.classList.toggle("hidden", Boolean(state.token));
  elements.appView.classList.toggle("hidden", !state.token);
  if (state.token) {
    elements.userLabel.textContent = `${state.user.name} • ${state.user.email}`;
    loadProjects().catch((error) => toast(error.message));
  }
}

function renderProjects() {
  elements.projectList.innerHTML = state.projects.length
    ? state.projects.map((project) => `
      <button class="project-button ${state.activeProject?.id === project.id ? "active" : ""}" data-project-id="${project.id}" type="button">
        <strong>${escapeHtml(project.name)}</strong>
        <span>${project.role} • ${project.task_count || 0} tasks • ${project.member_count || 1} members</span>
      </button>
    `).join("")
    : `<p class="muted">No projects yet.</p>`;
}

function renderWorkspace() {
  const project = state.activeProject;
  elements.emptyState.classList.toggle("hidden", Boolean(project));
  elements.projectWork.classList.toggle("hidden", !project);
  if (!project) return;

  const isAdmin = project.role === "Admin";
  elements.activeProjectName.textContent = project.name;
  elements.activeProjectDescription.textContent = project.description || "No description added.";
  elements.roleLabel.textContent = `${project.role} access`;
  document.querySelectorAll(".admin-only").forEach((node) => node.classList.toggle("hidden", !isAdmin));
  renderMetrics();
  renderMembers(isAdmin);
  renderAssignees();
  renderTasks(isAdmin);
}

function renderMetrics() {
  const dashboard = state.dashboard || { totalTasks: 0, tasksByStatus: [], tasksPerUser: [], overdueTasks: 0 };
  const countFor = (status) => dashboard.tasksByStatus.find((item) => item.status === status)?.count || 0;
  elements.metrics.innerHTML = [
    ["Total Tasks", dashboard.totalTasks],
    ["To Do", countFor("To Do")],
    ["In Progress", countFor("In Progress")],
    ["Overdue", dashboard.overdueTasks]
  ].map(([label, value]) => `<article class="metric"><span>${label}</span><strong>${value}</strong></article>`).join("");
}

function renderMembers(isAdmin) {
  elements.memberList.innerHTML = state.members.map((member) => `
    <div class="compact-row">
      <div>
        <strong>${escapeHtml(member.name)}</strong>
        <p>${escapeHtml(member.email)} • ${member.role}</p>
      </div>
      ${isAdmin && member.id !== state.user.id ? `<button class="danger" data-remove-member="${member.id}" type="button">Remove</button>` : ""}
    </div>
  `).join("");
}

function renderAssignees() {
  elements.taskAssignee.innerHTML = `<option value="">Unassigned</option>` + state.members.map((member) => (
    `<option value="${member.id}">${escapeHtml(member.name)} (${member.role})</option>`
  )).join("");
}

function renderTasks(isAdmin) {
  const filter = elements.statusFilter.value;
  const tasks = filter ? state.tasks.filter((task) => task.status === filter) : state.tasks;
  elements.taskBoard.innerHTML = tasks.length ? tasks.map((task) => {
    const overdue = new Date(task.due_date) < startOfToday() && task.status !== "Done";
    return `
      <article class="task-card">
        <div>
          <h4>${escapeHtml(task.title)}</h4>
          <p>${escapeHtml(task.description || "No description")}</p>
        </div>
        <div class="task-meta">
          <span class="badge status-${task.status.replaceAll(" ", "-")}">${task.status}</span>
          <span class="badge">${task.priority}</span>
          <span class="badge">${formatDate(task.due_date)}</span>
          ${overdue ? `<span class="badge" style="background:#fff1f0;color:#b42318;">Overdue</span>` : ""}
        </div>
        <p>Assigned to: ${escapeHtml(task.assignee_name || "Unassigned")}</p>
        <select data-status-task="${task.id}" ${!isAdmin && task.assigned_to !== state.user.id ? "disabled" : ""}>
          ${["To Do", "In Progress", "Done"].map((status) => `<option ${task.status === status ? "selected" : ""}>${status}</option>`).join("")}
        </select>
        ${isAdmin ? `<button class="danger" data-delete-task="${task.id}" type="button">Delete</button>` : ""}
      </article>
    `;
  }).join("") : `<p class="muted">No tasks match this view.</p>`;
}

function startOfToday() {
  const date = new Date();
  date.setHours(0, 0, 0, 0);
  return date;
}

function formatDate(value) {
  return new Date(value).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

elements.loginTab.addEventListener("click", () => setAuthMode("login"));
elements.signupTab.addEventListener("click", () => setAuthMode("signup"));

elements.authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  elements.authMessage.textContent = "";
  const payload = {
    email: elements.emailInput.value,
    password: elements.passwordInput.value
  };
  if (state.mode === "signup") {
    payload.name = elements.nameInput.value;
  }

  try {
    const data = await api(`/api/auth/${state.mode === "signup" ? "signup" : "login"}`, {
      method: "POST",
      body: JSON.stringify(payload)
    });
    saveSession(data);
    showApp();
    toast("Logged in successfully.");
  } catch (error) {
    elements.authMessage.textContent = error.message;
  }
});

elements.projectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({
        name: $("#projectName").value,
        description: $("#projectDescription").value
      })
    });
    elements.projectForm.reset();
    await loadProjects();
    state.activeProject = state.projects[0] || null;
    await loadProjectDetails();
    toast("Project created.");
  } catch (error) {
    toast(error.message);
  }
});

elements.projectList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-project-id]");
  if (!button) return;
  state.activeProject = state.projects.find((project) => project.id === Number(button.dataset.projectId));
  renderProjects();
  await loadProjectDetails().catch((error) => toast(error.message));
});

elements.memberForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api(`/api/projects/${state.activeProject.id}/members`, {
      method: "POST",
      body: JSON.stringify({
        email: elements.memberEmail.value,
        role: elements.memberRole.value
      })
    });
    elements.memberForm.reset();
    await loadProjectDetails();
    await loadProjects();
    toast("Member added.");
  } catch (error) {
    toast(error.message);
  }
});

elements.memberList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-remove-member]");
  if (!button) return;
  try {
    await api(`/api/projects/${state.activeProject.id}/members/${button.dataset.removeMember}`, { method: "DELETE" });
    await loadProjectDetails();
    await loadProjects();
    toast("Member removed.");
  } catch (error) {
    toast(error.message);
  }
});

elements.taskForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api(`/api/projects/${state.activeProject.id}/tasks`, {
      method: "POST",
      body: JSON.stringify({
        title: $("#taskTitle").value,
        description: $("#taskDescription").value,
        dueDate: $("#taskDueDate").value,
        priority: $("#taskPriority").value,
        assignedTo: elements.taskAssignee.value
      })
    });
    elements.taskForm.reset();
    await loadProjectDetails();
    await loadProjects();
    toast("Task created.");
  } catch (error) {
    toast(error.message);
  }
});

elements.taskBoard.addEventListener("change", async (event) => {
  const select = event.target.closest("[data-status-task]");
  if (!select) return;
  try {
    await api(`/api/projects/${state.activeProject.id}/tasks/${select.dataset.statusTask}`, {
      method: "PATCH",
      body: JSON.stringify({ status: select.value })
    });
    await loadProjectDetails();
    toast("Status updated.");
  } catch (error) {
    toast(error.message);
  }
});

elements.taskBoard.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete-task]");
  if (!button) return;
  try {
    await api(`/api/projects/${state.activeProject.id}/tasks/${button.dataset.deleteTask}`, { method: "DELETE" });
    await loadProjectDetails();
    await loadProjects();
    toast("Task deleted.");
  } catch (error) {
    toast(error.message);
  }
});

$("#refreshButton").addEventListener("click", () => loadProjectDetails().catch((error) => toast(error.message)));
elements.deleteProjectButton.addEventListener("click", async () => {
  if (!state.activeProject || state.activeProject.role !== "Admin") return;
  const confirmed = confirm(`Delete project "${state.activeProject.name}"? This will also delete its tasks and members.`);
  if (!confirmed) return;

  try {
    await api(`/api/projects/${state.activeProject.id}`, { method: "DELETE" });
    state.activeProject = null;
    state.members = [];
    state.tasks = [];
    state.dashboard = null;
    await loadProjects();
    toast("Project deleted.");
  } catch (error) {
    toast(error.message);
  }
});
elements.statusFilter.addEventListener("change", () => renderTasks(state.activeProject?.role === "Admin"));
$("#logoutButton").addEventListener("click", () => {
  clearSession();
  showApp();
});

setAuthMode("login");
showApp();
