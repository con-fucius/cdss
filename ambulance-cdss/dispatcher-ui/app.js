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
const SESSION_KEY = "ambulance_cdss_dispatch_session";
const SESSION_TIMEOUT_MS = 8 * 3600 * 1000; // 8 hours

// ── DOM refs & utilities (must precede all other code that uses el()) ────────

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

// ── EPIC 6.1: Dispatcher Session Management ──────────────────────────────────

const session = {
  token: null,
  dispatcherId: null,
  username: null,
  issuedAt: null,
};

function loadSession() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (Date.now() - (data.issuedAt || 0) > SESSION_TIMEOUT_MS) {
      sessionStorage.removeItem(SESSION_KEY);
      return null;
    }
    return data;
  } catch {
    return null;
  }
}

function saveSessionData(token, username, dispatcherId) {
  const data = { token, username, dispatcherId, issuedAt: Date.now() };
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(data));
  session.token = token;
  session.dispatcherId = dispatcherId;
  session.username = username;
  session.issuedAt = data.issuedAt;
}

function clearSession() {
  sessionStorage.removeItem(SESSION_KEY);
  session.token = null;
  session.dispatcherId = null;
  session.username = null;
  session.issuedAt = null;
}

function initLoginScreen() {
  const existing = loadSession();
  if (existing) {
    session.token = existing.token;
    session.dispatcherId = existing.dispatcherId;
    session.username = existing.username;
    session.issuedAt = existing.issuedAt;
    hide(el("login-screen"));
    show(el("intake-screen"));
    // Pre-fill dispatcher ID
    const idInput = el("dispatcher-id");
    if (idInput) idInput.value = existing.dispatcherId || "";
    // Gap 1: Auto-start transcription when returning to intake
    autoStartListening();
    return;
  }
  show(el("login-screen"));
  hide(el("intake-screen"));
}

// Login form handler
el("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  hide(el("login-error"));
  const username = el("login-username").value.trim();
  const pin = el("login-pin").value.trim();
  const btn = e.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const data = await apiCall("/auth/dispatcher-login", {
      method: "POST",
      body: JSON.stringify({ username, pin }),
    });
    saveSessionData(data.session_token, data.dispatcher_id, data.dispatcher_id);
    const idInput = el("dispatcher-id");
    if (idInput) idInput.value = data.dispatcher_id;
    hide(el("login-screen"));
    show(el("intake-screen"));
    // Gap 1: Auto-start transcription after login
    autoStartListening();
  } catch (err) {
    el("login-error").textContent = err.message;
    show(el("login-error"));
  } finally {
    btn.disabled = false;
  }
});

// Auto-logout check every 60 seconds
setInterval(() => {
  if (session.token && session.issuedAt && Date.now() - session.issuedAt > SESSION_TIMEOUT_MS) {
    clearSession();
    hide(el("intake-screen"));
    hide(el("script-screen"));
    show(el("login-screen"));
  }
}, 60000);

// Logout button
el("logout-btn").addEventListener("click", () => {
  sessionStorage.removeItem(SESSION_KEY);
  session.token = null;
  session.dispatcherId = null;
  session.username = null;
  session.issuedAt = null;
  location.reload();
});

