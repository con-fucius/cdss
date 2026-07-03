import React, { useCallback, useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Activity,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Droplets,
  FlaskConical,
  Heart,
  Info,
  ShieldAlert,
  X,
} from 'lucide-react';
import { computeScore, overrideAlert } from '../../lib/api';

// ─── Constants ────────────────────────────────────────────────────────────────

const OVERRIDE_REASONS = [
  { value: 'clinically_irrelevant', label: 'Clinically irrelevant for this patient' },
  { value: 'already_actioned', label: 'Already actioned / managed' },
  { value: 'patient_specific_exception', label: 'Patient-specific exception' },
  { value: 'incorrect_alert', label: 'Alert criteria incorrect' },
  { value: 'duplicate', label: 'Duplicate of prior alert' },
];

const ALERT_CONFIG = {
  CRITICAL: { bg: 'var(--scoring-critical-bg)', border: 'var(--scoring-critical)', text: 'var(--scoring-critical-text)', label: 'CRITICAL' },
  WARNING:  { bg: 'var(--scoring-warning-bg)',  border: 'var(--scoring-warning)',  text: 'var(--scoring-warning-text)',  label: 'WARNING' },
  INFO:     { bg: 'var(--scoring-info-bg)',     border: 'var(--scoring-info)',     text: 'var(--scoring-info-text)',     label: 'INFO' },
  BACKGROUND: { bg: 'transparent', border: 'var(--border-subtle)', text: 'var(--text-muted)', label: 'BACKGROUND' },
};

const SCORERS = ['news2', 'egfr_ckd_stage', 'who_hiv_stage', 'child_pugh', 'malaria_severity', 'diabetes_risk_hba1c', 'cvd_risk_score'];

const SCORER_META = {
  news2:               { label: 'NEWS2',       icon: Heart,        description: 'National Early Warning Score 2 (NHS 2017)' },
  egfr_ckd_stage:      { label: 'eGFR / CKD', icon: Droplets,     description: 'CKD-EPI 2021 (Race-free)' },
  who_hiv_stage:       { label: 'HIV Stage',  icon: FlaskConical, description: 'WHO/Kenya ARV HIV Clinical Staging 2022' },
  child_pugh:          { label: 'Child-Pugh', icon: Activity,     description: 'Hepatic function severity (Pugh 1973)' },
  malaria_severity:    { label: 'Malaria',    icon: AlertTriangle, description: 'WHO Severe Malaria (Kenya Guidelines 2023)' },
  diabetes_risk_hba1c: { label: 'HbA1c / DM', icon: FlaskConical, description: 'Kenya DM Guidelines V15 2024' },
  cvd_risk_score:      { label: 'CVD Risk',   icon: Heart,        description: 'WHO/ISH CVD Risk Chart AFRO-D' },
};


// ─── NEWS2 Calculator ────────────────────────────────────────────────────────

const NEWS2_FIELDS = [
  { key: 'rr',          label: 'Respiratory rate',  unit: '/min',  type: 'number', placeholder: '12–25' },
  { key: 'spo2',        label: 'SpO₂',              unit: '%',     type: 'number', placeholder: '94–100' },
  { key: 'bp_systolic', label: 'Systolic BP',       unit: 'mmHg', type: 'number', placeholder: '90–220' },
  { key: 'heart_rate',  label: 'Heart rate',        unit: 'bpm',  type: 'number', placeholder: '40–130' },
  { key: 'temperature', label: 'Temperature',       unit: '°C',   type: 'number', placeholder: '36.1–37.9' },
  { key: 'consciousness', label: 'Consciousness',   unit: '',      type: 'select', options: [
    { value: 'A', label: 'Alert (A)' },
    { value: 'V', label: 'Voice (V)' },
    { value: 'P', label: 'Pain (P)' },
    { value: 'U', label: 'Unresponsive (U)' },
  ]},
  { key: 'supplemental_o2', label: 'On O₂ supplementation', unit: '', type: 'checkbox' },
  { key: 'spo2_scale',   label: 'SpO₂ scale',      unit: '',      type: 'select', options: [
    { value: '1', label: 'Scale 1 (standard)' },
    { value: '2', label: 'Scale 2 (COPD/target 88–92%)' },
  ]},
];

