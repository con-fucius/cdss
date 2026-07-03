/* Ambulance CDSS Dispatcher Console — app.js
 *
 * Deliberately a single plain-JS file, no framework, no build step. This
 * is a controlled-workstation operator console for a narrow, fixed set
 * of screens (intake -> locked script -> terminal outcome), not a
 * general web app — the dependency-free, build-step-free approach keeps
 * it auditable end to end, consistent with the backend's "deliberately
 * small" posture (see app/config.py docstring in the API repo).
 *
 * Hard rule mirrored from the backend (docs/GOVERNANCE.md): an
 * out-of-script answer is a hard, loud, visible error here too — this
 * file never guesses, never silently retries with a different answer,
 * and never hides a 403/422/500 from the operator.
 */

const API_BASE = window.AMBULANCE_CDSS_API_BASE || "http://localhost:8000";

// ── State ──────────────────────────────────────────────────────────────────

const state = {
  incidentId: null,
  protocolId: null,
  dispatcherId: null,
  currentQuestion: null,
  transcript: [], // [{question_text, answer, is_backtrack}]
  backtrackingPermitted: false,
  terminalOutcome: null, // set when script reaches terminal outcome
  preArrivalConfirmed: false,
  triageEnrichment: null, // Phase 5.5: triage enrichment data
  outcomeReachedAt: null, // Phase 5.4: timestamp when terminal outcome was reached
  isOffline: false, // Phase 5.1: offline detection state
};

// ── DOM refs ───────────────────────────────────────────────────────────────

const el = (id) => document.getElementById(id);

const intakeScreen = el("intake-screen");
const scriptScreen = el("script-screen");

// ── Phase 5.1: Offline detection banner ────────────────────────────────────

let offlineBannerTimeout = null;

function setOfflineBanner(offline) {
  const banner = el("offline-banner");
  const statusEl = el("connection-status");

  state.isOffline = offline;

  if (offline) {
    banner.classList.remove("hidden");
    document.body.classList.add("offline");
    statusEl.textContent = "offline — API unreachable";
    statusEl.className = "app-header__status error";
  } else {
    banner.classList.add("hidden");
    document.body.classList.remove("offline");
    statusEl.className = "app-header__status ok";
  }
}

// Phase 5.6: Keyboard navigation — ensure all interactive elements are focusable
let keyboardNavInitialized = false;
function setupKeyboardNavigation() {
  if (keyboardNavInitialized) return;
  keyboardNavInitialized = true;

  // Add tabindex=0 to all buttons and interactive elements
  document.querySelectorAll("button, input, select, a").forEach((el) => {
    if (!el.hasAttribute("tabindex")) {
      el.setAttribute("tabindex", "0");
    }
  });

  // Add keyboard event listeners for Enter and Space on answer buttons
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      const target = e.target;
      if (target.classList.contains("answer-btn") && !target.disabled) {
        e.preventDefault();
        target.click();
      }
    }
  });
}

// ── Connection check ───────────────────────────────────────────────────────

async function checkConnection() {
  const statusEl = el("connection-status");
  try {
    const res = await fetch(`${API_BASE}/health`);
    const data = await res.json();
    state.backtrackingPermitted = !!data.backtracking_permitted;
    if (data.status === "ok") {
      statusEl.textContent = `connected — ${data.active_protocols} protocol(s) active`;
      statusEl.className = "app-header__status ok";
      setOfflineBanner(false);
    } else {
      statusEl.textContent = `degraded — database: ${data.database}, ${data.active_protocols} protocol(s) active`;
      statusEl.className = "app-header__status degraded";
      setOfflineBanner(false);
    }
    if (data.rejected_protocols > 0) {
      statusEl.textContent += ` (${data.rejected_protocols} protocol file(s) rejected at load — see server log)`;
    }
  } catch (err) {
    statusEl.textContent = "cannot reach API — check connection";
    statusEl.className = "app-header__status error";
    setOfflineBanner(true);
  }
}

checkConnection();
setInterval(checkConnection, 30000);

// ── API helper — never swallows errors, always surfaces them ────────────────

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

// ── Phase 5.3: Manual protocol selector ────────────────────────────────────

