/* Ambulance CDSS — Receiving Hospital Handoff Page — app.js
 *
 * Fetches the handoff summary JSON from the ambulance-cdss API and
 * renders it as a clean, readable clinical document for ER doctors.
 *
 * Epic 4.2 — Live Receiving UI:
 * - SSE connection for real-time vitals, status, unit location updates
 * - NEWS2 sparkline chart (plain Canvas, no charting library)
 * - ETA countdown timer
 * - Auto-reconnect with exponential backoff on SSE connection loss
 * - Live vitals timeline that updates without page refresh
 *
 * The incident_id and auth token are injected by the server into
 * the HTML page at serving time via __INCIDENT_ID__ and __TOKEN__
 * placeholders.
 */

const API_BASE = window.AMBULANCE_CDSS_API_BASE || "http://localhost:8000";
const INCIDENT_ID = new URLSearchParams(window.location.search).get("id") || "";
const TOKEN = new URLSearchParams(window.location.search).get("token") || "";

const el = (id) => document.getElementById(id);

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

function formatTimestampShort(iso) {
  if (!iso) return "\u2014";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function scoreClass(level) {
  if (!level) return "";
  const r = level.toLowerCase();
  if (r.includes("high") || r.includes("critical")) return "score-critical";
  if (r.includes("medium") || r.includes("elevated")) return "score-elevated";
  return "";
}

function gcsFlag(total) {
  if (total === null || total === undefined) return "";
  if (total <= 8) return "score-critical";
  if (total <= 12) return "score-elevated";
  return "";
}

// ── State ────────────────────────────────────────────────────────────────

const state = {
  handoffData: null,
  sseEventSource: null,
  sseReconnectDelay: 1000,
  sseMaxReconnectDelay: 30000,
  etaDeadline: null,
  etaTimer: null,
  news2History: [], // for sparkline chart
};

// ── SSE Connection (Epic 4.2) ────────────────────────────────────────────

function connectSSE() {
  if (INCIDENT_ID === "__INCIDENT_ID__" || TOKEN === "__TOKEN__") return;

  const url = `${API_BASE}/incidents/${encodeURIComponent(INCIDENT_ID)}/stream?token=${encodeURIComponent(TOKEN)}`;

  try {
    state.sseEventSource = new EventSource(url);
  } catch {
    showSSEBanner("SSE not supported in this browser", "error");
    return;
  }

  state.sseEventSource.addEventListener("connected", () => {
    hideSSEBanner();
    state.sseReconnectDelay = 1000;
  });

  state.sseEventSource.addEventListener("vitals_added", (e) => {
    try {
      const data = JSON.parse(e.data);
      handleVitalsAdded(data);
    } catch (err) {
      console.warn("SSE event parse error:", err);
    }
  });

  state.sseEventSource.addEventListener("medication_added", (e) => {
    try {
      const data = JSON.parse(e.data);
      handleMedicationAdded(data);
    } catch (err) {
      console.warn("SSE event parse error:", err);
    }
  });

  state.sseEventSource.addEventListener("status_changed", (e) => {
    try {
      const data = JSON.parse(e.data);
      handleStatusChanged(data);
    } catch (err) {
      console.warn("SSE event parse error:", err);
    }
  });

  state.sseEventSource.addEventListener("unit_location_updated", (e) => {
    try {
      const data = JSON.parse(e.data);
      handleUnitLocationUpdated(data);
    } catch (err) {
      console.warn("SSE event parse error:", err);
    }
  });

  state.sseEventSource.addEventListener("field_log_added", (e) => {
    try {
      const data = JSON.parse(e.data);
      handleFieldLogAdded(data);
    } catch (err) {
      console.warn("SSE event parse error:", err);
    }
  });

  state.sseEventSource.addEventListener("stream_closed", () => {
    state.sseEventSource.close();
    state.sseEventSource = null;
  });

  state.sseEventSource.onerror = () => {
    state.sseEventSource.close();
    state.sseEventSource = null;
    showSSEBanner(
      `Reconnecting... (${Math.round(state.sseReconnectDelay / 1000)}s)`,
      "warning",
    );
    setTimeout(() => {
      state.sseReconnectDelay = Math.min(
        state.sseReconnectDelay * 2,
        state.sseMaxReconnectDelay,
      );
      connectSSE();
    }, state.sseReconnectDelay);
  };
}

function showSSEBanner(text, type) {
  const banner = el("sse-banner");
  if (!banner) return;
  banner.textContent = text;
  banner.className = `sse-banner sse-banner--${type}`;
  show(banner);
}

function hideSSEBanner() {
  const banner = el("sse-banner");
  if (banner) hide(banner);
}

// ── SSE Event Handlers ───────────────────────────────────────────────────

async function handleVitalsAdded(_data) {
  // Refresh the handoff data to get latest vitals, then re-render vitals section
  try {
    const res = await fetch(
      `${API_BASE}/incidents/${encodeURIComponent(INCIDENT_ID)}/handoff?token=${encodeURIComponent(TOKEN)}`,
    );
    if (res.ok) {
      const updated = await res.json();
      state.handoffData = updated;
      renderVitalsTimeline(updated.vitals_timeline || []);
      renderClinicalAlerts(updated);
      // Update NEWS2 sparkline
      if (updated.vitals_timeline) {
        state.news2History = updated.vitals_timeline
          .filter((v) => v.news2_score !== null && v.news2_score !== undefined)
          .map((v) => ({
            score: parseFloat(v.news2_score) || 0,
            time: v.recorded_at,
          }));
        renderNews2Sparkline();
      }
    }
  } catch { /* best effort */ }
}

async function handleMedicationAdded(_data) {
  try {
    const res = await fetch(
      `${API_BASE}/incidents/${encodeURIComponent(INCIDENT_ID)}/handoff?token=${encodeURIComponent(TOKEN)}`,
    );
    if (res.ok) {
      const updated = await res.json();
      state.handoffData = updated;
      renderMedications(updated.medications_given || []);
    }
  } catch { /* best effort */ }
}

function handleStatusChanged(data) {
  const statusEl = el("status-value");
  if (statusEl && data.status) {
    statusEl.textContent = `Status: ${data.status.replace(/_/g, " ")}`;
  }
}

function handleUnitLocationUpdated(data) {
  if (data && data.location) {
    const lat = data.location.lat || data.lat;
    const lon = data.location.lon || data.lon;
    if (lat !== undefined && lon !== undefined) {
      updateReceivingMapUnitLocation(lat, lon);
    }
  }
}

async function handleFieldLogAdded(_data) {
  try {
    const res = await fetch(
      `${API_BASE}/incidents/${encodeURIComponent(INCIDENT_ID)}/handoff?token=${encodeURIComponent(TOKEN)}`,
    );
    if (res.ok) {
      const updated = await res.json();
      state.handoffData = updated;
      renderFieldActions(updated.field_actions || []);
    }
  } catch { /* best effort */ }
}

// ── NEWS2 Sparkline Chart (Epic 4.2) ────────────────────────────────────

function renderNews2Sparkline() {
  const canvas = el("news2-sparkline");
  if (!canvas || state.news2History.length === 0) return;

  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width || 800;
  canvas.height = rect.height || 120;
  const data = state.news2History;
  const w = canvas.width;
  const h = canvas.height;
  const padding = { top: 10, bottom: 20, left: 30, right: 10 };
  const plotW = w - padding.left - padding.right;
  const plotH = h - padding.top - padding.bottom;

  ctx.clearRect(0, 0, w, h);

  // Y axis: 0 to max score (or 20, whichever is larger)
  const maxScore = Math.max(20, ...data.map((d) => d.score));
  const yScale = (val) => padding.top + plotH * (1 - val / maxScore);
  const xScale = (i) => padding.left + (i / Math.max(1, data.length - 1)) * plotW;

  // Draw grid lines
  ctx.strokeStyle = "#dee2e6";
  ctx.lineWidth = 0.5;
  for (let y = 0; y <= maxScore; y += 5) {
    const py = yScale(y);
    ctx.beginPath();
    ctx.moveTo(padding.left, py);
    ctx.lineTo(w - padding.right, py);
    ctx.stroke();
    ctx.fillStyle = "#6c757d";
    ctx.font = "10px monospace";
    ctx.textAlign = "right";
    ctx.fillText(String(y), padding.left - 4, py + 3);
  }

  // Risk zones
  // High zone (>= 7): red
  ctx.fillStyle = "rgba(220, 53, 69, 0.08)";
  ctx.fillRect(padding.left, yScale(maxScore), plotW, yScale(7) - yScale(maxScore));
  // Medium zone (>= 5): amber
  ctx.fillStyle = "rgba(253, 126, 20, 0.08)";
  ctx.fillRect(padding.left, yScale(7), plotW, yScale(5) - yScale(7));

  // Draw line
  ctx.beginPath();
  ctx.strokeStyle = "#0d6efd";
  ctx.lineWidth = 2;
  data.forEach((d, i) => {
    const x = xScale(i);
    const y = yScale(d.score);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Draw dots
  data.forEach((d, i) => {
    const x = xScale(i);
    const y = yScale(d.score);
    let color = "#0d6efd";
    if (d.score >= 7) color = "#dc3545";
    else if (d.score >= 5) color = "#fd7e14";
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  });

  // X axis labels (times)
  ctx.fillStyle = "#6c757d";
  ctx.font = "9px monospace";
  ctx.textAlign = "center";
  const maxLabels = Math.min(data.length, 6);
  const step = Math.max(1, Math.floor(data.length / maxLabels));
  data.forEach((d, i) => {
    if (i % step === 0 || i === data.length - 1) {
      const time = formatTimestampShort(d.time);
      ctx.fillText(time, xScale(i), h - 4);
    }
  });
}

// ── ETA Countdown (Epic 4.2) ────────────────────────────────────────────

function startETACountdown(etaMinutes) {
  if (!etaMinutes || etaMinutes <= 0) return;

  state.etaDeadline = Date.now() + etaMinutes * 60 * 1000;

  if (state.etaTimer) clearInterval(state.etaTimer);

  state.etaTimer = setInterval(() => {
    const remaining = state.etaDeadline - Date.now();
    const etaEl = el("eta-countdown");
    if (!etaEl) return;

    if (remaining <= 0) {
      etaEl.textContent = "PATIENT ARRIVING";
      etaEl.className = "eta-countdown eta-countdown--arrived";
      clearInterval(state.etaTimer);
      return;
    }

    const mins = Math.floor(remaining / 60000);
    const secs = Math.floor((remaining % 60000) / 1000);
    etaEl.textContent = `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;

    if (mins < 2) {
      etaEl.className = "eta-countdown eta-countdown--flash";
    } else if (mins < 5) {
      etaEl.className = "eta-countdown eta-countdown--red";
    } else if (mins < 10) {
      etaEl.className = "eta-countdown eta-countdown--yellow";
    } else {
      etaEl.className = "eta-countdown eta-countdown--green";
    }
  }, 1000);
}

// ── Data Loading ─────────────────────────────────────────────────────────

async function loadHandoff() {
  if (INCIDENT_ID === "__INCIDENT_ID__" || TOKEN === "__TOKEN__") {
    showError(
      "This page must be accessed via a valid handoff link from the dispatcher.",
    );
    return;
  }

  try {
    const res = await fetch(
      `${API_BASE}/incidents/${encodeURIComponent(INCIDENT_ID)}/handoff?token=${encodeURIComponent(TOKEN)}`,
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      showError(data.detail || `Server returned ${res.status}`);
      return;
    }
    const data = await res.json();
    state.handoffData = data;
    renderHandoff(data);

    // Start SSE after initial render
    connectSSE();

    // Start ETA countdown if ETA is available
    if (data.eta_minutes) {
      startETACountdown(data.eta_minutes);
    }

    // Populate NEWS2 sparkline history
    if (data.vitals_timeline) {
      state.news2History = data.vitals_timeline
        .filter((v) => v.news2_score !== null && v.news2_score !== undefined)
        .map((v) => ({
          score: parseFloat(v.news2_score) || 0,
          time: v.recorded_at,
        }));
      renderNews2Sparkline();
    }
  } catch (err) {
    showError(
      `Could not connect to the ambulance CDSS server: ${err.message}`,
    );
  }
}

function showError(message) {
  hide(el("loading-screen"));
  hide(el("handoff-page"));
  el("error-message").textContent = message;
  show(el("error-screen"));
}

function renderHandoff(data) {
  hide(el("loading-screen"));
  show(el("handoff-page"));

  // Priority banner
  const banner = el("priority-banner");
  const code = data.priority_code || "PRIORITY NOT RECORDED";
  el("priority-code").textContent = code;
  if (code.startsWith("P1")) banner.className = "priority-banner priority-p1";
  else if (code.startsWith("P2")) banner.className = "priority-banner priority-p2";
  else if (code.startsWith("P3")) banner.className = "priority-banner priority-p3";
  else if (code.startsWith("P4")) banner.className = "priority-banner priority-p4";
  else banner.className = "priority-banner";

  el("incident-id").textContent = `ID: ${data.incident_id}`;
  el("status-value").textContent =
    `Status: ${(data.status || "").replace(/_/g, " ")}`;
  el("unit-value").textContent =
    `Unit: ${data.assigned_unit_id || "not assigned"}`;

  // Chief complaint
  el("chief-complaint").textContent = data.chief_complaint || "not recorded";

  // Facility
  const facilityName = data.routed_facility_name || data.routed_facility_id;
  el("facility-name").textContent = facilityName || "No facility routed";
  if (data.routed_facility_id && data.routed_facility_name) {
    el("facility-detail").textContent =
      `Facility ID: ${data.routed_facility_id}`;
  }

  // Protocol
  const parts = [];
  if (data.dispatch_protocol_id) {
    parts.push(
      `Dispatch: ${data.dispatch_protocol_id} (v${data.dispatch_protocol_version || "-"})`,
    );
  }
  if (data.field_protocol_id) {
    parts.push(
      `Field: ${data.field_protocol_id} (v${data.field_protocol_version || "-"})`,
    );
  }
  el("protocol-info").textContent =
    parts.length > 0 ? parts.join(" | ") : "No protocol recorded";

  // Dispatch Q&A
  renderDispatchQA(data.dispatch_qa || []);

  // Triage enrichment
  renderTriageEnrichment(data);

  // Clinical alerts
  renderClinicalAlerts(data);

  // Vitals timeline
  renderVitalsTimeline(data.vitals_timeline || []);

  // Medications
  renderMedications(data.medications_given || []);

  // Field actions
  renderFieldActions(data.field_actions || []);

  // Footer
  el("generated-at").textContent =
    `Generated at: ${formatTimestamp(new Date().toISOString())}`;

  // Gap 5a: Show active alert banner if patient is incoming
  checkAndShowAlertBanner(data);
}

function renderDispatchQA(qa) {
  const container = el("dispatch-qa");
  if (qa.length === 0) {
    container.innerHTML =
      '<div class="empty-data">No dispatch answers recorded.</div>';
    return;
  }
  container.innerHTML = "";
  qa.forEach((row) => {
    const div = document.createElement("div");
    div.className = `qa-item${row.is_backtrack ? " backtrack" : ""}`;
    div.innerHTML = `
      <div class="qa-item__question">${escapeHtml(row.question_text)}</div>
      <div class="qa-item__answer">${escapeHtml(row.answer)}${row.is_backtrack ? " (backtrack)" : ""}</div>
    `;
    container.appendChild(div);
  });
}

function renderTriageEnrichment(data) {
  const section = el("triage-section");
  const container = el("triage-data");

  const triage = data.triage_enrichment;
  if (!triage || typeof triage !== "object" || Object.keys(triage).length === 0) {
    hide(section);
    return;
  }

  const fields = [];
  if (triage.triage_level !== null && triage.triage_level !== undefined) {
    fields.push({ label: "Triage Level", value: triage.triage_level });
  }
  if (triage.top_diagnosis) {
    fields.push({ label: "Top Diagnosis", value: triage.top_diagnosis });
  }
  if (triage.esi_level !== null && triage.esi_level !== undefined) {
    fields.push({ label: "ESI Level", value: triage.esi_level });
  }
  if (triage.shock_index !== null && triage.shock_index !== undefined) {
    fields.push({ label: "Shock Index", value: triage.shock_index });
  }

  if (fields.length === 0) {
    hide(section);
    return;
  }

  container.innerHTML = "";
  const table = document.createElement("table");
  table.className = "vitals-table";
  const tbody = document.createElement("tbody");
  fields.forEach((f) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td style="font-weight:600;width:40%">${escapeHtml(f.label)}</td><td>${escapeHtml(String(f.value))}</td>`;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  container.appendChild(table);
  show(section);
}

function renderClinicalAlerts(data) {
  const alertsContainer = el("clinical-alerts");
  let hasAlerts = false;

  // NEWS2 alert
  const news2Alert = el("news2-alert");
  if (
    data.highest_news2 &&
    data.highest_news2.news2_score !== null &&
    data.highest_news2.news2_score !== undefined
  ) {
    hasAlerts = true;
    show(news2Alert);
    const score = data.highest_news2.news2_score;
    const level = data.highest_news2.news2_risk_level || "";
    const flag = score.includes("high")
      ? "critical"
      : score.includes("medium")
        ? "elevated"
        : "";
    news2Alert.className = `alert-card ${flag}`;
    el("news2-value").textContent = score;
    el("news2-detail").textContent =
      `${level} at ${formatTimestampShort(data.highest_news2.recorded_at)}`;
  }

  // GCS alert
  const gcsAlert = el("gcs-alert");
  if (
    data.lowest_gcs &&
    data.lowest_gcs.gcs_total !== null &&
    data.lowest_gcs.gcs_total !== undefined
  ) {
    hasAlerts = true;
    show(gcsAlert);
    const total = data.lowest_gcs.gcs_total;
    const flag = total <= 8 ? "critical" : total <= 12 ? "elevated" : "";
    gcsAlert.className = `alert-card ${flag}`;
    el("gcs-value").textContent = total;
    el("gcs-detail").textContent =
      `at ${formatTimestampShort(data.lowest_gcs.recorded_at)}`;
  }

  if (hasAlerts) {
    show(alertsContainer);
  }
}

function renderVitalsTimeline(vitals) {
  const container = el("vitals-timeline");
  if (vitals.length === 0) {
    container.innerHTML = '<div class="empty-data">No vitals recorded.</div>';
    return;
  }

  const table = document.createElement("table");
  table.className = "vitals-table";
  table.innerHTML = `
    <thead>
      <tr>
        <th>Time</th>
        <th>Recorded by</th>
        <th>RR</th>
        <th>SpO2</th>
        <th>BP</th>
        <th>HR</th>
        <th>Temp</th>
        <th>AVPU</th>
        <th>NEWS2</th>
        <th>GCS</th>
      </tr>
    </thead>
  `;
  const tbody = document.createElement("tbody");

  vitals.forEach((v) => {
    const tr = document.createElement("tr");
    const bp =
      v.bp_systolic !== null && v.bp_systolic !== undefined
        ? `${v.bp_systolic}/${v.bp_diastolic || "?"}`
        : "\u2014";
    const news2Class = scoreClass(v.news2_risk_level);
    const gcsClass = gcsFlag(v.gcs_total);

    tr.innerHTML = `
      <td>${formatTimestampShort(v.recorded_at)}</td>
      <td>${escapeHtml(v.recorded_by || "\u2014")}</td>
      <td>${v.respiratory_rate !== null && v.respiratory_rate !== undefined ? v.respiratory_rate : "\u2014"}</td>
      <td>${v.spo2 !== null && v.spo2 !== undefined ? v.spo2 + "%" : "\u2014"}</td>
      <td>${bp}</td>
      <td>${v.heart_rate !== null && v.heart_rate !== undefined ? v.heart_rate : "\u2014"}</td>
      <td>${v.temperature !== null && v.temperature !== undefined ? v.temperature + "\u00b0C" : "\u2014"}</td>
      <td>${v.consciousness || "\u2014"}</td>
      <td class="${news2Class}">${v.news2_score !== null && v.news2_score !== undefined ? v.news2_score : "\u2014"}</td>
      <td class="${gcsClass}">${v.gcs_total !== null && v.gcs_total !== undefined ? v.gcs_total : "\u2014"}</td>
    `;
    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  container.innerHTML = "";
  const wrapper = document.createElement("div");
  wrapper.className = "vitals-table-wrapper";
  wrapper.appendChild(table);
  container.appendChild(wrapper);
}

function renderMedications(meds) {
  const container = el("medications");
  if (meds.length === 0) {
    container.innerHTML =
      '<div class="empty-data">No medications or items recorded.</div>';
    return;
  }

  const administered = meds.filter((m) => m.administered !== false);
  const notAdministered = meds.filter((m) => m.administered === false);

  container.innerHTML = "";

  if (administered.length > 0) {
    const group = document.createElement("div");
    group.className = "medication-group";
    group.innerHTML = '<div class="medication-group__title">Administered</div>';
    administered.forEach((m) => {
      const item = document.createElement("div");
      item.className = "medication-item";
      item.innerHTML = `
        <span class="medication-item__name">${escapeHtml(m.drug_name)}</span>
        <span class="medication-item__detail">${escapeHtml(m.dose)} ${escapeHtml(m.route)}</span>
        <span class="medication-item__detail">${formatTimestampShort(m.given_at)} by ${escapeHtml(m.given_by)}</span>
      `;
      group.appendChild(item);
    });
    container.appendChild(group);
  }

  if (notAdministered.length > 0) {
    const group = document.createElement("div");
    group.className = "medication-group";
    group.innerHTML =
      '<div class="medication-group__title">Carried / considered, NOT administered</div>';
    notAdministered.forEach((m) => {
      const item = document.createElement("div");
      item.className = "medication-item not-administered";
      item.innerHTML = `
        <span class="medication-item__name">${escapeHtml(m.drug_name)}</span>
        <span class="medication-item__detail">${escapeHtml(m.dose)} ${escapeHtml(m.route)}</span>
        <span class="medication-item__detail">${formatTimestampShort(m.given_at)} by ${escapeHtml(m.given_by)}</span>
      `;
      group.appendChild(item);
    });
    container.appendChild(group);
  }
}

function renderFieldActions(actions) {
  const container = el("field-actions");
  if (actions.length === 0) {
    container.innerHTML =
      '<div class="empty-data">No field actions recorded.</div>';
    return;
  }
  container.innerHTML = "";
  actions.forEach((a) => {
    const div = document.createElement("div");
    const isConfirmation = a.action_type === "pre_arrival_confirmation";
    div.className = `action-item${isConfirmation ? " confirmation" : ""}`;

    const detail = isConfirmation
      ? `Pre-arrival instructions confirmed by ${escapeHtml(a.data?.confirmed_by || "?")}`
      : a.data?.note || a.data?.step_title || JSON.stringify(a.data || {});

    div.innerHTML = `
      <div class="action-item__header">
        <span class="action-item__type">${escapeHtml(a.action_type)}</span>
        <span class="action-item__time">${formatTimestampShort(a.timestamp)}</span>
      </div>
      <div class="action-item__detail">${escapeHtml(String(detail))}</div>
    `;
    container.appendChild(div);
  });
}

// ── EPIC 2.2: Unit tracking map for ER team ────────────────────────────

let receivingMap = null;
let unitBlip = null;
let incidentPin = null;
let facilityPin = null;
let unitTrackingPollTimer = null;

function initReceivingMap() {
  const mapDiv = el("receiving-map");
  if (!mapDiv || receivingMap) return;
  receivingMap = L.map("receiving-map").setView([-1.286389, 36.817223], 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
  }).addTo(receivingMap);
}

function updateReceivingMapUnitLocation(lat, lon) {
  if (!receivingMap) initReceivingMap();
  if (unitBlip) {
    unitBlip.setLatLng([lat, lon]);
  } else {
    unitBlip = L.circleMarker([lat, lon], {
      radius: 8, color: "#2f7de1", fillColor: "#2f7de1", fillOpacity: 0.9,
    }).addTo(receivingMap).bindPopup("Ambulance unit");
  }
  receivingMap.fitBounds(unitBlip.getBounds(), { maxZoom: 15, padding: [50, 50] });
}

function startReceivingUnitTracking() {
  if (unitTrackingPollTimer) return;
  unitTrackingPollTimer = setInterval(async () => {
    if (INCIDENT_ID === "__INCIDENT_ID__") return;
    try {
      const res = await fetch(`${API_BASE}/incidents/${encodeURIComponent(INCIDENT_ID)}/unit-location/latest?token=${encodeURIComponent(TOKEN)}`);
      if (res.ok) {
        const data = await res.json();
        if (data.location) {
          updateReceivingMapUnitLocation(data.location.lat, data.location.lon);
        }
      }
    } catch { /* ignore polling errors */ }
  }, 15000);
}

// ── Gap 5a: Active alert banner ─────────────────────────────────────────

function showAlertBanner(code, etaMinutes) {
  const banner = el("alert-banner");
  const textEl = el("alert-banner-text");
  const ackBtn = el("ack-btn");
  if (!banner || !textEl) return;

  const etaText = etaMinutes ? `— ETA: ${etaMinutes} min` : "";
  textEl.textContent = `INCOMING PATIENT — Priority ${code} ${etaText}`;

  let level = "p4";
  if (code.startsWith("P1")) level = "p1";
  else if (code.startsWith("P2")) level = "p2";
  else if (code.startsWith("P3")) level = "p3";

  banner.className = `alert-banner visible alert-banner--${level}`;
  if (ackBtn) ackBtn.style.display = "inline-block";

  // Play audible beep for P1/P2
  if (level === "p1" || level === "p2") {
    playAlertBeep(level === "p1" ? 880 : 660, level === "p1" ? 400 : 250);
  }
}

function playAlertBeep(freq, duration) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.value = 0.3;
    osc.start();
    osc.stop(ctx.currentTime + duration / 1000);
  } catch { /* Web Audio not available */ }
}

// ── Gap 5c: Acknowledgement mechanism ───────────────────────────────────

function getAckKey() {
  return `ack_${INCIDENT_ID}`;
}

function isAcknowledged() {
  const ack = sessionStorage.getItem(getAckKey());
  return ack ? JSON.parse(ack) : null;
}

function storeAcknowledgement(ackData) {
  sessionStorage.setItem(getAckKey(), JSON.stringify(ackData));
}

function showAcknowledgedBanner(ackData) {
  const banner = el("alert-banner");
  const textEl = el("alert-banner-text");
  const ackBtn = el("ack-btn");
  if (!banner || !textEl) return;

  const who = ackData.acknowledged_by || "ER team";
  const when = ackData.acknowledged_at || new Date().toISOString();
  textEl.innerHTML = `ACKNOWLEDGED — Bay being prepared <span class="ack-time">by ${escapeHtml(who)} at ${formatTimestampShort(when)}</span>`;
  banner.className = "alert-banner visible alert-banner--acknowledged";
  if (ackBtn) ackBtn.style.display = "none";
}

async function handleAcknowledge() {
  const ackData = {
    acknowledged_by: "receiving_ui",
    acknowledged_at: new Date().toISOString(),
  };

  try {
    await fetch(
      `${API_BASE}/incidents/${encodeURIComponent(INCIDENT_ID)}/field-log?token=${encodeURIComponent(TOKEN)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action_type: "acknowledgement",
          data: { message: "ER team notified", acknowledged_by: "receiving_ui" },
        }),
      },
    );
  } catch { /* best effort */ }

  storeAcknowledgement(ackData);
  showAcknowledgedBanner(ackData);
}

function checkAndShowAlertBanner(data) {
  const status = data.status || "";
  const activeStatuses = ["dispatched", "on_scene", "transporting"];
  if (!activeStatuses.includes(status)) return;

  // Check if already acknowledged
  const existingAck = isAcknowledged();
  if (existingAck) {
    showAcknowledgedBanner(existingAck);
    return;
  }

  const code = data.priority_code || "";
  if (code && (code.startsWith("P1") || code.startsWith("P2") || code.startsWith("P3") || code.startsWith("P4"))) {
    showAlertBanner(code, data.eta_minutes);
  }
}

// ── Gap 5e: Copy Summary ────────────────────────────────────────────────

function handleCopySummary() {
  const data = state.handoffData;
  if (!data) return;

  const lines = [];
  lines.push("AMBULANCE HANDOFF SUMMARY");
  lines.push("=".repeat(40));
  lines.push(`Priority: ${data.priority_code || "N/A"}`);
  lines.push(`Incident ID: ${data.incident_id || "N/A"}`);
  lines.push(`Status: ${(data.status || "").replace(/_/g, " ")}`);
  lines.push(`Unit: ${data.assigned_unit_id || "not assigned"}`);
  lines.push("");
  lines.push(`Chief Complaint: ${data.chief_complaint || "not recorded"}`);
  lines.push(`Receiving Facility: ${data.routed_facility_name || data.routed_facility_id || "No facility routed"}`);
  lines.push(`ETA: ${data.eta_minutes ? data.eta_minutes + " min" : "N/A"}`);
  lines.push("");

  if (data.dispatch_qa && data.dispatch_qa.length > 0) {
    lines.push("DISPATCH Q&A");
    data.dispatch_qa.forEach((qa) => {
      lines.push(`  Q: ${qa.question_text}`);
      lines.push(`  A: ${qa.answer}`);
    });
    lines.push("");
  }

  if (data.vitals_timeline && data.vitals_timeline.length > 0) {
    lines.push("VITALS");
    data.vitals_timeline.forEach((v) => {
      const bp = v.bp_systolic !== null ? `${v.bp_systolic}/${v.bp_diastolic || "?"}` : "N/A";
      lines.push(`  ${formatTimestampShort(v.recorded_at)} — HR:${v.heart_rate ?? "N/A"} BP:${bp} SpO2:${v.spo2 ?? "N/A"}% RR:${v.respiratory_rate ?? "N/A"} NEWS2:${v.news2_score ?? "N/A"}`);
    });
    lines.push("");
  }

  if (data.medications_given && data.medications_given.length > 0) {
    lines.push("MEDICATIONS");
    data.medications_given.forEach((m) => {
      lines.push(`  ${m.drug_name} ${m.dose} ${m.route} (${m.administered !== false ? "given" : "NOT given"})`);
    });
    lines.push("");
  }

  const text = lines.join("\n");
  navigator.clipboard.writeText(text).then(() => {
    const btn = el("copy-btn");
    if (btn) {
      btn.textContent = "Copied!";
      setTimeout(() => { btn.textContent = "Copy Summary"; }, 2000);
    }
  }).catch(() => {});
}

// Initialize
loadHandoff();

// Start map and tracking after load
setTimeout(() => {
  initReceivingMap();
  startReceivingUnitTracking();
}, 500);
