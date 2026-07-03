const DEFAULT_API_BASE = "";

export const API_BASE = (
  import.meta.env.VITE_API_URL || DEFAULT_API_BASE
).replace(/\/$/, "");

export class ApiError extends Error {
  constructor(message, { status, payload } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

export function normalizeContext(context) {
  if (!context) return context;
  if (!Array.isArray(context.medications)) {
    throw new TypeError("patientContext.medications must be an array");
  }
  return {
    ...context,
    filters: Array.isArray(context.filters) ? context.filters : [],
    active_conditions: Array.isArray(context.active_conditions)
      ? context.active_conditions
      : [],
    clinical_params: context.clinical_params || {},
    medications: context.medications
      .map((item) => String(item).trim())
      .filter(Boolean),
  };
}

function normalizeBody(body) {
  if (!body || typeof body !== "string") return body;
  try {
    const payload = JSON.parse(body);
    if (payload && typeof payload === "object" && payload.context) {
      payload.context = normalizeContext(payload.context);
      return JSON.stringify(payload);
    }
  } catch (_error) {
    return body;
  }
  return body;
}

function sessionHeader(body) {
  if (!body || typeof body !== "string") return {};
  try {
    const payload = JSON.parse(body);
    const sessionId = payload?.session_id;
    return sessionId ? { "X-Session-Id": String(sessionId) } : {};
  } catch (_error) {
    return {};
  }
}

function currentUserRole() {
  return (
    sessionStorage.getItem("kini_user_role") ||
    localStorage.getItem("kini_user_role") ||
    ""
  );
}

export function getStoredUserRole() {
  return currentUserRole();
}

export function setStoredUserRole(role) {
  const normalized = String(role || "")
    .trim()
    .toUpperCase();
  if (!normalized) {
    sessionStorage.removeItem("kini_user_role");
    localStorage.removeItem("kini_user_role");
    return;
  }
  sessionStorage.setItem("kini_user_role", normalized);
  localStorage.removeItem("kini_user_role");
}

function roleHeader() {
  const role = currentUserRole();
  return role ? { "X-User-Role": role } : {};
}

async function parseResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response.text();
}

export async function request(path, options = {}) {
  const body = normalizeBody(options.body);
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    body,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...roleHeader(),
      ...sessionHeader(body),
      ...(options.headers || {}),
    },
  });

  if (!response.ok) {
    let payload = null;
    try {
      payload = await parseResponse(response);
    } catch (_error) {
      payload = null;
    }
    const detail = payload?.detail || payload?.message || response.statusText;
    throw new ApiError(`${detail} (HTTP ${response.status})`, {
      status: response.status,
      payload,
    });
  }

  return parseResponse(response);
}

export function streamRequest(path, options = {}) {
  const body = normalizeBody(options.body);
  return fetch(`${API_BASE}${path}`, {
    ...options,
    body,
    headers: {
      "Content-Type": "application/json",
      ...roleHeader(),
      ...sessionHeader(body),
      ...(options.headers || {}),
    },
  });
}

const terminologyAutocompleteCache = new Map();

export async function autocompleteTerm(query) {
  const term = String(query || "").trim();
  if (term.length < 2) return [];
  if (terminologyAutocompleteCache.has(term)) {
    return terminologyAutocompleteCache.get(term);
  }
  const data = await request("/terminology/autocomplete", {
    method: "POST",
    body: JSON.stringify({ query: term, top_k: 8 }),
  });
  const results = Array.isArray(data.results) ? data.results : [];
  if (terminologyAutocompleteCache.size >= 50) {
    terminologyAutocompleteCache.delete(
      terminologyAutocompleteCache.keys().next().value,
    );
  }
  terminologyAutocompleteCache.set(term, results);
  return results;
}

export async function createEncounter(
  patientContext,
  encounterType = "initial",
  diseaseScope = "all",
) {
  return request("/patient/encounter", {
    method: "POST",
    body: JSON.stringify({
      patient_context: normalizeContext(patientContext),
      encounter_type: encounterType,
      disease_scope: diseaseScope,
    }),
  });
}