async function loadManualProtocols() {
  const select = el("manual-protocol-select");
  const applyBtn = el("apply-manual-protocol-btn");
  const resultEl = el("manual-protocol-result");

  try {
    // Load both dispatch protocols and field protocols
    const [dispatchData, fieldData] = await Promise.all([
      apiCall("/protocols").catch(() => ({ active: [] })),
      apiCall("/field-protocols").catch(() => ({ active: [] })),
    ]);

    select.innerHTML = '<option value="">— Select protocol —</option>';

    // Add dispatch protocols
    if (dispatchData.active && dispatchData.active.length > 0) {
      const group = document.createElement("optgroup");
      group.label = "Dispatch protocols";
      dispatchData.active.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.protocol_id;
        opt.textContent = `${p.disease_or_presentation} (${p.version})`;
        group.appendChild(opt);
      });
      select.appendChild(group);
    }

    // Add field protocols
    if (fieldData.active && fieldData.active.length > 0) {
      const group = document.createElement("optgroup");
      group.label = "Field protocols";
      fieldData.active.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = `field:${p.protocol_id}`;
        opt.textContent = `${p.disease_or_presentation} (${p.step_count} steps)`;
        group.appendChild(opt);
      });
      select.appendChild(group);
    }

    applyBtn.disabled = true;
    resultEl.textContent = "";

    select.addEventListener("change", () => {
      applyBtn.disabled = !select.value;
    });
  } catch (err) {
    select.innerHTML = '<option value="">Could not load protocols</option>';
    resultEl.textContent = `Error loading protocols: ${err.message}`;
  }
}

// ── Phase 5.5: Triage enrichment display ───────────────────────────────────

function renderTriageEnrichment(enrichment) {
  const card = el("triage-enrichment-card");
  if (!enrichment) {
    card.classList.add("hidden");
    return;
  }

  state.triageEnrichment = enrichment;
  card.classList.remove("hidden");

  // Set triage level badge with color coding
  const levelBadge = el("triage-level-badge");
  levelBadge.textContent = enrichment.triage_level || "Unknown";
  levelBadge.className = "triage-level-badge";
  if (enrichment.triage_level) {
    levelBadge.classList.add(
      `triage-level-${enrichment.triage_level.toLowerCase()}`,
    );
  }

  // Set top diagnosis
  const diagnosisEl = el("triage-top-diagnosis");
  diagnosisEl.textContent = enrichment.top_diagnosis || "Not determined";

  // Set ESI level
  const esiEl = el("triage-esi-level");
  esiEl.textContent = enrichment.esi_level
    ? `${enrichment.esi_level} of 5`
    : "Not calculated";

  // Set shock index if available
  const shockRow = el("triage-shock-index-row");
  const shockEl = el("triage-shock-index");
  if (enrichment.shock_index) {
    shockRow.classList.remove("hidden");
    shockEl.textContent = enrichment.shock_index.toFixed(2);
  } else {
    shockRow.classList.add("hidden");
  }

  // Set source
  const sourceEl = el("triage-source");
  sourceEl.textContent = enrichment.degraded_mode
    ? "Degraded (rules only)"
    : "Full NLP pipeline";
}

// Toggle triage enrichment panel
el("triage-enrichment-toggle").addEventListener("click", () => {
  const body = el("triage-enrichment-body");
  const toggle = el("triage-enrichment-toggle");
  const isExpanded = toggle.getAttribute("aria-expanded") === "true";

  if (isExpanded) {
    body.classList.add("hidden");
    toggle.setAttribute("aria-expanded", "false");
    toggle.querySelector(".triage-enrichment-toggle__icon").textContent = "▸";
  } else {
    body.classList.remove("hidden");
    toggle.setAttribute("aria-expanded", "true");
    toggle.querySelector(".triage-enrichment-toggle__icon").textContent = "▾";
  }
});

// ── Intake screen ──────────────────────────────────────────────────────────

