import React, { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Calendar,
  FlaskConical,
  Pill,
  Stethoscope,
  AlertTriangle,
} from 'lucide-react';
import { getPatientState } from '../../lib/api';

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(dateStr) {
  if (!dateStr) return 'Unknown date';
  try {
    return new Date(dateStr).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return dateStr;
  }
}

function sentenceCase(str) {
  if (!str) return '';
  return String(str).replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase());
}

// ─── Lab badge ────────────────────────────────────────────────────────────────

function LabBadge({ lab }) {
  const flag = String(lab.flag || '').toUpperCase();
  const isCritical = flag === 'CRITICAL' || flag === 'HH' || flag === 'LL';
  const className = `timeline-lab--abnormal ${isCritical ? 'timeline-lab--critical' : 'timeline-lab--warning'}`;
  return (
    <span className={className} title={`${lab.lab_type}: ${lab.value}${lab.unit ? ` ${lab.unit}` : ''} [${lab.flag}]`}>
      <AlertTriangle size={10} />
      {lab.lab_type}: {lab.value}{lab.unit ? ` ${lab.unit}` : ''} — {lab.flag}
    </span>
  );
}

// ─── Vitals row ───────────────────────────────────────────────────────────────

function VitalsRow({ vitals }) {
  if (!vitals || typeof vitals !== 'object') return null;
  const VITAL_LABELS = {
    bp_systolic: 'SBP',
    bp_diastolic: 'DBP',
    heart_rate: 'HR',
    respiratory_rate: 'RR',
    temperature: 'Temp',
    spo2: 'SpO₂',
    weight_kg: 'Wt',
    height_cm: 'Ht',
  };
  const entries = Object.entries(VITAL_LABELS)
    .map(([key, label]) => vitals[key] !== undefined ? { label, value: vitals[key] } : null)
    .filter(Boolean);
  if (entries.length === 0) return null;
  return (
    <div className="timeline-encounter__vitals">
      <Stethoscope size={12} className="timeline-section-icon" />
      <div className="timeline-vitals-chips">
        {entries.map(({ label, value }) => (
          <span key={label} className="timeline-vital-chip">
            <span className="timeline-vital-chip__label">{label}</span>
            <span className="timeline-vital-chip__value">{value}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

// ─── Encounter card ───────────────────────────────────────────────────────────

function EncounterCard({ encounter, index }) {
  const flaggedLabs = Array.isArray(encounter.labs)
    ? encounter.labs.filter(lab => lab.flag)
    : [];
  const meds = Array.isArray(encounter.active_medications)
    ? encounter.active_medications
    : Array.isArray(encounter.medications)
    ? encounter.medications
    : [];
  const diagnoses = Array.isArray(encounter.active_diagnoses)
    ? encounter.active_diagnoses
    : [];

  return (
    <motion.div
      className="timeline-encounter"
      initial={{ opacity: 0, x: -18 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.28, delay: index * 0.055, ease: 'easeOut' }}
    >
      {/* Spine dot */}
      <div className="timeline-encounter__dot" />

      <div className="timeline-encounter__card">
        {/* Header */}
        <div className="timeline-encounter__header">
          <div className="timeline-encounter__date">
            <Calendar size={13} />
            <span>{formatDate(encounter.encounter_date || encounter.created_at)}</span>
          </div>
          <span className="timeline-encounter__type">
            {sentenceCase(encounter.encounter_type || 'Encounter')}
          </span>
        </div>

        {/* Vitals */}
        {encounter.vitals && <VitalsRow vitals={encounter.vitals} />}

        {/* Flagged labs */}
        {flaggedLabs.length > 0 && (
          <div className="timeline-encounter__labs">
            <FlaskConical size={12} className="timeline-section-icon" />
            <div className="timeline-labs-list">
              {flaggedLabs.map((lab, i) => (
                <LabBadge key={`${lab.lab_type}-${i}`} lab={lab} />
              ))}
            </div>
          </div>
        )}

        {/* Active medications */}
        {meds.length > 0 && (
          <div className="timeline-encounter__meds">
            <Pill size={12} className="timeline-section-icon" />
            <div className="timeline-tags">
              {meds.map((med, i) => (
                <span key={i} className="timeline-tag">
                  {typeof med === 'string' ? med : med.drug_name || med.name || JSON.stringify(med)}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Active diagnoses */}
        {diagnoses.length > 0 && (
          <div className="timeline-encounter__diagnoses">
            <Stethoscope size={12} className="timeline-section-icon" />
            <div className="timeline-tags">
              {diagnoses.map((dx, i) => (
                <span key={i} className="timeline-tag timeline-tag--dx">
                  {typeof dx === 'string' ? dx : dx.condition_name || dx.name || JSON.stringify(dx)}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </motion.div>
  );
}

// ─── Skeleton placeholder ─────────────────────────────────────────────────────

function TimelineSkeleton() {
  return (
    <div className="timeline-skeleton" aria-busy="true" aria-label="Loading encounters">
      {[0, 1, 2].map(i => (
        <div key={i} className="timeline-skeleton__item">
          <div className="timeline-skeleton__dot" />
          <div className="timeline-skeleton__card">
            <div className="timeline-skeleton__line timeline-skeleton__line--short" />
            <div className="timeline-skeleton__line" />
            <div className="timeline-skeleton__line timeline-skeleton__line--medium" />
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function PatientTimeline({ patientRefHash }) {
  const [state, setState] = useState({ status: 'idle', encounters: [] });

  useEffect(() => {
    if (!patientRefHash) {
      setState({ status: 'idle', encounters: [] });
      return;
    }

    setState(prev => ({ ...prev, status: 'loading' }));

    getPatientState(patientRefHash)
      .then(data => {
        const raw = Array.isArray(data?.encounters) ? data.encounters : [];
        // Sort newest first
        const sorted = [...raw].sort((a, b) => {
          const da = new Date(a.encounter_date || a.created_at || 0).getTime();
          const db = new Date(b.encounter_date || b.created_at || 0).getTime();
          return db - da;
        });
        setState({ status: 'done', encounters: sorted });
      })
      .catch(err => {
        setState({ status: 'error', encounters: [], error: err.message || 'Unable to load patient timeline' });
      });
  }, [patientRefHash]);

  const { status, encounters, error } = state;

  return (
    <div className="patient-timeline">
      <div className="patient-timeline__header">
        <Calendar size={15} />
        <span className="patient-timeline__title">Patient Timeline</span>
      </div>

      <AnimatePresence mode="wait">
        {/* Loading */}
        {status === 'loading' && (
          <motion.div
            key="loading"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <TimelineSkeleton />
          </motion.div>
        )}

        {/* Error */}
        {status === 'error' && (
          <motion.div
            key="error"
            className="patient-timeline__error"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
          >
            <AlertTriangle size={16} />
            <span>{error}</span>
          </motion.div>
        )}

        {/* Empty */}
        {(status === 'idle' || (status === 'done' && encounters.length === 0)) && (
          <motion.div
            key="empty"
            className="patient-timeline__empty"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
          >
            <Calendar size={28} className="patient-timeline__empty-icon" />
            <p className="patient-timeline__empty-text">No encounters recorded yet</p>
            <span className="patient-timeline__empty-hint">
              {patientRefHash
                ? 'Save a patient encounter to see the timeline.'
                : 'Link a patient profile to view their encounter history.'}
            </span>
          </motion.div>
        )}

        {/* Timeline */}
        {status === 'done' && encounters.length > 0 && (
          <motion.div
            key="timeline"
            className="timeline-list"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            {encounters.map((encounter, i) => (
              <EncounterCard
                key={encounter.encounter_id || encounter.id || i}
                encounter={encounter}
                index={i}
              />
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
