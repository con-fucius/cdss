/* Ambulance CDSS Field Console — app.js
 *
 * Same plain-JS, no-framework, no-build-step approach as
 * dispatcher-ui/app.js, for the same reasons (controlled-device operator
 * console, not a general web app — see that file's header comment).
 *
 * This console harnesses the Phase 4/5/6 field-side and management
 * backend: field protocol selection and checklist progression, vitals
 * recording with server-computed NEWS2/GCS, unconditional medication
 * logging via POST /incidents/{id}/medication (no formulary gate —
 * Phase 0.5 resolved), free-form field log entries, handoff summary
 * via GET /incidents/{id}/handoff, and full incident retrieval.
 *
 * Same hard rule as the dispatcher console: every error from the API is
 * shown verbatim, never swallowed, never silently retried.
 *
 * Phase 6 additions:
 * - 6.1: Write queue for offline actions (localStorage FIFO)
 * - 6.2: Optimistic UI for step marking
 * - 6.3: Vitals pre-population from last recording
 * - 6.4: Triage context display on incident open
 * - 6.5: Offline mode indicator with queue count
 */

const API_BASE = window.AMBULANCE_CDSS_API_BASE || "http://localhost:8000";
const QUEUE_KEY = "ambulance_cdss_write_queue";
const SESSION_KEY = window.AMBULANCE_CDSS_SESSION_KEY || "ambulance_cdss_field_session";
const MAX_QUEUE_SIZE = 50;

// ── State ──────────────────────────────────────────────────────────────────

const state = {
  incidentId: null,
  recordedBy: null,
  sessionToken: null,
  fieldProtocolId: null,
  checklistState: null,
  lastVitals: null, // Phase 6.3: last vitals for pre-population
  isOffline: false, // Phase 6.5: offline detection state
  triageEnrichment: null, // Phase 6.4: triage enrichment from dispatch
  gpsWatchId: null, // Epic 3.1: geolocation watch ID
  gpsTimer: null, // Epic 3.1: interval timer for GPS pings
  incidentStatus: null, // Epic 3.1: track status to stop pings
  incidentOpenedAt: null, // Feature 7: timestamp when incident was opened
  chronometerInterval: null, // Feature 7: interval for chronometer
  lastNotes: null, // Feature 3: last known notes for dispatch polling
  lastNoteCount: null, // Structured notes count for change detection
  dispatchPollInterval: null, // Feature 3: interval for dispatch polling
  gpsCoords: null, // Feature 6: current GPS coordinates
  gpsLastPing: null, // Feature 6: timestamp of last GPS ping
  routedFacility: null, // Feature 6: routed facility info
  protocolStepTimes: [], // Feature 8: timestamps of step completions for ETA
};

// ── DOM refs ───────────────────────────────────────────────────────────────

const el = (id) => document.getElementById(id);
const lookupScreen = el("lookup-screen");
const workspaceScreen = el("workspace-screen");

// ── Phase 6.1: Write queue for offline actions ─────────────────────────────

function getWriteQueue() {
  try {
    return JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveWriteQueue(queue) {
  localStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
}

function addToWriteQueue(endpoint, method, body) {
  const queue = getWriteQueue();
  if (queue.length >= MAX_QUEUE_SIZE) {
    alert(
      "Write queue is full (50 actions). Some actions may be lost. Please note important actions on paper.",
    );
    return false;
  }
  queue.push({
    endpoint,
    method,
    body,
    queued_at: new Date().toISOString(),
    incident_id: state.incidentId,
  });
  saveWriteQueue(queue);
  updateOfflineQueueDisplay();
  return true;
}

// updateOfflineQueueDisplay is defined later in the Gap 3e section

async function drainWriteQueue() {
  const queue = getWriteQueue();
  if (queue.length === 0) return;

  // Show syncing status with count
  const statusEl = el("connection-status");
  const origText = statusEl.textContent;
  const origClass = statusEl.className;
  const totalCount = queue.length;
  statusEl.textContent = `Syncing... (${totalCount} remaining)`;
  statusEl.className = "app-header__status degraded";

  // Sort queue chronologically before syncing
  queue.sort((a, b) => new Date(a.queued_at) - new Date(b.queued_at));

  const remaining = [];
  let syncedCount = 0;

  for (const entry of queue) {
    // Update sync progress display
    const left = totalCount - syncedCount;
    statusEl.textContent = `Syncing... (${left} remaining)`;

    try {
      await apiCall(entry.endpoint, {
        method: entry.method,
        body: JSON.stringify(entry.body),
      });
      syncedCount++;
    } catch (err) {
      // 409 Conflict: log warning, skip this write, continue with others
      if (err.status === 409) {
        console.warn(`[Sync] Conflict (409) for ${entry.method} ${entry.endpoint} — skipping`);
        syncedCount++;
        continue;
      }
      // Network error: stop syncing, leave remaining in queue
      if (err.message.includes("Failed to fetch") || err.message.includes("NetworkError")) {
        console.warn("[Sync] Network error — stopping sync, remaining actions stay queued");
        remaining.push(entry);
        break;
      }
      // Other errors: keep in queue for retry
      remaining.push(entry);
    }

    // 1 second delay between writes to avoid overwhelming the server
    if (syncedCount < totalCount) {
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }

  // Add any entries we didn't process (from the break)
  for (const entry of queue.slice(syncedCount)) {
    if (!remaining.includes(entry)) {
      remaining.push(entry);
    }
  }

  saveWriteQueue(remaining);
  updateOfflineQueueDisplay();

  // Show sync complete
  if (remaining.length === 0 && queue.length > 0) {
    statusEl.textContent = "All actions synced";
    statusEl.className = "app-header__status ok";
    setTimeout(() => {
      statusEl.textContent = "connected";
      statusEl.className = "app-header__status ok";
    }, 3000);
  } else if (remaining.length > 0) {
    statusEl.textContent = `Sync paused — ${remaining.length} remaining`;
    statusEl.className = "app-header__status degraded";
  } else {
    statusEl.textContent = origText;
    statusEl.className = origClass;
  }
}

// ── Item 1: syncQueue() — explicit sync function for offline queue ────────

async function syncQueue() {
  const queue = getWriteQueue();
  if (queue.length === 0) {
    return { synced: 0, failed: 0, remaining: 0 };
  }

  // Sort chronologically
  queue.sort((a, b) => new Date(a.queued_at) - new Date(b.queued_at));

  const statusEl = el("connection-status");
  const origText = statusEl?.textContent;
  const origClass = statusEl?.className;
  if (statusEl) {
    statusEl.textContent = `Syncing... (${queue.length} remaining)`;
    statusEl.className = "app-header__status degraded";
  }

  const remaining = [];
  let synced = 0;
  let failed = 0;

  for (const entry of queue) {
    const left = queue.length - synced - failed;
    if (statusEl) statusEl.textContent = `Syncing... (${left} remaining)`;

    try {
      await apiCall(entry.endpoint, {
        method: entry.method,
        body: JSON.stringify(entry.body),
      });
      synced++;
    } catch (err) {
      if (err.status === 409) {
        console.warn(`[syncQueue] Conflict (409) for ${entry.method} ${entry.endpoint} — skipping`);
        failed++;
        continue;
      }
      if (err.message.includes("Failed to fetch") || err.message.includes("NetworkError")) {
        console.warn("[syncQueue] Network error — stopping sync");
        remaining.push(entry);
        break;
      }
      remaining.push(entry);
      failed++;
    }

    // 1 second delay between writes
    if (synced + failed < queue.length) {
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }

  // Add unprocessed entries
  for (const entry of queue.slice(synced + failed)) {
    if (!remaining.find(r => r.queued_at === entry.queued_at && r.endpoint === entry.endpoint)) {
      remaining.push(entry);
    }
  }

  saveWriteQueue(remaining);
  updateOfflineQueueDisplay();

  if (statusEl) {
    if (remaining.length === 0 && queue.length > 0) {
      statusEl.textContent = "All actions synced";
      statusEl.className = "app-header__status ok";
      setTimeout(() => {
        statusEl.textContent = "connected";
        statusEl.className = "app-header__status ok";
      }, 3000);
    } else if (remaining.length > 0) {
      statusEl.textContent = `Sync paused — ${remaining.length} remaining`;
      statusEl.className = "app-header__status degraded";
    } else {
      statusEl.textContent = origText || "connected";
      statusEl.className = origClass || "app-header__status ok";
    }
  }

  return { synced, failed, remaining: remaining.length };
}

// ── Item 1: mergeProtocolState — reconcile local steps with server ────────

async function mergeProtocolState() {
  if (!state.incidentId || !state.fieldProtocolId) return;

  try {
    // Fetch fresh state from server
    const serverState = await apiCall(
      `/incidents/${state.incidentId}/field-protocol/state`,
    );

    // Get local state
    const localState = state.checklistState;
    if (!localState || !localState.steps) {
      // No local state, just use server state
      state.checklistState = serverState;
      renderChecklist(serverState);
      return;
    }

    // Build maps of step statuses
    const localStepMap = {};
    for (const step of localState.steps) {
      localStepMap[step.step_id] = step.status;
    }

    const serverStepMap = {};
    for (const step of serverState.steps) {
      serverStepMap[step.step_id] = step.status;
    }

    // Find steps that are done locally but not on server (need re-send)
    const stepsToResend = [];
    for (const step of localState.steps) {
      if (step.status !== "pending" && serverStepMap[step.step_id] === "pending") {
        stepsToResend.push(step);
      }
    }

    // Re-send locally-completed steps that server doesn't have
    for (const step of stepsToResend) {
      try {
        await apiCallWithQueue(
          `/incidents/${state.incidentId}/field-protocol/step`,
          {
            method: "POST",
            body: JSON.stringify({
              step_id: step.step_id,
              status: step.status,
              recorded_by: state.recordedBy,
            }),
          },
        );
        console.log(`[Merge] Re-sent step ${step.step_id} (${step.status})`);
      } catch (err) {
        console.warn(`[Merge] Failed to re-send step ${step.step_id}:`, err.message);
      }
    }

    // Refresh state after merge
    const mergedState = await apiCall(
      `/incidents/${state.incidentId}/field-protocol/state`,
    );
    state.checklistState = mergedState;
    renderChecklist(mergedState);
  } catch (err) {
    console.warn("[Merge] Protocol state merge failed:", err.message);
  }
}

// ── Phase 6.5: Offline mode indicator ──────────────────────────────────────

function setOfflineBanner(offline) {
  const banner = el("offline-banner");
  const statusEl = el("connection-status");

  state.isOffline = offline;

  if (offline) {
    banner.classList.remove("hidden");
    document.body.classList.add("offline");
    statusEl.textContent = "offline — working offline";
    statusEl.className = "app-header__status error";
  } else {
    banner.classList.add("hidden");
    document.body.classList.remove("offline");
    statusEl.className = "app-header__status ok";
  }

  updateOfflineQueueDisplay();
}

// ── Epic 3.1: GPS auto-push ─────────────────────────────────────────────
// Pushes POST /incidents/{id}/unit-location every 30 seconds while the
// incident is open and the device has signal. Uses the existing write
// queue for offline resilience.

const GPS_PING_INTERVAL_MS = 30000;
const GPS_TERMINAL_STATUSES = new Set(["handoff_complete", "closed"]);

// updateGpsStatus is defined in the Feature 6 section below

async function sendGpsPing(position) {
  // Don't ping if no incident open or incident has reached terminal status
  if (!state.incidentId || GPS_TERMINAL_STATUSES.has(state.incidentStatus)) return;
  const coords = position.coords;

  // Feature 6: Store coordinates and last ping time
  state.gpsCoords = { lat: coords.latitude, lon: coords.longitude };
  state.gpsLastPing = Date.now();

  const body = {
    lat: coords.latitude,
    lon: coords.longitude,
    recorded_by: state.recordedBy || "field",
  };
  // apiCallWithQueue handles network errors via write queue — no try/catch needed
  const result = await apiCallWithQueue(`/incidents/${state.incidentId}/unit-location`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  updateGpsStatus(result?.queued ? "paused" : "active");
}

function startGpsTracking() {
  if (!navigator.geolocation) {
    updateGpsStatus("unavailable");
    return;
  }
  if (state.gpsWatchId !== null || state.gpsTimer !== null) return;

  // watchPosition fires callback immediately with first known position,
  // then again on movement. The 30s interval below covers cases where
  // watchPosition stalls or the device is stationary.
  state.gpsWatchId = navigator.geolocation.watchPosition(
    (pos) => sendGpsPing(pos),
    () => {}, // Ignore repeated errors — permission handled on initial denial
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 10000 },
  );

  // Interval timer as fallback (covers cases where watchPosition stalls)
  state.gpsTimer = setInterval(() => {
    if (!state.incidentId || GPS_TERMINAL_STATUSES.has(state.incidentStatus)) {
      stopGpsTracking();
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => sendGpsPing(pos),
      () => {},
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 10000 },
    );
  }, GPS_PING_INTERVAL_MS);
}

function stopGpsTracking() {
  if (state.gpsWatchId !== null) {
    navigator.geolocation.clearWatch(state.gpsWatchId);
    state.gpsWatchId = null;
  }
  if (state.gpsTimer !== null) {
    clearInterval(state.gpsTimer);
    state.gpsTimer = null;
  }
}

// ── Connection check ───────────────────────────────────────────────────────

async function checkConnection() {
  const statusEl = el("connection-status");
  try {
    const res = await fetch(`${API_BASE}/health`);
    const data = res.ok ? await res.json() : null;
    if (data && data.status === "ok") {
      statusEl.textContent = "connected";
      statusEl.className = "app-header__status ok";
    } else if (data) {
      statusEl.textContent = `degraded — database: ${data.database}`;
      statusEl.className = "app-header__status degraded";
    } else {
      statusEl.textContent = `error — HTTP ${res.status}`;
      statusEl.className = "app-header__status error";
    }

    if (state.isOffline) {
      setOfflineBanner(false);
      await drainWriteQueue();
      // Item 1: merge protocol state after reconnection
      await mergeProtocolState();
    }
  } catch (err) {
    statusEl.textContent = "cannot reach API — check connection";
    statusEl.className = "app-header__status error";
    setOfflineBanner(true);
  }
}
checkConnection();
setInterval(checkConnection, 30000);

// ── Mobile navigation — scroll to section ─────────────────────────────────

function scrollToSection(sectionId) {
  const el = document.getElementById(sectionId);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });

  // Update active state on mobile nav buttons
  document.querySelectorAll(".mobile-nav__btn").forEach((btn) => {
    btn.classList.toggle(
      "mobile-nav__btn--active",
      btn.dataset.section === sectionId.replace("tab-", ""),
    );
  });

  // On desktop, also switch tabs
  const tabName = sectionId.replace("tab-", "");
  switchTab(tabName);
}

// ── Epic 6.2: Paramedic Login & Session ──────────────────────────────────────

function loadSession() {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const sess = JSON.parse(raw);
    if (Date.now() - (sess.issued_at || 0) > 8 * 3600 * 1000) {
      localStorage.removeItem(SESSION_KEY);
      return null;
    }
    return sess;
  } catch {
    return null;
  }
}

