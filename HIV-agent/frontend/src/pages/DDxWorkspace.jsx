import React, { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  FileText,
  Loader2,
  Plus,
  X,
} from "lucide-react";
import { streamDDx } from "../lib/api";
import { MarkdownContent } from "../components/chat/MarkdownContent";

// ── Constants ─────────────────────────────────────────────────────────────────

const COMMON_SYMPTOMS = [
  "Fever",
  "Cough",
  "Headache",
  "Fatigue",
  "Weight loss",
  "Night sweats",
  "Shortness of breath",
  "Chest pain",
  "Abdominal pain",
  "Diarrhoea",
  "Vomiting",
  "Rash",
  "Joint pain",
  "Confusion",
  "Neck stiffness",
  "Haematuria",
  "Dysuria",
  "Polyuria",
  "Polydipsia",
];

const CANDIDATE_COLORS = [
  "var(--accent)",
  "#22c55e",
  "#f59e0b",
  "#8b5cf6",
  "#06b6d4",
];

// ── Tag Input ──────────────────────────────────────────────────────────────────

function TagInput({ tags, onAdd, onRemove, placeholder, suggestions }) {
  const [value, setValue] = useState("");
  const [showSuggestions, setShowSuggestions] = useState(false);
  const inputRef = useRef(null);

  const filtered = suggestions.filter(
    (s) => s.toLowerCase().includes(value.toLowerCase()) && !tags.includes(s),
  );

  const add = (sym) => {
    const trimmed = sym.trim();
    if (trimmed && !tags.includes(trimmed)) {
      onAdd(trimmed);
    }
    setValue("");
    setShowSuggestions(false);
    inputRef.current?.focus();
  };

  const handleKey = (e) => {
    if ((e.key === "Enter" || e.key === ",") && value.trim()) {
      e.preventDefault();
      add(value);
    } else if (e.key === "Backspace" && !value && tags.length) {
      onRemove(tags[tags.length - 1]);
    }
  };

  return (
    <div className="ddx-tag-input" onClick={() => inputRef.current?.focus()}>
      {tags.map((tag) => (
        <span key={tag} className="ddx-tag">
          {tag}
          <button
            type="button"
            className="ddx-tag__remove"
            onClick={() => onRemove(tag)}
            aria-label={`Remove ${tag}`}
          >
            <X size={10} />
          </button>
        </span>
      ))}
      <div className="ddx-tag-input__wrapper">
        <input
          ref={inputRef}
          className="ddx-tag-input__field"
          value={value}
          placeholder={tags.length === 0 ? placeholder : ""}
          onChange={(e) => {
            setValue(e.target.value);
            setShowSuggestions(true);
          }}
          onKeyDown={handleKey}
          onFocus={() => setShowSuggestions(true)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
        />
        {showSuggestions && value && filtered.length > 0 && (
          <ul className="ddx-suggestions">
            {filtered.slice(0, 6).map((s) => (
              <li key={s} className="ddx-suggestion" onMouseDown={() => add(s)}>
                {s}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ── Candidate Card ─────────────────────────────────────────────────────────────

function CandidateCard({ candidate, index, criteriaEvents }) {
  const criteria = criteriaEvents.find(
    (c) => c.condition === candidate.condition,
  );
  const color = CANDIDATE_COLORS[index % CANDIDATE_COLORS.length];

  return (
    <motion.div
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.07 }}
      className="ddx-candidate"
      style={{ borderLeft: `3px solid ${color}` }}
    >
      <div className="ddx-candidate__header">
        <span className="ddx-candidate__name">{candidate.condition}</span>
        <span className="ddx-candidate__match">
          {candidate.matched_symptoms?.length || candidate.weight || 0} symptom
          match
          {(candidate.matched_symptoms?.length || candidate.weight || 0) !== 1
            ? "es"
            : ""}
        </span>
      </div>
      {candidate.matched_symptoms?.length > 0 && (
        <div className="ddx-candidate__symptoms">
          {candidate.matched_symptoms.map((s) => (
            <span key={s} className="ddx-candidate__sym-tag">
              {s}
            </span>
          ))}
        </div>
      )}
      {criteria && (
        <div className="ddx-candidate__criteria">
          {criteria.criteria_chunks?.slice(0, 1).map((c, i) => (
            <p key={i} className="ddx-candidate__criteria-text">
              {c.slice(0, 200)}
              {c.length > 200 ? "…" : ""}
            </p>
          ))}
        </div>
      )}
    </motion.div>
  );
}

// ── Main DDx Workspace ─────────────────────────────────────────────────────────

export default function DDxWorkspace() {
  const [symptoms, setSymptoms] = useState([]);
  const [duration, setDuration] = useState("");
  const [vitals, setVitals] = useState({
    rr: "",
    bp_systolic: "",
    heart_rate: "",
    temperature: "",
    spo2: "",
  });
  const [labs, setLabs] = useState({
    creatinine: "",
    hb: "",
    glucose: "",
    cd4: "",
    hba1c: "",
  });
  const [isRunning, setIsRunning] = useState(false);
  const [candidates, setCandidates] = useState([]);
  const [criteriaEvents, setCriteriaEvents] = useState([]);
  const [llmChunks, setLlmChunks] = useState("");
  const [isDone, setIsDone] = useState(false);
  const [error, setError] = useState("");
  const [phase, setPhase] = useState(null); // null | 'stage1' | 'stage2' | 'stage3'
  const streamRef = useRef(null);

  const resetResults = () => {
    setCandidates([]);
    setCriteriaEvents([]);
    setLlmChunks("");
    setIsDone(false);
    setError("");
    setPhase(null);
  };

  const buildNonEmptyObj = (obj) =>
    Object.fromEntries(
      Object.entries(obj).filter(
        ([, v]) => v !== "" && v !== null && v !== undefined,
      ),
    );

  const runDDx = useCallback(() => {
    if (symptoms.length === 0) return;
    if (streamRef.current) streamRef.current.abort();
    resetResults();
    setIsRunning(true);
    setPhase("stage1");

    const request = {
      presenting_symptoms: symptoms,
      duration_days: duration ? Number(duration) : undefined,
      vital_signs: buildNonEmptyObj(vitals),
      relevant_labs: buildNonEmptyObj(labs),
    };

    streamRef.current = streamDDx(request, (event) => {
      switch (event.type) {
        case "ddx_candidates":
          setCandidates(event.candidates || []);
          setPhase("stage2");
          break;
        case "ddx_criteria":
          setCriteriaEvents((prev) => [...prev, event]);
          break;
        case "chunk":
          setPhase("stage3");
          setLlmChunks((prev) => prev + (event.content || ""));
          break;
        case "ddx_done":
          setIsDone(true);
          setIsRunning(false);
          setPhase(null);
          break;
        case "warning":
          setLlmChunks(event.message || "");
          setIsDone(true);
          setIsRunning(false);
          setPhase(null);
          break;
        case "error":
          setError(event.message || "DDx failed");
          setIsRunning(false);
          setPhase(null);
          break;
        default:
          break;
      }
    });
  }, [symptoms, duration, vitals, labs]);

  useEffect(() => {
    return () => {
      if (streamRef.current) streamRef.current.abort();
    };
  }, []);

  const phaseLabel = {
    stage1: "Stage 1: Searching evidence graph…",
    stage2: "Stage 2: Retrieving criteria…",
    stage3: "Stage 3: LLM synthesis…",
  };

  return (
    <div className="ddx-workspace">
      <div className="ddx-header">
        <div className="ddx-header__title">
          <Activity size={20} />
          <h1 className="ddx-title">Differential Diagnosis</h1>
        </div>
        <p className="ddx-subtitle">
          Evidence-grounded differential using Kenya clinical guidelines
        </p>
      </div>

      <div className="ddx-layout">
        {/* ── Panel 1: Input ── */}
        <div className="ddx-panel ddx-panel--input">
          <h2 className="ddx-panel__heading">
            <ClipboardList size={15} /> Clinical Presentation
          </h2>

          <label className="ddx-label">
            Presenting symptoms <span className="ddx-required">*</span>
          </label>
          <TagInput
            tags={symptoms}
            onAdd={(s) => setSymptoms((prev) => [...prev, s])}
            onRemove={(s) => setSymptoms((prev) => prev.filter((x) => x !== s))}
            placeholder="Type a symptom and press Enter…"
            suggestions={COMMON_SYMPTOMS}
          />

          <label className="ddx-label">Duration (days)</label>
          <input
            className="ddx-input"
            type="number"
            min={1}
            placeholder="e.g. 5"
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
          />

          <details className="ddx-expandable">
            <summary className="ddx-expandable__trigger">
              <ChevronRight size={14} /> Vital signs{" "}
              <span className="ddx-optional">(optional)</span>
            </summary>
            <div className="ddx-expandable__content">
              {[
                ["rr", "RR (/min)"],
                ["bp_systolic", "SBP (mmHg)"],
                ["heart_rate", "HR (bpm)"],
                ["temperature", "Temp (°C)"],
                ["spo2", "SpO₂ (%)"],
              ].map(([k, lbl]) => (
                <div key={k} className="ddx-field-row">
                  <label className="ddx-field-label">{lbl}</label>
                  <input
                    className="ddx-input ddx-input--sm"
                    type="number"
                    value={vitals[k]}
                    onChange={(e) =>
                      setVitals((p) => ({ ...p, [k]: e.target.value }))
                    }
                  />
                </div>
              ))}
            </div>
          </details>

          <details className="ddx-expandable">
            <summary className="ddx-expandable__trigger">
              <ChevronRight size={14} /> Lab values{" "}
              <span className="ddx-optional">(optional)</span>
            </summary>
            <div className="ddx-expandable__content">
              {[
                ["creatinine", "Creatinine (µmol/L)"],
                ["hb", "Haemoglobin (g/dL)"],
                ["glucose", "Glucose (mmol/L)"],
                ["cd4", "CD4 count (cells/µL)"],
                ["hba1c", "HbA1c (mmol/mol)"],
              ].map(([k, lbl]) => (
                <div key={k} className="ddx-field-row">
                  <label className="ddx-field-label">{lbl}</label>
                  <input
                    className="ddx-input ddx-input--sm"
                    type="number"
                    value={labs[k]}
                    onChange={(e) =>
                      setLabs((p) => ({ ...p, [k]: e.target.value }))
                    }
                  />
                </div>
              ))}
            </div>
          </details>

          <button
            id="ddx-run-btn"
            className="ddx-run-btn"
            disabled={symptoms.length === 0 || isRunning}
            onClick={runDDx}
          >
            {isRunning ? (
              <>
                <Loader2 size={15} className="ddx-spin" /> Running…
              </>
            ) : (
              <>
                <Activity size={15} /> Generate Differential
              </>
            )}
          </button>

          {error && (
            <div className="ddx-error">
              <AlertTriangle size={14} /> {error}
            </div>
          )}
        </div>

        {/* ── Panel 2: Candidates ── */}
        <div className="ddx-panel ddx-panel--candidates">
          <h2 className="ddx-panel__heading">
            <BookOpen size={15} />
            {candidates.length > 0
              ? `${candidates.length} Candidate${candidates.length !== 1 ? "s" : ""} Found`
              : "Candidate Conditions"}
          </h2>

          {phase === "stage1" && (
            <div className="ddx-status-msg">
              <Loader2 size={14} className="ddx-spin" /> Searching evidence
              graph…
            </div>
          )}

          {candidates.length === 0 &&
            phase !== "stage1" &&
            !isRunning &&
            !isDone && (
              <div className="ddx-empty">
                <Activity size={32} className="ddx-empty__icon" />
                <p>Enter symptoms and run the analysis to see candidates.</p>
              </div>
            )}

          <AnimatePresence>
            {candidates.map((cand, i) => (
              <CandidateCard
                key={cand.condition + i}
                candidate={cand}
                index={i}
                criteriaEvents={criteriaEvents}
              />
            ))}
          </AnimatePresence>

          {phase === "stage2" && candidates.length > 0 && (
            <div className="ddx-status-msg">
              <Loader2 size={14} className="ddx-spin" /> Retrieving guideline
              criteria…
            </div>
          )}
        </div>

        {/* ── Panel 3: LLM Synthesis ── */}
        <div className="ddx-panel ddx-panel--synthesis">
          <h2 className="ddx-panel__heading">
            <FileText size={15} />
            Clinical Synthesis
            {isDone && <span className="ddx-done-badge">Done</span>}
          </h2>

          {phase === "stage3" && !llmChunks && (
            <div className="ddx-status-msg">
              <Loader2 size={14} className="ddx-spin" /> LLM synthesising…
            </div>
          )}

          {!llmChunks && !isRunning && !isDone && (
            <div className="ddx-empty">
              <FileText size={32} className="ddx-empty__icon" />
              <p>LLM synthesis will stream here with guideline citations.</p>
            </div>
          )}

          {llmChunks && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="ddx-synthesis-content"
            >
              <MarkdownContent content={llmChunks} />
            </motion.div>
          )}

          {isDone && !llmChunks && (
            <div className="ddx-status-msg ddx-status-msg--info">
              Stage 3 synthesis was skipped (offline mode or no provider
              configured).
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
