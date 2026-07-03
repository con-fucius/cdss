import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronDown, ChevronUp, ShieldAlert, CheckCircle2 } from 'lucide-react';
import { overrideAlert } from '../../lib/api';

const OVERRIDE_REASONS = [
  { value: 'clinically_irrelevant', label: 'Clinically irrelevant for this patient' },
  { value: 'already_actioned', label: 'Already actioned / managed' },
  { value: 'patient_specific_exception', label: 'Patient-specific exception' },
  { value: 'incorrect_alert', label: 'Alert criteria incorrect' },
  { value: 'duplicate', label: 'Duplicate of prior alert' },
];

export function EvidencePanel({
  concepts = [],
  triples = [],
  interactions = [],
  drugInteractionStatus = null,
  reasoning = [],
  sessionId = null,
  patientRefHash = null,
}) {
  const [open, setOpen] = useState(false);
  const [overriddenAlerts, setOverriddenAlerts] = useState({});
  const [activeOverride, setActiveOverride] = useState(null);
  const [isOverriding, setIsOverriding] = useState(false);
  const [overrideError, setOverrideError] = useState('');

  const hasConcepts = Array.isArray(concepts) && concepts.length > 0;
  const hasTriples = Array.isArray(triples) && triples.length > 0;
  const hasInteractions = Array.isArray(interactions) && interactions.length > 0;
  const hasReasoning = Array.isArray(reasoning) && reasoning.length > 0;
  const hasDrugStatus = drugInteractionStatus && typeof drugInteractionStatus === 'object';

  if (!hasConcepts && !hasTriples && !hasInteractions && !hasReasoning && !hasDrugStatus) return null;

  const handleOverride = async (item, reason) => {
    if (!sessionId) return;
    setIsOverriding(true);
    setOverrideError('');
    try {
      await overrideAlert(
        'drug_interaction',
        item.alert_level || item.severity || 'WARNING',
        `${item.drug_a} + ${item.drug_b}`,
        reason,
        sessionId,
        patientRefHash,
      );
      const alertKey = `${item.drug_a}-${item.drug_b}`;
      setOverriddenAlerts(prev => ({ ...prev, [alertKey]: true }));
      setActiveOverride(null);
    } catch (err) {
      setOverrideError(err.message || 'Override failed');
    } finally {
      setIsOverriding(false);
    }
  };

  return (
    <div className="evidence-panel">
      <button
        className="evidence-panel__toggle"
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
      >
        <span>Evidence and terminology</span>
        {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="evidence-panel__body"
          >
            {hasConcepts && (
              <div className="evidence-section">
                <div className="evidence-section-title">Matched concepts</div>
                <ul className="evidence-list">
                  {concepts.map((concept, index) => (
                    <li key={`${concept.cui || concept.preferred_name || index}-${index}`}>
                      <span className="evidence-name">{concept.preferred_name || concept.cui}</span>
                      {concept.cui && <span className="evidence-cui">{concept.cui}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {hasTriples && (
              <div className="evidence-section">
                <div className="evidence-section-title">Evidence graph</div>
                <ul className="evidence-list">
                  {triples.map((triple, index) => (
                    <li key={`${triple.disease}-${triple.source}-${triple.relation}-${triple.target}-${index}`}>
                      <span>{triple.source}</span>
                      <span className="evidence-relation"> --{triple.relation}--&gt; </span>
                      <span>{triple.target}</span>
                      {triple.source_ref && <span className="evidence-source"> [{triple.source_ref}]</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {hasDrugStatus && (
              <div className="evidence-section">
                <div className="evidence-section-title">Drug interaction check</div>
                <div className="evidence-status-line">
                  Status: <strong>{drugInteractionStatus.status}</strong>
                  {drugInteractionStatus.reason && <span> ({drugInteractionStatus.reason})</span>}
                  {drugInteractionStatus.medications && (
                    <span> · {drugInteractionStatus.medications.join(', ')}</span>
                  )}
                </div>
                {!hasInteractions && drugInteractionStatus.status === 'ok' && (
                  <div className="evidence-empty-state">
                    No interactions found by the configured drug-interaction source.
                  </div>
                )}
                {drugInteractionStatus.status === 'degraded' && (
                  <div className="evidence-empty-state warning">
                    Drug interaction check was unavailable; no interaction conclusion was made.
                  </div>
                )}
              </div>
            )}

            {hasInteractions && (
              <div className="evidence-section">
                <div className="evidence-section-title">Drug interaction findings</div>
                <ul className="evidence-list">
                  {interactions.map((item, index) => {
                    const alertKey = `${item.drug_a}-${item.drug_b}`;
                    const isOverridden = overriddenAlerts[alertKey];
                    const isSevere = item.severity && (
                      item.severity.toLowerCase() === 'severe' ||
                      item.severity.toLowerCase() === 'high' ||
                      item.alert_level === 'CRITICAL' ||
                      item.alert_level === 'WARNING'
                    );
                    const showOverrideBtn = isSevere && sessionId && !isOverridden;
                    const isActivelyOverriding = activeOverride === alertKey;

                    return (
                      <li key={`${alertKey}-${index}`} className={isOverridden ? 'evidence-item-overridden' : ''}>
                        <div className="evidence-interaction-header">
                          <span>{item.drug_a}</span>
                          <span className="evidence-relation"> + </span>
                          <span>{item.drug_b}</span>
                          <span className={`evidence-source${isSevere ? ' evidence-source-severe' : ''}`}>
                            [{item.severity}]
                          </span>
                          {item.description && (
                            <span className="evidence-description">{item.description}</span>
                          )}
                          {isOverridden && (
                            <span className="status-badge-compact success">
                              <CheckCircle2 size={12} /> Overridden
                            </span>
                          )}
                          {showOverrideBtn && !isActivelyOverriding && (
                            <button
                              className="btn-ghost-sm"
                              onClick={() => { setActiveOverride(alertKey); setOverrideError(''); }}
                            >
                              <ShieldAlert size={12} /> Override
                            </button>
                          )}
                        </div>

                        {isActivelyOverriding && (
                          <div className="evidence-override-dropdown">
                            <p className="evidence-override-label">Select override reason:</p>
                            {OVERRIDE_REASONS.map(({ value, label }) => (
                              <button
                                key={value}
                                className="scoring-override-option"
                                disabled={isOverriding}
                                onClick={() => handleOverride(item, value)}
                              >
                                {label}
                              </button>
                            ))}
                            <button
                              className="btn-ghost-sm"
                              disabled={isOverriding}
                              onClick={() => { setActiveOverride(null); setOverrideError(''); }}
                            >
                              Cancel
                            </button>
                            {overrideError && (
                              <p className="scoring-override-error">{overrideError}</p>
                            )}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}

            {hasReasoning && (
              <div className="evidence-section">
                <div className="evidence-section-title">Reasoning summary</div>
                <ul className="evidence-list">
                  {reasoning.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