function saveSessionData(sessionToken, unitId) {
  localStorage.setItem(SESSION_KEY, JSON.stringify({
    session_token: sessionToken,
    unit_id: unitId,
    issued_at: Date.now(),
  }));
}

function clearSession() {
  localStorage.removeItem(SESSION_KEY);
}

function initLoginScreen() {
  const existing = loadSession();
  if (existing) {
    state.sessionToken = existing.session_token;
    state.recordedBy = existing.unit_id;
    const recorderInput = el("recorder-id");
    if (recorderInput) recorderInput.value = existing.unit_id;
    hide(el("login-screen"));
    show(el("lookup-screen"));
    return;
  }
  show(el("login-screen"));
  hide(el("lookup-screen"));
}

el("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  hide(el("login-error"));
  const unitId = el("login-unit-id").value.trim();
  const pin = el("login-pin").value.trim();
  const btn = e.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const data = await apiCall("/auth/dispatcher-login", {
      method: "POST",
      body: JSON.stringify({ username: unitId, pin }),
    });
    state.sessionToken = data.session_token;
    state.recordedBy = unitId;
    saveSessionData(data.session_token, unitId);
    const recorderInput = el("recorder-id");
    if (recorderInput) recorderInput.value = unitId;
    hide(el("login-screen"));
    show(el("lookup-screen"));
  } catch (err) {
    el("login-error").textContent = err.message;
    show(el("login-error"));
  } finally {
    btn.disabled = false;
  }
});

el("logout-btn").addEventListener("click", () => {
  stopGpsTracking();
  stopChronometer();
  stopDispatchPolling();
  clearSession();
  state.sessionToken = null;
  state.recordedBy = null;
  state.incidentId = null;
  state.incidentStatus = null;
  state.fieldProtocolId = null;
  state.checklistState = null;
  state.lastVitals = null;
  state.triageEnrichment = null;
  state.lastNotes = null;
  state.lastNoteCount = null;
  state.gpsCoords = null;
  state.gpsLastPing = null;
  state.routedFacility = null;
  state.protocolStepTimes = [];
  hide(el("workspace-screen"));
  hide(el("lookup-screen"));
  show(el("login-screen"));
});

initLoginScreen();

// ── EPIC 3.3: Quick action buttons (fat-finger, haptic, debounce) ──────

let lastQuickActionTime = 0;
const QUICK_ACTION_DEBOUNCE_MS = 2000;

document.querySelectorAll(".quick-action-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const now = Date.now();
    if (now - lastQuickActionTime < QUICK_ACTION_DEBOUNCE_MS) return;
    lastQuickActionTime = now;

    const action = btn.dataset.action;
    if (!action || !state.incidentId) return;

    // Haptic feedback for mobile devices
    if (navigator.vibrate) navigator.vibrate(50);

    btn.disabled = true;
    try {
      await apiCallWithQueue(`/incidents/${state.incidentId}/field-log`, {
        method: "POST",
        body: JSON.stringify({
          step_id: "quick_action",
          action_type: action.toLowerCase().replace(/\s+/g, "_"),
          recorded_by: state.recordedBy,
          data: { note: action, source: "quick_action" },
        }),
      });
      btn.classList.add("logged");
      setTimeout(() => btn.classList.remove("logged"), 3000);
    } catch (err) {
      btn.classList.add("error-flash");
      setTimeout(() => btn.classList.remove("error-flash"), 2000);
    } finally {
      setTimeout(() => { btn.disabled = false; }, QUICK_ACTION_DEBOUNCE_MS);
    }
  });
});

// ── Query-param auto-open: ?incident_id=XXX&unit_id=YYY ─────────────────

(function autoOpenFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const incidentId = params.get("incident_id");
  if (!incidentId) return;
  const incidentInput = el("incident-id-input");
  if (incidentInput) incidentInput.value = incidentId;
  if (state.sessionToken && incidentId) {
    setTimeout(() => {
      el("lookup-form").dispatchEvent(
        new Event("submit", { cancelable: true }),
      );
    }, 200);
  }
})();

// ── API helper ─────────────────────────────────────────────────────────────

async function apiCall(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.sessionToken) {
    headers["Authorization"] = `Bearer ${state.sessionToken}`;
  }
  const res = await fetch(`${API_BASE}${path}`, {
    headers,
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(
      data.detail?.message || data.detail || `Request failed (${res.status})`,
    );
    error.status = res.status;
    error.body = data;
    throw error;
  }
  return data;
}

// Phase 6.1: Enhanced apiCall that queues on network failure
async function apiCallWithQueue(path, options = {}) {
  try {
    const result = await apiCall(path, options);
    // On success, try draining the queue
    await drainWriteQueue();
    return result;
  } catch (err) {
    // If it's a network error, queue the action
    if (
      err.message.includes("Failed to fetch") ||
      err.message.includes("NetworkError")
    ) {
      if (options.method && options.method !== "GET") {
        addToWriteQueue(
          path,
          options.method,
          options.body ? JSON.parse(options.body) : {},
        );
        return {
          queued: true,
          message: "Action queued for sync when connection is restored",
        };
      }
    }
    throw err;
  }
}

// ── Incident lookup ────────────────────────────────────────────────────────

el("lookup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  hide(el("lookup-error"));

  const incidentId = el("incident-id-input").value.trim();
  const recordedBy = el("recorder-id").value.trim();
  const btn = e.target.querySelector("button[type=submit]");
  btn.disabled = true;

  try {
    const incident = await apiCall(`/incidents/${incidentId}`);
    state.incidentId = incident.incident_id;
    state.recordedBy = recordedBy;

    renderIncidentBanner(incident);
    renderDispatchSummary(incident);

    // Phase 6.4: Display triage context if available
    renderTriageContext(incident.triage_enrichment);

    // Feature 1: Render status bar
    renderStatusBar(incident);

    await Promise.all([loadFieldProtocols(), loadMedicationSuggestions()]);

    if (incident.field_protocol_id) {
      state.fieldProtocolId = incident.field_protocol_id;
      await refreshChecklist();
      showProtocolSelected();
    } else {
      showProtocolSelector();
    }

    await Promise.all([loadVitalsHistory(), loadFieldLog()]);

    hide(lookupScreen);
    show(workspaceScreen);
    switchTab("checklist");

    // Epic 3.1: Start GPS auto-push on incident open
    state.incidentStatus = incident.status;
    if (!GPS_TERMINAL_STATUSES.has(incident.status)) {
      startGpsTracking();
    }

    // Feature 7: Start chronometer
    const callTime = incident.call_received_at || incident.created_at;
    startChronometer(callTime ? new Date(callTime) : new Date());

    // Feature 3: Start dispatch polling
    state.lastNotes = incident.notes || null;
    state.lastNoteCount = null;
    startDispatchPolling();
  } catch (err) {
    el("lookup-error").textContent = err.message;
    show(el("lookup-error"));
  } finally {
    btn.disabled = false;
  }
});