export async function getPatientState(patientRefHash) {
  if (!patientRefHash) return {};
  return request(`/patient/state/${encodeURIComponent(patientRefHash)}`);
}

export async function addVitals(patientRefHash, encounterId, vitals) {
  return request("/patient/vitals", {
    method: "POST",
    body: JSON.stringify({
      patient_ref_hash: patientRefHash,
      encounter_id: encounterId,
      vitals,
    }),
  });
}

export async function addLabs(patientRefHash, encounterId, labs) {
  return request("/patient/labs", {
    method: "POST",
    body: JSON.stringify({
      patient_ref_hash: patientRefHash,
      encounter_id: encounterId,
      labs,
    }),
  });
}

export async function computeScore(scorer, inputs, patientRef = null) {
  return request("/clinical/score", {
    method: "POST",
    body: JSON.stringify({ scorer, inputs, patient_ref: patientRef }),
  });
}

export async function overrideAlert(
  alertType,
  alertLevel,
  alertSummary,
  overrideReason,
  sessionId,
  patientRefHash = null,
) {
  return request("/alerts/override", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      patient_ref_hash: patientRefHash,
      alert_type: alertType,
      alert_level: alertLevel,
      alert_summary: alertSummary,
      override_reason: overrideReason,
    }),
  });
}

// ── DDx API ──────────────────────────────────────────────────────────────────

/**
 * Stream a differential diagnosis over SSE.
 * onEvent(event: { type: string, ...data }) called for each SSE event.
 * Returns a controller with { abort() }.
 */
export function streamDDx(request, onEvent) {
  const controller = new AbortController();
  const { signal } = controller;

  (async () => {
    try {
      const res = await fetch(`${API_BASE}/clinical/ddx`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal,
      });
      if (!res.ok) throw new Error(`DDx request failed: ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              onEvent(JSON.parse(line.slice(6)));
            } catch (_) {
              /* ignore */
            }
          }
        }
      }
    } catch (err) {
      if (err.name !== "AbortError")
        onEvent({ type: "error", message: err.message });
    }
  })();

  return { abort: () => controller.abort() };
}

// ── Pathway API ──────────────────────────────────────────────────────────────

export async function listPathways() {
  return request("/clinical/pathways");
}

export function streamPathway(pathwayId, patientRef, onEvent) {
  const controller = new AbortController();
  const { signal } = controller;

  (async () => {
    try {
      const res = await fetch(`${API_BASE}/clinical/pathway/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pathway_id: pathwayId,
          patient_ref: patientRef,
        }),
        signal,
      });
      if (!res.ok) throw new Error(`Pathway run failed: ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              onEvent(JSON.parse(line.slice(6)));
            } catch (_) {
              /* ignore */
            }
          }
        }
      }
    } catch (err) {
      if (err.name !== "AbortError")
        onEvent({ type: "error", message: err.message });
    }
  })();

  return { abort: () => controller.abort() };
}

// ── Document API ─────────────────────────────────────────────────────────────

export async function generateDocument(
  documentType,
  patientRef,
  encounterId,
  additionalContext = null,
) {
  return request("/clinical/documents/generate", {
    method: "POST",
    body: JSON.stringify({
      document_type: documentType,
      patient_ref: patientRef,
      encounter_id: encounterId,
      additional_context: additionalContext,
    }),
  });
}

export async function getDocument(documentId) {
  return request(`/clinical/documents/${encodeURIComponent(documentId)}`);
}

export async function listPatientDocuments(patientRef) {
  return request(
    `/clinical/documents/patient/${encodeURIComponent(patientRef)}`,
  );
}

export async function reviewDocument(documentId, reviewedBy) {
  return request(
    `/clinical/documents/${encodeURIComponent(documentId)}/review`,
    {
      method: "PATCH",
      body: JSON.stringify({ reviewed_by: reviewedBy }),
    },
  );
}
