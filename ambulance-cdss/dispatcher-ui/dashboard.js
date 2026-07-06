/* Ambulance CDSS — Control Room Dashboard — dashboard.js
 *
 * Live tactical view for supervisors: active incidents table,
 * priority-colour rows, stats bar, and shift handover report.
 * Polls GET /dashboard/active-incidents and GET /dashboard/stats
 * every 30 seconds without page reload.
 */

const API_BASE = window.AMBULANCE_CDSS_API_BASE || "http://localhost:8000";
const REFRESH_INTERVAL_MS = 10000; // Gap 2: 10s polling instead of 30s
const P1_DISPATCH_OVERDUE_MS = 2 * 60 * 1000; // 2 minutes
const P1_ON_SCENE_OVERDUE_MS = 10 * 60 * 1000; // Gap 2: 10 minutes dispatched without on_scene

// ── DOM refs ────────────────────────────────────────────────────────────────

const el = (id) => document.getElementById(id);

// Set default shift times on load
(function setDefaultShiftTimes() {
  const now = new Date();
  const shiftEnd = new Date(now);
  const shiftStart = new Date(now.getTime() - 8 * 3600 * 1000); // 8 hours ago
  function formatDatetimeLocal(d) {
    return d.toISOString().slice(0, 16); // YYYY-MM-DDTHH:MM
  }
  const startEl = el("shift-start");
  const endEl = el("shift-end");
  if (startEl) startEl.value = formatDatetimeLocal(shiftStart);
  if (endEl) endEl.value = formatDatetimeLocal(shiftEnd);
})();

// ── Connection check ────────────────────────────────────────────────────────

async function checkConnection() {
  const statusEl = el("connection-status");
  try {
    const res = await fetch(`${API_BASE}/health`);
    const data = res.ok ? await res.json() : null;
    if (data && data.status === "ok") {
      statusEl.textContent = `connected — ${data.active_protocols} protocol(s)`;
      statusEl.className = "app-header__status ok";
    } else if (data) {
      statusEl.textContent = `degraded — ${data.active_protocols} protocol(s)`;
      statusEl.className = "app-header__status degraded";
    } else {
      statusEl.textContent = `error — HTTP ${res.status}`;
      statusEl.className = "app-header__status error";
    }
  } catch {
    statusEl.textContent = "cannot reach API";
    statusEl.className = "app-header__status error";
  }
}

// ── Stats bar ───────────────────────────────────────────────────────────────

async function loadStats() {
  try {
    const res = await fetch(`${API_BASE}/dashboard/stats?window_hours=24`);
    if (!res.ok) return;
    const data = await res.json();

    el("stat-total").textContent = data.total_incidents ?? "—";
    el("stat-active").textContent = data.active_incidents ?? "—";

    // by_status is a dict like {"IncidentStatus.RECEIVED": 496}
    const byStatus = data.by_status || {};
    const byPriority = data.by_priority || {};

    el("stat-purged").textContent =
      byStatus["IncidentStatus.CLOSED"] || byStatus["closed"] || 0;

    // by_priority is a dict like {"P1": 5, "P2": 10} or {"no_outcome_yet": 513}
    let p1 = 0, p2 = 0, p3 = 0;
    for (const [key, val] of Object.entries(byPriority)) {
      const upper = key.toUpperCase();
      if (upper.startsWith("P1")) p1 += val;
      else if (upper.startsWith("P2")) p2 += val;
      else if (upper.startsWith("P3")) p3 += val;
    }
    el("stat-p1").textContent = p1 || "0";
    el("stat-p2").textContent = p2 || "0";
    el("stat-p3").textContent = p3 || "0";
  } catch {
    // Stats unavailable — leave as dashes
  }
}

// ── Active incidents table ──────────────────────────────────────────────────

let allIncidents = [];
let currentPage = 1;
let pageSize = 10;

function timeSince(isoString) {
  if (!isoString) return "—";
  const ms = Date.now() - new Date(isoString).getTime();
  if (ms < 0) return "just now";
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "<1m";
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return `${hrs}h ${rem}m`;
}

function priorityRowClass(priorityCode) {
  if (!priorityCode) return "";
  const p = priorityCode.toUpperCase();
  if (p.startsWith("P1")) return "row--p1";
  if (p.startsWith("P2")) return "row--p2";
  if (p.startsWith("P3")) return "row--p3";
  return "";
}

