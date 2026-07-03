import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  UserCircle,
  ChevronDown,
  ChevronUp,
  Plus,
  Trash2,
  AlertCircle,
  Save,
  ShieldCheck,
  ShieldAlert,
  AlertTriangle,
  Info,
  Activity,
  RefreshCw,
} from 'lucide-react';
import { sentenceLabel } from '../../lib/format';
import {
  autocompleteTerm,
  computeScore,
  createEncounter,
  getPatientState,
  overrideAlert,
  request,
} from '../../lib/api';
import { EvidencePanel } from '../chat/EvidencePanel';
import { PatientTimeline } from './PatientTimeline';

// ─── Scoring constants ────────────────────────────────────────────────────────

const SCORERS = [
  { key: 'news2',               label: 'NEWS2' },
  { key: 'egfr_ckd_stage',      label: 'eGFR / CKD' },
  { key: 'who_hiv_stage',       label: 'HIV stage' },
  { key: 'child_pugh',          label: 'Child-Pugh' },
  { key: 'malaria_severity',    label: 'Malaria severity' },
  { key: 'diabetes_risk_hba1c', label: 'HbA1c / DM' },
  { key: 'cvd_risk_score',      label: 'CVD risk' },
];

const SCORER_DESCRIPTIONS = {
  news2:               'National Early Warning Score 2 (NHS 2017)',
  egfr_ckd_stage:      'CKD-EPI 2021 (Race-free)',
  who_hiv_stage:       'WHO/Kenya ARV HIV Clinical Staging 2022',
  child_pugh:          'Hepatic function severity (Pugh 1973)',
  malaria_severity:    'WHO Severe Malaria (Kenya Guidelines 2023)',
  diabetes_risk_hba1c: 'Kenya DM Guidelines V15 2024',
  cvd_risk_score:      'WHO/ISH CVD Risk Chart AFRO-D',
};

const ALERT_CONFIG = {
  CRITICAL:   { border: 'var(--scoring-critical)', bg: 'var(--scoring-critical-bg)', text: 'var(--scoring-critical-text)', label: 'Critical' },
  WARNING:    { border: 'var(--scoring-warning)',  bg: 'var(--scoring-warning-bg)',  text: 'var(--scoring-warning-text)',  label: 'Warning' },
  INFO:       { border: 'var(--border)',           bg: 'transparent',                text: 'var(--text-muted)',            label: 'Info' },
  BACKGROUND: { border: 'var(--border)',           bg: 'transparent',                text: 'var(--text-muted)',            label: '' },
};

const OVERRIDE_REASONS = [
  { value: 'clinically_irrelevant',     label: 'Clinically irrelevant for this patient' },
  { value: 'already_actioned',          label: 'Already actioned / managed' },
  { value: 'patient_specific_exception', label: 'Patient-specific exception' },
  { value: 'incorrect_alert',           label: 'Alert criteria incorrect' },
  { value: 'duplicate',                 label: 'Duplicate of prior alert' },
];

const HIV_CONDITIONS = [
  'Asymptomatic',
  'Persistent generalized lymphadenopathy',
  'Unexplained weight loss (< 10%)',
  'Minor mucocutaneous manifestations',
  'Herpes zoster (within 5 years)',
  'Recurrent upper respiratory tract infections',
  'Unexplained weight loss (> 10%)',
  'Unexplained chronic diarrhoea > 1 month',
  'Unexplained persistent fever',
  'Oral candidiasis',
  'Oral hairy leukoplakia',
  'Pulmonary TB',
  'Severe bacterial infections',
  'Toxoplasmosis of brain',
  'Cryptococcal meningitis',
  'Disseminated non-TB mycobacterial infection',
  'Oesophageal candidiasis',
  'PCP (Pneumocystis pneumonia)',
  'CMV disease',
  'HIV encephalopathy',
  'HIV wasting syndrome',
  'Extrapulmonary TB',
  "Kaposi's sarcoma",
];

// ─── Scoring field definitions ────────────────────────────────────────────────