function renderIncidentBanner(incident) {
  el("incident-banner").textContent =
    `Incident ${incident.incident_id} — status: ${incident.status}`;
  // Feature 1: Also update the status bar
  renderStatusBar(incident);
}

function renderDispatchSummary(incident) {
  const box = el("dispatch-summary");
  if (!incident.priority_code) {
    box.textContent = "No dispatch outcome recorded on this incident yet.";
    return;
  }
  box.innerHTML = `
    <span class="dispatch-summary__priority">${escapeHtml(incident.priority_code)}</span>
    <span>${escapeHtml(incident.chief_complaint)}</span>
    <br>
    <span>Recommended unit: ${escapeHtml(incident.recommended_unit_type || "—")}</span>
    ${incident.assigned_unit_id ? ` · Assigned unit: ${escapeHtml(incident.assigned_unit_id)}` : ""}
  `;
}

// ── Phase 6.4: Triage context display ──────────────────────────────────────

function renderTriageContext(enrichment) {
  const card = el("triage-context-card");
  if (!enrichment) {
    card.classList.add("hidden");
    return;
  }

  state.triageEnrichment = enrichment;
  card.classList.remove("hidden");

  // Set triage level badge
  const levelBadge = el("triage-level-badge");
  levelBadge.textContent = enrichment.triage_level || "Unknown";
  levelBadge.className = "triage-level-badge";
  if (enrichment.triage_level) {
    levelBadge.classList.add(
      `triage-level-${enrichment.triage_level.toLowerCase()}`,
    );
  }

  // Set diagnosis
  const diagnosisEl = el("triage-diagnosis");
  diagnosisEl.textContent = enrichment.top_diagnosis || "Not determined";

  // Set ESI
  const esiEl = el("triage-esi");
  esiEl.textContent = enrichment.esi_level
    ? `${enrichment.esi_level} of 5`
    : "Not calculated";
}

// ── Tabs ───────────────────────────────────────────────────────────────────

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