function renderIncidents() {
  const tbody = el("incident-tbody");
  const filtered = getFilteredIncidents();
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  if (currentPage > totalPages) currentPage = totalPages;
  const start = (currentPage - 1) * pageSize;
  const page = filtered.slice(start, start + pageSize);

  if (page.length === 0) {
    tbody.innerHTML =
      '<tr><td colspan="7" class="empty-row">No active incidents</td></tr>';
  } else {
    tbody.innerHTML = "";
    const now = Date.now();

    for (const inc of page) {
      const tr = document.createElement("tr");
      const rowClass = priorityRowClass(inc.priority_code);
      if (rowClass) tr.className = rowClass;

      const dispatchedAt = inc.dispatched_at
        ? new Date(inc.dispatched_at).getTime()
        : null;
      const createdAt = new Date(inc.created_at).getTime();
      const isUndispatchedP1 =
        inc.priority_code &&
        inc.priority_code.toUpperCase().startsWith("P1") &&
        !dispatchedAt &&
        now - createdAt > P1_DISPATCH_OVERDUE_MS;

      if (isUndispatchedP1) {
        tr.classList.add("row--blink");
      }

      const complaint = (inc.chief_complaint || "—");
      const truncated = complaint.length > 60 ? complaint.substring(0, 57) + "..." : complaint;

      tr.innerHTML = `
        <td class="cell-priority">${escapeHtml(inc.priority_code || "—")}</td>
        <td class="cell-id">${escapeHtml(inc.incident_id?.substring(0, 8) || "—")}</td>
        <td>${escapeHtml(truncated)}</td>
        <td><span class="status-badge status-${inc.status || "received"}">${escapeHtml(inc.status || "—")}</span></td>
        <td>${escapeHtml(inc.assigned_unit_id || "—")}</td>
        <td>${inc.eta_minutes != null ? inc.eta_minutes + " min" : "—"}</td>
        <td>${timeSince(inc.created_at)}</td>
      `;
      tbody.appendChild(tr);
    }
  }

  // Update pagination controls
  const pageInfo = el("page-info");
  if (pageInfo) {
    pageInfo.textContent = filtered.length > 0
      ? `Showing ${start + 1}\u2013${Math.min(start + pageSize, filtered.length)} of ${filtered.length}`
      : '';
  }
  const prevBtn = el("prev-page-btn");
  const nextBtn = el("next-page-btn");
  if (prevBtn) prevBtn.disabled = currentPage <= 1;
  if (nextBtn) nextBtn.disabled = currentPage >= totalPages;
}

async function loadIncidents() {
  try {
    const res = await fetch(`${API_BASE}/dashboard/active-incidents?limit=100`);
    if (!res.ok) return;
    const data = await res.json();
    allIncidents = data.incidents || [];
    renderIncidents();
    // Gap 2: Check P1 alerts and play sound for new P1s
    checkP1Alerts(data.incidents);
  } catch {
    // Incidents unavailable — leave last known data
  }
}

// ── Notification helper ───────────────────────────────────────────────────

function showNotification(message, type = "info") {
  const div = document.createElement("div");
  div.className = "error-banner";
  div.setAttribute("role", "alert");
  div.setAttribute("aria-live", "assertive");
  if (type === "error") {
    div.style.background = "var(--danger-bg)";
    div.style.borderColor = "var(--danger)";
    div.style.color = "#f4a89e";
  } else {
    div.style.background = "var(--warning-bg)";
    div.style.borderColor = "var(--warning)";
    div.style.color = "#f0c98a";
  }
  div.textContent = message;
  div.style.position = "fixed";
  div.style.top = "12px";
  div.style.left = "50%";
  div.style.transform = "translateX(-50%)";
  div.style.zIndex = "2000";
  div.style.maxWidth = "600px";
  div.style.width = "calc(100% - 24px)";
  div.style.textAlign = "center";
  document.body.appendChild(div);
  setTimeout(() => div.remove(), 3000);
}

// ── Shift handover ──────────────────────────────────────────────────────────

let handoverDebounce = false;

