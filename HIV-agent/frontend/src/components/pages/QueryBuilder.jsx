import React, { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { 
  Plus, 
  Trash2, 
  Send, 
  AlertCircle,
  Loader2,
  ExternalLink,
  Database
} from 'lucide-react';
import { request, streamRequest } from '../../lib/api';
import { sentenceLabel } from '../../lib/format';
import { MarkdownContent, SourcesDisplay } from '../chat/MarkdownContent';
import { EvidencePanel } from '../chat/EvidencePanel';
import { AgentActionLog } from '../common/AgentActionLog';

function labelFor(id, diseases) {
  const found = diseases.find(disease => disease.id === id);
  return sentenceLabel(found?.display_name || id || 'selected guideline');
}

export function QueryBuilderPage({ onNavigate, sessionId, patientContext, setPatientContext, diseases, chatProps }) {
  const indexedDiseases = diseases.filter(disease => disease.status === 'indexed');
  const selectableDiseases = indexedDiseases.length > 0 ? indexedDiseases : diseases;
  const [selectedDisease, setSelectedDisease] = useState(selectableDiseases[0]?.id || '');
  const [queryParts, setQueryParts] = useState([
    { type: 'symptom', value: '', label: 'Symptom or indicator' }
  ]);
  const [contextOptions, setContextOptions] = useState(null);
  const [structuredContext, setStructuredContext] = useState({
    patient_type: '',
    condition: '',
    comorbidity: '',
    filters: []
  });
  const [optionsError, setOptionsError] = useState('');
  const [result, setResult] = useState(null);
  const [resultEvidence, setResultEvidence] = useState({ concepts: [], triples: [], interactions: [], drugInteractionStatus: null, reasoning: [] });
  const [resultActions, setResultActions] = useState([]);
  const [terminologyExpansion, setTerminologyExpansion] = useState({ expanded_query: '', concepts: [], changed: false });
  const [terminologyLoading, setTerminologyLoading] = useState(false);
  const [terminologyError, setTerminologyError] = useState('');
  const [kbLookupType, setKbLookupType] = useState('dosing');
  const [kbLookupFilters, setKbLookupFilters] = useState({
    line: 'first-line',
    population: 'adults and adolescents'
  });
  const [kbLookup, setKbLookup] = useState(null);
  const [kbLookupLoading, setKbLookupLoading] = useState(false);
  const [kbLookupError, setKbLookupError] = useState('');
  const [showActivity, setShowActivity] = useState(true);
  const [isExecuting, setIsExecuting] = useState(false);

  useEffect(() => {
    if (selectableDiseases.length > 0 && !selectableDiseases.some(disease => disease.id === selectedDisease)) {
      setSelectedDisease(selectableDiseases[0].id);
    }
  }, [selectableDiseases, selectedDisease]);

  useEffect(() => {
    if (!selectedDisease) return;
    setOptionsError('');
    request(`/context-options?disease=${encodeURIComponent(selectedDisease)}`)
      .then(data => setContextOptions(data))
      .catch(error => {
        setContextOptions(null);
        setOptionsError(error.message || 'Unable to load context options');
      });
  }, [selectedDisease]);

  const addPart = (type) => {
    const labels = {
      symptom: 'Symptom or indicator',
      lab: 'Lab result',
      medication: 'Medication'
    };
    setQueryParts([...queryParts, { type, value: '', label: labels[type] || type }]);
  };

  const removePart = (idx) => {
    setQueryParts(queryParts.filter((_, i) => i !== idx));
  };

  const updatePart = (idx, value) => {
    const next = [...queryParts];
    next[idx].value = value;
    setQueryParts(next);
  };

  const buildStructuredQuery = () => {
    const parts = queryParts.filter(p => p.value).map(p => `${p.label}: ${p.value}`);
    const contextLines = [
      structuredContext.patient_type && `Patient type: ${structuredContext.patient_type}`,
      structuredContext.condition && `Clinical condition: ${structuredContext.condition}`,
      structuredContext.comorbidity && `Comorbidity: ${structuredContext.comorbidity}`,
      structuredContext.filters.length && `Filters: ${structuredContext.filters.join(', ')}`
    ].filter(Boolean);
    return `[Structured query for ${labelFor(selectedDisease, diseases)}]\n${[...contextLines, ...parts].join('\n')}\nProvide clinical recommendations based on these parameters.`;
  };

  const buildContext = () => {
    const clinicalParams = queryParts
      .filter(part => part.type === 'lab' && part.value)
      .reduce((acc, part) => ({ ...acc, [part.label.toLowerCase().replaceAll(' ', '_')]: part.value }), {});

    return {
      ...patientContext,
      active_conditions: selectedDisease ? Array.from(new Set([...(patientContext.active_conditions || []), selectedDisease])) : patientContext.active_conditions,
      patient_type: structuredContext.patient_type || patientContext.patient_type || '',
      condition: structuredContext.condition || patientContext.condition || '',
      comorbidity: structuredContext.comorbidity || patientContext.comorbidity || '',
      filters: structuredContext.filters,
      clinical_params: {
        ...(patientContext.clinical_params || {}),
        ...clinicalParams
      },
      medications: Array.from(new Set([
        ...(Array.isArray(patientContext.medications) ? patientContext.medications : []),
        ...queryParts.filter(part => part.type === 'medication' && part.value).map(part => part.value.trim())
      ].filter(Boolean)))
    };
  };

  const generateQuery = async () => {
    const finalQuery = buildStructuredQuery();
    const nextContext = buildContext();
    setPatientContext(nextContext);
    setIsExecuting(true);
    setResult({ query: finalQuery, content: '', sources: [], error: '' });
    setResultEvidence({ concepts: [], triples: [], interactions: [], drugInteractionStatus: null, reasoning: [] });
    setResultActions([{ text: 'Executing structured query', detail: 'Submitting generated context to retrieval stream', done: false }]);

    try {
      const response = await streamRequest('/chat/stream', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId, message: finalQuery, context: nextContext })
      });
      if (!response.ok) throw new Error(`Query execution failed (HTTP ${response.status})`);
      if (!response.body) throw new Error('Query execution stream is unavailable');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffered = '';
      let content = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffered += decoder.decode(value, { stream: true });
        const events = buffered.split('\n\n');
        buffered = events.pop() || '';

        for (const event of events) {
          const payloads = event
            .split('\n')
            .filter(line => line.startsWith('data: '))
            .map(line => line.slice(6));
          for (const payload of payloads) {
            if (!payload.trim()) continue;
            try {
              const data = JSON.parse(payload);
              if (data.type === 'activity' || data.type === 'loading') {
                setResultActions(prev => [...prev, { text: data.message, detail: data.detail || '', done: false }]);
              } else if (data.type === 'chunk') {
                content += data.content;
                setResult(prev => ({ ...prev, content }));
              } else if (data.type === 'sources') {
                setResult(prev => ({ ...prev, sources: data.sources || [] }));
              } else if (data.type === 'concepts') {
                setResultEvidence(prev => ({ ...prev, concepts: data.concepts || [] }));
              } else if (data.type === 'evidence') {
                setResultEvidence(prev => ({ ...prev, triples: data.triples || [] }));
              } else if (data.type === 'drug_interactions') {
                setResultEvidence(prev => ({
                  ...prev,
                  interactions: data.interactions || [],
                  drugInteractionStatus: data
                }));
              } else if (data.type === 'reasoning') {
                setResultEvidence(prev => ({
                  ...prev,
                  reasoning: [...prev.reasoning, data.summary || data.content || 'Provider returned reasoning metadata']
                }));
                setResultActions(prev => [...prev, { text: 'Reasoning summary', detail: data.summary || '', done: true }]);
              } else if (data.type === 'done') {
                setResultActions(prev => prev.map(action => action.text === 'Executing structured query' ? { ...action, done: true } : action));
                if (data.latency_ms) setResultActions(prev => [...prev, { text: 'Result ready', detail: `${Math.round(data.latency_ms)} ms`, done: true }]);
              } else if (data.type === 'error') {
                throw new Error(data.message || 'Query execution failed');
              }
            } catch (parseError) {
              if (parseError instanceof SyntaxError) continue;
              throw parseError;
            }
          }
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Query execution failed';
      setResult(prev => ({ ...(prev || { query: finalQuery, content: '', sources: [] }), error: message }));
      setResultActions(prev => [...prev, { text: 'Query execution failed', detail: message, done: true }]);
    } finally {
      setIsExecuting(false);
    }
  };

  const previewTerminologyExpansion = async () => {
    const query = buildStructuredQuery();
    setTerminologyLoading(true);
    setTerminologyError('');
    try {
      const data = await request('/terminology/expand', {
        method: 'POST',
        body: JSON.stringify({ query, disease: selectedDisease })
      });
      setTerminologyExpansion(data);
    } catch (error) {
      setTerminologyExpansion({ expanded_query: '', concepts: [], changed: false });
      setTerminologyError(error.message || 'Unable to expand terminology');
    } finally {
      setTerminologyLoading(false);
    }
  };

  const runStructuredKbLookup = async () => {
    setKbLookupLoading(true);
    setKbLookupError('');
    try {
      const data = await request('/kb/lookup', {
        method: 'POST',
        body: JSON.stringify({
          disease: selectedDisease,
          query_type: kbLookupType,
          filters: kbLookupFilters
        })
      });
      setKbLookup(data);
    } catch (error) {
      setKbLookup(null);
      setKbLookupError(error.message || 'Unable to query structured knowledge base');
    } finally {
      setKbLookupLoading(false);
    }
  };

  const openResultInChat = () => {
    const query = result?.query || buildStructuredQuery();
    if (chatProps?.setMessages && result?.content) {
      const now = new Date().toISOString();
      chatProps.setMessages(prev => [
        ...prev,
        { role: 'user', content: query, timestamp: now, id: crypto.randomUUID() },
        { role: 'assistant', content: result.content, timestamp: now, sources: result.sources || [], concepts: resultEvidence.concepts, triples: resultEvidence.triples, interactions: resultEvidence.interactions, drugInteractionStatus: resultEvidence.drugInteractionStatus, reasoning: resultEvidence.reasoning, id: crypto.randomUUID() }
      ]);
      onNavigate('/chat');
      return;
    }
    onNavigate('/chat', query);
  };

  return (
    <div className="page-wrapper">
      <div className="builder-container">
        <header className="page-header-minimal">
          <div className="header-info">
            <h1>Clinical query builder</h1>
            <p>Structure clinical inquiries with retrievable context</p>
          </div>
        </header>

        <div className="builder-grid">
          <section className="builder-main">
            <div className="builder-card">
              <div className="card-header">
                <span>Query configuration</span>
              </div>
              <div className="card-body">
                <div className="input-group-modern">
                  <label>Target condition</label>
                  <select 
                    value={selectedDisease} 
                    onChange={e => setSelectedDisease(e.target.value)}
                    className="builder-select-premium"
                  >
                    {selectableDiseases.map(d => (
                      <option key={d.id} value={d.id}>{sentenceLabel(d.display_name)}{d.status !== 'indexed' ? ' (not indexed)' : ''}</option>
                    ))}
                  </select>
                </div>

                {contextOptions && (
                  <div className="structured-context-grid">
                    <label>
                      <span>Patient type</span>
                      <select value={structuredContext.patient_type} onChange={event => setStructuredContext(prev => ({ ...prev, patient_type: event.target.value }))}>
                        <option value="">Any</option>
                        {(contextOptions.patient_types || []).map(option => <option key={option} value={option}>{option}</option>)}
                      </select>
                    </label>
                    <label>
                      <span>Clinical condition</span>
                      <select value={structuredContext.condition} onChange={event => setStructuredContext(prev => ({ ...prev, condition: event.target.value }))}>
                        <option value="">Any</option>
                        {(contextOptions.conditions || []).map(option => <option key={option} value={option}>{option}</option>)}
                      </select>
                    </label>
                    <label>
                      <span>Comorbidity</span>
                      <select value={structuredContext.comorbidity} onChange={event => setStructuredContext(prev => ({ ...prev, comorbidity: event.target.value }))}>
                        <option value="">None selected</option>
                        {(contextOptions.comorbidities || []).map(option => <option key={option} value={option}>{option}</option>)}
                      </select>
                    </label>
                    <div className="builder-filter-row">
                      {(contextOptions.filters || []).slice(0, 6).map(option => (
                        <button
                          key={option}
                          className={`condition-chip-sm ${structuredContext.filters.includes(option) ? 'active' : ''}`}
                          onClick={() => setStructuredContext(prev => ({
                            ...prev,
                            filters: prev.filters.includes(option)
                              ? prev.filters.filter(item => item !== option)
                              : [...prev.filters, option]
                          }))}
                        >
                          {option}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {optionsError && (
                  <div className="inline-warning">
                    <AlertCircle size={13} />
                    <span>{optionsError}</span>
                  </div>
                )}

                <div className="query-parts-list">
                  {queryParts.map((part, idx) => (
                    <div key={idx} className="query-part-item">
                      <div className="part-header">
                        <span className="part-label">{part.label}</span>
                        <button className="btn-icon-tiny" onClick={() => removePart(idx)}>
                          <Trash2 size={12} />
                        </button>
                      </div>
                      <input 
                        type="text" 
                        value={part.value} 
                        onChange={e => updatePart(idx, e.target.value)}
                        placeholder={`Enter ${part.label.toLowerCase()}...`}
                      />
                    </div>
                  ))}
                </div>

                <div className="builder-actions">
                  <button className="btn-outline-sm" onClick={() => addPart('symptom')}>
                    <Plus size={14} /> <span>Add symptom</span>
                  </button>
                  <button className="btn-outline-sm" onClick={() => addPart('lab')}>
                    <Plus size={14} /> <span>Add lab result</span>
                  </button>
                  <button className="btn-outline-sm" onClick={() => addPart('medication')}>
                    <Plus size={14} /> <span>Add medication</span>
                  </button>
                </div>
              </div>
            </div>
          </section>

          <aside className="builder-sidebar">
            <div className="preview-card">
              <div className="card-header">
                <span>Generated context</span>
              </div>
              <div className="card-body">
                <pre className="query-preview">
                  {`Condition: ${labelFor(selectedDisease, diseases)}\n`}
                  {structuredContext.patient_type ? `Patient type: ${structuredContext.patient_type}\n` : ''}
                  {structuredContext.condition ? `Clinical condition: ${structuredContext.condition}\n` : ''}
                  {structuredContext.comorbidity ? `Comorbidity: ${structuredContext.comorbidity}\n` : ''}
                  {structuredContext.filters.length ? `Filters: ${structuredContext.filters.join(', ')}\n` : ''}
                  {patientContext?.active_conditions?.length ? `Active context: ${patientContext.active_conditions.join(', ')}\n` : ''}
                  {queryParts.filter(p => p.value).map(p => `${p.label}: ${p.value}`).join('\n') || 'No parameters added...'}
                </pre>

                <div className="builder-preview-section">
                  <div className="card-header compact">
                    <span>Terminology expansion</span>
                  </div>
                  <button className="btn-outline-sm w-full" onClick={previewTerminologyExpansion} disabled={terminologyLoading}>
                    {terminologyLoading ? 'Expanding...' : 'Preview terminology expansion'}
                  </button>
                  {terminologyError && (
                    <div className="inline-warning compact">
                      <AlertCircle size={13} />
                      <span>{terminologyError}</span>
                    </div>
                  )}
                  {terminologyExpansion.expanded_query && (
                    <pre className="query-preview terminology-expanded-preview">
                      {terminologyExpansion.expanded_query}
                    </pre>
                  )}
                  {terminologyExpansion.concepts?.length > 0 && (
                    <div className="terminology-concept-list">
                      {terminologyExpansion.concepts.map(concept => (
                        <div className="terminology-concept-item" key={concept.cui}>
                          <strong>{concept.preferred_name || concept.cui}</strong>
                          {concept.cui && <small>{concept.cui}</small>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="builder-preview-section">
                  <div className="card-header compact">
                    <span>Structured knowledge base</span>
                  </div>
                  <div className="kb-lookup-controls">
                    <label>
                      <span>Table type</span>
                      <select value={kbLookupType} onChange={event => setKbLookupType(event.target.value)}>
                        <option value="dosing">Dosing</option>
                        <option value="regimen">Regimen</option>
                        <option value="diagnostic_criteria">Diagnostic criteria</option>
                      </select>
                    </label>
                    <label>
                      <span>Line</span>
                      <select value={kbLookupFilters.line} onChange={event => setKbLookupFilters(prev => ({ ...prev, line: event.target.value }))}>
                        <option value="first-line">First-line</option>
                        <option value="">Any</option>
                      </select>
                    </label>
                    <label>
                      <span>Population</span>
                      <select value={kbLookupFilters.population} onChange={event => setKbLookupFilters(prev => ({ ...prev, population: event.target.value }))}>
                        <option value="adults and adolescents">Adults/adolescents</option>
                        <option value="children and adolescents">Children/adolescents</option>
                        <option value="">Any</option>
                      </select>
                    </label>
                  </div>
                  <button className="btn-outline-sm w-full" onClick={runStructuredKbLookup} disabled={kbLookupLoading}>
                    <Database size={13} />
                    <span>{kbLookupLoading ? 'Looking up...' : 'Lookup structured KB'}</span>
                  </button>
                  {kbLookupError && (
                    <div className="inline-warning compact">
                      <AlertCircle size={13} />
                      <span>{kbLookupError}</span>
                    </div>
                  )}
                  {kbLookup?.status === 'ok' && (
                    <div className="structured-kb-result">
                      <strong>{kbLookup.result.table_type}</strong>
                      <span>{kbLookup.result.text}</span>
                      <small>Source: {kbLookup.result.source}</small>
                      <pre className="query-preview">{JSON.stringify(kbLookup.result.data, null, 2)}</pre>
                    </div>
                  )}
                  {kbLookup?.status === 'not_found' && (
                    <div className="inline-warning compact">
                      <AlertCircle size={13} />
                      <span>No structured KB row matched the selected filters.</span>
                    </div>
                  )}
                  {kbLookup?.status === 'degraded' && (
                    <div className="inline-warning compact">
                      <AlertCircle size={13} />
                      <span>Structured KB lookup is unavailable: {kbLookup.reason}</span>
                    </div>
                  )}
                </div>

                <button 
                  className="btn-action-primary w-full mt-4" 
                  onClick={generateQuery}
                  disabled={!queryParts.some(p => p.value) || isExecuting}
                >
                  {isExecuting ? <Loader2 size={16} className="spinner" /> : <Send size={16} />}
                  <span>{isExecuting ? 'Running query' : 'Execute query'}</span>
                </button>
              </div>
            </div>
          </aside>
        </div>

        <section className="builder-result-panel">
          <div className="builder-result-header">
            <div>
              <h2>Result</h2>
            </div>
            <button className="btn-outline-sm" onClick={openResultInChat} disabled={!result?.query}>
              <ExternalLink size={13} />
              <span>Open in chat</span>
            </button>
          </div>

          <AgentActionLog
            isOpen={showActivity}
            onToggle={() => setShowActivity(!showActivity)}
            actions={resultActions}
          />

          <div className={`builder-result-body ${!result?.content && !result?.error ? 'empty' : ''}`}>
            {result?.error ? (
              <div className="inline-warning">
                <AlertCircle size={13} />
                <span>{result.error}</span>
              </div>
            ) : result?.content ? (
              <>
                <MarkdownContent content={result.content} />
                <SourcesDisplay sources={result.sources || []} />
                <EvidencePanel {...resultEvidence} />
              </>
            ) : (
              <p>No result yet. Set context, add at least one parameter, then execute.</p>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