function switchTab(tabName) {
  document.querySelectorAll(".tab-btn").forEach((b) => {
    b.classList.toggle("tab-btn--active", b.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-panel").forEach((p) => {
    p.classList.toggle("tab-panel--active", p.id === `tab-${tabName}`);
  });
  if (tabName === "summary") refreshIncidentSummary();
  if (tabName === "vitals") prefillVitalsFromLastRecording();
}

// ── Feature 1: Incident Status Bar ────────────────────────────────────────

const STATUS_LABELS = {
  received: "Received",
  dispatched: "Dispatched",
  on_scene: "On Scene",
  transporting: "Transporting",
  handoff_complete: "Handoff Complete",
};

function renderStatusBar(incident) {
  const statusBar = el("incident-status-bar");
  if (!statusBar) return;

  // Status badge
  const statusBadge = el("status-bar-status");
  const statusText = STATUS_LABELS[incident.status] || incident.status;
  statusBadge.textContent = statusText;
  statusBadge.className = `status-bar__badge status-${incident.status}`;

  // Elapsed time (from call_received_at or created_at)
  const callTime = incident.call_received_at || incident.created_at;
  if (callTime) {
    const elapsed = formatElapsedShort(Date.now() - new Date(callTime).getTime());
    el("status-bar-elapsed").textContent = elapsed;
  }

  // Protocol and step progress
  const protocolEl = el("status-bar-protocol");
  if (incident.field_protocol_id) {
    protocolEl.textContent = `Protocol: ${incident.field_protocol_id}`;
  } else {
    protocolEl.textContent = "No protocol selected";
  }

  // Unit info
  const unitEl = el("status-bar-unit");
  const parts = [];
  if (incident.assigned_unit_id) parts.push(`Unit: ${incident.assigned_unit_id}`);
  if (state.recordedBy) parts.push(`Recorder: ${state.recordedBy}`);
  unitEl.textContent = parts.join(" · ") || "";

  // Step progress
  const progressEl = el("status-bar-progress");
  if (state.checklistState && state.checklistState.steps) {
    const steps = state.checklistState.steps;
    const done = steps.filter((s) => s.status !== "pending").length;
    progressEl.textContent = `Steps: ${done}/${steps.length} completed`;
  } else {
    progressEl.textContent = "";
  }

  // Feature 2: Update contextual status actions
  renderStatusActions(incident.status);
}

function updateStatusBarElapsed() {
  if (!state.incidentOpenedAt) return;
  const elapsed = formatElapsedShort(Date.now() - state.incidentOpenedAt.getTime());
  const elapsedEl = el("status-bar-elapsed");
  if (elapsedEl) elapsedEl.textContent = elapsed;

  // Also update chronometer (Feature 7)
  const chronoDisplay = el("chronometer-display");
  if (chronoDisplay) {
    const totalSeconds = Math.floor((Date.now() - state.incidentOpenedAt.getTime()) / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    chronoDisplay.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;

    // Feature 7: Color coding
    chronoDisplay.classList.remove("timer-green", "timer-yellow", "timer-red");
    if (totalSeconds < 15 * 60) {
      chronoDisplay.classList.add("timer-green");
    } else if (totalSeconds < 30 * 60) {
      chronoDisplay.classList.add("timer-yellow");
    } else {
      chronoDisplay.classList.add("timer-red");
    }
  }
}

function formatElapsedShort(ms) {
  if (ms < 0) return "0:00";
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

// ── Feature 2: Quick Status Actions ────────────────────────────────────────

function renderStatusActions(status) {
  const container = el("status-actions");
  const btnTransporting = el("status-action-transporting");
  const btnArrived = el("status-action-arrived");
  const btnHandoff = el("status-action-handoff");

  // Hide all, then show contextual ones
  hide(container);
  hide(btnTransporting);
  hide(btnArrived);
  hide(btnHandoff);

  if (status === "on_scene") {
    show(container);
    show(btnTransporting);
  } else if (status === "transporting") {
    show(container);
    show(btnArrived);
    show(btnHandoff);
  } else if (status === "dispatched" || status === "received") {
    show(container);
    // On scene button could go here but on_scene is set by a different flow
  }
}

el("status-action-transporting").addEventListener("click", async () => {
  const btn = el("status-action-transporting");
  btn.disabled = true;
  try {
    await apiCallWithQueue(`/incidents/${state.incidentId}/status`, {
      method: "POST",
      body: JSON.stringify({ status: "transporting" }),
    });
    state.incidentStatus = "transporting";
    renderStatusBar({ status: "transporting", call_received_at: state.incidentOpenedAt?.toISOString() });
    await loadFieldLog();
  } catch (err) {
    alert(`Status update failed: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
});

el("status-action-arrived").addEventListener("click", async () => {
  const btn = el("status-action-arrived");
  btn.disabled = true;
  try {
    // Route to facility first if not already routed
    await apiCallWithQueue(`/incidents/${state.incidentId}/field-log`, {
      method: "POST",
      body: JSON.stringify({
        step_id: "facility_arrival",
        action_type: "disposition",
        data: { note: "Arrived at facility" },
        recorded_by: state.recordedBy,
      }),
    });
    renderStatusBar({ status: "transporting", call_received_at: state.incidentOpenedAt?.toISOString() });
    await loadFieldLog();
  } catch (err) {
    alert(`Status update failed: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
});

el("status-action-handoff").addEventListener("click", async () => {
  const btn = el("status-action-handoff");
  btn.disabled = true;
  try {
    await apiCallWithQueue(`/incidents/${state.incidentId}/status`, {
      method: "POST",
      body: JSON.stringify({ status: "handoff_complete" }),
    });
    state.incidentStatus = "handoff_complete";
    stopGpsTracking();
    renderStatusBar({ status: "handoff_complete", call_received_at: state.incidentOpenedAt?.toISOString() });
    await loadFieldLog();
  } catch (err) {
    alert(`Status update failed: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
});

// ── Feature 3: Dispatch Updates Polling ────────────────────────────────────

const DISPATCH_POLL_MS = 15000;

function startDispatchPolling() {
  stopDispatchPolling();
  state.dispatchPollInterval = setInterval(pollDispatchUpdates, DISPATCH_POLL_MS);
  pollDispatchUpdates(); // immediate first poll
}

function stopDispatchPolling() {
  if (state.dispatchPollInterval) {
    clearInterval(state.dispatchPollInterval);
    state.dispatchPollInterval = null;
  }
}

async function pollDispatchUpdates() {
  if (!state.incidentId) return;
  try {
    // Load structured notes for cross-visibility
    const notesData = await apiCall(`/incidents/${state.incidentId}/notes`);
    const notes = notesData.notes || [];

    // Also check incident status for status bar updates
    const incident = await apiCall(`/incidents/${state.incidentId}`);

    const panel = el("dispatch-updates-panel");
    const indicator = el("dispatch-updates-indicator");

    if (notes.length > 0) {
      panel.classList.remove("hidden");
      renderStructuredNotes(notes);
      // Flash indicator on new notes
      const noteCount = notes.length;
      if (state.lastNoteCount && noteCount > state.lastNoteCount) {
        indicator.classList.add("has-new");
        setTimeout(() => indicator.classList.remove("has-new"), 3000);
      }
      state.lastNoteCount = noteCount;
    }

    // Update status bar if status changed
    if (incident.status !== state.incidentStatus) {
      state.incidentStatus = incident.status;
      renderStatusBar(incident);
    }
  } catch {
    // Silent fail for polling — don't spam the UI
  }
}

function renderStructuredNotes(notes) {
  const list = el("dispatch-updates-list");
  if (!list) return;

  // Remove empty message if present
  const empty = list.querySelector(".dispatch-updates__empty");
  if (empty) empty.remove();

  list.innerHTML = "";

  notes.forEach((note) => {
    const item = document.createElement("div");
    const roleClass = note.author_role === "field" ? "dispatch-updates__item--field"
      : note.note_type === "correction" ? "dispatch-updates__item--correction"
      : note.author_role === "system" ? "dispatch-updates__item--system"
      : "dispatch-updates__item--dispatch";
    item.className = `dispatch-updates__item ${roleClass}`;

    const time = note.created_at
      ? new Date(note.created_at).toLocaleTimeString()
      : "";
    const roleLabel = note.author_role === "field" ? "Field"
      : note.author_role === "system" ? "System"
      : "Dispatch";
    const typeLabel = note.note_type === "correction" ? " [Correction]"
      : note.note_type === "field_log" ? " [Field Log]"
      : note.note_type === "dispatcher_note" ? " [Note]"
      : "";

    item.innerHTML = `
      <span class="dispatch-updates__item-time">${escapeHtml(time)}</span>
      <span class="dispatch-updates__item-role">${roleLabel}${typeLabel} — ${escapeHtml(note.author_id)}</span>
      <span class="dispatch-updates__item-note">${escapeHtml(note.note_text)}</span>
    `;
    list.appendChild(item);
  });
}

// ── Feature 6: Enhanced GPS Status ────────────────────────────────────────

function updateGpsStatus(status) {
  const gpsEl = el("gps-status");
  if (!gpsEl) return;
  if (status === "active") {
    const ago = state.gpsLastPing ? `${Math.round((Date.now() - state.gpsLastPing) / 1000)}s ago` : "";
    gpsEl.textContent = `GPS: Active${ago ? " - last ping " + ago : ""}`;
    gpsEl.className = "gps-status ok";
  } else if (status === "unavailable") {
    gpsEl.textContent = "GPS: Inactive";
    gpsEl.className = "gps-status error";
  } else if (status === "paused") {
    gpsEl.textContent = "GPS: Inactive (offline)";
    gpsEl.className = "gps-status warning";
  } else {
    gpsEl.textContent = "GPS: Inactive";
    gpsEl.className = "gps-status";
  }
}

// ── Feature 7: Chronometer ────────────────────────────────────────────────

function startChronometer(openedAt) {
  stopChronometer();
  state.incidentOpenedAt = openedAt;
  state.chronometerInterval = setInterval(updateStatusBarElapsed, 1000);
  updateStatusBarElapsed();
  show(el("chronometer-bar"));
}

function stopChronometer() {
  if (state.chronometerInterval) {
    clearInterval(state.chronometerInterval);
    state.chronometerInterval = null;
  }
  state.incidentOpenedAt = null;
  hide(el("chronometer-bar"));
}

// ── Feature 5: Medication Interaction Warning ──────────────────────────────

const MED_INTERACTIONS = [
  {
    drugs: ["adrenaline", "epinephrine"],
    condition: () => {
      // Check if beta-blockers were given (search medication history)
      const history = el("medication-history");
      if (!history) return false;
      return /beta.?blocker|propranolol|atenolol|metoprolol|bisoprolol|carvedilol/i.test(history.textContent);
    },
    warning: "Caution: beta-blocker interaction — adrenaline effect may be reduced",
  },
  {
    drugs: ["aspirin"],
    condition: () => {
      // Check if there's an active bleeding note
      const log = el("field-log-history");
      if (!log) return false;
      return /active bleed|hemorrhag|bleeding/i.test(log.textContent);
    },
    warning: "Caution: aspirin may worsen active bleeding",
  },
  {
    drugs: ["morphine"],
    condition: () => {
      // Check if respiratory rate < 12 from last vitals
      if (state.lastVitals && state.lastVitals.respiratory_rate !== null) {
        return state.lastVitals.respiratory_rate < 12;
      }
      return false;
    },
    warning: "Caution: respiratory depression risk — RR < 12",
  },
];

function checkMedicationInteraction(drugName) {
  const banner = el("med-interaction-banner");
  if (!banner || !drugName) return;

  const lower = drugName.toLowerCase();
  for (const interaction of MED_INTERACTIONS) {
    if (interaction.drugs.some((d) => lower.includes(d))) {
      if (interaction.condition()) {
        banner.innerHTML = `<span class="med-interaction-banner__icon">⚠</span> ${interaction.warning}`;
        show(banner);
        return;
      }
    }
  }
  hide(banner);
}

// ── Feature 4: Vitals Trend Chart ─────────────────────────────────────────

function renderVitalsTrendChart(history) {
  const chartContainer = el("vitals-trend-chart");
  const barsContainer = el("vitals-trend-bars");
  const arrowEl = el("vitals-trend-arrow");

  if (!history || history.length < 2) {
    hide(chartContainer);
    return;
  }

  // Filter to readings with NEWS2 scores
  const withScores = history.filter((v) => v.news2_score !== null && v.news2_score !== undefined);
  if (withScores.length < 2) {
    hide(chartContainer);
    return;
  }

  show(chartContainer);
  barsContainer.innerHTML = "";

  const maxScore = Math.max(...withScores.map((v) => v.news2_score), 12);

  withScores.forEach((v) => {
    const bar = document.createElement("div");
    bar.className = "vitals-trend-chart__bar";

    const score = v.news2_score;
    const heightPct = Math.max((score / maxScore) * 100, 5);
    bar.style.height = `${heightPct}%`;

    // Color by risk level
    let color = "var(--success)";
    if (score >= 7) color = "var(--danger)";
    else if (score >= 4) color = "var(--warning)";
    bar.style.background = color;

    const label = document.createElement("span");
    label.className = "vitals-trend-chart__bar-label";
    label.textContent = score;
    bar.appendChild(label);

    const timeLabel = document.createElement("span");
    timeLabel.className = "vitals-trend-chart__bar-time";
    const d = new Date(v.recorded_at);
    timeLabel.textContent = `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
    bar.appendChild(timeLabel);

    barsContainer.appendChild(bar);
  });

  // Trend arrow
  const first = withScores[0].news2_score;
  const last = withScores[withScores.length - 1].news2_score;
  const delta = last - first;

  arrowEl.classList.remove("trend-up", "trend-down", "trend-stable");
  if (delta > 0) {
    arrowEl.textContent = `↑ +${delta}`;
    arrowEl.classList.add("trend-up");
  } else if (delta < 0) {
    arrowEl.textContent = `↓ ${delta}`;
    arrowEl.classList.add("trend-down");
  } else {
    arrowEl.textContent = "→ Stable";
    arrowEl.classList.add("trend-stable");
  }
}

// ── Feature 8: Checklist Enhancements ─────────────────────────────────────

function renderChecklistEnhancements(data) {
  if (!data || !data.steps) return;

  // Progress bar
  const checklistCard = el("checklist-card");
  if (!checklistCard) return;

  let progressBar = checklistCard.querySelector(".step-progress-bar");
  if (!progressBar) {
    progressBar = document.createElement("div");
    progressBar.className = "step-progress-bar";
    progressBar.innerHTML = '<div class="step-progress-bar__fill"></div>';
    checklistCard.querySelector(".card-header").after(progressBar);
  }

  const total = data.steps.length;
  const done = data.steps.filter((s) => s.status !== "pending").length;
  const fill = progressBar.querySelector(".step-progress-bar__fill");
  fill.style.width = `${total > 0 ? (done / total) * 100 : 0}%`;

  // ETA based on average step completion time
  let etaEl = checklistCard.querySelector(".step-eta");
  if (!etaEl) {
    etaEl = document.createElement("div");
    etaEl.className = "step-eta";
    progressBar.after(etaEl);
  }

  if (state.protocolStepTimes.length >= 2) {
    const times = state.protocolStepTimes;
    const intervals = [];
    for (let i = 1; i < times.length; i++) {
      intervals.push(times[i] - times[i - 1]);
    }
    const avgMs = intervals.reduce((a, b) => a + b, 0) / intervals.length;
    const remaining = total - done;
    const etaMs = avgMs * remaining;
    const etaMin = Math.round(etaMs / 60000);
    etaEl.textContent = remaining > 0 ? `Est. ${etaMin} min remaining (${remaining} steps left)` : "All steps completed";
  } else {
    const remaining = total - done;
    etaEl.textContent = remaining > 0 ? `${remaining} steps remaining` : "All steps completed";
  }
}

// ── Field protocol selection ───────────────────────────────────────────────

async function loadFieldProtocols() {
  const select = el("field-protocol-select");
  try {
    const data = await apiCall("/field-protocols");
    select.innerHTML = "";
    if (data.active.length === 0) {
      select.innerHTML =
        '<option value="">No field protocols available</option>';
      return;
    }
    data.active.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.protocol_id;
      opt.textContent = `${p.disease_or_presentation} (${p.step_count} steps)`;
      select.appendChild(opt);
    });
  } catch (err) {
    select.innerHTML = '<option value="">Could not load protocols</option>';
  }
}

el("select-protocol-btn").addEventListener("click", async () => {
  hide(el("protocol-select-error"));
  const protocolId = el("field-protocol-select").value;
  if (!protocolId) return;

  try {
    const data = await apiCallWithQueue(
      `/incidents/${state.incidentId}/field-protocol`,
      {
        method: "POST",
        body: JSON.stringify({ protocol_id: protocolId }),
      },
    );
    if (!data.queued) {
      state.fieldProtocolId = protocolId;
      state.checklistState = data;
      renderChecklist(data);
      showProtocolSelected();
      checkForDecisionStep();
    }
  } catch (err) {
    el("protocol-select-error").textContent = err.message;
    show(el("protocol-select-error"));
  }
});

function showProtocolSelector() {
  show(el("protocol-select-card"));
  hide(el("checklist-card"));
}

function showProtocolSelected() {
  hide(el("protocol-select-card"));
  show(el("checklist-card"));
}

async function refreshChecklist() {
  const data = await apiCall(
    `/incidents/${state.incidentId}/field-protocol/state`,
  );
  state.checklistState = data;
  renderChecklist(data);
  checkForDecisionStep();
}

function renderChecklist(data) {
  el("checklist-protocol-name").textContent = state.fieldProtocolId;
  const doneCount = data.steps.filter((s) => s.status !== "pending").length;
  el("checklist-progress").textContent =
    `${doneCount} / ${data.steps.length} completed`;

  const list = el("step-list");
  list.innerHTML = "";
  data.steps.forEach((step) => {
    const li = document.createElement("li");
    li.className = `step-item status-${step.status}`;
    li.dataset.stepId = step.step_id;
    li.innerHTML = `
      <div class="step-item__header">
        <span class="step-item__title">${escapeHtml(step.title)}</span>
        <span class="step-item__status">${step.status.replace(/_/g, " ")}</span>
      </div>
      ${step.description ? `<div class="step-item__description">${escapeHtml(step.description)}</div>` : ""}
      ${step.guideline_ref ? `<div class="step-item__guideline-ref">${escapeHtml(step.guideline_ref)}</div>` : ""}
      <div class="step-item__actions"></div>
    `;
    const actions = li.querySelector(".step-item__actions");

    if (step.status === "pending") {
      actions.append(
        makeStepActionButton("Mark done", "action-done", () =>
          markStep(step.step_id, "done"),
        ),
        makeStepActionButton("Skip", "action-skip", () =>
          markStep(step.step_id, "skipped"),
        ),
        makeStepActionButton("Not applicable", "", () =>
          markStep(step.step_id, "not_applicable"),
        ),
      );
    } else {
      const note = document.createElement("span");
      note.className = "step-item__status";
      note.style.fontSize = "12px";
      note.textContent =
        "Field log entries are append-only. Add a new log entry if reassessment is needed.";
      actions.appendChild(note);
    }
    list.appendChild(li);
  });

  if (data.is_complete) {
    show(el("checklist-complete-banner"));
  } else {
    hide(el("checklist-complete-banner"));
  }

  // Feature 8: Checklist enhancements — progress bar, ETA
  renderChecklistEnhancements(data);
}

function makeStepActionButton(label, extraClass, handler) {
  const btn = document.createElement("button");
  btn.className = `step-action-btn ${extraClass}`.trim();
  btn.textContent = label;
  btn.addEventListener("click", handler);
  return btn;
}

// ── Phase 6.2: Optimistic UI for step marking ──────────────────────────────

async function markStep(stepId, status) {
  const stepElement = document.querySelector(`[data-step-id="${stepId}"]`);
  const actionsContainer = stepElement?.querySelector(".step-item__actions");

  // Phase 6.2: Optimistic UI — immediately show "Pending..." state
  if (stepElement) {
    stepElement.classList.add("status-pending");
    stepElement.classList.remove(
      "status-done",
      "status-skipped",
      "status-not_applicable",
    );
    const statusSpan = stepElement.querySelector(".step-item__status");
    if (statusSpan) statusSpan.textContent = "pending...";

    // Disable action buttons
    if (actionsContainer) {
      actionsContainer.querySelectorAll("button").forEach((btn) => {
        btn.disabled = true;
        btn.style.opacity = "0.5";
      });
    }
  }

  try {
    const data = await apiCallWithQueue(
      `/incidents/${state.incidentId}/field-protocol/step`,
      {
        method: "POST",
        body: JSON.stringify({
          step_id: stepId,
          status,
          recorded_by: state.recordedBy,
        }),
      },
    );

    if (data.queued) {
      // Phase 6.2: Show "Failed — queued" on optimistic UI
      if (stepElement) {
        stepElement.classList.remove("status-pending");
        stepElement.classList.add("status-queued");
        const statusSpan = stepElement.querySelector(".step-item__status");
        if (statusSpan)
          statusSpan.textContent = "queued — will sync when online";
      }
      return;
    }

    state.checklistState = data;
    renderChecklist(data);
    checkForDecisionStep();

    // Feature 8: Track step completion time for ETA calculation
    if (status === "done" || status === "skipped") {
      state.protocolStepTimes.push(Date.now());
      renderChecklistEnhancements(data);
    }

    // Feature 8: Highlight the completed step briefly
    if (stepElement && (status === "done" || status === "skipped")) {
      const newStepEl = document.querySelector(`[data-step-id="${stepId}"]`);
      if (newStepEl) {
        newStepEl.classList.add("step-just-completed");
        setTimeout(() => newStepEl.classList.remove("step-just-completed"), 1200);
      }
    }

    // Feature 1: Update status bar after step change
    if (state.incidentId) {
      try {
        const inc = await apiCall(`/incidents/${state.incidentId}`);
        renderStatusBar(inc);
      } catch { /* non-fatal */ }
    }

    // When a disposition step is marked done, automatically transition
    // the incident status to handoff_complete so the dispatcher dashboard
    // reflects the field unit's progress without a separate manual call.
    if (status === "done") {
      const matchingStep = data.steps.find((s) => s.step_id === stepId);
      if (matchingStep && matchingStep.action_type === "disposition") {          try {
          await apiCall(`/incidents/${state.incidentId}/status`, {
            method: "POST",
            body: JSON.stringify({ status: "handoff_complete" }),
          });
          state.incidentStatus = "handoff_complete";
          stopGpsTracking();
        } catch (statusErr) {
          // Non-fatal: the field log already recorded the step. Surface
          // the status update failure without blocking the UI.
          console.warn(
            "Status update to handoff_complete failed:",
            statusErr.message,
          );
        }
      }
    }
  } catch (err) {
    // Phase 6.2: Revert optimistic UI on failure
    if (stepElement) {
      stepElement.classList.remove("status-pending");
      stepElement.classList.add("status-pending");
      const statusSpan = stepElement.querySelector(".step-item__status");
      if (statusSpan) statusSpan.textContent = "failed — queued";
    }
    alert(`Could not update step: ${err.message}`);
  }
}

// ── Phase 6.3: Vitals pre-population ───────────────────────────────────────

function prefillVitalsFromLastRecording() {
  const banner = el("vitals-prefill-banner");
  const timestampEl = el("vitals-prefill-timestamp");

  if (!state.lastVitals) {
    banner.classList.add("hidden");
    return;
  }

  const v = state.lastVitals;

  // Pre-fill form fields with last known values
  if (v.respiratory_rate !== null && v.respiratory_rate !== undefined)
    el("v-rr").value = v.respiratory_rate;
  if (v.spo2 !== null && v.spo2 !== undefined) el("v-spo2").value = v.spo2;
  if (v.spo2_scale !== null && v.spo2_scale !== undefined)
    el("v-spo2-scale").value = v.spo2_scale;
  if (v.supplemental_o2 !== null && v.supplemental_o2 !== undefined) {
    el("v-supp-o2").value = v.supplemental_o2 ? "true" : "false";
  }
  if (v.bp_systolic !== null && v.bp_systolic !== undefined)
    el("v-bp-sys").value = v.bp_systolic;
  if (v.bp_diastolic !== null && v.bp_diastolic !== undefined)
    el("v-bp-dia").value = v.bp_diastolic;
  if (v.heart_rate !== null && v.heart_rate !== undefined)
    el("v-hr").value = v.heart_rate;
  if (v.consciousness !== null && v.consciousness !== undefined)
    el("v-consciousness").value = v.consciousness;
  if (v.temperature !== null && v.temperature !== undefined)
    el("v-temp").value = v.temperature;
  if (v.gcs_eye !== null && v.gcs_eye !== undefined)
    el("v-gcs-eye").value = v.gcs_eye;
  if (v.gcs_verbal !== null && v.gcs_verbal !== undefined)
    el("v-gcs-verbal").value = v.gcs_verbal;
  if (v.gcs_motor !== null && v.gcs_motor !== undefined)
    el("v-gcs-motor").value = v.gcs_motor;

  // Show prefill banner
  timestampEl.textContent = formatTimestamp(v.recorded_at);
  banner.classList.remove("hidden");
}

// ── Vitals ─────────────────────────────────────────────────────────────────

el("vitals-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  hide(el("vitals-error"));

  const num = (id) => {
    const v = el(id).value;
    return v === "" ? null : Number(v);
  };
  const bool = (id) => {
    const v = el(id).value;
    return v === "" ? null : v === "true";
  };
  const str = (id) => {
    const v = el(id).value;
    return v === "" ? null : v;
  };

  const body = {
    recorded_by: state.recordedBy,
    respiratory_rate: num("v-rr"),
    spo2: num("v-spo2"),
    spo2_scale: num("v-spo2-scale"),
    supplemental_o2: bool("v-supp-o2"),
    bp_systolic: num("v-bp-sys"),
    bp_diastolic: num("v-bp-dia"),
    heart_rate: num("v-hr"),
    consciousness: str("v-consciousness"),
    temperature: num("v-temp"),
    gcs_eye: num("v-gcs-eye"),
    gcs_verbal: num("v-gcs-verbal"),
    gcs_motor: num("v-gcs-motor"),
  };

  try {
    const data = await apiCallWithQueue(
      `/incidents/${state.incidentId}/vitals`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
    e.target.reset();
    el("vitals-prefill-banner").classList.add("hidden");

    if (!data.queued) {
      await loadVitalsHistory();
      // Improvement 4 — show trend alert banners if applicable
      renderTrendAlert(data.trend_alert);
      renderGcsTrendAlert(data.gcs_trend_alert);
      renderNews2MissingFields(data.news2_missing_fields);
      // Gap 3d: Show NEWS2 badge
      displayNews2Badge(data);
    }
  } catch (err) {
    el("vitals-error").textContent = err.message;
    show(el("vitals-error"));
  }
});

async function loadVitalsHistory() {
  const container = el("vitals-history");
  try {
    const full = await apiCall(`/incidents/${state.incidentId}/full`);
    const history = full.vitals_history || [];

    // Phase 6.3: Store last vitals for pre-population
    if (history.length > 0) {
      state.lastVitals = history[history.length - 1];
    }

    if (history.length === 0) {
      container.innerHTML =
        '<div class="record-empty">No vitals recorded yet.</div>';
      return;
    }
    container.innerHTML = "";
    history
      .slice()
      .reverse()
      .forEach((v) => {
        const div = document.createElement("div");
        div.className = "record-item";
        div.innerHTML = `
        <div class="record-item__meta">${formatTimestamp(v.recorded_at)} — recorded by ${escapeHtml(v.recorded_by)}</div>
        <div class="record-item__body">
          ${vitalField("RR", v.respiratory_rate, "/min")}
          ${vitalField("SpO2", v.spo2, "%")}
          ${vitalField("BP", v.bp_systolic && v.bp_diastolic ? `${v.bp_systolic}/${v.bp_diastolic}` : null, "mmHg")}
          ${vitalField("HR", v.heart_rate, "bpm")}
          ${vitalField("Temp", v.temperature, "\u00b0C")}
          ${vitalField("AVPU", v.consciousness, "")}
          ${v.news2_score !== null && v.news2_score !== undefined ? `<span class="record-item__field"><strong>NEWS2</strong> ${v.news2_score}${scoreFlag(news2FlagLevel(v.news2_risk_level))}</span>` : ""}
          ${v.gcs_total !== null && v.gcs_total !== undefined ? `<span class="record-item__field"><strong>GCS</strong> ${v.gcs_total}${scoreFlag(gcsFlagLevel(v.gcs_total))}</span>` : ""}
        </div>
      `;
        container.appendChild(div);
      });
    renderMedicationHistoryFromFull(full);

    // Feature 4: Render vitals trend chart
    renderVitalsTrendChart(history);
  } catch (err) {
    container.innerHTML = `<div class="record-empty">Could not load vitals history: ${escapeHtml(err.message)}</div>`;
  }
}

function vitalField(label, value, unit) {
  if (value === null || value === undefined || value === "") return "";
  return `<span class="record-item__field"><strong>${label}</strong> ${value}${unit ? " " + unit : ""}</span>`;
}

function news2FlagLevel(riskLevel) {
  if (!riskLevel) return "normal";
  const r = riskLevel.toLowerCase();
  if (r.includes("high")) return "critical";
  if (r.includes("medium")) return "elevated";
  return "normal";
}

function gcsFlagLevel(total) {
  if (total === null || total === undefined) return "normal";
  if (total <= 8) return "critical";
  if (total <= 12) return "elevated";
  return "normal";
}

function scoreFlag(level) {
  return `<span class="score-flag flag-${level}">${level}</span>`;
}

// ── NEWS2 trend alert banner (Improvement 4) ────────────────────────────────

function renderTrendAlert(alert) {
  const container = el("vitals-trend-alert");
  if (!alert || alert.trend === "no_prior_data") {
    hide(container);
    return;
  }

  const shouldShow =
    alert.trend === "rapid_deterioration" ||
    alert.crossed_risk_boundary === true;

  if (!shouldShow) {
    hide(container);
    return;
  }

  const parts = [`NEWS2 alert: ${alert.trend.replace(/_/g, " ")}`];
  if (alert.new_news2 !== null && alert.new_news2 !== undefined) {
    parts.push(`current ${alert.new_news2}`);
  }
  if (alert.prior_news2 !== null && alert.prior_news2 !== undefined) {
    parts.push(`prior ${alert.prior_news2}`);
  }
  if (alert.delta !== null && alert.delta !== undefined) {
    parts.push(`delta ${alert.delta > 0 ? "+" : ""}${alert.delta}`);
  }

  container.textContent = parts.join(" — ");
  show(container);
}

// ── GCS trend alert banner (Improvement 4) ────────────────────────────────

function renderGcsTrendAlert(alert) {
  const container = el("gcs-trend-alert");
  if (!container) return;
  if (!alert || alert.trend === "no_prior_data") {
    hide(container);
    return;
  }

  const shouldShow =
    alert.trend === "rapid_deterioration" ||
    alert.crossed_severity_threshold === true;

  if (!shouldShow) {
    hide(container);
    return;
  }

  const parts = [`GCS alert: ${alert.trend.replace(/_/g, " ")}`];
  if (alert.new_gcs !== null && alert.new_gcs !== undefined) {
    parts.push(`current ${alert.new_gcs}`);
  }
  if (alert.prior_gcs !== null && alert.prior_gcs !== undefined) {
    parts.push(`prior ${alert.prior_gcs}`);
  }
  if (alert.delta !== null && alert.delta !== undefined) {
    parts.push(`delta ${alert.delta > 0 ? "+" : ""}${alert.delta}`);
  }

  container.textContent = parts.join(" — ");
  // Use warning-banner for deteriorating, error-banner for rapid_deterioration
  if (alert.trend === "rapid_deterioration") {
    container.className = "error-banner";
  } else {
    container.className = "warning-banner";
  }
  show(container);
}

// ── NEWS2 missing fields banner (Improvement 3.3) ──────────────────────────

function renderNews2MissingFields(missingFields) {
  const container = el("news2-missing-fields-banner");
  if (!container) return;
  if (!missingFields || missingFields.length === 0) {
    hide(container);
    return;
  }
  container.textContent = `NEWS2 incomplete: missing ${missingFields.join(", ")}. Record these to get a score.`;
  show(container);
}

// ── Medication drug suggestions (non-gating, convenience only) ─────────────

async function loadMedicationSuggestions() {
  // Phase 0.5 resolved: medication logging is unconditional. The backend
  // no longer rejects any drug name. GET /formulary is deprecated and its
  // drugs list is now optional suggestions only, not an allowlist.
  // This function populates the <select> with suggestions if any are
  // configured, but never disables the form or blocks submission.
  const select = el("m-drug");
  const submitBtn = el("medication-submit-btn");
  const unavailableBanner = el("formulary-unavailable");

  // Ensure submission is enabled regardless of what the formulary returns.
  submitBtn.disabled = false;
  hide(unavailableBanner);

  try {
    const data = await apiCall("/formulary");
    if (Array.isArray(data.drugs) && data.drugs.length > 0) {
      select.innerHTML =
        '<option value="">Select suggestion or type below...</option>';
      data.drugs.forEach((drug) => {
        const opt = document.createElement("option");
        opt.value = drug;
        opt.textContent = drug;
        select.appendChild(opt);
      });
    } else {
      select.innerHTML =
        '<option value="">No suggestions configured — enter name below</option>';
    }
  } catch (_err) {
    // /formulary being unreachable does not block medication logging.
    select.innerHTML =
      '<option value="">Suggestions unavailable — enter name below</option>';
  }

  setupMedicationAutocomplete();
}

// ── Medication free-text autocomplete ─────────────────────────────────

const MEDICATION_SUGGESTIONS = [
  'Salbutamol', 'Adrenaline', 'Aspirin', 'Nitroglycerin', 'Morphine',
  'Midazolam', 'Diazepam', 'Tranexamic acid', 'Ondansetron',
  'Paracetamol', 'Ibuprofen', 'Amoxicillin', 'Ceftriaxone',
  'Ringers Lactate', 'Normal Saline', 'Plasma-Lyte',
  'Naloxone', 'Flumazenil', 'Atropine', 'Lidocaine',
  'Amiodarone', 'Fentanyl', 'Ketamine', 'Propofol',
  'Epinephrine', 'Chlorpheniramine', 'Dexamethasone',
  'Oxygen', 'Glucose gel', 'Oral rehydration salts',
];

function setupMedicationAutocomplete() {
  const input = el("m-drug-text");
  const dropdown = el("drug-text-suggestions");
  if (!input || !dropdown) return;

  let activeIndex = -1;

  function showDrugSuggestions(query) {
    if (!query) { hide(dropdown); return; }
    const lower = query.toLowerCase();
    const matches = MEDICATION_SUGGESTIONS.filter(s => s.toLowerCase().includes(lower));
    if (matches.length === 0) { hide(dropdown); return; }

    dropdown.innerHTML = "";
    activeIndex = -1;
    matches.forEach((text, i) => {
      const item = document.createElement("div");
      item.className = "suggestion-item";
      item.textContent = text;
      item.dataset.index = i;
      item.addEventListener("mousedown", (e) => {
        e.preventDefault();
        input.value = text;
        // Clear the select so the text input takes precedence
        const select = el("m-drug");
        if (select) select.value = "";
        hide(dropdown);
      });
      dropdown.appendChild(item);
    });
    show(dropdown);
  }

  input.addEventListener("input", () => showDrugSuggestions(input.value.trim()));

  input.addEventListener("keydown", (e) => {
    if (dropdown.classList.contains("hidden")) return;
    const items = dropdown.querySelectorAll(".suggestion-item");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      activeIndex = Math.min(activeIndex + 1, items.length - 1);
      items.forEach((it, i) => it.classList.toggle("active", i === activeIndex));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      items.forEach((it, i) => it.classList.toggle("active", i === activeIndex));
    } else if (e.key === "Enter" && activeIndex >= 0) {
      e.preventDefault();
      input.value = items[activeIndex].textContent;
      const select = el("m-drug");
      if (select) select.value = "";
      hide(dropdown);
    } else if (e.key === "Escape") {
      hide(dropdown);
    }
  });

  document.addEventListener("click", (e) => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) {
      hide(dropdown);
    }
  });
}

// ── Medications ────────────────────────────────────────────────────────────

el("medication-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  hide(el("medication-error"));

  // Drug name: use the select's value if one was chosen, else fall through
  // to the free-text input (m-drug-text). The form HTML has both since
  // Phase 0.5 removed the gate — a selector is a convenience, not a constraint.
  const drugFromSelect = el("m-drug").value.trim();
  const drugFromText = el("m-drug-text") ? el("m-drug-text").value.trim() : "";
  const drug = drugFromSelect || drugFromText;
  const dose = el("m-dose").value.trim();
  const route = el("m-route").value.trim();
  const administered =
    !el("m-not-administered") || !el("m-not-administered").checked;

  if (!drug) {
    el("medication-error").textContent = "Drug name is required.";
    show(el("medication-error"));
    return;
  }

  try {
    await apiCallWithQueue(`/incidents/${state.incidentId}/medication`, {
      method: "POST",
      body: JSON.stringify({
        drug_name: drug,
        dose: dose || "not recorded",
        route: route || "not recorded",
        given_by: state.recordedBy,
        administered,
      }),
    });
    e.target.reset();
    el("m-drug").value = "";
    hide(el("drug-text-suggestions"));
    hide(el("med-interaction-banner"));
    await loadFieldLog();
  } catch (err) {
    el("medication-error").textContent = err.message;
    show(el("medication-error"));
  }
});

