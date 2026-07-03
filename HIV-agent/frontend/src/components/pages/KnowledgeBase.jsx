import React, { useCallback, useDeferredValue, useEffect, useMemo, useState, useTransition } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Database,
  Filter,
  Globe,
  Layers,
  MessageSquare,
  RefreshCw,
  Search,
  ShieldCheck,
  Terminal,
  XCircle
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { request } from '../../lib/api';
import { sentenceLabel } from '../../lib/format';

const STATUS_OPTIONS = [
  { value: 'all', label: 'All sources' },
  { value: 'indexed', label: 'Indexed' },
  { value: 'not_indexed', label: 'Needs indexing' },
  { value: 'warning', label: 'Warnings' }
];

const SORT_OPTIONS = [
  { value: 'readiness_desc', label: 'Readiness: high to low' },
  { value: 'readiness_asc', label: 'Readiness: low to high' },
  { value: 'name_asc', label: 'Name: A to Z' },
  { value: 'status', label: 'Status priority' }
];

function normalizeDisease(disease) {
  return {
    id: disease.id,
    display_name: sentenceLabel(disease.display_name || disease.id || 'Unknown guideline'),
    guideline: disease.guideline || 'Clinical guideline source',
    status: disease.status || 'not_indexed',
    source_mode: disease.source_mode || '',
    table_name: disease.table_name || '',
    chunk_count: disease.chunk_count ?? null,
    pageindex_rows: disease.pageindex_rows ?? 0,
    pageindex_status: disease.pageindex_status || 'missing',
    graph_nodes: disease.graph_nodes ?? 0,
    graph_edges: disease.graph_edges ?? 0,
    graph_status: disease.graph_status || 'missing',
    guideline_warning: disease.guideline_warning || ''
  };
}

function getReadinessScore(disease) {
  let score = 15;
  if (disease.status === 'indexed') score += 45;
  if (disease.pageindex_status === 'ready') score += 20;
  if (disease.graph_status === 'ready') score += 15;
  if (!disease.guideline_warning) score += 15;
  if (disease.guideline) score += 5;
  return Math.min(score, 100);
}

function getStatusMeta(disease) {
  if (disease.status === 'indexed') {
    const legacy = disease.source_mode === 'legacy_documents';
    return {
      label: legacy ? 'Legacy indexed' : 'Indexed',
      tone: legacy ? 'warning' : 'indexed',
      icon: <CheckCircle2 size={13} />,
      summary: legacy
        ? 'Queryable through the legacy HIV document table; full disease table reindex is still pending.'
        : 'Ready for retrieval and grounded clinical answers.'
    };
  }

  return {
    label: 'Needs indexing',
    tone: 'not_indexed',
    icon: <XCircle size={13} />,
    summary: 'Configured source exists, but the vector index is not active yet.'
  };
}

function formatTime(value) {
  if (!value) return 'Not checked';
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  }).format(value);
}

function getHealthValue(health, key) {
  if (!health?.components) return 'Unknown';
  return health.components[key] || 'Unknown';
}

function getModelHealthValue(health) {
  if (!health?.components) return 'Unknown';
  const status = health.components.llm_api || 'Unknown';
  const provider = health.llm_provider ? sentenceLabel(health.llm_provider) : '';
  const model = health.llm_model || '';
  return [status, provider, model].filter(Boolean).join(' · ');
}

