import React, { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Circle,
  Loader2,
  MapPin,
  ShieldAlert,
  Stethoscope,
  XCircle,
} from "lucide-react";
import { listPathways, streamPathway } from "../lib/api";

// ── Step Status Icon ──────────────────────────────────────────────────────────

function StepIcon({ status }) {
  switch (status) {
    case "completed":
      return (
        <CheckCircle2
          size={18}
          className="pathway-step-icon pathway-step-icon--completed"
        />
      );
    case "current":
      return (
        <MapPin
          size={18}
          className="pathway-step-icon pathway-step-icon--current"
        />
      );
    case "blocked":
      return (
        <XCircle
          size={18}
          className="pathway-step-icon pathway-step-icon--blocked"
        />
      );
    default:
      return (
        <Circle
          size={18}
          className="pathway-step-icon pathway-step-icon--pending"
        />
      );
  }
}

// ── Step Card ─────────────────────────────────────────────────────────────────

function StepCard({ event }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className={`pathway-step pathway-step--${event.status}`}
    >
      <StepIcon status={event.status} />
      <div className="pathway-step__body">
        <div className="pathway-step__header">
          <span className="pathway-step__num">Step {event.step_number}</span>
          <span className="pathway-step__name">{event.name}</span>
          <span
            className={`pathway-step__badge pathway-step__badge--${event.status}`}
          >
            {event.status}
          </span>
        </div>
        {event.guideline_ref && (
          <p className="pathway-step__ref">📋 {event.guideline_ref}</p>
        )}
        {event.blocking_inputs?.length > 0 && (
          <div className="pathway-step__blocking">
            <span className="pathway-step__blocking-label">Needed:</span>
            {event.blocking_inputs.map((inp) => (
              <span key={inp} className="pathway-step__blocking-tag">
                {inp}
              </span>
            ))}
          </div>
        )}
      </div>
    </motion.div>
  );
}

// ── PathwayExplorer ───────────────────────────────────────────────────────────