el("intake-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  hide(el("intake-error"));

  // Phase 5.1: Don't allow new incidents when offline
  if (state.isOffline) {
    el("intake-error").textContent =
      "Cannot create incidents while offline. Please wait for connection to be restored.";
    show(el("intake-error"));
    return;
  }

  const chiefComplaint = el("chief-complaint").value.trim();
  const dispatcherId = el("dispatcher-id").value.trim();
  const lat = el("caller-lat").value
    ? parseFloat(el("caller-lat").value)
    : null;
  const lon = el("caller-lon").value
    ? parseFloat(el("caller-lon").value)
    : null;
  const locText = el("caller-location-text").value.trim() || null;

  const submitBtn = e.target.querySelector("button[type=submit]");
  submitBtn.disabled = true;

  try {
    const data = await apiCall("/incidents", {
      method: "POST",
      body: JSON.stringify({
        chief_complaint: chiefComplaint,
        caller_location_lat: lat,
        caller_location_lon: lon,
        caller_location_text: locText,
      }),
    });

    state.incidentId = data.incident.incident_id;
    state.dispatcherId = dispatcherId;
    state.transcript = [];

    renderIncidentBanner(data.incident);

    if (!data.protocol_matched) {
      show(el("no-protocol-banner"));
      hide(el("question-card"));
      hide(el("terminal-card"));
      // Phase 5.3: Load manual protocol selector
      await loadManualProtocols();
    } else {
      hide(el("no-protocol-banner"));
      state.protocolId = data.protocol_id;
      state.currentQuestion = data.current_question;

      // Phase 5.5: Display triage enrichment if available
      if (data.triage_enrichment) {
        renderTriageEnrichment(data.triage_enrichment);
      }

      renderQuestion(data.current_question);
    }

    hide(intakeScreen);
    show(scriptScreen);
    renderTranscript();

    // Phase 5.6: Setup keyboard navigation after UI is visible
    setupKeyboardNavigation();
  } catch (err) {
    el("intake-error").textContent = err.message;
    show(el("intake-error"));
  } finally {
    submitBtn.disabled = false;
  }
});

// ── Phase 5.3: Manual protocol application ─────────────────────────────────

el("apply-manual-protocol-btn").addEventListener("click", async () => {
  const select = el("manual-protocol-select");
  const resultEl = el("manual-protocol-result");
  const protocolId = select.value;

  if (!protocolId || !state.incidentId) return;

  // Field protocols (prefixed with "field:") are not assignable via this endpoint
  if (protocolId.startsWith("field:")) {
    resultEl.textContent = "Field protocols are assigned from the field console, not the dispatcher.";
    resultEl.className = "action-result error";
    return;
  }

  const btn = el("apply-manual-protocol-btn");
  btn.disabled = true;
  resultEl.textContent = "Applying...";

  try {
    const data = await apiCall(`/incidents/${state.incidentId}/select-protocol`, {
      method: "POST",
      body: JSON.stringify({
        protocol_id: protocolId,
        dispatcher_id: state.dispatcherId || "unknown",
      }),
    });

    state.protocolId = data.protocol_id;
    state.currentQuestion = data.current_question;

    resultEl.textContent = `Protocol ${data.protocol_id} (v${data.protocol_version}) applied.`;
    resultEl.className = "action-result success";

    // Hide the manual selector and no-protocol banner
    hide(el("no-protocol-banner"));

    // Show the first question from the assigned protocol
    if (data.current_question) {
      renderQuestion(data.current_question);
      renderTranscript();
    }
  } catch (err) {
    if (err.status === 409) {
      resultEl.textContent = `${err.body?.detail?.message || err.message}`;
    } else {
      resultEl.textContent = `Error: ${err.message}`;
    }
    resultEl.className = "action-result error";
  } finally {
    btn.disabled = false;
  }
});

// ── Question rendering ───────────────────────────────────────────────────

function renderIncidentBanner(incident) {
  el("incident-banner").textContent =
    `Incident ${incident.incident_id} — created ${incident.created_at}`;
}

function renderQuestion(question) {
  hide(el("terminal-card"));
  hide(el("out-of-script-error"));
  hide(el("guidance-panel"));
  show(el("question-card"));

  el("question-id-label").textContent = question.question_id;
  el("question-text").textContent = question.text;

  const guidanceBadge = el("guidance-badge");
  if (question.allow_guidance_lookup) {
    show(guidanceBadge);
    guidanceBadge.onclick = () => fetchGuidance(question.question_id);
  } else {
    hide(guidanceBadge);
  }

  const optionsEl = el("answer-options");
  optionsEl.innerHTML = "";
  optionsEl.setAttribute("aria-busy", "false"); // Phase 5.2: Reset busy state

  question.valid_answers.forEach((answer) => {
    const btn = document.createElement("button");
    btn.className = "answer-btn";
    btn.textContent = formatAnswerLabel(answer, question);
    btn.addEventListener("click", () =>
      submitAnswer(question.question_id, answer),
    );
    // Phase 5.6: Ensure keyboard accessibility
    btn.setAttribute("tabindex", "0");
    optionsEl.appendChild(btn);
  });

  // Phase 5.5: Render triage enrichment if we have it
  if (state.triageEnrichment) {
    renderTriageEnrichment(state.triageEnrichment);
  }
}