const SCORER_FIELDS = {
  news2: [
    { key: 'rr',             label: 'Respiratory rate', unit: '/min',   type: 'number', placeholder: '12–25' },
    { key: 'spo2',           label: 'SpO₂',             unit: '%',      type: 'number', placeholder: '94–100' },
    { key: 'bp_systolic',    label: 'Systolic BP',      unit: 'mmHg',  type: 'number', placeholder: '90–220' },
    { key: 'heart_rate',     label: 'Heart rate',       unit: 'bpm',   type: 'number', placeholder: '40–130' },
    { key: 'temperature',    label: 'Temperature',      unit: '°C',    type: 'number', placeholder: '36.1–37.9' },
    { key: 'consciousness',  label: 'Consciousness',    unit: '',       type: 'select', options: [
      { value: 'A', label: 'Alert (A)' },
      { value: 'V', label: 'Voice (V)' },
      { value: 'P', label: 'Pain (P)' },
      { value: 'U', label: 'Unresponsive (U)' },
    ]},
    { key: 'supplemental_o2', label: 'On O₂ supplementation', unit: '', type: 'checkbox' },
    { key: 'spo2_scale',     label: 'SpO₂ scale',       unit: '',       type: 'select', options: [
      { value: '1', label: 'Scale 1 (standard)' },
      { value: '2', label: 'Scale 2 (COPD / 88–92%)' },
    ]},
  ],
  egfr_ckd_stage: [
    { key: 'creatinine', label: 'Serum creatinine', unit: 'µmol/L', type: 'number', placeholder: '60–120' },
    { key: 'age',        label: 'Age',              unit: 'years',  type: 'number', placeholder: '18–90' },
    { key: 'sex',        label: 'Biological sex',   unit: '',       type: 'select', options: [
      { value: 'male',   label: 'Male' },
      { value: 'female', label: 'Female' },
    ]},
  ],
  child_pugh: [
    { key: 'bilirubin',      label: 'Bilirubin',            unit: 'µmol/L', type: 'number', placeholder: '17–34' },
    { key: 'albumin',        label: 'Albumin',               unit: 'g/L',    type: 'number', placeholder: '35–50' },
    { key: 'inr',            label: 'INR',                   unit: '',       type: 'number', placeholder: '0.8–1.2' },
    { key: 'ascites',        label: 'Ascites',               unit: '',       type: 'select', options: [
      { value: 'none',            label: 'None' },
      { value: 'mild',            label: 'Mild / controlled' },
      { value: 'moderate-severe', label: 'Moderate–severe / refractory' },
    ]},
    { key: 'encephalopathy', label: 'Encephalopathy grade',  unit: '',       type: 'select', options: [
      { value: '0', label: 'Grade 0 (none)' },
      { value: '1', label: 'Grade 1–2' },
      { value: '3', label: 'Grade 3–4' },
    ]},
  ],
  malaria_severity: [
    { key: 'gcs',         label: 'GCS score',     unit: '',        type: 'number', placeholder: '3–15' },
    { key: 'rr',          label: 'Resp rate',     unit: '/min',    type: 'number', placeholder: '12–40' },
    { key: 'bp_systolic', label: 'Systolic BP',   unit: 'mmHg',   type: 'number', placeholder: '70–180' },
    { key: 'hb',          label: 'Haemoglobin',   unit: 'g/dL',   type: 'number', placeholder: '7–16' },
    { key: 'glucose',     label: 'Blood glucose', unit: 'mmol/L', type: 'number', placeholder: '2.5–10' },
    { key: 'parasitaemia', label: 'Parasitaemia', unit: '%',      type: 'number', placeholder: '0–10' },
    { key: 'creatinine',  label: 'Creatinine',    unit: 'µmol/L', type: 'number', placeholder: '60–265' },
    { key: 'jaundice',    label: 'Jaundice',      unit: '',        type: 'checkbox' },
  ],
  diabetes_risk_hba1c: [
    { key: 'hba1c', label: 'HbA1c',                 unit: 'mmol/mol', type: 'number', placeholder: '48–86' },
    { key: 'fpg',   label: 'Fasting plasma glucose', unit: 'mmol/L',  type: 'number', placeholder: '3.9–11' },
  ],
  cvd_risk_score: [
    { key: 'age',              label: 'Age',              unit: 'years', type: 'number', placeholder: '40–74' },
    { key: 'sex',              label: 'Biological sex',   unit: '',      type: 'select', options: [
      { value: 'male',   label: 'Male' },
      { value: 'female', label: 'Female' },
    ]},
    { key: 'bp_systolic',     label: 'Systolic BP',      unit: 'mmHg', type: 'number', placeholder: '100–200' },
    { key: 'total_cholesterol', label: 'Total cholesterol', unit: 'mmol/L', type: 'number', placeholder: '3.5–7' },
    { key: 'smoking',         label: 'Current smoker',   unit: '',      type: 'checkbox' },
    { key: 'diabetes',        label: 'Diabetes',         unit: '',      type: 'checkbox' },
  ],
};

// ─── Generic scorer form ──────────────────────────────────────────────────────