// Initialize login on page load
initLoginScreen();

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
  const headers = { "Content-Type": "application/json" };
  if (session.token) {
    headers["Authorization"] = `Bearer ${session.token}`;
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { ...headers, ...options.headers },
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
    const [dispatchData] = await Promise.all([
      apiCall("/protocols").catch(() => ({ active: [] })),
    ]);

    select.innerHTML = '';

    if (dispatchData.active && dispatchData.active.length > 0) {
      const defaultOpt = document.createElement("option");
      defaultOpt.value = "";
      defaultOpt.textContent = "— Select protocol —";
      select.appendChild(defaultOpt);

      const group = document.createElement("optgroup");
      group.label = "Dispatch protocols";
      dispatchData.active.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.protocol_id;
        opt.textContent = `${p.disease_or_presentation} (${p.version})`;
        group.appendChild(opt);
      });
      select.appendChild(group);
      applyBtn.disabled = true;
      resultEl.textContent = "";
    } else {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No dispatch protocols available";
      opt.disabled = true;
      select.appendChild(opt);
      applyBtn.disabled = true;
      resultEl.textContent = "No dispatch protocols available — all protocols pending medical director approval. Create the incident manually and assign a field protocol from the field console.";
      resultEl.className = "action-result";
    }

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

    // Epic 7.4: Start triage enrichment polling if not yet resolved
    if (!state.triageEnrichment) {
      startTriagePolling();
    }

    // Epic 1.4: Start auto-save transcript polling
    startTranscriptAutoSave();
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
    guidanceBadge.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        fetchGuidance(question.question_id);
      }
    };
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

  // Gap 7: Initialize relay mode for pre-arrival instructions
  initRelayMode(outcome.pre_arrival_instructions);

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
      data.facilities.forEach((f, idx) => {
        const div = document.createElement("div");
        div.className = "facility-item";
        div.innerHTML = `<span>${f.name}</span><span>${f.distance_km.toFixed(1)} km — ${f.capacity_status ?? "capacity unknown"}</span>`;
        listEl.appendChild(div);

        // EPIC 2.2: drop green facility pins on the unit tracking map
        if (unitTrackingMap && f.lat && f.lon) {
          if (facilityPin) {
            facilityPin.remove();
          }
          facilityPin = L.circleMarker([f.lat, f.lon], {
            radius: 10,
            color: "#198754",
            fillColor: "#198754",
            fillOpacity: 0.8,
          })
            .addTo(unitTrackingMap)
            .bindPopup(escapeHtml(f.name));
          if (idx === 0) {
            unitTrackingMap.fitBounds(facilityPin.getBounds(), {
              maxZoom: 14,
              padding: [50, 50],
            });
          }
        }
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

  // Gap 7: Stop relay mode timer
  if (relayState.timerInterval) {
    clearInterval(relayState.timerInterval);
    relayState.timerInterval = null;
  }

  // Gap 8: Stop notes auto-save
  stopNotesAutoSave();

  // Clear notes list
  const notesList = el("dispatcher-notes-list");
  if (notesList) notesList.innerHTML = "";

  // Epic 1.4: Stop auto-save transcript
  stopTranscriptAutoSave();
  lastTranscriptLength = 0;

  el("intake-form").reset();
  hide(scriptScreen);
  show(intakeScreen);

  // Hide triage enrichment card
  el("triage-enrichment-card").classList.add("hidden");
  // Hide handoff and field URL cards
  hide(el("handoff-delivery-card"));
  hide(el("field-url-card"));
});

// ── EPIC 2.2: Live Unit Tracking Map ────────────────────────────────────

let unitTrackingMap = null;
let unitBlip = null;
let incidentPin = null;
let facilityPin = null;
let unitTrackingPollTimer = null;

function initUnitTrackingMap() {
  const mapDiv = el("unit-tracking-map");
  if (!mapDiv || unitTrackingMap) return;

  const center = window.AMBULANCE_CDSS_MAP_CENTER || [-1.286389, 36.817223];
  unitTrackingMap = L.map("unit-tracking-map").setView(center, 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
  }).addTo(unitTrackingMap);
}

function startUnitTrackingPoll() {
  if (unitTrackingPollTimer) return;
  unitTrackingPollTimer = setInterval(async () => {
    if (!state.incidentId) { stopUnitTrackingPoll(); return; }
    try {
      const data = await apiCall(`/incidents/${state.incidentId}/unit-location/latest`);
      if (data.location && unitTrackingMap) {
        const lat = data.location.lat;
        const lon = data.location.lon;
        if (unitBlip) {
          unitBlip.setLatLng([lat, lon]);
        } else {
          unitBlip = L.circleMarker([lat, lon], {
            radius: 8, color: "#2f7de1", fillColor: "#2f7de1", fillOpacity: 0.9,
          }).addTo(unitTrackingMap).bindPopup("Unit");
        }
        unitTrackingMap.fitBounds(unitBlip.getBounds(), { maxZoom: 15, padding: [50, 50] });
      }
    } catch (err) {
      console.warn("Unit tracking poll failed:", err.message);
    }
  }, 15000);
}

function stopUnitTrackingPoll() {
  if (unitTrackingPollTimer) { clearInterval(unitTrackingPollTimer); unitTrackingPollTimer = null; }
}

