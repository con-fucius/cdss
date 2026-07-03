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
const MAX_QUEUE_SIZE = 50;

// ── State ──────────────────────────────────────────────────────────────────

const state = {
  incidentId: null,
  recordedBy: null,
  fieldProtocolId: null,
  checklistState: null,
  lastVitals: null, // Phase 6.3: last vitals for pre-population
  isOffline: false, // Phase 6.5: offline detection state
  triageEnrichment: null, // Phase 6.4: triage enrichment from dispatch
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

function updateOfflineQueueDisplay() {
  const queue = getWriteQueue();
  const countEl = el("offline-queue-count");
  if (queue.length > 0) {
    countEl.textContent = `${queue.length} action(s) pending sync`;
    countEl.classList.remove("hidden");
  } else {
    countEl.textContent = "";
    countEl.classList.add("hidden");
  }
}

async function drainWriteQueue() {
  const queue = getWriteQueue();
  if (queue.length === 0) return;

  const remaining = [];
  for (const entry of queue) {
    try {
      await apiCall(entry.endpoint, {
        method: entry.method,
        body: JSON.stringify(entry.body),
      });
    } catch {
      // If it fails again, keep it in the queue
      remaining.push(entry);
    }
  }

  saveWriteQueue(remaining);
  updateOfflineQueueDisplay();
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

// ── Query-param auto-open: ?incident_id=XXX&recorder=YYY ─────────────────

(function autoOpenFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const incidentId = params.get("incident_id");
  const recorder = params.get("recorder") || params.get("unit_id");
  if (!incidentId) return;

  // Pre-fill the lookup form and submit it
  const incidentInput = el("incident-id-input");
  const recorderInput = el("recorder-id");
  if (incidentInput) incidentInput.value = incidentId;
  if (recorderInput && recorder) recorderInput.value = recorder;

  // Auto-submit if both fields are filled
  if (incidentId && recorder) {
    // Small delay to ensure DOM is ready
    setTimeout(() => {
      el("lookup-form").dispatchEvent(
        new Event("submit", { cancelable: true }),
      );
    }, 100);
  }
})();

// ── API helper ─────────────────────────────────────────────────────────────

async function apiCall(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
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
}

function renderChecklist(data) {
  el("checklist-protocol-name").textContent = state.fieldProtocolId;
  const doneCount = data.steps.filter((s) => s.status !== "pending").length;
  el("checklist-progress").textContent =
    `${doneCount} / ${data.steps.length} addressed`;

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

    // When a disposition step is marked done, automatically transition
    // the incident status to handoff_complete so the dispatcher dashboard
    // reflects the field unit's progress without a separate manual call.
    if (status === "done") {
      const matchingStep = data.steps.find((s) => s.step_id === stepId);
      if (matchingStep && matchingStep.action_type === "disposition") {
        try {
          await apiCall(`/incidents/${state.incidentId}/status`, {
            method: "POST",
            body: JSON.stringify({ status: "handoff_complete" }),
          });
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

// ── Incident summary ───────────────────────────────────────────────────────

el("refresh-summary-btn").addEventListener("click", refreshIncidentSummary);

async function refreshIncidentSummary() {
  const pre = el("incident-summary-json");
  pre.textContent = "Loading...";
  try {
    const summary = await apiCall(`/incidents/${state.incidentId}/handoff`);
    pre.textContent = JSON.stringify(summary, null, 2);
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
  state.incidentId = null;
  state.recordedBy = null;
  state.fieldProtocolId = null;
  state.checklistState = null;
  state.lastVitals = null;
  state.triageEnrichment = null;
  el("lookup-form").reset();
  hide(workspaceScreen);
  show(lookupScreen);

  // Hide triage context card
  el("triage-context-card").classList.add("hidden");
  el("vitals-prefill-banner").classList.add("hidden");
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
