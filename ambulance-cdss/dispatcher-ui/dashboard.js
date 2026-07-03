/* Ambulance CDSS — Control Room Dashboard — dashboard.js
 *
 * Live tactical view for supervisors: active incidents table,
 * priority-colour rows, stats bar, and shift handover report.
 * Polls GET /dashboard/active-incidents and GET /dashboard/stats
 * every 30 seconds without page reload.
 */

const API_BASE = window.AMBULANCE_CDSS_API_BASE || "http://localhost:8000";
const REFRESH_INTERVAL_MS = 30000;
const P1_DISPATCH_OVERDUE_MS = 2 * 60 * 1000; // 2 minutes

// ── DOM refs ────────────────────────────────────────────────────────────────

const el = (id) => document.getElementById(id);

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

    // Aggregate by status
    const byStatus = {};
    const byPriority = {};
    for (const row of data.by_status || []) {
      byStatus[row.status] = parseInt(row.count, 10);
    }
    for (const row of data.by_priority || []) {
      byPriority[row.priority_code] = parseInt(row.count, 10);
    }

    el("stat-total").textContent = data.total_incidents ?? "—";
    el("stat-active").textContent =
      (byStatus.received || 0) +
      (byStatus.dispatched || 0) +
      (byStatus.on_scene || 0) +
      (byStatus.transporting || 0) || "—";
    el("stat-p1").textContent = byPriority["P1"] ?? "0";
    el("stat-p2").textContent = byPriority["P2"] ?? "0";
    el("stat-p3").textContent = byPriority["P3"] ?? "0";
    el("stat-purged").textContent = byStatus.closed ?? "0";
  } catch {
    // Stats unavailable — leave as dashes
  }
}

// ── Active incidents table ──────────────────────────────────────────────────

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

function renderIncidents(incidents) {
  const tbody = el("incident-tbody");
  if (!incidents || incidents.length === 0) {
    tbody.innerHTML =
      '<tr><td colspan="7" class="empty-row">No active incidents</td></tr>';
    return;
  }

  tbody.innerHTML = "";
  const now = Date.now();

  for (const inc of incidents) {
    const tr = document.createElement("tr");
    const rowClass = priorityRowClass(inc.priority_code);
    if (rowClass) tr.className = rowClass;

    // Blink P1 incidents not dispatched within 2 minutes
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

    tr.innerHTML = `
      <td class="cell-priority">${escapeHtml(inc.priority_code || "—")}</td>
      <td class="cell-id">${escapeHtml(inc.incident_id?.substring(0, 8) || "—")}</td>
      <td>${escapeHtml(inc.chief_complaint || "—")}</td>
      <td><span class="status-badge status-${inc.status || "received"}">${escapeHtml(inc.status || "—")}</span></td>
      <td>${escapeHtml(inc.assigned_unit_id || "—")}</td>
      <td>${inc.eta_minutes != null ? inc.eta_minutes + " min" : "—"}</td>
      <td>${timeSince(inc.created_at)}</td>
    `;
    tbody.appendChild(tr);
  }
}

async function loadIncidents() {
  try {
    const res = await fetch(`${API_BASE}/dashboard/active-incidents?limit=100`);
    if (!res.ok) return;
    const data = await res.json();
    renderIncidents(data.incidents);
  } catch {
    // Incidents unavailable — leave last known data
  }
}

// ── Shift handover ──────────────────────────────────────────────────────────

el("handover-btn").addEventListener("click", async () => {
  const start = el("shift-start").value;
  const end = el("shift-end").value;
  if (!start || !end) {
    alert("Please select both shift start and end times.");
    return;
  }

  const startISO = new Date(start).toISOString();
  const endISO = new Date(end).toISOString();

  try {
    const res = await fetch(
      `${API_BASE}/dashboard/shift-handover?shift_start=${encodeURIComponent(startISO)}&shift_end=${encodeURIComponent(endISO)}`,
    );
    if (!res.ok) {
      alert(`Handover request failed (${res.status})`);
      return;
    }
    const data = await res.json();
    el("handover-text").textContent =
      data.text_rendering || JSON.stringify(data, null, 2);
    el("handover-result").classList.remove("hidden");
  } catch (err) {
    alert(`Error: ${err.message}`);
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

// ── Utilities ───────────────────────────────────────────────────────────────

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

// ── Init + auto-refresh ─────────────────────────────────────────────────────

async function refresh() {
  await Promise.all([checkConnection(), loadStats(), loadIncidents()]);
}

refresh();
setInterval(refresh, REFRESH_INTERVAL_MS);