function showIncidentPinOnTrackingMap() {
  if (!unitTrackingMap) return;
  const lat = parseFloat(el("caller-lat")?.value);
  const lon = parseFloat(el("caller-lon")?.value);
  if (lat && lon && !isNaN(lat) && !isNaN(lon)) {
    if (incidentPin) {
      incidentPin.setLatLng([lat, lon]);
    } else {
      incidentPin = L.circleMarker([lat, lon], {
        radius: 10, color: "#d3402f", fillColor: "#d3402f", fillOpacity: 0.7,
      }).addTo(unitTrackingMap).bindPopup("Incident location");
    }
    unitTrackingMap.fitBounds([incidentPin.getBounds()], { maxZoom: 13, padding: [50, 50] });
  }
}

// Show unit tracking map after unit is dispatched
const _origDispatchUnitClick = el("dispatch-unit-btn")?.onclick;
document.addEventListener("DOMContentLoaded", () => {
  el("dispatch-unit-btn").addEventListener("click", () => {
    setTimeout(() => {
      initUnitTrackingMap();
      showIncidentPinOnTrackingMap();
      startUnitTrackingPoll();
      setTimeout(() => unitTrackingMap && unitTrackingMap.invalidateSize(), 200);
    }, 1000);
  });
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
// (show/hide/escapeHtml/el are defined at the top of this file)

// ── Epic 1.1: Streaming audio capture (SpeechRecognition) ─────────────────

let recognition = null;
let isListening = false;

function setupAudioCapture() {
  const startBtn = el("start-listening-btn");
  const statusEl = el("listening-status");
  const transcriptEl = el("live-transcript");
  const suggestionEl = el("suggestion-cards");

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    startBtn.textContent = "🎙 Manual entry only (SpeechRecognition unsupported)";
    startBtn.disabled = true;
    startBtn.title = "SpeechRecognition requires Chrome or Edge browser.";
    return;
  }

  recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = "en-US";

  recognition.onresult = (event) => {
    let finalText = "";
    let interimText = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const transcript = event.results[i][0].transcript;
      if (event.results[i].isFinal) {
        finalText += transcript;
      } else {
        interimText += transcript;
      }
    }
    if (finalText) {
      // Only process final results for entity extraction
      processTranscript(finalText);
      // Auto-append to the chief complaint field if empty or very short
      const ccInput = el("chief-complaint");
      if (!ccInput.value.trim() || ccInput.value.trim().length < 5) {
        ccInput.value = finalText.trim();
      }
    }
    // Show live transcript
    show(transcriptEl);
    transcriptEl.textContent = interimText || finalText || "Listening…";
  };

  recognition.onerror = (event) => {
    if (event.error === "not-allowed") {
      statusEl.textContent = "⚠ Microphone permission denied";
      show(statusEl);
    }
  };

  recognition.onend = () => {
    if (isListening) {
      // Auto-restart if still in listening mode
      try { recognition.start(); } catch (e) { /* ignore */ }
    }
  };

  startBtn.addEventListener("click", () => {
    if (isListening) {
      recognition.stop();
      isListening = false;
      startBtn.textContent = "🎙 Start listening";
      startBtn.classList.remove("btn-danger");
      startBtn.classList.add("btn-secondary");
      statusEl.classList.add("hidden");
      hide(transcriptEl);
    } else {
      recognition.start();
      isListening = true;
      startBtn.textContent = "⏹ Stop listening";
      statusEl.textContent = "● Listening…";
      statusEl.className = "listening-status";
    }
  });
}

// ── Epic 1.2: Process transcript for entity extraction ─────────────────────