function ScorerForm({ scorerKey, inputs, onChange }) {
  const fields = SCORER_FIELDS[scorerKey];

  if (scorerKey === 'who_hiv_stage') {
    const selected = Array.isArray(inputs.clinical_features) ? inputs.clinical_features : [];
    return (
      <div className="ctx-scorer-fields">
        <div className="ctx-scorer-field">
          <label className="param-label">CD4 count <span className="ctx-scorer-unit">(cells/µL, optional)</span></label>
          <input
            className="param-item input"
            type="number"
            placeholder="e.g. 350"
            value={inputs.cd4_count ?? ''}
            onChange={e => onChange('cd4_count', e.target.value === '' ? undefined : Number(e.target.value))}
          />
        </div>
        <div className="ctx-scorer-field ctx-scorer-field--full">
          <label className="param-label">Clinical conditions</label>
          <div className="condition-chips" style={{ marginTop: 6 }}>
            {HIV_CONDITIONS.map(cond => (
              <button
                key={cond}
                type="button"
                className={`condition-chip-sm${selected.includes(cond) ? ' active' : ''}`}
                onClick={() => {
                  const next = selected.includes(cond)
                    ? selected.filter(c => c !== cond)
                    : [...selected, cond];
                  onChange('clinical_features', next);
                }}
              >
                {cond}
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (!fields) return null;

  return (
    <div className="ctx-scorer-fields">
      {fields.map(({ key, label, unit, type, placeholder, options }) => (
        <div key={key} className="ctx-scorer-field">
          <label className="param-label">
            {label}{unit ? <span className="ctx-scorer-unit"> ({unit})</span> : null}
          </label>
          {type === 'select' ? (
            <select
              className="param-item input"
              value={inputs[key] ?? ''}
              onChange={e => onChange(key, e.target.value)}
            >
              <option value="">—</option>
              {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          ) : type === 'checkbox' ? (
            <label className="ctx-scorer-checkbox">
              <input
                type="checkbox"
                checked={!!inputs[key]}
                onChange={e => onChange(key, e.target.checked)}
              />
              <span>Yes</span>
            </label>
          ) : (
            <input
              className="param-item input"
              type="number"
              placeholder={placeholder}
              value={inputs[key] ?? ''}
              onChange={e => onChange(key, e.target.value === '' ? undefined : Number(e.target.value))}
            />
          )}
        </div>
      ))}
      {scorerKey === 'diabetes_risk_hba1c' && (
        <p className="ctx-scorer-hint">Provide at least one of HbA1c or FPG.</p>
      )}
    </div>
  );
}

// ─── Score result ─────────────────────────────────────────────────────────────

function ScoreResult({ scorerKey, result, alertLevel, sessionId, patientRefHash }) {
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [overriding, setOverriding] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [overrideError, setOverrideError] = useState('');

  if (dismissed || !result) return null;

  const cfg = ALERT_CONFIG[alertLevel] || ALERT_CONFIG.INFO;
  const showOverride = alertLevel === 'CRITICAL' || alertLevel === 'WARNING';

  const handleOverride = async (reason) => {
    setOverriding(true);
    try {
      const summary = buildSummary(scorerKey, result);
      await overrideAlert(scorerKey, alertLevel, summary, reason, sessionId || 'unknown', patientRefHash);
      setDismissed(true);
    } catch (err) {
      setOverrideError(err.message || 'Override failed');
    } finally {
      setOverriding(false);
      setOverrideOpen(false);
    }
  };

  return (
    <div className="ctx-score-result" style={{ borderLeftColor: cfg.border }}>
      <div className="ctx-score-result__header">
        {alertLevel === 'CRITICAL' && <ShieldAlert size={12} style={{ color: cfg.border }} />}
        {alertLevel === 'WARNING' && <AlertTriangle size={12} style={{ color: cfg.border }} />}
        {(alertLevel === 'INFO' || alertLevel === 'BACKGROUND') && <Info size={12} style={{ color: cfg.border }} />}
        <span className="ctx-score-result__summary" style={{ color: cfg.border }}>
          {buildSummary(scorerKey, result)}
        </span>
        {showOverride && (
          <div style={{ position: 'relative', marginLeft: 'auto' }}>
            <button className="btn-ghost-sm" onClick={() => setOverrideOpen(v => !v)}>
              Override
            </button>
            {overrideOpen && (
              <div className="ctx-score-override-menu">
                {OVERRIDE_REASONS.map(({ value, label }) => (
                  <button
                    key={value}
                    className="ctx-score-override-option"
                    disabled={overriding}
                    onClick={() => handleOverride(value)}
                  >
                    {label}
                  </button>
                ))}
                {overrideError && <p className="ctx-score-error">{overrideError}</p>}
              </div>
            )}
          </div>
        )}
      </div>
      <dl className="ctx-score-result__details">
        {Object.entries(result)
          .filter(([k]) => k !== 'source_guideline' && !Array.isArray(result[k]) && typeof result[k] !== 'object')
          .map(([k, v]) => (
            <div key={k} className="ctx-score-result__row">
              <dt>{k.replace(/_/g, ' ')}</dt>
              <dd>{String(v)}</dd>
            </div>
          ))}
        {Array.isArray(result.drug_implications) && result.drug_implications.length > 0 && (
          <div className="ctx-score-result__row ctx-score-result__row--full">
            <dt>Drug implications</dt>
            <dd>{result.drug_implications.join('; ')}</dd>
          </div>
        )}
        {result.source_guideline && (
          <div className="ctx-score-result__row ctx-score-result__row--source">
            <dt>Source</dt>
            <dd>{result.source_guideline}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}

function buildSummary(scorerKey, result) {
  if (!result) return scorerKey;
  if (scorerKey === 'news2') return `NEWS2: ${result.score} — ${result.risk_level || ''}`;
  if (scorerKey === 'egfr_ckd_stage') return `eGFR: ${result.egfr} mL/min/1.73m² — ${result.ckd_stage || ''}`;
  if (scorerKey === 'who_hiv_stage') return `WHO HIV Stage ${result.stage}`;
  if (scorerKey === 'malaria_severity') return result.is_severe ? 'Severe malaria' : 'Non-severe malaria';
  if (scorerKey === 'child_pugh') return `Child-Pugh ${result.class} (score ${result.score})`;
  if (scorerKey === 'diabetes_risk_hba1c') return result.diagnosis || 'DM assessment';
  if (scorerKey === 'cvd_risk_score') return `${result.risk_category || ''} CVD risk — ${result.ten_year_risk_pct || 0}%`;
  return scorerKey;
}

// ─── Scores tab ───────────────────────────────────────────────────────────────

function ScoresTab({ sessionId, patientRefHash, chatMessages }) {
  const [activeScorer, setActiveScorer] = useState('news2');
  const [inputs, setInputs] = useState(Object.fromEntries(SCORERS.map(s => [s.key, {}])));
  const [results, setResults] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [chatScores, setChatScores] = useState([]);

  useEffect(() => {
    if (!chatMessages?.length) return;
    const last = chatMessages[chatMessages.length - 1];
    if (last?.role === 'assistant' && Array.isArray(last.clinicalScores) && last.clinicalScores.length) {
      setChatScores(last.clinicalScores);
    }
  }, [chatMessages]);

  const handleChange = useCallback((key, value) => {
    setInputs(prev => ({ ...prev, [activeScorer]: { ...prev[activeScorer], [key]: value } }));
  }, [activeScorer]);

  const runScore = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const raw = inputs[activeScorer] || {};
      let apiInputs = { ...raw };

      if (activeScorer === 'who_hiv_stage') {
        apiInputs = { clinical_features: raw.clinical_features || [], cd4: raw.cd4_count };
      } else if (activeScorer === 'child_pugh') {
        apiInputs = {
          labs:     { bilirubin: raw.bilirubin, albumin: raw.albumin, inr: raw.inr },
          clinical: { ascites: raw.ascites || 'none', encephalopathy: Number(raw.encephalopathy || 0) },
        };
      } else if (activeScorer === 'malaria_severity') {
        apiInputs = {
          vitals:   { gcs: raw.gcs, rr: raw.rr, bp_systolic: raw.bp_systolic },
          labs:     { hb: raw.hb, glucose: raw.glucose, parasitaemia: raw.parasitaemia, creatinine: raw.creatinine },
          clinical: { jaundice: !!raw.jaundice },
        };
      }

      const res = await computeScore(activeScorer, apiInputs, patientRefHash);
      if (res.status === 'incomplete_inputs') {
        setError(`Missing: ${res.missing}`);
      } else {
        setResults(prev => ({ ...prev, [activeScorer]: res }));
      }
    } catch (err) {
      setError(err.message || 'Scoring failed');
    } finally {
      setLoading(false);
    }
  }, [activeScorer, inputs, patientRefHash]);

  const alertCount = Object.values(results).filter(r => r && ['CRITICAL', 'WARNING'].includes(r.alert_level)).length
    + chatScores.filter(s => ['CRITICAL', 'WARNING'].includes(s.alert_level)).length;

  return (
    <div className="ctx-scores-tab">
      {/* Session scores from chat */}
      {chatScores.length > 0 && (
        <div className="context-section">
          <label className="context-section-label">From current session</label>
          {chatScores.map((s, i) => (
            <ScoreResult
              key={`cs-${i}`}
              scorerKey={s.scorer}
              result={s.score_result}
              alertLevel={s.alert_level}
              sessionId={sessionId}
              patientRefHash={patientRefHash}
            />
          ))}
        </div>
      )}

      {/* Scorer selector */}
      <div className="context-section">
        <label className="context-section-label">Clinical scorer</label>
        <div className="param-item">
          <select
            className="param-item input"
            value={activeScorer}
            onChange={e => setActiveScorer(e.target.value)}
            style={{ width: '100%' }}
          >
            {SCORERS.map(({ key, label }) => (
              <option key={key} value={key}>{label}</option>
            ))}
          </select>
        </div>
        <p className="ctx-scorer-desc">{SCORER_DESCRIPTIONS[activeScorer]}</p>
      </div>

      {/* Inputs */}
      <div className="context-section">
        <label className="context-section-label">Inputs</label>
        <ScorerForm
          scorerKey={activeScorer}
          inputs={inputs[activeScorer] || {}}
          onChange={handleChange}
        />
      </div>

      {/* Run */}
      <div className="context-section">
        <button
          className="btn-save-context"
          style={{ width: '100%', justifyContent: 'center' }}
          disabled={loading}
          onClick={runScore}
        >
          {loading ? 'Calculating…' : `Calculate ${SCORERS.find(s => s.key === activeScorer)?.label}`}
        </button>
        {error && (
          <div className="inline-warning context-inline-warning" style={{ marginTop: 8 }}>
            <AlertCircle size={13} />
            <span>{error}</span>
          </div>
        )}
      </div>

      {/* Result */}
      {results[activeScorer] && (
        <div className="context-section">
          <label className="context-section-label">Result</label>
          <ScoreResult
            scorerKey={activeScorer}
            result={results[activeScorer].score_result}
            alertLevel={results[activeScorer].alert_level}
            sessionId={sessionId}
            patientRefHash={patientRefHash}
          />
        </div>
      )}
    </div>
  );
}

// ─── Main panel ───────────────────────────────────────────────────────────────

export function PatientContextPanel({
  context,
  onContextChange,
  diseases,
  sessionId,
  userRole,
  patientRefHash,
  onPatientRefHashChange,
  chatMessages,
}) {
  const canManageMemory = String(userRole).toUpperCase() === 'ADMIN';
  const [isExpanded, setIsExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState('context'); // 'context' | 'scores'

  const activeConditions = useMemo(() => context.active_conditions || [], [context.active_conditions]);
  const clinicalParams = context.clinical_params || {};
  const medications = Array.isArray(context.medications) ? context.medications : [];

  const [medicationInput, setMedicationInput] = useState('');
  const [termSuggestions, setTermSuggestions] = useState([]);
  const [termsLoading, setTermsLoading] = useState(false);
  const [contextOptions, setContextOptions] = useState(null);
  const [contextOptionsError, setContextOptionsError] = useState('');
  const [memoryCandidates, setMemoryCandidates] = useState([]);
  const [pendingMemory, setPendingMemory] = useState([]);
  const [approvedMemory, setApprovedMemory] = useState([]);
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [memoryError, setMemoryError] = useState('');
  const [savingMemoryId, setSavingMemoryId] = useState('');
  const [drugCheckLoading, setDrugCheckLoading] = useState(false);
  const [drugCheckError, setDrugCheckError] = useState('');
  const [drugCheckStatus, setDrugCheckStatus] = useState(null);
  const [encounterId, setEncounterId] = useState('');
  const [savingProfile, setSavingProfile] = useState(false);
  const [saveStatus, setSaveStatus] = useState('');
  const [saveError, setSaveError] = useState('');

  const updateContext = (key, value) => {
    onContextChange(prev => ({ ...prev, [key]: value }));
    setSaveStatus('');
  };

  const updateClinicalParam = (key, value) => {
    onContextChange(prev => ({
      ...prev,
      clinical_params: { ...(prev.clinical_params || {}), [key]: value },
    }));
    setSaveStatus('');
  };

  const normaliseDiseaseId = useCallback(
    (value) => String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, ''),
    [],
  );

  const diseaseIdForName = useCallback(
    (name) => {
      const n = normaliseDiseaseId(name);
      const exact = diseases.find(d => d.id === n);
      if (exact) return exact.id;
      const byName = diseases.find(d => normaliseDiseaseId(d.display_name) === n);
      return byName?.id || n;
    },
    [diseases, normaliseDiseaseId],
  );

  const applyPatientState = useCallback(
    (state) => {
      const next = { ...context };
      const conds = Array.from(
        new Set([
          ...(state.active_conditions || []).map(diseaseIdForName),
          ...(state.active_diagnoses || []).map(d => diseaseIdForName(d.condition_name)),
        ]),
      ).filter(Boolean);
      if (conds.length) next.active_conditions = conds;

      const meds = (state.active_medications || state.medications || [])
        .map(m => m.drug_name || m.name)
        .filter(Boolean);
      if (meds.length) next.medications = Array.from(new Set(meds));

      const vit = state.most_recent_vitals || {};
      const cp = { ...(next.clinical_params || {}) };
      [
        ['bp_systolic', 'systolic_bp'], ['bp_diastolic', 'diastolic_bp'],
        ['heart_rate', 'heart_rate'], ['respiratory_rate', 'rr'],
        ['temperature', 'temperature'], ['spo2', 'spo2'],
        ['weight_kg', 'weight'], ['height_cm', 'height'],
      ].forEach(([src, tgt]) => {
        if (vit[src] !== undefined && cp[tgt] === undefined) cp[tgt] = vit[src];
      });
      Object.entries(state.latest_labs_by_type || {}).forEach(([t, l]) => {
        if (cp[t] === undefined) cp[t] = l.value;
      });
      next.clinical_params = cp;
      onContextChange(next);
    },
    [context, diseaseIdForName, onContextChange],
  );

  const savePatientProfile = async () => {
    setSavingProfile(true);
    setSaveError('');
    setSaveStatus('');
    try {
      const diseaseScope = activeConditions[0] || diseases[0]?.id || 'all';
      const data = await createEncounter(context, 'initial', diseaseScope);
      setEncounterId(data.encounter_id);
      if (onPatientRefHashChange) onPatientRefHashChange(data.patient_ref_hash);
      setSaveStatus('Patient context saved.');
    } catch (err) {
      setSaveError(err.message || 'Unable to save patient context');
    } finally {
      setSavingProfile(false);
    }
  };

  const getMemoryContext = () => ({ ...context, active_conditions: activeConditions, medications });

  const distillSessionMemory = async () => {
    if (!sessionId) return;
    setMemoryLoading(true);
    setMemoryError('');
    try {
      const data = await request('/memory/distill-session', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId, patient_context: getMemoryContext() }),
      });
      setMemoryCandidates(data.pending || []);
      setPendingMemory([]);
      setApprovedMemory([]);
    } catch (err) {
      setMemoryCandidates([]);
      setMemoryError(err.message || 'Unable to distill session memory');
    } finally {
      setMemoryLoading(false);
    }
  };

  const loadPendingMemory = async () => {
    setMemoryLoading(true);
    setMemoryError('');
    try {
      const data = await request('/memory/pending/list', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId, patient_context: getMemoryContext() }),
      });
      setPendingMemory(data.pending || []);
      setMemoryCandidates([]);
      setApprovedMemory([]);
    } catch (err) {
      setPendingMemory([]);
      setMemoryError(err.message || 'Unable to load pending memory');
    } finally {
      setMemoryLoading(false);
    }
  };

  const loadApprovedMemory = async () => {
    setMemoryLoading(true);
    setMemoryError('');
    try {
      const data = await request('/memory/long-term/list', {
        method: 'POST',
        body: JSON.stringify({ patient_context: getMemoryContext() }),
      });
      setApprovedMemory(data.memory || []);
      setMemoryCandidates([]);
      setPendingMemory([]);
    } catch (err) {
      setApprovedMemory([]);
      setMemoryError(err.message || 'Unable to load approved memory');
    } finally {
      setMemoryLoading(false);
    }
  };

  const saveMemoryCandidate = async (mem) => {
    setSavingMemoryId(mem.id);
    setMemoryError('');
    try {
      await request('/memory/pending', {
        method: 'POST',
        body: JSON.stringify({
          session_id: sessionId,
          patient_context: getMemoryContext(),
          fact_type: mem.fact_type,
          fact_text: mem.fact_text,
          source_message_ids: mem.source_message_ids || [],
        }),
      });
      setMemoryCandidates(prev => prev.filter(m => m.id !== mem.id));
    } catch (err) {
      setMemoryError(err.message || 'Unable to save memory candidate');
    } finally {
      setSavingMemoryId('');
    }
  };

  const addMedication = (value = medicationInput.trim()) => {
    if (!value) return;
    updateContext('medications', Array.from(new Set([...medications, value])));
    setMedicationInput('');
  };

  const removeMedication = (value) => {
    updateContext('medications', medications.filter(m => m !== value));
  };

  const runDrugInteractionCheck = async () => {
    if (medications.length === 0) {
      setDrugCheckError('Add at least one medication before checking interactions.');
      return;
    }
    setDrugCheckLoading(true);
    setDrugCheckError('');
    try {
      const data = await request('/drug-interactions/check', {
        method: 'POST',
        body: JSON.stringify({ medications }),
      });
      setDrugCheckStatus(data);
    } catch (err) {
      setDrugCheckStatus(null);
      setDrugCheckError(err.message || 'Unable to check drug interactions');
    } finally {
      setDrugCheckLoading(false);
    }
  };

  useEffect(() => {
    if (!patientRefHash) return;
    getPatientState(patientRefHash)
      .then(state => { if (state && Object.keys(state).length > 0) applyPatientState(state); })
      .catch(err => setSaveError(err.message || 'Unable to load saved patient state'));
  }, [applyPatientState, patientRefHash]);

  useEffect(() => {
    const term = medicationInput.trim();
    if (term.length < 2) { setTermSuggestions([]); setTermsLoading(false); return; }
    let active = true;
    setTermsLoading(true);
    const t = window.setTimeout(async () => {
      try {
        const s = await autocompleteTerm(term);
        if (active) setTermSuggestions(s);
      } catch { if (active) setTermSuggestions([]); }
      finally { if (active) setTermsLoading(false); }
    }, 250);
    return () => { active = false; window.clearTimeout(t); };
  }, [medicationInput]);

  useEffect(() => {
    const disease = activeConditions[0] || diseases[0]?.id || 'hiv';
    request(`/context-options?disease=${encodeURIComponent(disease)}`)
      .then(data => { setContextOptions(data); setContextOptionsError(''); })
      .catch(err => { setContextOptions(null); setContextOptionsError(err.message || 'Unable to load context options'); });
  }, [activeConditions, diseases]);

  return (
    <div className={`patient-context-container ${isExpanded ? 'expanded' : 'collapsed'}`}>
      {/* Header toggle */}
      <div className="context-header" onClick={() => setIsExpanded(v => !v)}>
        <div className="context-title-group">
          <UserCircle size={20} className="text-accent" />
          <div className="context-text">
            <span className="context-label">Patient context</span>
            <span className="context-active-info">
              {activeConditions.length > 0
                ? activeConditions.map(c => sentenceLabel(diseases.find(d => d.id === c)?.display_name || c)).join(', ')
                : 'No active clinical profile'}
            </span>
          </div>
        </div>
        <div className="context-toggle">
          {isExpanded ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
        </div>
      </div>

      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="context-body"
            onClick={e => e.stopPropagation()}
          >
            {/* Tab bar */}
            <div className="ctx-tab-bar">
              <button
                className={`ctx-tab${activeTab === 'context' ? ' ctx-tab--active' : ''}`}
                onClick={() => setActiveTab('context')}
              >
                Context
              </button>
              <button
                className={`ctx-tab${activeTab === 'scores' ? ' ctx-tab--active' : ''}`}
                onClick={() => setActiveTab('scores')}
              >
                Clinical scores
              </button>
            </div>

            {/* ── Context tab ── */}
            {activeTab === 'context' && (
              <>
                <div className="context-section">
                  <label className="context-section-label">Clinical conditions</label>
                  <div className="condition-chips">
                    {diseases.map(d => (
                      <button
                        key={d.id}
                        className={`condition-chip-sm ${activeConditions.includes(d.id) ? 'active' : ''}`}
                        onClick={() => {
                          const next = activeConditions.includes(d.id)
                            ? activeConditions.filter(id => id !== d.id)
                            : [...activeConditions, d.id];
                          updateContext('active_conditions', next);
                        }}
                      >
                        {sentenceLabel(d.display_name)}
                      </button>
                    ))}
                  </div>
                </div>

                {contextOptions && (
                  <div className="context-section">
                    <label className="context-section-label">Context options</label>
                    <div className="params-grid-mini">
                      <div className="param-item">
                        <span className="param-label">Patient type</span>
                        <select value={context.patient_type || ''} onChange={e => updateContext('patient_type', e.target.value)}>
                          <option value="">Any</option>
                          {(contextOptions.patient_types || []).map(o => <option key={o} value={o}>{o}</option>)}
                        </select>
                      </div>
                      <div className="param-item">
                        <span className="param-label">Condition</span>
                        <select value={context.condition || ''} onChange={e => updateContext('condition', e.target.value)}>
                          <option value="">Any</option>
                          {(contextOptions.conditions || []).map(o => <option key={o} value={o}>{o}</option>)}
                        </select>
                      </div>
                      <div className="param-item">
                        <span className="param-label">Comorbidity</span>
                        <select value={context.comorbidity || ''} onChange={e => updateContext('comorbidity', e.target.value)}>
                          <option value="">None</option>
                          {(contextOptions.comorbidities || []).map(o => <option key={o} value={o}>{o}</option>)}
                        </select>
                      </div>
                      <div className="param-item filters-mini">
                        <span className="param-label">Clinical focus</span>
                        <div className="condition-chips compact">
                          {(contextOptions.filters || []).map(o => (
                            <button
                              key={o}
                              className={`condition-chip-sm ${context.filters?.includes(o) ? 'active' : ''}`}
                              onClick={() => updateContext('filters', context.filters?.includes(o)
                                ? context.filters.filter(f => f !== o)
                                : [...(context.filters || []), o])}
                            >
                              {o}
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {contextOptionsError && (
                  <div className="inline-warning context-inline-warning">
                    <AlertCircle size={13} /><span>{contextOptionsError}</span>
                  </div>
                )}

                <div className="context-section">
                  <label className="context-section-label">Current medications</label>
                  <div className="medication-tag-input">
                    <div className="condition-chips">
                      {medications.map(m => (
                        <button
                          key={m}
                          className="condition-chip-sm active"
                          onClick={() => removeMedication(m)}
                          title="Remove"
                        >
                          {m}<Trash2 size={10} />
                        </button>
                      ))}
                    </div>
                    <div className="medication-input-row">
                      <input
                        type="text"
                        placeholder="Add treatment or prophylaxis..."
                        value={medicationInput}
                        onChange={e => setMedicationInput(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addMedication(); } }}
                      />
                      <button className="btn-icon-tiny" onClick={() => addMedication()} title="Add">
                        <Plus size={12} />
                      </button>
                    </div>
                    {(termsLoading || termSuggestions.length > 0) && (
                      <div className="terminology-autocomplete medication-terminology-autocomplete" role="listbox">
                        {termsLoading && <div className="terminology-autocomplete-empty">Searching...</div>}
                        {termSuggestions.map((item, i) => (
                          <button
                            key={`${item.cui}-${i}`}
                            className="terminology-autocomplete-item"
                            role="option"
                            onClick={() => { addMedication(item.preferred_name || item.cui); setTermSuggestions([]); }}
                          >
                            <span>{item.preferred_name || item.cui}</span>
                            {item.cui && <small>{item.cui}</small>}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                <div className="context-section">
                  <label className="context-section-label">Drug interaction check</label>
                  <div className="context-memory-actions">
                    <button className="condition-chip-sm" onClick={runDrugInteractionCheck} disabled={drugCheckLoading}>
                      <span>{drugCheckLoading ? 'Checking...' : 'Check interactions'}</span>
                    </button>
                    <span className="status-badge-compact">{medications.length} med{medications.length !== 1 ? 's' : ''}</span>
                  </div>
                  {drugCheckError && (
                    <div className="inline-warning context-inline-warning">
                      <AlertCircle size={13} /><span>{drugCheckError}</span>
                    </div>
                  )}
                  <EvidencePanel
                    interactions={drugCheckStatus?.interactions || []}
                    drugInteractionStatus={drugCheckStatus?.status ? {
                      status: drugCheckStatus.status,
                      reason: drugCheckStatus.reason,
                      medications: drugCheckStatus.medications || medications,
                    } : null}
                    sessionId={sessionId}
                    patientRefHash={patientRefHash}
                  />
                </div>

                <div className="context-section">
                  <label className="context-section-label">Clinical parameters</label>
                  <div className="params-grid-mini">
                    <div className="param-item">
                      <span className="param-label">CD4 count</span>
                      <input type="text" placeholder="cells/µL" value={clinicalParams.cd4_count || ''} onChange={e => updateClinicalParam('cd4_count', e.target.value)} />
                    </div>
                    <div className="param-item">
                      <span className="param-label">Viral load</span>
                      <input type="text" placeholder="copies/mL" value={clinicalParams.viral_load || ''} onChange={e => updateClinicalParam('viral_load', e.target.value)} />
                    </div>
                    {(contextOptions?.clinical_params || []).map(param => (
                      <div className="param-item" key={param.id}>
                        <span className="param-label">{param.label}</span>
                        {param.options ? (
                          <select value={clinicalParams[param.id] || ''} onChange={e => updateClinicalParam(param.id, e.target.value)}>
                            <option value="">Any</option>
                            {param.options.map(o => <option key={o} value={o}>{o}</option>)}
                          </select>
                        ) : (
                          <input type="text" placeholder={param.unit || 'Value'} value={clinicalParams[param.id] || ''} onChange={e => updateClinicalParam(param.id, e.target.value)} />
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                {canManageMemory && (
                  <div className="context-section">
                    <label className="context-section-label">Patient timeline</label>
                    <PatientTimeline patientRefHash={patientRefHash} />
                  </div>
                )}

                {canManageMemory && (
                  <div className="context-section">
                    <label className="context-section-label">Session memory</label>
                    <div className="context-memory-actions">
                      <button className="condition-chip-sm" onClick={distillSessionMemory} disabled={memoryLoading || !sessionId}>
                        <ShieldCheck size={10} />
                        <span>{memoryLoading ? 'Distilling...' : 'Distill session'}</span>
                      </button>
                      <button className="condition-chip-sm" onClick={loadPendingMemory} disabled={memoryLoading}><span>Pending</span></button>
                      <button className="condition-chip-sm" onClick={loadApprovedMemory} disabled={memoryLoading}><span>Approved</span></button>
                    </div>
                    {memoryError && (
                      <div className="inline-warning context-inline-warning">
                        <AlertCircle size={13} /><span>{memoryError}</span>
                      </div>
                    )}
                    {[...memoryCandidates, ...pendingMemory, ...approvedMemory].length === 0 && !memoryError && (
                      <div className="memory-empty-mini">Distill or load memory candidates.</div>
                    )}
                    {[...memoryCandidates, ...pendingMemory, ...approvedMemory].map(mem => (
                      <div className="memory-item-mini" key={mem.id}>
                        <div><strong>{mem.fact_type}</strong><span>{mem.fact_text}</span></div>
                        {memoryCandidates.includes(mem) && (
                          <button className="btn-ghost-sm" onClick={() => saveMemoryCandidate(mem)} disabled={savingMemoryId === mem.id}>
                            {savingMemoryId === mem.id ? 'Saving...' : 'Save'}
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                <div className="context-footer">
                  <div className="status-badge-compact">
                    <AlertCircle size={10} />
                    <span>Applies to responses</span>
                  </div>
                  <button className="btn-save-context" onClick={savePatientProfile} disabled={savingProfile}>
                    <Save size={14} />
                    <span>{savingProfile ? 'Saving...' : 'Save profile'}</span>
                  </button>
                  {saveStatus && <span className="status-badge-compact">{saveStatus}</span>}
                  {saveError && <span className="status-badge-compact warning">{saveError}</span>}
                </div>
              </>
            )}

            {/* ── Scores tab ── */}
            {activeTab === 'scores' && (
              <ScoresTab
                sessionId={sessionId}
                patientRefHash={patientRefHash}
                chatMessages={chatMessages}
              />
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
