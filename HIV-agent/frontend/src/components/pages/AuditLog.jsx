import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  Download,
  Filter,
  RefreshCw,
  Search
} from 'lucide-react';
import { request } from '../../lib/api';

function eventStatus(eventType) {
  if (eventType?.includes('ERROR')) return 'warning';
  return 'success';
}

function eventLabel(eventType) {
  return String(eventType || 'Audit event').replaceAll('_', ' ').toLowerCase();
}

function eventSummary(log) {
  const payload = log.log_data || {};
  if (log.event_type === 'QUERY_LOG') return payload.query_text || 'Clinical query';
  if (log.event_type === 'RESPONSE_LOG') return `Response (${payload.response_length || 0} chars)`;
  if (log.event_type === 'FEEDBACK_LOG') return `Feedback: ${log.feedback_type || 'unspecified'}`;
  return eventLabel(log.event_type);
}

export function AuditLogPage({ userRole }) {
  const [logs, setLogs] = useState([]);
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [storageBackend, setStorageBackend] = useState('unknown');
  const [backendError, setBackendError] = useState('');
  const [filters, setFilters] = useState({
    start_date: '',
    end_date: '',
    session_id: '',
    disease: '',
    feedback_type: '',
    limit: 50
  });
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [expandedLogId, setExpandedLogId] = useState(null);

  const loadLogs = useCallback(async () => {
    setIsLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      Object.entries(filters).forEach(([key, value]) => {
        if (value !== '' && value !== undefined && value !== null) params.set(key, value);
      });
      const data = await request(`/admin/audit?${params.toString()}`, {
        headers: { 'X-User-Role': userRole || 'CLINICIAN' }
      });
      setLogs(data.logs || []);
      setTotal(data.total || data.logs?.length || 0);
      setPage(data.page || 1);
      setStorageBackend(data.storage_backend || 'unknown');
      setBackendError(data.backend_error || '');
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Unable to load audit logs');
    } finally {
      setIsLoading(false);
    }
  }, [filters, userRole]);

  useEffect(() => {
    loadLogs();
  }, [loadLogs]);

  const updateFilter = (key, value) => {
    setFilters(prev => ({ ...prev, [key]: value }));
  };

  const filteredLogs = logs.filter(log => {
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    return [
      log.event_type,
      log.session_id,
      log.query_id,
      log.disease,
      log.feedback_type,
      eventSummary(log)
    ].some(value => String(value || '').toLowerCase().includes(needle));
  });

  const exportCsv = () => {
    const headers = ['timestamp', 'event_type', 'session_id', 'query_id', 'disease', 'feedback_type', 'summary'];
    const rows = filteredLogs.map(log => headers.map(header => {
      const value = header === 'summary' ? eventSummary(log) : log[header];
      return `"${String(value || '').replaceAll('"', '""')}"`;
    }).join(','));
    const blob = new Blob([[headers.join(','), ...rows].join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `cdss-audit-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="page-wrapper">
      <div className="audit-container">
        <header className="page-header-minimal">
          <div className="header-info">
            <h1>Audit log and analytics</h1>
            <p>Traceability and operational compliance tracking</p>
          </div>
          <div className="header-actions">
            <button className="btn-outline-sm" onClick={loadLogs} disabled={isLoading}>
              <RefreshCw size={14} className={isLoading ? 'spinner' : ''} />
              <span>Refresh</span>
            </button>
            <button className="btn-outline-sm" onClick={exportCsv} disabled={filteredLogs.length === 0}>
              <Download size={14} />
              <span>Export csv</span>
            </button>
          </div>
        </header>

        {String(userRole).toUpperCase() !== 'ADMIN' && (
          <div className="audit-empty-note">
            <AlertCircle size={14} />
            <span>Audit endpoint requires admin role. Current role is {String(userRole || 'clinician').toLowerCase()}.</span>
          </div>
        )}

        <div className="audit-filters">
          <div className="search-bar-inline">
            <Search size={14} />
            <input
              type="text"
              placeholder="Search by event, session, disease..."
              value={query}
              onChange={event => setQuery(event.target.value)}
            />
          </div>
          <input
            type="date"
            className="audit-filter-input"
            value={filters.start_date}
            onChange={event => updateFilter('start_date', event.target.value)}
            placeholder="Start date"
          />
          <input
            type="date"
            className="audit-filter-input"
            value={filters.end_date}
            onChange={event => updateFilter('end_date', event.target.value)}
            placeholder="End date"
          />
          <input
            type="text"
            className="audit-filter-input"
            value={filters.session_id}
            onChange={event => updateFilter('session_id', event.target.value)}
            placeholder="Session id"
          />
          <input
            type="text"
            className="audit-filter-input"
            value={filters.disease}
            onChange={event => updateFilter('disease', event.target.value)}
            placeholder="Disease"
          />
          <input
            type="text"
            className="audit-filter-input"
            value={filters.feedback_type}
            onChange={event => updateFilter('feedback_type', event.target.value)}
            placeholder="Feedback"
          />
          <select
            className="audit-filter-input"
            value={filters.limit}
            onChange={event => updateFilter('limit', event.target.value)}
          >
            <option value="25">25</option>
            <option value="50">50</option>
            <option value="100">100</option>
            <option value="250">250</option>
          </select>
          <div className="filter-group">
            <button className="btn-ghost-sm" onClick={loadLogs}>
              <Filter size={12} /> Apply
            </button>
          </div>
        </div>

        {error && (
          <div className="audit-empty-note">
            <AlertCircle size={14} />
            <span>{error}</span>
          </div>
        )}

        <div className="audit-table-wrapper">
          <table className="audit-table-modern">
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Event</th>
                <th>Summary</th>
                <th>Session</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {filteredLogs.map(log => (
                <React.Fragment key={log.id}>
                  <tr>
                    <td className="timestamp-cell">{log.timestamp}</td>
                    <td className="action-cell">{eventLabel(log.event_type)}</td>
                    <td className="action-cell">{eventSummary(log)}</td>
                    <td className="ref-cell"><code>{String(log.session_id || '').slice(0, 12)}</code></td>
                    <td>
                      <span className={`status-tag ${eventStatus(log.event_type)}`}>
                        {eventStatus(log.event_type)}
                      </span>
                    </td>
                    <td>
                      <button className="btn-ghost-sm" onClick={() => setExpandedLogId(expandedLogId === log.id ? null : log.id)}>
                        Details
                      </button>
                    </td>
                  </tr>
                  {expandedLogId === log.id && (
                    <tr>
                      <td colSpan="6" className="audit-detail-cell">
                        <pre>{JSON.stringify(log.log_data || {}, null, 2)}</pre>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
              {!isLoading && filteredLogs.length === 0 && (
                <tr>
                  <td colSpan="6" className="action-cell">No audit events match the current filter.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="audit-pagination">
          <button className="btn-ghost-sm" onClick={() => setPage(prev => Math.max(1, prev - 1))} disabled={page <= 1}>
            Previous
          </button>
          <span>Page {page} · {total} events</span>
          <button className="btn-ghost-sm" onClick={() => setPage(prev => prev + 1)} disabled={logs.length < (filters.limit || 50)}>
            Next
          </button>
        </div>

        <div className="audit-empty-note">
          <AlertCircle size={14} />
          <span>
            Audit rows are served from {storageBackend.replaceAll('_', ' ')}.
            {backendError ? ` Primary backend error: ${backendError}` : ''}
          </span>
        </div>
      </div>
    </div>
  );
}