async function processTranscript(transcript) {
  const suggestionEl = el("suggestion-cards");
  try {
    const data = await apiCall("/triage/extract-entities", {
      method: "POST",
      body: JSON.stringify({ transcript }),
    });
    if (!data) return;

    const suggestions = [];
    if (data.chief_complaint_suggestion) {
      // Gap 1: Auto-populate chief complaint field if empty
      const ccInput = el("chief-complaint");
      if (!ccInput.value.trim()) {
        ccInput.value = data.chief_complaint_suggestion;
        suggestions.push({
          type: "chief_complaint",
          text: `Auto-filled: "${data.chief_complaint_suggestion}" — Tap to confirm`,
          value: data.chief_complaint_suggestion,
        });
        // Gap 1.5: Show protocol match banner with the complaint
        showProtocolMatchBanner(data.chief_complaint_suggestion);
      } else {
        suggestions.push({
          type: "chief_complaint",
          text: `We heard: ${data.chief_complaint_suggestion} — apply?`,
          value: data.chief_complaint_suggestion,
        });
      }
    }
    if (data.location_text) {
      suggestions.push({
        type: "location",
        text: `Location heard: ${data.location_text}`,
        value: data.location_text,
      });
    }
    if (data.vitals && Object.keys(data.vitals).length > 0) {
      const vitalsParts = Object.entries(data.vitals)
        .map(([k, v]) => `${k.replace(/_/g, " ")}=${v}`)
        .join(", ");
      suggestions.push({
        type: "vitals",
        text: `Vitals extracted: ${vitalsParts}`,
        value: data.vitals,
      });
    }
    // EPIC 1.2: Show clinical entities extracted by MedSpaCy
    if (data.entities && data.entities.length > 0) {
      const active = data.entities.filter(e => !e.negated);
      const negated = data.entities.filter(e => e.negated);
      if (active.length > 0) {
        const entityText = active
          .map(e => `${e.label} (${e.category}${e.severity_weight >= 0.8 ? ", HIGH" : ""})`)
          .join("; ");
        suggestions.push({
          type: "clinical_entities",
          text: `Clinical findings: ${entityText}`,
          value: "",
        });
      }
      if (negated.length > 0) {
        const negText = negated.map(e => e.label).join(", ");
        suggestions.push({
          type: "negated_entities",
          text: `Negated (not present): ${negText}`,
          value: "",
        });
      }
    }
    // Show extraction confidence if available
    if (data.confidence !== undefined && data.confidence > 0) {
      const badge = data.degraded_mode ? " (regex fallback)" : " (NLP)";
      suggestions.push({
        type: "confidence",
        text: `Extraction confidence: ${Math.round(data.confidence * 100)}%${badge}`,
        value: "",
      });
    }

    if (suggestions.length > 0) {
      show(suggestionEl);
      suggestionEl.innerHTML = "";
      suggestions.forEach((s) => {
        const card = document.createElement("div");
        card.className = "suggestion-card";
        const isInfoOnly = s.type === "clinical_entities" || s.type === "negated_entities" || s.type === "confidence";
        card.innerHTML = `
          <span class="suggestion-card__text">${escapeHtml(s.text)}</span>
          ${isInfoOnly
            ? `<button class="suggestion-card__dismiss">Dismiss</button>`
            : `<div class="suggestion-card__actions">
                <button class="suggestion-card__accept" data-type="${s.type}" data-value="${typeof s.value === "string" ? escapeHtml(s.value) : ""}">Accept</button>
                <button class="suggestion-card__dismiss">Dismiss</button>
              </div>`
          }
        `;
        suggestionEl.appendChild(card);

        const acceptBtn = card.querySelector(".suggestion-card__accept");
        if (acceptBtn) {
          acceptBtn.addEventListener("click", () => {
            if (s.type === "chief_complaint") {
              el("chief-complaint").value = s.value;
            } else if (s.type === "location") {
              el("caller-location-text").value = s.value;
            }
            card.remove();
            if (suggestionEl.children.length === 0) hide(suggestionEl);
          });
        }

        card.querySelector(".suggestion-card__dismiss").addEventListener("click", () => {
          card.remove();
          if (suggestionEl.children.length === 0) hide(suggestionEl);
        });
      });
    }
  } catch (err) {
    // Silently ignore extraction failures — degraded mode
  }
}

// Initialize audio capture on page load
setupAudioCapture();

// ── Gap 1: Auto-start listening on intake screen ─────────────────────────

function autoStartListening() {
  if (recognition && !isListening) {
    try {
      recognition.start();
      isListening = true;
      const startBtn = el("start-listening-btn");
      const statusEl = el("listening-status");
      startBtn.textContent = "⏹ Stop listening";
      startBtn.classList.remove("btn-secondary");
      startBtn.classList.add("btn-danger");
      statusEl.textContent = "● Listening…";
      statusEl.className = "listening-status";
      show(statusEl);
    } catch (e) {
      // SpeechRecognition may not be available — silently ignore
    }
  }
}

// ── EPIC 2.1: Leaflet.js Map Integration ─────────────────────────────────

let intakeMap = null;
let intakeMarker = null;