function renderMedicationHistoryFromFull(full) {
  const container = el("medication-history");
  const fromDedicatedTable = full.medications_given || [];
  // Historical entries recorded via field-log before the dedicated
  // /medication endpoint existed. Read-only — no new writes go here.
  const fromFieldLogFallback = (full.field_log || []).filter(
    (entry) => entry.step_id === "medication_given",
  );

  if (fromDedicatedTable.length === 0 && fromFieldLogFallback.length === 0) {
    container.innerHTML =
      '<div class="record-empty">No medications recorded yet.</div>';
    return;
  }

  container.innerHTML = "";
  fromDedicatedTable.forEach((m) => {
    const administeredLabel =
      m.administered === false ? " (not administered)" : "";
    const div = document.createElement("div");
    div.className = "record-item";
    div.innerHTML = `
      <div class="record-item__meta">${formatTimestamp(m.given_at)} — logged by ${escapeHtml(m.given_by)}${administeredLabel}</div>
      <div class="record-item__body">
        <span class="record-item__field"><strong>${escapeHtml(m.drug_name)}</strong></span>
        <span class="record-item__field">${escapeHtml(m.dose)}</span>
        <span class="record-item__field">${escapeHtml(m.route)}</span>
      </div>
    `;
    container.appendChild(div);
  });

  if (fromFieldLogFallback.length > 0) {
    const heading = document.createElement("div");
    heading.className = "record-item__meta";
    heading.style.marginTop = "8px";
    heading.textContent =
      "Legacy entries (recorded via field log before dedicated medication endpoint):";
    container.appendChild(heading);
    fromFieldLogFallback.forEach((entry) => {
      const div = document.createElement("div");
      div.className = "record-item";
      div.innerHTML = `
        <div class="record-item__meta">${formatTimestamp(entry.timestamp)} — ${escapeHtml(entry.recorded_by)}</div>
        <div class="record-item__body">
          <span class="record-item__field"><strong>${escapeHtml(entry.data.drug_name || "")}</strong></span>
          <span class="record-item__field">${escapeHtml(entry.data.dose || "")}</span>
          <span class="record-item__field">${escapeHtml(entry.data.route || "")}</span>
        </div>
      `;
      container.appendChild(div);
    });
  }
}