function News2Form({ inputs, onChange }) {
  return (
    <div className="scoring-fields">
      {NEWS2_FIELDS.map(({ key, label, unit, type, placeholder, options }) => (
        <div key={key} className="scoring-field">
          <label className="scoring-field__label">{label}{unit && <span className="scoring-field__unit"> ({unit})</span>}</label>
          {type === 'select' ? (
            <select
              id={`news2-${key}`}
              className="scoring-field__input"
              value={inputs[key] ?? ''}
              onChange={e => onChange(key, e.target.value)}
            >
              <option value="">—</option>
              {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          ) : type === 'checkbox' ? (
            <label className="scoring-field__checkbox">
              <input
                id={`news2-${key}`}
                type="checkbox"
                checked={!!inputs[key]}
                onChange={e => onChange(key, e.target.checked)}
              />
              <span className="scoring-field__checkbox-label">Yes</span>
            </label>
          ) : (
            <input
              id={`news2-${key}`}
              className="scoring-field__input"
              type="number"
              placeholder={placeholder}
              value={inputs[key] ?? ''}
              onChange={e => onChange(key, e.target.value === '' ? undefined : Number(e.target.value))}
            />
          )}
        </div>
      ))}
    </div>
  );
}

// ─── eGFR Calculator ─────────────────────────────────────────────────────────

const EGFR_FIELDS = [
  { key: 'creatinine', label: 'Serum creatinine', unit: 'µmol/L', type: 'number', placeholder: '60–120' },
  { key: 'age',        label: 'Age',              unit: 'years',  type: 'number', placeholder: '18–90' },
  { key: 'sex',        label: 'Biological sex',   unit: '',       type: 'select', options: [
    { value: 'male',   label: 'Male' },
    { value: 'female', label: 'Female' },
  ]},
];

function EgfrForm({ inputs, onChange }) {
  return (
    <div className="scoring-fields">
      {EGFR_FIELDS.map(({ key, label, unit, type, placeholder, options }) => (
        <div key={key} className="scoring-field">
          <label className="scoring-field__label">{label}{unit && <span className="scoring-field__unit"> ({unit})</span>}</label>
          {type === 'select' ? (
            <select id={`egfr-${key}`} className="scoring-field__input" value={inputs[key] ?? ''} onChange={e => onChange(key, e.target.value)}>
              <option value="">—</option>
              {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          ) : (
            <input
              id={`egfr-${key}`}
              className="scoring-field__input"
              type="number"
              placeholder={placeholder}
              value={inputs[key] ?? ''}
              onChange={e => onChange(key, e.target.value === '' ? undefined : Number(e.target.value))}
            />
          )}
        </div>
      ))}
    </div>
  );
}

// ─── WHO HIV Stage ────────────────────────────────────────────────────────────

const HIV_CLINICAL_CONDITIONS = [
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

function WhoHivForm({ inputs, onChange }) {
  const selected = Array.isArray(inputs.clinical_features) ? inputs.clinical_features : [];
  const toggle = (cond) => {
    const next = selected.includes(cond) ? selected.filter(c => c !== cond) : [...selected, cond];
    onChange('clinical_features', next);
  };
  return (
    <div className="scoring-fields">
      <div className="scoring-field">
        <label className="scoring-field__label">CD4 count <span className="scoring-field__unit">(cells/µL, optional)</span></label>
        <input
          id="hiv-cd4"
          className="scoring-field__input"
          type="number"
          placeholder="e.g. 350"
          value={inputs.cd4_count ?? ''}
          onChange={e => onChange('cd4_count', e.target.value === '' ? undefined : Number(e.target.value))}
        />
      </div>
      <div className="scoring-field scoring-field--full">
        <label className="scoring-field__label">Clinical conditions</label>
        <div className="scoring-conditions">
          {HIV_CLINICAL_CONDITIONS.map(cond => (
            <button
              key={cond}
              type="button"
              id={`hiv-cond-${cond.replace(/\s+/g, '-').toLowerCase()}`}
              className={`scoring-condition-tag${selected.includes(cond) ? ' scoring-condition-tag--active' : ''}`}
              onClick={() => toggle(cond)}
            >
              {cond}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── Child-Pugh Calculator ────────────────────────────────────────────────────

const CHILD_PUGH_FIELDS = [
  { key: 'bilirubin',     label: 'Bilirubin',         unit: 'µmol/L', type: 'number', placeholder: '17–34' },
  { key: 'albumin',       label: 'Albumin',            unit: 'g/L',    type: 'number', placeholder: '35–50' },
  { key: 'inr',           label: 'INR',                unit: '',       type: 'number', placeholder: '0.8–1.2' },
  { key: 'ascites',       label: 'Ascites',            unit: '',       type: 'select', options: [
    { value: 'none',               label: 'None' },
    { value: 'mild',               label: 'Mild / controlled' },
    { value: 'moderate-severe',    label: 'Moderate–Severe / refractory' },
  ]},
  { key: 'encephalopathy', label: 'Encephalopathy grade', unit: '', type: 'select', options: [
    { value: '0', label: 'Grade 0 (None)' },
    { value: '1', label: 'Grade 1–2' },
    { value: '3', label: 'Grade 3–4' },
  ]},
];

function ChildPughForm({ inputs, onChange }) {
  return (
    <div className="scoring-fields">
      {CHILD_PUGH_FIELDS.map(({ key, label, unit, type, placeholder, options }) => (
        <div key={key} className="scoring-field">
          <label className="scoring-field__label">{label}{unit && <span className="scoring-field__unit"> ({unit})</span>}</label>
          {type === 'select' ? (
            <select id={`child-pugh-${key}`} className="scoring-field__input" value={inputs[key] ?? ''} onChange={e => onChange(key, e.target.value)}>
              <option value="">—</option>
              {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          ) : (
            <input id={`child-pugh-${key}`} className="scoring-field__input" type="number" placeholder={placeholder} value={inputs[key] ?? ''} onChange={e => onChange(key, e.target.value === '' ? undefined : Number(e.target.value))} />
          )}
        </div>
      ))}
    </div>
  );
}

// ─── Malaria Severity Calculator ─────────────────────────────────────────────

const MALARIA_FIELDS = [
  { key: 'gcs',          label: 'GCS score',       unit: '',      type: 'number', placeholder: '3–15' },
  { key: 'rr',           label: 'Resp rate',       unit: '/min',  type: 'number', placeholder: '12–40' },
  { key: 'bp_systolic',  label: 'Systolic BP',     unit: 'mmHg', type: 'number', placeholder: '70–180' },
  { key: 'hb',           label: 'Haemoglobin',     unit: 'g/dL', type: 'number', placeholder: '7–16' },
  { key: 'glucose',      label: 'Blood glucose',   unit: 'mmol/L', type: 'number', placeholder: '2.5–10' },
  { key: 'parasitaemia', label: 'Parasitaemia',    unit: '%',    type: 'number', placeholder: '0–10' },
  { key: 'creatinine',   label: 'Creatinine',      unit: 'µmol/L', type: 'number', placeholder: '60–265' },
  { key: 'jaundice',     label: 'Jaundice present', unit: '',    type: 'checkbox' },
];

function MalariaForm({ inputs, onChange }) {
  return (
    <div className="scoring-fields">
      {MALARIA_FIELDS.map(({ key, label, unit, type, placeholder }) => (
        <div key={key} className="scoring-field">
          <label className="scoring-field__label">{label}{unit && <span className="scoring-field__unit"> ({unit})</span>}</label>
          {type === 'checkbox' ? (
            <label className="scoring-field__checkbox">
              <input id={`malaria-${key}`} type="checkbox" checked={!!inputs[key]} onChange={e => onChange(key, e.target.checked)} />
              <span className="scoring-field__checkbox-label">Yes</span>
            </label>
          ) : (
            <input id={`malaria-${key}`} className="scoring-field__input" type="number" placeholder={placeholder} value={inputs[key] ?? ''} onChange={e => onChange(key, e.target.value === '' ? undefined : Number(e.target.value))} />
          )}
        </div>
      ))}
    </div>
  );
}

// ─── HbA1c / Diabetes Calculator ─────────────────────────────────────────────

function DiabetesForm({ inputs, onChange }) {
  return (
    <div className="scoring-fields">
      <div className="scoring-field">
        <label className="scoring-field__label">HbA1c <span className="scoring-field__unit">(mmol/mol)</span></label>
        <input id="diabetes-hba1c" className="scoring-field__input" type="number" placeholder="48–86" value={inputs.hba1c ?? ''} onChange={e => onChange('hba1c', e.target.value === '' ? undefined : Number(e.target.value))} />
      </div>
      <div className="scoring-field">
        <label className="scoring-field__label">Fasting plasma glucose <span className="scoring-field__unit">(mmol/L)</span></label>
        <input id="diabetes-fpg" className="scoring-field__input" type="number" placeholder="3.9–11" value={inputs.fpg ?? ''} onChange={e => onChange('fpg', e.target.value === '' ? undefined : Number(e.target.value))} />
      </div>
      <p className="scoring-help-text">Provide at least one of HbA1c or FPG.</p>
    </div>
  );
}

// ─── CVD Risk Calculator ──────────────────────────────────────────────────────

function CvdRiskForm({ inputs, onChange }) {
  return (
    <div className="scoring-fields">
      <div className="scoring-field">
        <label className="scoring-field__label">Age <span className="scoring-field__unit">(years)</span></label>
        <input id="cvd-age" className="scoring-field__input" type="number" placeholder="40–74" value={inputs.age ?? ''} onChange={e => onChange('age', e.target.value === '' ? undefined : Number(e.target.value))} />
      </div>
      <div className="scoring-field">
        <label className="scoring-field__label">Biological sex</label>
        <select id="cvd-sex" className="scoring-field__input" value={inputs.sex ?? ''} onChange={e => onChange('sex', e.target.value)}>
          <option value="">—</option>
          <option value="male">Male</option>
          <option value="female">Female</option>
        </select>
      </div>
      <div className="scoring-field">
        <label className="scoring-field__label">Systolic BP <span className="scoring-field__unit">(mmHg)</span></label>
        <input id="cvd-bp" className="scoring-field__input" type="number" placeholder="100–200" value={inputs.bp_systolic ?? ''} onChange={e => onChange('bp_systolic', e.target.value === '' ? undefined : Number(e.target.value))} />
      </div>
      <div className="scoring-field">
        <label className="scoring-field__label">Total cholesterol <span className="scoring-field__unit">(mmol/L)</span></label>
        <input id="cvd-chol" className="scoring-field__input" type="number" placeholder="3.5–7" value={inputs.total_cholesterol ?? ''} onChange={e => onChange('total_cholesterol', e.target.value === '' ? undefined : Number(e.target.value))} />
      </div>
      <div className="scoring-field">
        <label className="scoring-field__label">Current smoker</label>
        <label className="scoring-field__checkbox">
          <input id="cvd-smoking" type="checkbox" checked={!!inputs.smoking} onChange={e => onChange('smoking', e.target.checked)} />
          <span className="scoring-field__checkbox-label">Yes</span>
        </label>
      </div>
      <div className="scoring-field">
        <label className="scoring-field__label">Diabetes</label>
        <label className="scoring-field__checkbox">
          <input id="cvd-diabetes" type="checkbox" checked={!!inputs.diabetes} onChange={e => onChange('diabetes', e.target.checked)} />
          <span className="scoring-field__checkbox-label">Yes</span>
        </label>
      </div>
    </div>
  );
}

// ─── Alert Badge ──────────────────────────────────────────────────────────────

function AlertBadge({ level }) {
  const cfg = ALERT_CONFIG[level] || ALERT_CONFIG.INFO;
  return (
    <span className="scoring-alert-badge" style={{ background: cfg.bg, borderColor: cfg.border, color: cfg.text }}>
      {level === 'CRITICAL' && <ShieldAlert size={12} />}
      {level === 'WARNING' && <AlertTriangle size={12} />}
      {(level === 'INFO' || level === 'BACKGROUND') && <Info size={12} />}
      {cfg.label}
    </span>
  );
}

// ─── Score Result Card ────────────────────────────────────────────────────────

function ScoreResultCard({ result, alertLevel, scorer, sessionId, patientRefHash, onOverridden }) {
  const [open, setOpen] = useState(false);
  const [overriding, setOverriding] = useState(false);
  const [overrideError, setOverrideError] = useState('');
  const [dismissed, setDismissed] = useState(false);
  const cfg = ALERT_CONFIG[alertLevel] || ALERT_CONFIG.INFO;
  const showOverride = alertLevel === 'CRITICAL' || alertLevel === 'WARNING';

  if (dismissed) return null;

  const handleOverride = async (reason) => {
    setOverriding(true);
    setOverrideError('');
    try {
      const summary = buildSummaryText(scorer, result);
      await overrideAlert(scorer, alertLevel, summary, reason, sessionId || 'unknown', patientRefHash);
      setDismissed(true);
      onOverridden?.({ scorer, alertLevel, reason });
    } catch (err) {
      setOverrideError(err.message || 'Override failed');
    } finally {
      setOverriding(false);
      setOpen(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95 }}
      className="scoring-result"
      style={{ borderColor: cfg.border, background: cfg.bg }}
    >
      <div className="scoring-result__header">
        <AlertBadge level={alertLevel} />
        <span className="scoring-result__summary">{buildSummaryText(scorer, result)}</span>
        {showOverride && (
          <div className="scoring-override-wrapper">
            <button
              id={`override-btn-${scorer}`}
              className="scoring-override-btn"
              onClick={() => setOpen(v => !v)}
              title="Override this alert"
            >
              Override <ChevronDown size={12} />
            </button>
            <AnimatePresence>
              {open && (
                <motion.div
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  className="scoring-override-dropdown"
                >
                  {OVERRIDE_REASONS.map(({ value, label }) => (
                    <button
                      key={value}
                      id={`override-reason-${scorer}-${value}`}
                      className="scoring-override-option"
                      disabled={overriding}
                      onClick={() => handleOverride(value)}
                    >
                      {label}
                    </button>
                  ))}
                  {overrideError && <p className="scoring-override-error">{overrideError}</p>}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}
      </div>
      <ScoreDetails scorer={scorer} result={result} />
    </motion.div>
  );
}

function buildSummaryText(scorer, result) {
  if (!result) return scorer;
  if (scorer === 'news2') return `NEWS2 Score: ${result.score} — ${result.risk_level || ''}`;
  if (scorer === 'egfr') return `eGFR: ${result.egfr} mL/min/1.73m² — ${result.ckd_stage || ''}`;
  if (scorer === 'who_hiv_stage') return `WHO HIV Stage ${result.stage}`;
  if (scorer === 'malaria_severity') return result.is_severe ? 'Severe Malaria' : 'Non-severe Malaria';
  if (scorer === 'child_pugh') return `Child-Pugh ${result.class} (${result.score})`;
  return JSON.stringify(result);
}

function ScoreDetails({ scorer, result }) {
  if (!result) return null;
  const entries = Object.entries(result).filter(([k]) => !['source_guideline'].includes(k));
  return (
    <dl className="scoring-result__details">
      {entries.map(([k, v]) => (
        <div key={k} className="scoring-result__detail-row">
          <dt>{k.replace(/_/g, ' ')}</dt>
          <dd>{Array.isArray(v) ? v.join(', ') : String(v)}</dd>
        </div>
      ))}
      {result.source_guideline && (
        <div className="scoring-result__detail-row scoring-result__detail-row--source">
          <dt>Source</dt>
          <dd>{result.source_guideline}</dd>
        </div>
      )}
    </dl>
  );
}

// ─── Main Panel ───────────────────────────────────────────────────────────────

export function ScoringPanel({ sessionId, patientRefHash, chatMessages }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState('news2');
  const [inputs, setInputs] = useState(Object.fromEntries(SCORERS.map(s => [s, {}])));
  const [results, setResults] = useState({});
  const [loading, setLoading] = useState({});
  const [errors, setErrors] = useState({});
  const [chatScores, setChatScores] = useState([]);  // scores arriving from chat_stream
  const [overriddenCount, setOverriddenCount] = useState(0);

  // Sync clinical_score events from the latest chat message
  useEffect(() => {
    if (!chatMessages?.length) return;
    const lastMsg = chatMessages[chatMessages.length - 1];
    if (lastMsg?.role === 'assistant' && Array.isArray(lastMsg.clinicalScores) && lastMsg.clinicalScores.length) {
      setChatScores(lastMsg.clinicalScores);
    }
  }, [chatMessages]);

  const handleInputChange = useCallback((scorer, key, value) => {
    setInputs(prev => ({ ...prev, [scorer]: { ...prev[scorer], [key]: value } }));
  }, []);

  const runScore = useCallback(async (scorer) => {
    setLoading(prev => ({ ...prev, [scorer]: true }));
    setErrors(prev => ({ ...prev, [scorer]: '' }));
    try {
      const currentInputs = inputs[scorer] || {};
      let apiInputs = { ...currentInputs };
      // Map scorer-specific input structures
      if (scorer === 'egfr_ckd_stage') {
        if (!apiInputs.creatinine || !apiInputs.age || !apiInputs.sex) { setLoading(p => ({ ...p, [scorer]: false })); return; }
      } else if (scorer === 'who_hiv_stage') {
        apiInputs = { clinical_features: currentInputs.clinical_features || [], cd4: currentInputs.cd4 };
      } else if (scorer === 'child_pugh') {
        apiInputs = {
          labs: { bilirubin: currentInputs.bilirubin, albumin: currentInputs.albumin, inr: currentInputs.inr },
          clinical: { ascites: currentInputs.ascites || 'none', encephalopathy: Number(currentInputs.encephalopathy || 0) }
        };
      } else if (scorer === 'malaria_severity') {
        const { gcs, rr, bp_systolic, hb, glucose, parasitaemia, creatinine, jaundice } = currentInputs;
        apiInputs = {
          vitals: { gcs, rr, bp_systolic },
          labs: { hb, glucose, parasitaemia, creatinine },
          clinical: { jaundice: !!jaundice }
        };
      } else if (scorer === 'cvd_risk_score') {
        if (!apiInputs.age || !apiInputs.sex || !apiInputs.bp_systolic || !apiInputs.total_cholesterol) {
          setLoading(p => ({ ...p, [scorer]: false })); return;
        }
      }

      const res = await computeScore(scorer === 'egfr_ckd_stage' ? 'egfr_ckd_stage' : scorer, apiInputs, patientRefHash);
      if (res.status === 'incomplete_inputs') {
        setErrors(prev => ({ ...prev, [scorer]: `Missing: ${res.missing}` }));
      } else {
        setResults(prev => ({ ...prev, [scorer]: res }));
      }
    } catch (err) {
      setErrors(prev => ({ ...prev, [scorer]: err.message || 'Scoring failed' }));
    } finally {
      setLoading(prev => ({ ...prev, [scorer]: false }));
    }
  }, [inputs, patientRefHash]);

  const handleOverridden = useCallback(() => {
    setOverriddenCount(n => n + 1);
  }, []);

  const highAlertCount = Object.values(results).filter(r => r && ['CRITICAL', 'WARNING'].includes(r.alert_level)).length
    + chatScores.filter(s => ['CRITICAL', 'WARNING'].includes(s.alert_level)).length;

  const TabIcon = SCORER_META[activeTab]?.icon || Activity;

  return (
    <div className={`scoring-panel${isExpanded ? ' scoring-panel--expanded' : ''}`}>
      {/* Header / toggle */}
      <button
        id="scoring-panel-toggle"
        className="scoring-panel__toggle"
        onClick={() => setIsExpanded(v => !v)}
        aria-expanded={isExpanded}
      >
        <span className="scoring-panel__toggle-left">
          <Activity size={15} />
          <span>Clinical Scores</span>
          {highAlertCount > 0 && (
            <span className="scoring-panel__badge scoring-panel__badge--alert">{highAlertCount}</span>
          )}
        </span>
        {isExpanded ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
      </button>

      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: 'easeInOut' }}
            className="scoring-panel__body"
          >
            {/* Chat-stream scores section */}
            {chatScores.length > 0 && (
              <div className="scoring-chat-scores">
                <p className="scoring-section-label">From current session</p>
                <AnimatePresence>
                  {chatScores.map((s, i) => (
                    <ScoreResultCard
                      key={`chat-${s.scorer}-${i}`}
                      scorer={s.scorer}
                      result={s.score_result}
                      alertLevel={s.alert_level}
                      sessionId={sessionId}
                      patientRefHash={patientRefHash}
                      onOverridden={handleOverridden}
                    />
                  ))}
                </AnimatePresence>
              </div>
            )}

            {/* Calculator tabs */}
            <div className="scoring-tabs">
              {SCORERS.map(scorer => {
                const meta = SCORER_META[scorer];
                const Icon = meta.icon;
                const res = results[scorer];
                const hasAlert = res && ['CRITICAL', 'WARNING'].includes(res.alert_level);
                return (
                  <button
                    key={scorer}
                    id={`scoring-tab-${scorer}`}
                    className={`scoring-tab${activeTab === scorer ? ' scoring-tab--active' : ''}${hasAlert ? ' scoring-tab--alert' : ''}`}
                    onClick={() => setActiveTab(scorer)}
                  >
                    <Icon size={13} />
                    {meta.label}
                    {hasAlert && <span className="scoring-tab__dot" />}
                  </button>
                );
              })}
            </div>

            <div className="scoring-calculator">
              <p className="scoring-calculator__description">{SCORER_META[activeTab]?.description}</p>

              {activeTab === 'news2' && <News2Form inputs={inputs.news2} onChange={(k, v) => handleInputChange('news2', k, v)} />}
              {activeTab === 'egfr_ckd_stage' && <EgfrForm inputs={inputs.egfr_ckd_stage} onChange={(k, v) => handleInputChange('egfr_ckd_stage', k, v)} />}
              {activeTab === 'who_hiv_stage' && <WhoHivForm inputs={inputs.who_hiv_stage} onChange={(k, v) => handleInputChange('who_hiv_stage', k, v)} />}
              {activeTab === 'child_pugh' && <ChildPughForm inputs={inputs.child_pugh} onChange={(k, v) => handleInputChange('child_pugh', k, v)} />}
              {activeTab === 'malaria_severity' && <MalariaForm inputs={inputs.malaria_severity} onChange={(k, v) => handleInputChange('malaria_severity', k, v)} />}
              {activeTab === 'diabetes_risk_hba1c' && <DiabetesForm inputs={inputs.diabetes_risk_hba1c} onChange={(k, v) => handleInputChange('diabetes_risk_hba1c', k, v)} />}
              {activeTab === 'cvd_risk_score' && <CvdRiskForm inputs={inputs.cvd_risk_score} onChange={(k, v) => handleInputChange('cvd_risk_score', k, v)} />}

              <button
                id={`scoring-run-${activeTab}`}
                className="scoring-run-btn"
                disabled={loading[activeTab]}
                onClick={() => runScore(activeTab)}
              >
                {loading[activeTab] ? 'Calculating…' : `Calculate ${SCORER_META[activeTab]?.label}`}
              </button>

              {errors[activeTab] && (
                <p className="scoring-error">{errors[activeTab]}</p>
              )}

              <AnimatePresence>
                {results[activeTab] && (
                  <ScoreResultCard
                    key={`calc-${activeTab}`}
                    scorer={activeTab}
                    result={results[activeTab].score_result}
                    alertLevel={results[activeTab].alert_level}
                    sessionId={sessionId}
                    patientRefHash={patientRefHash}
                    onOverridden={handleOverridden}
                  />
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