function formatAnswerLabel(answer, question) {
  if (answer === "yes") return "Yes";
  if (answer === "no") return "No";
  if (answer === "acknowledged") return "Acknowledged";
  // select-type options: title-case underscores
  return answer.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// ── Phase 5.2: Answer submission with loading state ────────────────────────

async function submitAnswer(questionId, answer) {
  hide(el("out-of-script-error"));

  // Phase 5.2: Set loading state — disable buttons, set aria-busy
  setAnswerButtonsDisabled(true);
  el("answer-options").setAttribute("aria-busy", "true");
  el("question-card").classList.add("submitting");

  try {
    const data = await apiCall(`/incidents/${state.incidentId}/answer`, {
      method: "POST",
      body: JSON.stringify({
        current_question_id: questionId,
        answer,
        dispatcher_id: state.dispatcherId,
      }),
    });

    state.transcript.push({
      question_text: state.currentQuestion.text,
      answer,
      is_backtrack: false,
    });
    renderTranscript();

    // Show version mismatch warning if present
    if (data.warnings?.version_mismatch) {
      const banner = el("out-of-script-error");
      banner.textContent =
        `Note: protocol was updated since this call started ` +
        `(v${data.warnings.snapshot_version} → v${data.warnings.live_version}). ` +
        `Answers are being processed against the updated version.`;
      banner.className = "warning-banner";
      show(banner);
    }

    // Phase 5.5: Update triage enrichment if provided in response
    if (data.triage_enrichment) {
      renderTriageEnrichment(data.triage_enrichment);
    }

    if (data.terminal) {
      state.currentQuestion = null;
      // Phase 5.4: Record when outcome was reached
      state.outcomeReachedAt = Date.now();
      renderTerminalOutcome(data.outcome);
    } else {
      state.currentQuestion = data.current_question;
      renderQuestion(data.current_question);
    }
  } catch (err) {
    // Out-of-script answers and any other server-side rejection are
    // surfaced verbatim — never retried automatically, never hidden.
    const banner = el("out-of-script-error");
    if (err.status === 422 && err.body?.detail?.valid_answers) {
      banner.textContent = `${err.body.detail.message} Valid answers: ${err.body.detail.valid_answers.join(", ")}`;
    } else {
      banner.textContent = err.message;
    }
    show(banner);
  } finally {
    // Phase 5.2: Clear loading state
    setAnswerButtonsDisabled(false);
    el("answer-options").setAttribute("aria-busy", "false");
    el("question-card").classList.remove("submitting");
  }
}

function setAnswerButtonsDisabled(disabled) {
  document.querySelectorAll(".answer-btn").forEach((b) => {
    b.disabled = disabled;
    // Phase 5.2: Visual feedback during loading
    if (disabled) {
      b.style.opacity = "0.5";
      b.style.pointerEvents = "none";
    } else {
      b.style.opacity = "1";
      b.style.pointerEvents = "auto";
    }
  });
}

// ── Mode 2 guidance lookup ─────────────────────────────────────────────────

async function fetchGuidance(questionId) {
  const panel = el("guidance-panel");
  const textEl = el("guidance-text");
  textEl.textContent = "Loading…";
  show(panel);

  try {
    const data = await apiCall(
      `/incidents/${state.incidentId}/guidance-lookup`,
      {
        method: "POST",
        body: JSON.stringify({
          question_id: questionId,
          dispatcher_id: state.dispatcherId,
        }),
      },
    );
    textEl.textContent = data.guidance_note;
  } catch (err) {
    textEl.textContent = `Guidance unavailable: ${err.message}`;
  }
}

// ── Phase 5.4: Pre-arrival instruction timer ───────────────────────────────

let preArrivalTimerInterval = null;

function startPreArrivalTimer() {
  // Clear any existing timer
  if (preArrivalTimerInterval) {
    clearInterval(preArrivalTimerInterval);
  }

  const timerDisplay = el("terminal-timer-display");

  // Use Date.now() delta, not setInterval counting (avoids drift)
  const updateTimer = () => {
    if (!state.outcomeReachedAt) {
      timerDisplay.textContent = "00:00";
      return;
    }

    const elapsed = Math.floor((Date.now() - state.outcomeReachedAt) / 1000);
    const minutes = Math.floor(elapsed / 60);
    const seconds = elapsed % 60;
    timerDisplay.textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  };

  // Update immediately
  updateTimer();

  // Update every second
  preArrivalTimerInterval = setInterval(updateTimer, 1000);
}

function stopPreArrivalTimer() {
  if (preArrivalTimerInterval) {
    clearInterval(preArrivalTimerInterval);
    preArrivalTimerInterval = null;
  }
}

// ── Terminal outcome ─────────────────────────────────────────────────────

function renderTerminalOutcome(outcome) {
  hide(el("question-card"));
  // Hide triage enrichment when terminal outcome is shown
  el("triage-enrichment-card").classList.add("hidden");
  const card = el("terminal-card");
  show(card);

  state.terminalOutcome = outcome;
  state.preArrivalConfirmed = false;

  card.classList.remove("priority-low", "priority-medium");
  if (outcome.priority_code.startsWith("P3"))
    card.classList.add("priority-low");
  if (outcome.priority_code.startsWith("P2"))
    card.classList.add("priority-medium");

  el("terminal-priority").textContent = outcome.priority_code;
  el("terminal-unit-type").textContent =
    `Recommended unit: ${outcome.recommended_unit_type}`;

  const list = el("terminal-instructions");
  list.innerHTML = "";
  outcome.pre_arrival_instructions.forEach((instr) => {
    const li = document.createElement("li");
    li.textContent = instr;
    list.appendChild(li);
  });

  // Reset confirm button
  const confirmBtn = el("confirm-pre-arrival-btn");
  confirmBtn.disabled = false;
  confirmBtn.textContent = "Confirm — all instructions read to caller";
  el("confirm-pre-arrival-result").textContent = "";

  el("dispatch-result").textContent = "";
  el("facility-result").textContent = "";
  el("facility-list").innerHTML = "";

  // Phase 5.4: Start the pre-arrival timer
  startPreArrivalTimer();
}

el("confirm-pre-arrival-btn").addEventListener("click", async () => {
  if (state.preArrivalConfirmed || !state.terminalOutcome) return;

  const resultEl = el("confirm-pre-arrival-result");
  const btn = el("confirm-pre-arrival-btn");
  btn.disabled = true;
  resultEl.textContent = "Confirming…";

  try {
    await apiCall(`/incidents/${state.incidentId}/confirm-pre-arrival`, {
      method: "POST",
      body: JSON.stringify({
        dispatcher_id: state.dispatcherId,
        terminal_outcome_id: state.terminalOutcome.priority_code,
        all_instructions_read: true,
      }),
    });
    state.preArrivalConfirmed = true;
    btn.textContent = "Instructions confirmed";
    resultEl.textContent = `Confirmed at ${new Date().toLocaleString()}`;

    // Phase 5.4: Stop the timer when instructions are confirmed
    stopPreArrivalTimer();
  } catch (err) {
    btn.disabled = false;
    resultEl.textContent = `Error: ${err.message}`;
  }
});

el("dispatch-unit-btn").addEventListener("click", async () => {
  const resultEl = el("dispatch-result");
  resultEl.textContent = "Dispatching…";
  try {
    const data = await apiCall(`/incidents/${state.incidentId}/dispatch-unit`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    if (data.assigned) {
      resultEl.textContent = `Unit ${data.assigned_unit_id} assigned — ETA ${data.eta_minutes ?? "unknown"} min`;
      resultEl.className = "action-result success";

      // Show field URL card so dispatcher can send it to the paramedic
      if (data.field_url) {
        el("field-link-url").value = data.field_url;
        show(el("field-url-card"));
      }
    } else {
      resultEl.textContent = data.message;
      resultEl.className = "action-result";
    }
  } catch (err) {
    resultEl.textContent = `Error: ${err.message}`;
    resultEl.className = "action-result error";
  }
});

el("route-facility-btn").addEventListener("click", async () => {
  const resultEl = el("facility-result");
  const listEl = el("facility-list");
  listEl.innerHTML = "";

  const lat = parseFloat(el("caller-lat").value);
  const lon = parseFloat(el("caller-lon").value);
  if (Number.isNaN(lat) || Number.isNaN(lon)) {
    resultEl.textContent =
      "Caller lat/lon required for facility routing — not provided at intake.";
    return;
  }

  resultEl.textContent = "Searching…";
  try {
    const data = await apiCall(
      `/incidents/${state.incidentId}/route-facility`,
      {
        method: "POST",
        body: JSON.stringify({ lat, lon }),
      },
    );
    if (data.facilities.length === 0) {
      resultEl.textContent = data.message;
    } else {
      resultEl.textContent = `${data.facilities.length} facility(ies) found:`;
      data.facilities.forEach((f) => {
        const div = document.createElement("div");
        div.className = "facility-item";
        div.innerHTML = `<span>${f.name}</span><span>${f.distance_km.toFixed(1)} km — ${f.capacity_status ?? "capacity unknown"}</span>`;
        listEl.appendChild(div);
      });
    }
  } catch (err) {
    resultEl.textContent = `Error: ${err.message}`;
  }
});

el("new-call-btn").addEventListener("click", () => {
  state.incidentId = null;
  state.protocolId = null;
  state.currentQuestion = null;
  state.transcript = [];
  state.triageEnrichment = null;
  state.outcomeReachedAt = null;

  // Phase 5.4: Stop timer when starting new call
  stopPreArrivalTimer();

  el("intake-form").reset();
  hide(scriptScreen);
  show(intakeScreen);

  // Hide triage enrichment card
  el("triage-enrichment-card").classList.add("hidden");
  // Hide handoff and field URL cards
  hide(el("handoff-delivery-card"));
  hide(el("field-url-card"));
});

// ── Handoff delivery: Send to ER ──────────────────────────────────────────

// Show handoff delivery card when terminal outcome is reached
const _origRenderTerminalOutcome = renderTerminalOutcome;
renderTerminalOutcome = function (outcome) {
  _origRenderTerminalOutcome(outcome);
  show(el("handoff-delivery-card"));
};

el("send-to-er-btn").addEventListener("click", async () => {
  const resultEl = el("handoff-link-result");
  const btn = el("send-to-er-btn");
  btn.disabled = true;
  resultEl.textContent = "Generating handoff link...";
  resultEl.className = "action-result";

  try {
    const data = await apiCall(`/incidents/${state.incidentId}/handoff-link`);
    el("handoff-link-url").value = data.handoff_url;
    show(el("handoff-link-display"));
    resultEl.textContent = "Link generated. Send it to the receiving hospital.";
    resultEl.className = "action-result success";
  } catch (err) {
    resultEl.textContent = `Error: ${err.message}`;
    resultEl.className = "action-result error";
  } finally {
    btn.disabled = false;
  }
});

el("copy-handoff-link-btn").addEventListener("click", () => {
  const input = el("handoff-link-url");
  input.select();
  navigator.clipboard
    .writeText(input.value)
    .then(() => {
      el("copy-handoff-link-btn").textContent = "Copied";
      setTimeout(() => {
        el("copy-handoff-link-btn").textContent = "Copy";
      }, 2000);
    })
    .catch(() => {
      // Fallback for older browsers
      document.execCommand("copy");
      el("copy-handoff-link-btn").textContent = "Copied";
      setTimeout(() => {
        el("copy-handoff-link-btn").textContent = "Copy";
      }, 2000);
    });
});

el("copy-field-link-btn").addEventListener("click", () => {
  const input = el("field-link-url");
  input.select();
  navigator.clipboard
    .writeText(input.value)
    .then(() => {
      el("copy-field-link-btn").textContent = "Copied";
      setTimeout(() => {
        el("copy-field-link-btn").textContent = "Copy";
      }, 2000);
    })
    .catch(() => {
      document.execCommand("copy");
      el("copy-field-link-btn").textContent = "Copied";
      setTimeout(() => {
        el("copy-field-link-btn").textContent = "Copy";
      }, 2000);
    });
});

// ── Transcript ─────────────────────────────────────────────────────────────

function renderTranscript() {
  const container = el("transcript");
  container.innerHTML = "";
  if (state.transcript.length === 0) {
    container.innerHTML =
      '<div class="transcript-entry__q">No answers recorded yet.</div>';
    return;
  }
  state.transcript.forEach((entry) => {
    const div = document.createElement("div");
    div.className = "transcript-entry";
    div.innerHTML = `
      <div class="transcript-entry__q">${escapeHtml(entry.question_text)}</div>
      <div class="transcript-entry__a${entry.is_backtrack ? " backtrack" : ""}">${escapeHtml(entry.answer)}${entry.is_backtrack ? " (backtrack)" : ""}</div>
    `;
    container.appendChild(div);
  });
  container.scrollTop = container.scrollHeight;
}

// ── Utilities ────────────────────────────────────────────────────────────

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