export default function PathwayExplorer({ patientRefHash }) {
  const [pathways, setPathways] = useState([]);
  const [loadingList, setLoadingList] = useState(true);
  const [selectedId, setSelectedId] = useState(null);
  const [filterDisease, setFilterDisease] = useState("all");
  const [steps, setSteps] = useState([]);
  const [summary, setSummary] = useState(null);
  const [contraindications, setContraindications] = useState([]);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");
  const streamRef = useRef(null);

  useEffect(() => {
    listPathways()
      .then((data) => setPathways(data?.pathways || []))
      .catch(() => setPathways([]))
      .finally(() => setLoadingList(false));
  }, []);

  const diseases = ["all", ...new Set(pathways.map((p) => p.disease))];

  const visiblePathways =
    filterDisease === "all"
      ? pathways
      : pathways.filter((p) => p.disease === filterDisease);

  const runPathway = useCallback(
    (pathwayId) => {
      if (!patientRefHash) return;
      if (streamRef.current) streamRef.current.abort();
      setSelectedId(pathwayId);
      setSteps([]);
      setSummary(null);
      setContraindications([]);
      setError("");
      setIsRunning(true);

      streamRef.current = streamPathway(pathwayId, patientRefHash, (event) => {
        switch (event.type) {
          case "step":
            setSteps((prev) => [...prev, event]);
            break;
          case "contraindication":
            setContraindications((prev) => [...prev, event]);
            break;
          case "pathway_summary":
            setSummary(event);
            setIsRunning(false);
            break;
          case "warning":
            setError(event.message);
            setIsRunning(false);
            break;
          case "error":
            setError(event.message);
            setIsRunning(false);
            break;
          default:
            break;
        }
      });
    },
    [patientRefHash],
  );

  useEffect(() => {
    return () => {
      if (streamRef.current) streamRef.current.abort();
    };
  }, []);

  const selectedPathway = pathways.find((p) => p.pathway_id === selectedId);

  return (
    <div className="pathway-explorer">
      <div className="pathway-explorer__header">
        <Stethoscope size={20} />
        <div>
          <h1 className="pathway-explorer__title">Clinical Pathways</h1>
          <p className="pathway-explorer__subtitle">
            Guideline-anchored treatment pathways evaluated against patient
            state
          </p>
        </div>
      </div>

      {!patientRefHash && (
        <div className="pathway-notice">
          <AlertTriangle size={16} /> Load a patient in the Patient Context
          panel to evaluate pathways.
        </div>
      )}

      <div className="pathway-layout">
        {/* ── Pathway List ── */}
        <div className="pathway-list-panel">
          <div className="pathway-filter-bar">
            {diseases.map((d) => (
              <button
                key={d}
                id={`pathway-filter-${d}`}
                className={`pathway-filter-btn${filterDisease === d ? " pathway-filter-btn--active" : ""}`}
                onClick={() => setFilterDisease(d)}
              >
                {d === "all" ? "All" : d.replace("_", " ").toUpperCase()}
              </button>
            ))}
          </div>

          {loadingList && (
            <div className="pathway-loading">
              <Loader2 size={20} className="ddx-spin" /> Loading pathways…
            </div>
          )}

          <div className="pathway-list">
            {visiblePathways.map((pw) => (
              <button
                key={pw.pathway_id}
                id={`pathway-${pw.pathway_id}`}
                className={`pathway-item${selectedId === pw.pathway_id ? " pathway-item--active" : ""}${pw.step_count === 0 ? " pathway-item--shell" : ""}`}
                onClick={() =>
                  pw.step_count > 0 ? runPathway(pw.pathway_id) : null
                }
                disabled={!patientRefHash || pw.step_count === 0}
                title={
                  pw.step_count === 0
                    ? "Pathway definition pending"
                    : pw.target_population
                }
              >
                <div className="pathway-item__info">
                  <span className="pathway-item__name">{pw.pathway_name}</span>
                  <span className="pathway-item__disease">
                    {pw.disease.replace("_", " ")}
                  </span>
                </div>
                <div className="pathway-item__meta">
                  {pw.step_count > 0 ? (
                    <span className="pathway-item__steps">
                      {pw.step_count} steps
                    </span>
                  ) : (
                    <span className="pathway-item__pending">Pending</span>
                  )}
                  {selectedId !== pw.pathway_id &&
                    pw.step_count > 0 &&
                    patientRefHash && <ChevronRight size={14} />}
                  {selectedId === pw.pathway_id && isRunning && (
                    <Loader2 size={14} className="ddx-spin" />
                  )}
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* ── Pathway Detail ── */}
        <div className="pathway-detail-panel">
          {!selectedId && (
            <div className="pathway-empty">
              <Stethoscope size={40} className="pathway-empty__icon" />
              <p>
                Select a pathway from the list to evaluate it against the
                current patient.
              </p>
            </div>
          )}

          {selectedId && (
            <>
              <div className="pathway-detail__header">
                <h2 className="pathway-detail__title">
                  {selectedPathway?.pathway_name}
                </h2>
                <p className="pathway-detail__population">
                  {selectedPathway?.target_population}
                </p>
              </div>

              {error && (
                <div className="pathway-detail__error">
                  <AlertTriangle size={15} /> {error}
                </div>
              )}

              {contraindications.length > 0 && (
                <div className="pathway-contraindications">
                  {contraindications.map((c, i) => (
                    <div key={i} className="pathway-contraindication">
                      <ShieldAlert size={14} />
                      <strong>{c.drug}</strong> contraindicated with{" "}
                      <strong>{c.condition}</strong>
                      {c.source_ref && (
                        <span className="pathway-contraindication__ref">
                          {" "}
                          ({c.source_ref})
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <div className="pathway-steps">
                <AnimatePresence>
                  {steps.map((step, i) => (
                    <StepCard key={step.step_id || i} event={step} />
                  ))}
                </AnimatePresence>

                {isRunning && steps.length > 0 && (
                  <div className="pathway-loading-inline">
                    <Loader2 size={14} className="ddx-spin" /> Evaluating next
                    step…
                  </div>
                )}
              </div>

              {summary && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="pathway-summary"
                >
                  <h3 className="pathway-summary__title">Summary</h3>
                  <div className="pathway-summary__grid">
                    <div className="pathway-summary__stat">
                      <CheckCircle2
                        size={16}
                        className="pathway-step-icon--completed"
                      />
                      <span>
                        {summary.completed_steps?.length || 0} Completed
                      </span>
                    </div>
                    {summary.current_step && (
                      <div className="pathway-summary__stat">
                        <MapPin
                          size={16}
                          className="pathway-step-icon--current"
                        />
                        <span>Current: {summary.current_step.name}</span>
                      </div>
                    )}
                    {summary.monitoring_due?.length > 0 && (
                      <div className="pathway-summary__monitoring">
                        <AlertTriangle size={14} />
                        Monitoring due:{" "}
                        {summary.monitoring_due.map((m) => m.drug).join(", ")}
                      </div>
                    )}
                  </div>
                  {summary.next_actions?.length > 0 && (
                    <div className="pathway-summary__actions">
                      <p className="pathway-summary__actions-label">
                        Next required data:
                      </p>
                      {summary.next_actions.map((a) => (
                        <span key={a} className="pathway-summary__action-tag">
                          {a}
                        </span>
                      ))}
                    </div>
                  )}
                </motion.div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