function initIntakeMap() {
  if (intakeMap) return;

  // Fallback: if Leaflet failed to load, show a message
  if (typeof L === "undefined") {
    const mapDiv = el("intake-map");
    if (mapDiv) {
      mapDiv.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-dim);font-size:14px;">Map unavailable — enter coordinates manually</div>';
    }
    return;
  }

  const center = window.AMBULANCE_CDSS_MAP_CENTER || [-1.286389, 36.817223];
  const zoom = window.AMBULANCE_CDSS_MAP_ZOOM || 13;

  try {
    intakeMap = L.map("intake-map").setView(center, zoom);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; OpenStreetMap contributors',
      maxZoom: 19,
    }).addTo(intakeMap);
  } catch (err) {
    const mapDiv = el("intake-map");
    if (mapDiv) {
      mapDiv.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-dim);font-size:14px;">Map unavailable — enter coordinates manually</div>';
    }
    return;
  }

  // Click to drop pin (throttled to respect Nominatim rate limits)
  intakeMap.on("click", (e) => {
    placeIntakeMarker(e.latlng.lat, e.latlng.lng);
    throttledReverseGeocode(e.latlng.lat, e.latlng.lng);
  });

  // Address search with forward geocoding
  const searchInput = el("address-search");
  let searchTimeout = null;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    const query = searchInput.value.trim();
    if (query.length < 3) return;
    searchTimeout = setTimeout(() => throttledForwardGeocode(query), 500);
  });

  // Clear location button
  el("clear-location-btn").addEventListener("click", () => {
    if (intakeMarker) {
      intakeMap.removeLayer(intakeMarker);
      intakeMarker = null;
    }
    el("caller-lat").value = "";
    el("caller-lon").value = "";
    el("caller-location-text").value = "";
    el("map-status").textContent = "";
  });

  // Fix map rendering after tab switch
  setTimeout(() => intakeMap.invalidateSize(), 100);

  // Initialize address suggestions after map is ready
  setupAddressSuggestions();
}

function placeIntakeMarker(lat, lon) {
  if (intakeMarker) {
    intakeMarker.setLatLng([lat, lon]);
  } else {
    intakeMarker = L.marker([lat, lon]).addTo(intakeMap);
  }
  el("caller-lat").value = lat.toFixed(6);
  el("caller-lon").value = lon.toFixed(6);
  el("map-status").textContent = `Pinned: ${lat.toFixed(4)}, ${lon.toFixed(4)}`;
}

// Nominatim rate limiting: max 1 request per second per usage policy
let lastNominatimRequest = 0;
const NOMINATIM_MIN_INTERVAL_MS = 1100;

function throttleNominatim(fn) {
  return async function (...args) {
    const now = Date.now();
    const elapsed = now - lastNominatimRequest;
    if (elapsed < NOMINATIM_MIN_INTERVAL_MS) {
      await new Promise((r) => setTimeout(r, NOMINATIM_MIN_INTERVAL_MS - elapsed));
    }
    const result = await fn.apply(this, args);
    lastNominatimRequest = Date.now();
    return result;
  };
}

