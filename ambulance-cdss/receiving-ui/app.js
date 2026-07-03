/* Ambulance CDSS — Receiving Hospital Handoff Page — app.js
 *
 * Fetches the handoff summary JSON from the ambulance-cdss API and
 * renders it as a clean, readable clinical document for ER doctors.
 *
 * The incident_id and auth token are injected by the server into
 * the HTML page at serving time via __INCIDENT_ID__ and __TOKEN__
 * placeholders.
 */

const API_BASE = window.AMBULANCE_CDSS_API_BASE || "http://localhost:8000";
const INCIDENT_ID = "__INCIDENT_ID__";
const TOKEN = "__TOKEN__";

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

async function loadHandoff() {
  if (INCIDENT_ID === "__INCIDENT_ID__" || TOKEN === "__TOKEN__") {
    showError(
      "This page must be accessed via a valid handoff link from the dispatcher.",
    );
    return;
  }

  try {
    const res = await fetch(
      `${API_BASE}/incidents/${encodeURIComponent(INCIDENT_ID)}/handoff`,
    );
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      showError(data.detail || `Server returned ${res.status}`);
      return;
    }
    const data = await res.json();
    renderHandoff(data);
  } catch (err) {
    showError(`Could not connect to the ambulance CDSS server: ${err.message}`);
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
  if (code.startsWith("P2")) banner.className = "priority-banner priority-p2";
  else if (code.startsWith("P3") || code.startsWith("P4"))
    banner.className = "priority-banner priority-p3";
  else if (code.startsWith("P1")) banner.className = "priority-banner";
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
  // Check if triage enrichment exists in the full incident data
  // The handoff endpoint may not include it directly, but we can
  // render what we have
  const section = el("triage-section");
  const container = el("triage-data");

  // Triage enrichment is not in the handoff summary directly,
  // but the highest_news2 and lowest_gcs are. We render those
  // as part of clinical alerts instead. If the handoff data
  // includes triage fields in the future, render them here.
  hide(section);
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
  container.appendChild(table);
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

// Initialize
loadHandoff();