el("handover-btn").addEventListener("click", async () => {
  const start = el("shift-start").value;
  const end = el("shift-end").value;
  if (!start || !end) {
    showNotification("Please select both shift start and end times.", "error");
    return;
  }

  if (handoverDebounce) return;
  handoverDebounce = true;
  const btn = el("handover-btn");
  btn.disabled = true;

  const startISO = new Date(start).toISOString();
  const endISO = new Date(end).toISOString();

  try {
    const res = await fetch(
      `${API_BASE}/dashboard/shift-handover?shift_start=${encodeURIComponent(startISO)}&shift_end=${encodeURIComponent(endISO)}`,
    );
    if (!res.ok) {
      showNotification(`Handover request failed (${res.status})`, "error");
      return;
    }
    const data = await res.json();
    el("handover-text").textContent =
      data.text_rendering || JSON.stringify(data, null, 2);
    el("handover-result").classList.remove("hidden");
  } catch (err) {
    showNotification(`Error: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
    handoverDebounce = false;
  }
});

el("copy-handover-btn").addEventListener("click", () => {
  navigator.clipboard
    .writeText(el("handover-text").textContent)
    .then(() => {
      el("copy-handover-btn").textContent = "Copied";
      setTimeout(() => (el("copy-handover-btn").textContent = "Copy to clipboard"), 2000);
    })
    .catch(() => {
      document.execCommand("copy");
      el("copy-handover-btn").textContent = "Copied";
      setTimeout(() => (el("copy-handover-btn").textContent = "Copy to clipboard"), 2000);
    });
});

// ── Filter / search / sort ─────────────────────────────────────────────────

function getFilteredIncidents() {
  const search = (el("incident-search")?.value || "").toLowerCase();
  const priority = el("filter-priority")?.value || "";
  const status = el("filter-status")?.value || "";
  return allIncidents.filter((inc) => {
    if (priority && !(inc.priority_code || "").toUpperCase().startsWith(priority)) return false;
    if (status && inc.status !== status) return false;
    if (search) {
      const haystack = `${inc.incident_id} ${inc.chief_complaint} ${inc.status} ${inc.assigned_unit_id} ${inc.priority_code}`.toLowerCase();
      if (!haystack.includes(search)) return false;
    }
    return true;
  });
}

let sortAgeAsc = false;

el("incident-search")?.addEventListener("input", () => {
  currentPage = 1;
  renderIncidents();
});

el("filter-priority")?.addEventListener("change", () => {
  currentPage = 1;
  renderIncidents();
});

el("filter-status")?.addEventListener("change", () => {
  currentPage = 1;
  renderIncidents();
});

el("sort-age-btn")?.addEventListener("click", () => {
  sortAgeAsc = !sortAgeAsc;
  allIncidents.sort((a, b) => {
    const ta = new Date(a.created_at).getTime();
    const tb = new Date(b.created_at).getTime();
    return sortAgeAsc ? ta - tb : tb - ta;
  });
  el("sort-age-btn").textContent = sortAgeAsc ? "Sort by age \u2193" : "Sort by age \u2191";
  renderIncidents();
});

el("prev-page-btn")?.addEventListener("click", () => {
  if (currentPage > 1) {
    currentPage--;
    renderIncidents();
  }
});

el("next-page-btn")?.addEventListener("click", () => {
  const filtered = getFilteredIncidents();
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  if (currentPage < totalPages) {
    currentPage++;
    renderIncidents();
  }
});

el("page-size")?.addEventListener("change", (e) => {
  pageSize = parseInt(e.target.value, 10) || 10;
  currentPage = 1;
  renderIncidents();
});

// ── Utilities ───────────────────────────────────────────────────────────────

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

// ── Gap 2: P1 alert tracking + sound notification ──────────────────────

let knownP1Incidents = new Set();

function playAlertBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = "sine";
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.5);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.5);
  } catch {
    // Web Audio not available
  }
}

function checkP1Alerts(incidents) {
  const banner = el("p1-alert-banner");
  const textEl = el("p1-alert-text");
  const now = Date.now();
  let currentP1 = new Set();
  const overdueDispatched = [];

  for (const inc of incidents || []) {
    if (!inc.priority_code || !inc.priority_code.toUpperCase().startsWith("P1")) continue;
    currentP1.add(inc.incident_id);

    // Gap 2: Check if dispatched >10 min without moving to on_scene
    if (inc.status === "dispatched" && inc.dispatched_at) {
      const dispatchedMs = new Date(inc.dispatched_at).getTime();
      if (now - dispatchedMs > P1_ON_SCENE_OVERDUE_MS) {
        overdueDispatched.push(inc);
      }
    }

    // Gap 2: Sound notification for new P1 incidents
    if (!knownP1Incidents.has(inc.incident_id)) {
      playAlertBeep();
    }
  }

  knownP1Incidents = currentP1;

  if (overdueDispatched.length > 0) {
    const ids = overdueDispatched.map(i => i.incident_id?.substring(0, 8)).join(", ");
    textEl.textContent = `${overdueDispatched.length} P1 incident(s) dispatched >10min without on_scene: ${ids}`;
    banner.classList.remove("hidden");
    document.body.classList.add("p1-alert-active");
  } else {
    banner.classList.add("hidden");
    document.body.classList.remove("p1-alert-active");
  }
}

// ── Init + auto-refresh ─────────────────────────────────────────────────────

async function refresh() {
  await Promise.all([checkConnection(), loadStats(), loadIncidents()]);
}

refresh();
setInterval(refresh, REFRESH_INTERVAL_MS);