// ── Field log ──────────────────────────────────────────────────────────────

el("field-log-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  hide(el("field-log-error"));

  const actionType = el("fl-action-type").value;
  const note = el("fl-note").value.trim();

  try {
    await apiCallWithQueue(`/incidents/${state.incidentId}/field-log`, {
      method: "POST",
      body: JSON.stringify({
        step_id: "manual_entry",
        action_type: actionType,
        recorded_by: state.recordedBy,
        data: { note },
      }),
    });
    e.target.reset();
    await loadFieldLog();
  } catch (err) {
    el("field-log-error").textContent = err.message;
    show(el("field-log-error"));
  }
});

async function loadFieldLog() {
  const container = el("field-log-history");
  try {
    const full = await apiCall(`/incidents/${state.incidentId}/full`);
    const entries = full.field_log || [];
    if (entries.length === 0) {
      container.innerHTML =
        '<div class="record-empty">No field log entries yet.</div>';
    } else {
      container.innerHTML = "";
      entries
        .slice()
        .reverse()
        .forEach((entry) => {
          const div = document.createElement("div");
          div.className = "record-item";
          const detail =
            entry.data.note ||
            entry.data.step_title ||
            JSON.stringify(entry.data);
          div.innerHTML = `
          <div class="record-item__meta">${formatTimestamp(entry.timestamp)} — ${escapeHtml(entry.action_type)} — ${escapeHtml(entry.recorded_by)}</div>
          <div class="record-item__body">${escapeHtml(String(detail))}</div>
        `;
          container.appendChild(div);
        });
    }
    renderMedicationHistoryFromFull(full);
  } catch (err) {
    container.innerHTML = `<div class="record-empty">Could not load field log: ${escapeHtml(err.message)}</div>`;
  }
}