async function reverseGeocode(lat, lon) {
  const baseUrl = window.AMBULANCE_CDSS_GEOCODING_BASE_URL || "https://nominatim.openstreetmap.org";
  try {
    const resp = await fetch(
      `${baseUrl}/reverse?format=json&lat=${lat}&lon=${lon}`,
      { headers: { "User-Agent": "AmbulanceCDSS/1.0" } },
    );
    const data = await resp.json();
    if (data.display_name) {
      el("caller-location-text").value = data.display_name;
      el("map-status").textContent = data.display_name;
    }
  } catch {
    el("map-status").textContent = `Pinned: ${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  }
}

async function forwardGeocode(query) {
  const baseUrl = window.AMBULANCE_CDSS_GEOCODING_BASE_URL || "https://nominatim.openstreetmap.org";
  try {
    const resp = await fetch(
      `${baseUrl}/search?format=json&q=${encodeURIComponent(query)}`,
      { headers: { "User-Agent": "AmbulanceCDSS/1.0" } },
    );
    const results = await resp.json();
    if (results.length > 0) {
      if (window._showAddressSuggestions) {
        window._showAddressSuggestions(results);
      } else {
        const r = results[0];
        placeIntakeMarker(parseFloat(r.lat), parseFloat(r.lon));
        intakeMap.setView([parseFloat(r.lat), parseFloat(r.lon)], 16);
        el("caller-location-text").value = r.display_name;
        el("map-status").textContent = r.display_name;
      }
    }
  } catch {
    // Silently ignore geocoding failures
  }
}

// Wrap geocoding functions with rate limiter
const throttledReverseGeocode = throttleNominatim(reverseGeocode);
const throttledForwardGeocode = throttleNominatim(forwardGeocode);

// Initialize map when intake screen is shown
const _origShowIntake = show;
show = function (node) {
  _origShowIntake(node);
  if (node === intakeScreen) {
    setTimeout(() => {
      initIntakeMap();
      setTimeout(() => intakeMap && intakeMap.invalidateSize(), 100);
    }, 100);
  }
};

// If intake screen is already visible (e.g. session restored), init map now
if (intakeScreen && !intakeScreen.classList.contains("hidden")) {
  setTimeout(() => initIntakeMap(), 200);
}

// ── Epic 1.4: Auto-save transcript (30s interval) ───────────────────────

let transcriptSaveTimer = null;
let lastTranscriptLength = 0;

function startTranscriptAutoSave() {
  if (transcriptSaveTimer) return;
  transcriptSaveTimer = setInterval(async () => {
    if (!state.incidentId || !state.transcript || state.transcript.length === lastTranscriptLength) return;
    lastTranscriptLength = state.transcript.length;
    const lastEntry = state.transcript[state.transcript.length - 1];
    try {
      await apiCall(`/incidents/${state.incidentId}/transcript`, {
        method: "PATCH",
        body: JSON.stringify({
          speaker: "dispatcher",
          text: `[Q] ${lastEntry.question_text}\n[A] ${lastEntry.answer}`,
        }),
      });
    } catch {
      // Non-fatal: auto-save is best-effort
    }
  }, 30000);
}

function stopTranscriptAutoSave() {
  if (transcriptSaveTimer) {
    clearInterval(transcriptSaveTimer);
    transcriptSaveTimer = null;
  }
  lastTranscriptLength = 0;
}

// ── Epic 7.4: Triage enrichment live polling ───────────────────────────────

let triagePollTimer = null;
let triagePollCount = 0;
const TRIAGE_POLL_MAX = 10; // 30 seconds max (3s intervals × 10)

function startTriagePolling() {
  if (triagePollTimer) return; // Already polling
  triagePollTimer = setInterval(async () => {
    triagePollCount++;
    if (triagePollCount >= TRIAGE_POLL_MAX) {
      stopTriagePolling();
      return;
    }
    if (!state.incidentId) {
      stopTriagePolling();
      return;
    }
    try {
      const data = await apiCall(`/incidents/${state.incidentId}`);
      if (data.triage_enrichment) {
        renderTriageEnrichment(data.triage_enrichment);
        stopTriagePolling();
      }
    } catch (err) {
      console.warn("Triage enrichment poll failed:", err.message);
    }
  }, 3000);
}

function stopTriagePolling() {
  if (triagePollTimer) {
    clearInterval(triagePollTimer);
    triagePollTimer = null;
  }
  triagePollCount = 0;
}

// ── Gap 7: Pre-arrival instruction relay mode ──────────────────────────

let relayState = {
  instructions: [],
  currentIndex: 0,
  confirmedInstructions: [],
  startTime: null,
  timerInterval: null,
};

function initRelayMode(instructions) {
  relayState.instructions = instructions || [];
  relayState.currentIndex = 0;
  relayState.confirmedInstructions = [];
  relayState.startTime = Date.now();

  if (relayState.timerInterval) clearInterval(relayState.timerInterval);

  const allList = el("terminal-instructions");
  allList.classList.add("hidden");

  if (relayState.instructions.length === 0) {
    hide(el("relay-progress"));
    hide(el("relay-controls"));
    hide(el("relay-current-instruction"));
    hide(el("relay-complete-banner"));
    return;
  }

  show(el("relay-progress"));
  show(el("relay-controls"));
  show(el("relay-current-instruction"));
  hide(el("relay-complete-banner"));

  el("relay-next-btn").textContent = "Next Instruction";
  el("relay-status").textContent = "";
  el("relay-confirm-btn").disabled = false;

  renderRelayInstruction();

  relayState.timerInterval = setInterval(updateRelayElapsed, 1000);
  updateRelayElapsed();
}

function renderRelayInstruction() {
  const total = relayState.instructions.length;
  const idx = relayState.currentIndex;

  el("relay-progress-text").textContent = `Instruction ${idx + 1} of ${total}`;

  if (idx >= total) {
    finishRelayMode();
    return;
  }

  el("relay-current-instruction").textContent = relayState.instructions[idx];
  el("relay-confirm-btn").textContent = "Caller Confirmed";
  el("relay-confirm-btn").disabled = false;
  el("relay-status").textContent = "";

  const nextBtn = el("relay-next-btn");
  if (idx === total - 1) {
    nextBtn.textContent = "Finish";
  } else {
    nextBtn.textContent = "Next Instruction";
  }
}

function updateRelayElapsed() {
  if (!relayState.startTime) return;
  const elapsed = Math.floor((Date.now() - relayState.startTime) / 1000);
  const minutes = Math.floor(elapsed / 60);
  const seconds = elapsed % 60;
  el("relay-elapsed").textContent =
    `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function finishRelayMode() {
  hide(el("relay-current-instruction"));
  hide(el("relay-controls"));
  hide(el("relay-progress"));
  show(el("relay-complete-banner"));

  if (relayState.timerInterval) {
    clearInterval(relayState.timerInterval);
    relayState.timerInterval = null;
  }
}

el("relay-confirm-btn").addEventListener("click", () => {
  if (relayState.currentIndex >= relayState.instructions.length) return;

  relayState.confirmedInstructions.push(relayState.currentIndex);
  el("relay-status").textContent = "Confirmed";
  el("relay-status").className = "action-result success";
  el("relay-confirm-btn").disabled = true;
});

el("relay-next-btn").addEventListener("click", () => {
  if (relayState.currentIndex >= relayState.instructions.length) return;

  if (!relayState.confirmedInstructions.includes(relayState.currentIndex)) {
    relayState.confirmedInstructions.push(relayState.currentIndex);
  }

  relayState.currentIndex++;
  renderRelayInstruction();
});

// ── Chief complaint autocomplete suggestions ──────────────────────────

const CHIEF_COMPLAINT_SUGGESTIONS = [
  'chest pain', 'difficulty breathing', 'not breathing', 'cardiac arrest',
  'choking', 'stroke', 'seizure', 'unconscious', 'car accident',
  'stab wound', 'gunshot', 'fall', 'burn', 'severe bleeding',
  'head injury', 'confusion', 'diabetic emergency', 'allergic reaction',
  'drowning', 'electric shock', 'heat stroke', 'poisoning',
  'pregnant - bleeding', 'pregnant - seizure', 'child not breathing',
  'child choking', 'child seizure', 'child fever',
  'maumivu ya kifua', 'kushindwa kupumua', 'mshtuko', 'kutokwa na damu',
];

function setupComplaintSuggestions() {
  const input = el("chief-complaint");
  const dropdown = el("complaint-suggestions");
  if (!input || !dropdown) return;

  let activeIndex = -1;

  function showSuggestions(query) {
    if (!query) { hide(dropdown); return; }
    const lower = query.toLowerCase();
    const matches = CHIEF_COMPLAINT_SUGGESTIONS.filter(s => s.toLowerCase().includes(lower));
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
        hide(dropdown);
      });
      dropdown.appendChild(item);
    });
    show(dropdown);
  }

  input.addEventListener("input", () => showSuggestions(input.value.trim()));

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