export function KnowledgeBasePage({ onNavigate, diseases, userRole }) {
  const toast = useToast();
  const upstreamDiseases = useMemo(() => diseases.map(normalizeDisease), [diseases]);
  const [refreshedDiseases, setRefreshedDiseases] = useState(null);
  const [health, setHealth] = useState(null);
  const [pageindexStats, setPageindexStats] = useState(null);
  const [error, setError] = useState('');
  const [lastChecked, setLastChecked] = useState(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [minimumReadiness, setMinimumReadiness] = useState(0);
  const [sortMode, setSortMode] = useState('readiness_desc');
  const [selectedDiseaseId, setSelectedDiseaseId] = useState('');
  const [isPending, startTransition] = useTransition();
  const deferredQuery = useDeferredValue(searchQuery);
  const localDiseases = refreshedDiseases || upstreamDiseases;

  const refreshStatus = useCallback(async ({ silent = false } = {}) => {
    setIsRefreshing(true);
    setError('');

    try {
      const [healthData, diseaseData, pageindexData] = await Promise.all([
        request('/health'),
        request('/diseases'),
        request('/pageindex/stats').catch(() => ({ total: 0, by_disease: {}, error: 'PageIndex stats unavailable' }))
      ]);
      const nextDiseases = (diseaseData.diseases || []).map(normalizeDisease);

      setHealth(healthData);
      setPageindexStats(pageindexData || { total: 0, by_disease: {} });
      setRefreshedDiseases(nextDiseases);
      setLastChecked(new Date());
      if (!silent) toast.addToast('Knowledge base status refreshed', 'success');
    } catch (refreshError) {
      const message = refreshError instanceof Error ? refreshError.message : 'Unable to reach knowledge base server';
      setError(message);
      setLastChecked(new Date());
      if (!silent) toast.addToast(message, 'error', 5000);
    } finally {
      setIsRefreshing(false);
    }
  }, [toast]);

  useEffect(() => {
    refreshStatus({ silent: true });
    const interval = window.setInterval(() => refreshStatus({ silent: true }), 30000);
    return () => window.clearInterval(interval);
  }, [refreshStatus]);

  const indexedCount = localDiseases.filter(d => d.status === 'indexed').length;
  const warningCount = localDiseases.filter(d => d.guideline_warning).length;
  const selectedDisease = localDiseases.find(d => d.id === selectedDiseaseId) || localDiseases[0] || null;
  const hasServerSignal = Boolean(health || error || lastChecked);
  const isBootstrapping = !hasServerSignal && localDiseases.length === 0;
  const isServerDegraded = health?.status && health.status !== 'ok';
  const readinessAverage = localDiseases.length
    ? Math.round(localDiseases.reduce((sum, disease) => sum + getReadinessScore(disease), 0) / localDiseases.length)
    : 0;

  const filteredDiseases = useMemo(() => {
    const query = deferredQuery.trim().toLowerCase();

    return localDiseases
      .map(disease => ({ ...disease, readiness: getReadinessScore(disease) }))
      .filter(disease => {
        const matchesQuery = !query || [
          disease.display_name,
          disease.guideline,
          disease.status,
          disease.guideline_warning
        ].some(value => String(value || '').toLowerCase().includes(query));
        const matchesStatus =
          statusFilter === 'all' ||
          disease.status === statusFilter ||
          (statusFilter === 'warning' && Boolean(disease.guideline_warning));
        const matchesReadiness = disease.readiness >= minimumReadiness;

        return matchesQuery && matchesStatus && matchesReadiness;
      })
      .sort((a, b) => {
        if (sortMode === 'readiness_asc') return a.readiness - b.readiness;
        if (sortMode === 'name_asc') return a.display_name.localeCompare(b.display_name);
        if (sortMode === 'status') return Number(b.status === 'indexed') - Number(a.status === 'indexed');
        return b.readiness - a.readiness;
      });
  }, [deferredQuery, localDiseases, minimumReadiness, sortMode, statusFilter]);

  const handleFilterChange = (setter, value) => {
    startTransition(() => setter(value));
  };

  const handleAnalyzeGaps = (disease) => {
    if (!disease) return;

    onNavigate(
      '/chat',
      `[Knowledge base review]\nAssess retrieval readiness for ${disease.display_name}.\nGuideline: ${disease.guideline}\nIndex status: ${disease.status}\nKnown warning: ${disease.guideline_warning || 'none'}\nReturn: data gaps, clinical risk, and next validation steps.`
    );
  };

  const handleOpenGuidelines = (disease) => {
    if (!disease) return;
    onNavigate('/guidelines');
  };

  return (
    <div className="page-wrapper">
      <div className="kb-container">
        <header className="page-header-minimal kb-header-operational">
          <div className="header-info">
            <h1>Clinical knowledge base</h1>
            <p>Validated clinical data sources, vector readiness, and retrieval guardrails</p>
          </div>
          <div className="header-actions kb-header-actions">
            <div className={`kb-live-chip ${error ? 'error' : health ? 'online' : 'unknown'}`}>
              <span>{error ? 'Server unreachable' : health ? `Server ${health.status}` : 'Backend booting'}</span>
            </div>
          </div>
        </header>

        <div className="kb-stats-grid-modern">
          <div className="stat-card-premium">
            <div className="stat-content">
              <div className="stat-value">{isBootstrapping ? '--' : localDiseases.length}</div>
              <div className="stat-label">Guidelines configured</div>
            </div>
          </div>
          <div className="stat-card-premium">
            <div className="stat-content">
              <div className="stat-value">{isBootstrapping ? '--' : indexedCount}</div>
              <div className="stat-label">Active vector indices</div>
            </div>
          </div>
          <div className="stat-card-premium">
            <div className="stat-content">
              <div className="stat-value">Kenya</div>
              <div className="stat-label">Primary jurisdiction</div>
            </div>
          </div>
          <div className="stat-card-premium">
            <div className="stat-content">
              <div className="stat-value">{isBootstrapping ? '--' : `${readinessAverage}%`}</div>
              <div className="stat-label">Mean retrieval readiness</div>
            </div>
          </div>
        </div>

        <section className="kb-command-panel">
          <div className="kb-command-main">
            <div className="search-bar-inline kb-search">
              <Search size={14} className="search-icon" />
              <input
                type="text"
                placeholder="Search guideline, source, warning, status..."
                value={searchQuery}
                onChange={(event) => handleFilterChange(setSearchQuery, event.target.value)}
              />
            </div>
            <div className="kb-filter-group" aria-label="Status filter">
              {STATUS_OPTIONS.map(option => (
                <button
                  key={option.value}
                  className={`kb-filter-pill ${statusFilter === option.value ? 'active' : ''}`}
                  onClick={() => handleFilterChange(setStatusFilter, option.value)}
                >
                  <Filter size={12} />
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="kb-controls-stack">
            <div className="kb-sort-control">
              <label htmlFor="kb-sort-mode">Sort sources</label>
              <select
                id="kb-sort-mode"
                value={sortMode}
                onChange={(event) => handleFilterChange(setSortMode, event.target.value)}
              >
                {SORT_OPTIONS.map(option => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </div>
            <div className="kb-control-divider" />
            <div className="kb-readiness-control">
              <label htmlFor="readiness-threshold">Minimum readiness</label>
              <div className="kb-range-row">
                <input
                  id="readiness-threshold"
                  type="range"
                  min="0"
                  max="100"
                  step="5"
                  value={minimumReadiness}
                  onChange={(event) => handleFilterChange(setMinimumReadiness, Number(event.target.value))}
                />
                <span>{minimumReadiness}%</span>
              </div>
            </div>
          </div>
        </section>

        <section className="kb-operator-strip">
          <div className="kb-operator-item">
            <span className="operator-label">Last check</span>
            <strong>{formatTime(lastChecked)}</strong>
          </div>
          <div className="kb-operator-item">
            <span className="operator-label">LanceDB</span>
            <strong>{getHealthValue(health, 'lancedb')}</strong>
          </div>
          <div className="kb-operator-item">
            <span className="operator-label">Tables</span>
            <strong>{getHealthValue(health, 'tables')}</strong>
          </div>
          <div className="kb-operator-item">
            <span className="operator-label">PageIndex</span>
            <strong>{getHealthValue(health, 'pageindex')}</strong>
          </div>
          <div className="kb-operator-item">
            <span className="operator-label">PageIndex rows</span>
            <strong>{pageindexStats?.total ?? 0}</strong>
          </div>
          <div className="kb-operator-item">
            <span className="operator-label">Postgres</span>
            <strong>{getHealthValue(health, 'database')}</strong>
          </div>
          <div className="kb-operator-item">
            <span className="operator-label">Model reachability</span>
            <strong>{getModelHealthValue(health)}</strong>
          </div>
          <div className="kb-operator-item warning">
            <span className="operator-label">Warnings</span>
            <strong>{warningCount}</strong>
          </div>
        </section>

        {error && (
          <div className="kb-health-alert" role="status">
            <AlertTriangle size={16} />
            <div>
              <strong>Knowledge server unavailable</strong>
              <p>{error}. Cached UI state is still visible, but source freshness and index readiness need recheck.</p>
            </div>
          </div>
        )}

        {!error && isServerDegraded && (
          <div className="kb-health-alert degraded" role="status">
            <AlertTriangle size={16} />
            <div>
              <strong>Knowledge base is degraded</strong>
              <p>The backend responded, but one or more retrieval components are not healthy. Inspect LanceDB, tables, and model reachability before trusting clinical coverage.</p>
            </div>
          </div>
        )}

        <div className="kb-section-heading-row">
          <div className="kb-section-title">
            <Layers size={14} />
            <span>Clinical data indices</span>
            {isPending && <span className="kb-pending-note">Updating filters...</span>}
          </div>
          <div className="kb-section-actions">
            <span>{filteredDiseases.length} shown</span>
            <button className="btn-outline-sm" onClick={() => refreshStatus()} disabled={isRefreshing}>
              <RefreshCw size={13} className={isRefreshing ? 'spinner' : ''} />
              Refresh
            </button>
          </div>
        </div>

        <div className="kb-grid-modern kb-source-table-wrap">
          {filteredDiseases.length > 0 && (
            <table className="kb-source-table">
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Status</th>
                  <th>Readiness</th>
                  <th>Engine</th>
                  <th>Sync</th>
                  <th>Integrity</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredDiseases.map(disease => {
                  const status = getStatusMeta(disease);
                  const engineLabel = disease.source_mode === 'legacy_documents'
                    ? 'LanceDB / Legacy document table'
                    : 'LanceDB / Vector';
                  const syncLabel = disease.status === 'indexed'
                    ? `${disease.chunk_count ?? 'Unknown'} rows / PageIndex ${disease.pageindex_rows}`
                    : 'Pending';
                  const integrityLabel = disease.status !== 'indexed'
                    ? 'Blocked'
                    : disease.pageindex_status === 'ready' && disease.graph_status === 'ready'
                      ? `Graph ${disease.graph_nodes}/${disease.graph_edges} ready`
                      : disease.graph_status === 'ready'
                        ? `Graph ready / PageIndex missing`
                        : disease.pageindex_status === 'ready'
                          ? 'PageIndex ready / Graph missing'
                          : 'Graph + PageIndex missing';
                  return (
                    <tr
                      key={disease.id}
                      className={selectedDisease?.id === disease.id ? 'selected' : ''}
                      onClick={() => setSelectedDiseaseId(disease.id)}
                    >
                      <td className="source-cell">
                        <strong>{disease.display_name}</strong>
                        <span>{disease.guideline}</span>
                        {disease.table_name && <small>{disease.table_name}</small>}
                        {disease.guideline_warning && <small>{disease.guideline_warning}</small>}
                      </td>
                      <td>
                        <div className={`status-pill ${status.tone}`}>
                          {status.icon}
                          {status.label}
                        </div>
                        <span className="muted-cell">{status.summary}</span>
                      </td>
                      <td className="readiness-cell">
                        <div className="table-meter-row">
                          <div className="meter-track">
                            <div className="meter-fill" style={{ width: `${disease.readiness}%` }} />
                          </div>
                          <strong>{disease.readiness}%</strong>
                        </div>
                      </td>
                      <td>{engineLabel}</td>
                      <td>{syncLabel}</td>
                      <td>{integrityLabel}</td>
                      <td className="table-actions" onClick={event => event.stopPropagation()}>
                        <button className="btn-ghost-sm" onClick={() => handleOpenGuidelines(disease)}>
                          <BookOpen size={12} />
                          Source
                        </button>
                        <button className="btn-ghost-sm" onClick={() => handleAnalyzeGaps(disease)}>
                          <MessageSquare size={12} />
                          Review
                        </button>
                        {String(userRole).toUpperCase() === 'ADMIN' && (
                          <button className="btn-ghost-sm" onClick={() => onNavigate('/audit')}>
                            <Terminal size={12} />
                            Audit
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          {filteredDiseases.length === 0 && (
            <div className={`kb-empty-state-modern ${isBootstrapping ? 'booting' : ''}`}>
              <div className="kb-empty-copy">
                <div className="empty-visual">
                  {isBootstrapping ? <RefreshCw size={22} strokeWidth={1.8} className="spinner" /> : <Search size={22} strokeWidth={1.8} />}
                </div>
                <div>
                  <h3>{isBootstrapping ? 'Waiting for Knowledge Server' : localDiseases.length === 0 ? 'No Guidelines Confirmed' : 'No Sources Match Filters'}</h3>
                  <p>
                    {isBootstrapping
                      ? 'Frontend is ready. Refresh when the backend finishes startup to load sources, index state, and retrieval health.'
                      : localDiseases.length === 0
                      ? 'The backend did not return configured clinical sources. Treat retrieval as unavailable until this check succeeds.'
                      : 'Relax the search, status, or readiness filter to inspect more sources.'}
                  </p>
                </div>
              </div>
              <div className="kb-empty-actions">
                <button className="btn-outline-md" onClick={() => refreshStatus()} disabled={isRefreshing}>
                  <RefreshCw size={14} className={isRefreshing ? 'spinner' : ''} />
                  {isRefreshing ? 'Checking...' : 'Retry connection'}
                </button>
                <button className="btn-outline-md" onClick={() => onNavigate('/chat')}>
                  Continue to Chat
                  <ArrowRight size={14} />
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="kb-system-info kb-system-intelligence">
          <div className="kb-contract-layout">
            <div className="info-text">
              <span className="kb-contract-kicker">Operational policy</span>
              <h3>Retrieval Intelligence Contract</h3>
              <p>Kini answers from indexed clinical guidance first, exposes uncertainty when source coverage is weak, and routes evidence gaps to clinician review instead of hiding retrieval failure.</p>
            </div>
            <div className="specs-grid">
              <div className="spec-tile">
                <span className="spec-label">Engine</span>
                <span className="spec-value">Rag 2.0</span>
              </div>
              <div className="spec-tile">
                <span className="spec-label">Review</span>
                <span className="spec-value">Gap Review</span>
              </div>
              <div className="spec-tile">
                <span className="spec-label">Accuracy</span>
                <span className="spec-value">Source-Grounded</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