// ── Facility routing ──────────────────────────────────────────────────────

el("route-facility-btn").addEventListener("click", () => {
  const resultEl = el("facility-route-result");
  const listEl = el("facility-route-list");
  listEl.innerHTML = "";
  resultEl.textContent = "Locating…";

  if (!navigator.geolocation) {
    resultEl.textContent = "Geolocation not available on this device.";
    return;
  }

  navigator.geolocation.getCurrentPosition(
    async (pos) => {
      const { latitude: lat, longitude: lon } = pos.coords;
      resultEl.textContent = "Searching for nearest facility…";
      try {
        const data = await apiCall(
          `/incidents/${state.incidentId}/route-facility`,
          {
            method: "POST",
            body: JSON.stringify({ lat, lon }),
          },
        );
        if (!data.facilities || data.facilities.length === 0) {
          resultEl.textContent = data.message || "No facilities found.";
        } else {
          resultEl.textContent = `${data.facilities.length} facility(ies) found:`;
          data.facilities.forEach((f) => {
            const div = document.createElement("div");
            div.className = "facility-item";
            div.innerHTML =
              `<strong>${escapeHtml(f.name)}</strong><br>` +
              `${f.distance_km.toFixed(1)} km` +
              (f.services ? ` — ${f.services.join(", ")}` : "") +
              (f.capacity_status ? ` — ${f.capacity_status}` : "");
            listEl.appendChild(div);
          });
        }
      } catch (err) {
        resultEl.textContent = `Error: ${err.message}`;
      }
    },
    () => {
      resultEl.textContent = "GPS location unavailable. Cannot route to facility.";
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 },
  );
});

// ── Notify & Route — one-tap disposition action ─────────────────────────────

el("notify-route-btn").addEventListener("click", () => {
  const resultEl = el("notify-route-result");
  const btn = el("notify-route-btn");
  btn.disabled = true;
  resultEl.textContent = "Locating…";

  if (!navigator.geolocation) {
    resultEl.textContent = "Geolocation not available.";
    btn.disabled = false;
    return;
  }

  navigator.geolocation.getCurrentPosition(
    async (pos) => {
      const { latitude: lat, longitude: lon } = pos.coords;
      resultEl.textContent = "Routing to nearest facility…";

      // Step 1: Route to nearest facility
      let facilityName = "unknown";
      try {
        const routeData = await apiCall(
          `/incidents/${state.incidentId}/route-facility`,
          {
            method: "POST",
            body: JSON.stringify({ lat, lon }),
          },
        );
        if (routeData.facilities && routeData.facilities.length > 0) {
          facilityName = routeData.facilities[0].name;
        }
      } catch (routeErr) { /* best effort — continue with disposition even if routing fails */ }

      // Step 2: Log disposition in field log
      try {
        await apiCall(`/incidents/${state.incidentId}/field-log`, {
          method: "POST",
          body: JSON.stringify({
            step_id: "notify_and_route",
            action_type: "disposition",
            data: { routed_facility: facilityName, auto_route: true },
            recorded_by: state.recordedBy || "field",
          }),
        });
      } catch (logErr) {
        console.warn("Disposition log failed:", logErr.message);
      }

      // Step 3: Transition status to transporting
      try {
        await apiCall(`/incidents/${state.incidentId}/status`, {
          method: "POST",
          body: JSON.stringify({ status: "transporting" }),
        });
        state.incidentStatus = "transporting";
      } catch (statusErr) {
        console.warn("Status update failed:", statusErr.message);
      }

      resultEl.textContent = `Routed to ${facilityName}. Status: transporting.`;
      btn.disabled = false;
    },
    () => {
      resultEl.textContent = "GPS unavailable. Cannot route.";
      btn.disabled = false;
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 },
  );
});

// ── Incident summary ───────────────────────────────────────────────────────

el("refresh-summary-btn").addEventListener("click", refreshIncidentSummary);

async function refreshIncidentSummary() {
  const pre = el("incident-summary-json");
  pre.textContent = "Loading...";
  try {
    const summary = await apiCall(`/incidents/${state.incidentId}/handoff`);
    // Prefer the backend's pre-rendered plain-text handoff over raw JSON.
    // text_rendering is a deterministic, human-readable document assembled
    // from the incident record — this is what the paramedic should see.
    pre.textContent = summary.text_rendering || JSON.stringify(summary, null, 2);
  } catch (err) {
    try {
      const full = await apiCall(`/incidents/${state.incidentId}/full`);
      pre.textContent = JSON.stringify(full, null, 2);
    } catch (fallbackErr) {
      pre.textContent = `Could not load incident: ${fallbackErr.message}`;
    }
  }
}

// ── Close incident ─────────────────────────────────────────────────────────

el("close-incident-btn").addEventListener("click", () => {
  // Epic 3.1: Stop GPS tracking on incident close
  stopGpsTracking();

  // Feature 7: Stop chronometer
  stopChronometer();

  // Feature 3: Stop dispatch polling
  stopDispatchPolling();

  state.incidentId = null;
  state.recordedBy = null;
  state.fieldProtocolId = null;
  state.checklistState = null;
  state.lastVitals = null;
  state.triageEnrichment = null;
  state.incidentStatus = null;
  state.lastNotes = null;
  state.lastNoteCount = null;
  state.gpsCoords = null;
  state.routedFacility = null;
  state.protocolStepTimes = [];
  el("lookup-form").reset();
  hide(workspaceScreen);
  show(lookupScreen);

  // Hide triage context card
  el("triage-context-card").classList.add("hidden");
  el("vitals-prefill-banner").classList.add("hidden");

  // Hide new feature panels
  hide(el("dispatch-updates-panel"));
  hide(el("med-interaction-banner"));

  updateGpsStatus("");
});

// ── Utilities ──────────────────────────────────────────────────────────────

function show(node) {
  node.classList.remove("hidden");
}
function hide(node) {
  node.classList.add("hidden");
}

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

function formatTimestamp(iso) {
  if (!iso) return "\u2014";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// ── EPIC 3.4: Voice-assisted log entry (SpeechRecognition) ─────────────

let fieldRecognition = null;
let fieldIsListening = false;
let fieldVoiceTimeout = null;

function setupVoiceLogEntry() {
  const voiceBtn = el("voice-log-btn");
  const statusEl = el("voice-log-status");
  if (!voiceBtn) return;

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    voiceBtn.textContent = "Dictate (unsupported)";
    voiceBtn.disabled = true;
    return;
  }

  fieldRecognition = new SpeechRecognition();
  fieldRecognition.continuous = false;
  fieldRecognition.interimResults = true;
  fieldRecognition.lang = "en-US";

  fieldRecognition.onresult = (event) => {
    let text = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      text += event.results[i][0]. transcript;
    }
    const noteField = el("fl-note");
    if (noteField) noteField.value = text;
  };

  fieldRecognition.onerror = () => {
    fieldIsListening = false;
    voiceBtn.textContent = "Dictate";
    voiceBtn.classList.remove("btn-danger");
    voiceBtn.classList.add("btn-secondary");
    hide(statusEl);
  };

  fieldRecognition.onend = () => {
    fieldIsListening = false;
    voiceBtn.textContent = "Dictate";
    voiceBtn.classList.remove("btn-danger");
    voiceBtn.classList.add("btn-secondary");
    hide(statusEl);
    if (fieldVoiceTimeout) { clearTimeout(fieldVoiceTimeout); fieldVoiceTimeout = null; }
  };

  voiceBtn.addEventListener("click", () => {
    if (fieldIsListening) {
      fieldRecognition.stop();
    } else {
      fieldRecognition.start();
      fieldIsListening = true;
      voiceBtn.textContent = "⏹ Stop";
      voiceBtn.classList.remove("btn-secondary");
      voiceBtn.classList.add("btn-danger");
      statusEl.textContent = "Listening...";
      show(statusEl);
      // Auto-stop after 10 seconds of silence
      fieldVoiceTimeout = setTimeout(() => { if (fieldIsListening) fieldRecognition.stop(); }, 10000);
    }
  });
}

setupVoiceLogEntry();

// ── Gap 3a: Global voice command listener ───────────────────────────────

let globalRecognition = null;
let globalVoiceActive = false;

function setupGlobalVoiceCommands() {
  const toggleBtn = el("voice-control-toggle");
  const dotEl = el("voice-control-dot");
  if (!toggleBtn) return;

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    toggleBtn.disabled = true;
    toggleBtn.querySelector("span:last-child").textContent = "Voice (n/a)";
    return;
  }

  globalRecognition = new SpeechRecognition();
  globalRecognition.continuous = true;
  globalRecognition.interimResults = false;
  globalRecognition.lang = "en-US";

  globalRecognition.onresult = (event) => {
    const last = event.results[event.results.length - 1];
    if (!last.isFinal) return;
    const transcript = last[0].transcript.toLowerCase().trim();
    handleVoiceCommand(transcript);
  };

  globalRecognition.onerror = () => {
    // Restart on error if still active
    if (globalVoiceActive) {
      try { globalRecognition.start(); } catch {}
    }
  };

  globalRecognition.onend = () => {
    // Auto-restart if still active (continuous mode)
    if (globalVoiceActive) {
      try { globalRecognition.start(); } catch {}
    }
  };

  toggleBtn.addEventListener("click", () => {
    if (globalVoiceActive) {
      globalVoiceActive = false;
      globalRecognition.stop();
      toggleBtn.classList.remove("mobile-nav__btn--active");
      dotEl.classList.remove("active");
    } else {
      globalVoiceActive = true;
      try { globalRecognition.start(); } catch {}
      toggleBtn.classList.add("mobile-nav__btn--active");
      dotEl.classList.add("active");
    }
  });
}

function handleVoiceCommand(transcript) {
  // Step completion commands
  if (/next step|step complete|mark done/.test(transcript)) {
    completeNextPendingStep();
    return;
  }
  if (/mark skip/.test(transcript)) {
    skipNextPendingStep();
    return;
  }
  // Tab navigation commands
  if (/vitals/.test(transcript)) {
    scrollToSection("tab-vitals");
    return;
  }
  if (/medications?/.test(transcript)) {
    scrollToSection("tab-medications");
    return;
  }
  if (/checklist/.test(transcript)) {
    scrollToSection("tab-checklist");
    return;
  }
  if (/log|field log/.test(transcript)) {
    scrollToSection("tab-log");
    return;
  }
  if (/summary/.test(transcript)) {
    scrollToSection("tab-summary");
    return;
  }
}

function completeNextPendingStep() {
  if (!state.checklistState || !state.checklistState.steps) return;
  const next = state.checklistState.steps.find((s) => s.status === "pending");
  if (next) markStep(next.step_id, "done");
}