setupComplaintSuggestions();

// ── Address search autocomplete suggestions ───────────────────────────

function setupAddressSuggestions() {
  const input = el("address-search");
  const dropdown = el("address-suggestions");
  if (!input || !dropdown) return;

  let activeIndex = -1;
  let currentResults = [];

  function showAddressSuggestions(results) {
    currentResults = results;
    if (!results || results.length === 0) { hide(dropdown); return; }

    dropdown.innerHTML = "";
    activeIndex = -1;
    results.forEach((r, i) => {
      const item = document.createElement("div");
      item.className = "suggestion-item";
      item.textContent = r.display_name;
      item.dataset.index = i;
      item.addEventListener("mousedown", (e) => {
        e.preventDefault();
        selectAddressResult(r);
      });
      dropdown.appendChild(item);
    });
    show(dropdown);
  }

  function selectAddressResult(r) {
    placeIntakeMarker(parseFloat(r.lat), parseFloat(r.lon));
    intakeMap.setView([parseFloat(r.lat), parseFloat(r.lon)], 16);
    el("caller-location-text").value = r.display_name;
    el("map-status").textContent = r.display_name;
    input.value = r.display_name;
    hide(dropdown);
  }

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
      selectAddressResult(currentResults[activeIndex]);
    } else if (e.key === "Escape") {
      hide(dropdown);
    }
  });

  document.addEventListener("click", (e) => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) {
      hide(dropdown);
    }
  });

  // Expose for the existing input handler to call
  window._showAddressSuggestions = showAddressSuggestions;
}

// ── Gap 8: Dispatcher notes during call ────────────────────────────────

let notesAutoSaveTimer = null;

async function loadNotes() {
  if (!state.incidentId) return;
  try {
    const data = await apiCall(`/incidents/${state.incidentId}/notes`);
    const notes = data.notes || [];
    renderNotesList(notes);
  } catch {
    // Notes unavailable — non-fatal
  }
}

function renderNotesList(notes) {
  const list = el("dispatcher-notes-list");
  if (!list) return;
  list.innerHTML = "";
  if (!notes || notes.length === 0) return;

  notes.forEach((note) => {
    const item = document.createElement("div");
    item.className = "dispatcher-note-item";

    // Color by role/type
    const roleClass = note.author_role === "field" ? "note-field"
      : note.note_type === "correction" ? "note-correction"
      : note.author_role === "system" ? "note-system"
      : "note-dispatcher";
    item.classList.add(roleClass);

    const time = note.created_at
      ? new Date(note.created_at).toLocaleString()
      : "";
    const roleLabel = note.author_role === "field" ? "Field"
      : note.author_role === "system" ? "System"
      : "Dispatch";
    const typeLabel = note.note_type === "correction" ? " [Correction]"
      : note.note_type === "field_log" ? " [Field Log]"
      : "";

    item.innerHTML =
      `<div class="dispatcher-note-item__time">${escapeHtml(time)} — ${roleLabel}${typeLabel} — ${escapeHtml(note.author_id)}</div>` +
      `<div class="dispatcher-note-item__text">${escapeHtml(note.note_text)}</div>`;
    list.appendChild(item);
  });
}

async function saveNote() {
  const input = el("dispatcher-notes-input");
  const text = input.value.trim();
  if (!text || !state.incidentId) return;

  const resultEl = el("save-note-result");
  const btn = el("save-note-btn");
  btn.disabled = true;
  resultEl.textContent = "Saving...";

  try {
    await apiCall(`/incidents/${state.incidentId}/notes`, {
      method: "PATCH",
      body: JSON.stringify({
        note_text: text,
        author_id: state.dispatcherId || "dispatcher",
        author_role: "dispatcher",
        note_type: "dispatcher_note",
      }),
    });
    resultEl.textContent = "Saved";
    resultEl.className = "action-result success";
    input.value = "";
    await loadNotes();
  } catch (err) {
    resultEl.textContent = `Error: ${err.message}`;
    resultEl.className = "action-result error";
  } finally {
    btn.disabled = false;
    setTimeout(() => { resultEl.textContent = ""; }, 3000);
  }
}

el("save-note-btn").addEventListener("click", saveNote);

function startNotesAutoSave() {
  if (notesAutoSaveTimer) return;
  notesAutoSaveTimer = setInterval(() => {
    // Auto-load notes every 15 seconds for cross-visibility
    loadNotes();
    // Auto-save unsent text every 60 seconds
    const input = el("dispatcher-notes-input");
    if (input && input.value.trim() && state.incidentId) {
      saveNote();
    }
  }, 15000);
}

function stopNotesAutoSave() {
  if (notesAutoSaveTimer) {
    clearInterval(notesAutoSaveTimer);
    notesAutoSaveTimer = null;
  }
}

// ── Gap 1.5: Protocol match banner on intake ───────────────────────────

function showProtocolMatchBanner(protocolId) {
  const banner = el("protocol-match-banner");
  const text = el("protocol-match-text");
  text.textContent = `Protocol matched: ${protocolId} — Tap to confirm`;
  show(banner);
}

function hideProtocolMatchBanner() {
  hide(el("protocol-match-banner"));
}

el("protocol-match-confirm-btn").addEventListener("click", () => {
  el("intake-form").requestSubmit();
});

// Load notes when entering script screen
const _origShowScript = show;
show = function (node) {
  _origShowScript(node);
  if (node === scriptScreen) {
    loadNotes();
    startNotesAutoSave();
  }
  if (node === intakeScreen) {
    hideProtocolMatchBanner();
  }
};