function skipNextPendingStep() {
  if (!state.checklistState || !state.checklistState.steps) return;
  const next = state.checklistState.steps.find((s) => s.status === "pending");
  if (next) markStep(next.step_id, "skipped");
}

setupGlobalVoiceCommands();

// ── Gap 3b: Voice entry for vitals ─────────────────────────────────────

let vitalsRecognition = null;
let vitalsIsListening = false;

const _VITALS_PATTERNS = {
  bp: /(?:b\.?p\.?|blood pressure)\s+(\d{2,3})\s*(?:over|\/)\s*(\d{2,3})/i,
  hr: /(?:heart rate|pulse|h\.?r\.?)\s+(\d{2,3})/i,
  spo2: /(?:oxygen|sats?|s\.?p\.?o\.?2?)\s*(?:is\s+)?(\d{2,3})/i,
  rr: /(?:respiratory rate|breaths?|r\.?r\.?)\s+(\d{2,3})/i,
  temp: /(?:temperature|temp)\s+(\d{2,3}(?:\.\d)?)/i,
  gcs: /(?:gcs|glasgow)\s+(\d{1,2})/i,
};

function setupVitalsVoiceEntry() {
  const btn = el("voice-vitals-btn");
  const statusEl = el("voice-vitals-status");
  if (!btn) return;

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    btn.textContent = "Voice Input (n/a)";
    btn.disabled = true;
    return;
  }

  vitalsRecognition = new SpeechRecognition();
  vitalsRecognition.continuous = false;
  vitalsRecognition.interimResults = true;
  vitalsRecognition.lang = "en-US";

  vitalsRecognition.onresult = (event) => {
    let text = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      text += event.results[i][0].transcript;
    }
    if (event.results[event.results.length - 1].isFinal) {
      parseAndPreviewVitals(text);
    }
  };

  vitalsRecognition.onerror = () => {
    vitalsIsListening = false;
    btn.textContent = "Voice Input";
    btn.classList.remove("btn-danger");
    btn.classList.add("btn-secondary");
    hide(statusEl);
  };

  vitalsRecognition.onend = () => {
    vitalsIsListening = false;
    btn.textContent = "Voice Input";
    btn.classList.remove("btn-danger");
    btn.classList.add("btn-secondary");
    hide(statusEl);
  };

  btn.addEventListener("click", () => {
    if (vitalsIsListening) {
      vitalsRecognition.stop();
    } else {
      vitalsRecognition.start();
      vitalsIsListening = true;
      btn.textContent = "⏹ Stop";
      btn.classList.remove("btn-secondary");
      btn.classList.add("btn-danger");
      statusEl.textContent = "Listening…";
      show(statusEl);
    }
  });

  // Wire up preview apply/dismiss
  el("voice-vitals-apply").addEventListener("click", applyVitalsPreview);
  el("voice-vitals-dismiss").addEventListener("click", () => {
    hide(el("voice-vitals-preview"));
  });
}

function parseAndPreviewVitals(text) {
  const parsed = {};
  const lower = text.toLowerCase();

  for (const [key, pattern] of Object.entries(_VITALS_PATTERNS)) {
    const m = lower.match(pattern);
    if (m) {
      if (key === "bp") {
        parsed.bp_systolic = parseInt(m[1], 10);
        parsed.bp_diastolic = parseInt(m[2], 10);
      } else if (key === "gcs") {
        // GCS total needs to be decomposed; fill all three fields with placeholder
        // In practice, paramedic will adjust eye/verbal/motor manually
        parsed.gcs_eye = parseInt(m[1], 10);
      } else if (key === "hr") {
        parsed.heart_rate = parseInt(m[1], 10);
      } else if (key === "spo2") {
        parsed.spo2 = parseInt(m[1], 10);
      } else if (key === "rr") {
        parsed.respiratory_rate = parseInt(m[1], 10);
      } else if (key === "temp") {
        parsed.temperature = parseFloat(m[1]);
      }
    }
  }

  if (Object.keys(parsed).length === 0) return;

  const container = el("voice-vitals-preview-items");
  container.innerHTML = "";
  const labels = {
    bp_systolic: "BP sys",
    bp_diastolic: "BP dia",
    heart_rate: "HR",
    spo2: "SpO2",
    respiratory_rate: "RR",
    temperature: "Temp",
    gcs_eye: "GCS",
  };
  for (const [key, val] of Object.entries(parsed)) {
    const span = document.createElement("span");
    span.className = "voice-vitals-preview__item";
    span.textContent = `${labels[key] || key}: ${val}`;
    container.appendChild(span);
  }

  // Store parsed values for apply
  el("voice-vitals-preview").dataset.parsed = JSON.stringify(parsed);
  show(el("voice-vitals-preview"));
}

function applyVitalsPreview() {
  const previewEl = el("voice-vitals-preview");
  const parsed = JSON.parse(previewEl.dataset.parsed || "{}");

  const fieldMap = {
    bp_systolic: "v-bp-sys",
    bp_diastolic: "v-bp-dia",
    heart_rate: "v-hr",
    spo2: "v-spo2",
    respiratory_rate: "v-rr",
    temperature: "v-temp",
    gcs_eye: "v-gcs-eye",
  };

  for (const [key, fieldId] of Object.entries(fieldMap)) {
    if (parsed[key] !== undefined) {
      const input = el(fieldId);
      if (input) input.value = parsed[key];
    }
  }

  hide(previewEl);
}

setupVitalsVoiceEntry();

// ── Feature 5: Medication interaction check on drug input ────────────────

function setupMedInteractionCheck() {
  const select = el("m-drug");
  const textInput = el("m-drug-text");
  if (select) {
    select.addEventListener("change", () => {
      if (select.value) checkMedicationInteraction(select.value);
    });
  }
  if (textInput) {
    let debounce = null;
    textInput.addEventListener("input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        if (textInput.value.trim()) checkMedicationInteraction(textInput.value.trim());
      }, 500);
    });
  }
}

setupMedInteractionCheck();

// ── Gap 3c: Large-target binary decision overlay ──────────────────────

function showDecisionOverlay(step) {
  const overlay = el("decision-overlay");
  const questionEl = el("decision-question");

  questionEl.textContent = step.title + (step.description ? "\n" + step.description : "");

  overlay.classList.remove("hidden");

  const yesBtn = el("decision-yes");
  const noBtn = el("decision-no");
  const skipBtn = el("decision-skip");

  // Remove old listeners by cloning
  const newYes = yesBtn.cloneNode(true);
  const newNo = noBtn.cloneNode(true);
  const newSkip = skipBtn.cloneNode(true);
  yesBtn.parentNode.replaceChild(newYes, yesBtn);
  noBtn.parentNode.replaceChild(newNo, noBtn);
  skipBtn.parentNode.replaceChild(newSkip, skipBtn);

  newYes.addEventListener("click", () => {
    hideDecisionOverlay();
    markStep(step.step_id, "done");
  });

  newNo.addEventListener("click", () => {
    hideDecisionOverlay();
    markStep(step.step_id, "skipped");
  });

  newSkip.addEventListener("click", () => {
    hideDecisionOverlay();
  });
}

function hideDecisionOverlay() {
  el("decision-overlay").classList.add("hidden");
}

function isDecisionStep(step) {
  if (step.step_type === "decision") return true;
  const t = (step.title + " " + (step.description || "")).toLowerCase();
  return /\bpresent\??|\bclear\??|\babnormal\??|\bpositive\??|\bconfirmed\??/.test(t);
}

function checkForDecisionStep() {
  if (!state.checklistState || !state.checklistState.steps) return;
  const pending = state.checklistState.steps.filter((s) => s.status === "pending");
  if (pending.length > 0 && isDecisionStep(pending[0])) {
    showDecisionOverlay(pending[0]);
  }
}

// ── Gap 3d: Auto NEWS2 calculation display ─────────────────────────────

let lastNews2Score = null;

function displayNews2Badge(data) {
  const badgeEl = el("news2-score-badge");
  const alertEl = el("news2-alert-banner");

  if (data.news2_score === null || data.news2_score === undefined) {
    hide(badgeEl);
    hide(alertEl);
    return;
  }

  const score = data.news2_score;
  const prevScore = lastNews2Score;
  lastNews2Score = score;

  let text = `NEWS2: ${score}`;
  let level = "normal";
  if (data.news2_risk_level) {
    const rl = data.news2_risk_level.toLowerCase();
    if (rl.includes("high")) level = "critical";
    else if (rl.includes("medium")) level = "elevated";
  }

  if (prevScore !== null && prevScore !== undefined && prevScore !== score) {
    const delta = score - prevScore;
    const arrow = delta > 0 ? "↑" : "↓";
    text += ` → ${score} ${arrow}${Math.abs(delta)}`;
  }

  badgeEl.textContent = text;
  badgeEl.className = `news2-score-badge score-flag flag-${level}`;
  show(badgeEl);

  // Critical alert banner
  if (score >= 7) {
    alertEl.textContent = `NEWS2 ALERT: Score ${score} — patient at high risk. Requires immediate clinical review.`;
    show(alertEl);
  } else {
    hide(alertEl);
  }
}

// ── Gap 3e: Offline queue status surfacing ─────────────────────────────

function setupOfflineQueueUI() {
  const toggleBtn = el("offline-queue-toggle");
  const listEl = el("offline-queue-list");
  const arrowEl = el("offline-queue-arrow");
  const sectionEl = el("offline-queue-section");

  if (!toggleBtn) return;

  toggleBtn.addEventListener("click", () => {
    const isHidden = listEl.classList.contains("hidden");
    if (isHidden) {
      renderOfflineQueueList();
      show(listEl);
      arrowEl.textContent = "▾";
    } else {
      hide(listEl);
      arrowEl.textContent = "▸";
    }
  });
}

function renderOfflineQueueList() {
  const listEl = el("offline-queue-list");
  const queue = getWriteQueue();

  if (queue.length === 0) {
    listEl.innerHTML = '<div class="record-empty">No queued actions.</div>';
    return;
  }

  listEl.innerHTML = "";
  queue.forEach((entry, i) => {
    const div = document.createElement("div");
    div.className = "record-item";
    div.innerHTML = `
      <div class="record-item__meta">${formatTimestamp(entry.queued_at)} — ${escapeHtml(entry.method)} ${escapeHtml(entry.endpoint)}</div>
    `;
    listEl.appendChild(div);
  });
}

function updateOfflineQueueDisplay() {
  const queue = getWriteQueue();
  const countEl = el("offline-queue-count");
  const sectionEl = el("offline-queue-section");

  if (queue.length > 0) {
    countEl.textContent = `${queue.length} action(s) queued`;
    countEl.classList.remove("hidden");
    if (sectionEl) sectionEl.classList.remove("hidden");
  } else {
    countEl.textContent = "";
    countEl.classList.add("hidden");
    if (sectionEl) sectionEl.classList.add("hidden");
  }
}

setupOfflineQueueUI();
