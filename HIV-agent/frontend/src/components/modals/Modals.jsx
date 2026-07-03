import React, { useState, useEffect, useRef, useMemo } from 'react';

export function FeedbackModal({ isOpen, onClose, onSubmit }) {
  const [reason, setReason] = useState('inaccurate');
  const [note, setNote] = useState('');
  const [correction, setCorrection] = useState('');

  if (!isOpen) return null;

  return (
    <div className="modal-overlay">
      <div className="modal">
        <div className="modal-header">
          <h3>Feedback</h3>
          <button className="btn btn-icon" onClick={onClose}>×</button>
        </div>
        <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <p>Help us improve by providing more details.</p>

          <label>
            <strong>Reason</strong>
            <select value={reason} onChange={e => setReason(e.target.value)} style={{ width: '100%', marginTop: '0.5rem', padding: '0.5rem' }}>
              <option value="inaccurate">Inaccurate</option>
              <option value="outdated">Outdated</option>
              <option value="incomplete">Incomplete</option>
              <option value="other">Other</option>
            </select>
          </label>

          <label>
            <strong>Note (Optional)</strong>
            <textarea
              value={note}
              onChange={e => setNote(e.target.value)}
              placeholder="What went wrong?"
              style={{ width: '100%', marginTop: '0.5rem', padding: '0.5rem', minHeight: '60px' }}
            />
          </label>

          <label>
            <strong>Suggested Correction (Optional)</strong>
            <textarea
              value={correction}
              onChange={e => setCorrection(e.target.value)}
              placeholder="What is the correct answer?"
              style={{ width: '100%', marginTop: '0.5rem', padding: '0.5rem', minHeight: '60px' }}
            />
          </label>

          <div style={{ display: 'flex', gap: '1rem', justifyContent: 'flex-end', marginTop: '1rem' }}>
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" onClick={() => onSubmit(reason, note, correction)}>Submit Feedback</button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function SearchModal({ isOpen, onClose, conversations, currentMessages, onJump }) {
  const [query, setQuery] = useState('');
  const [searchAll, setSearchAll] = useState(true);
  const inputRef = useRef(null);

  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isOpen]);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    if (isOpen) {
      window.addEventListener('keydown', handleKeyDown);
      return () => window.removeEventListener('keydown', handleKeyDown);
    }
  }, [isOpen, onClose]);

  const results = useMemo(() => {
    if (!query.trim()) return [];

    const allMessages = searchAll
      ? conversations.flatMap((conv, ci) =>
        conv.messages.map((m, mi) => ({ ...m, convId: conv.id, convIndex: ci, msgIndex: mi }))
      )
      : currentMessages.map((m, i) => ({ ...m, convId: 'current', convIndex: 0, msgIndex: i }));

    return allMessages
      .filter(m => m.content.toLowerCase().includes(query.toLowerCase()))
      .slice(0, 20);
  }, [query, searchAll, conversations, currentMessages]);

  if (!isOpen) return null;

  return (
    <div className="search-modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="search-title">
      <div className="search-modal search-modal-wide" onClick={e => e.stopPropagation()}>
        <div className="search-header">
          <input
            ref={inputRef}
            type="text"
            id="search-input"
            className="search-input"
            placeholder="Search in conversation..."
            value={query}
            onChange={e => setQuery(e.target.value)}
            aria-label="Search query"
          />
          <label className="search-all-checkbox">
            <input
              type="checkbox"
              checked={searchAll}
              onChange={(e) => setSearchAll(e.target.checked)}
            />
            All conversations
          </label>
          <button className="search-close" onClick={onClose} aria-label="Close search">×</button>
        </div>
        <div className="search-results" role="listbox" aria-label="Search results">
          {results.length === 0 && query && (
            <div className="search-no-results">No results found</div>
          )}
          {results.map((result, i) => (
            <div
              key={i}
              className="search-result-item"
              role="option"
              onClick={() => { onJump(result.convId, result.msgIndex); onClose(); }}
            >
              <span className={`result-role ${result.role}`}>
                {searchAll && result.convId !== 'current' ? `Conversation ${result.convIndex + 1}` : result.role}
              </span>
              <span className="result-preview">{result.content.substring(0, 150)}...</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function ShortcutsModal({ isOpen, onClose }) {
  const shortcuts = [
    { key: 'Ctrl + F', action: 'Search in conversation' },
    { key: 'Ctrl + N', action: 'New conversation' },
    { key: 'Ctrl + K', action: 'Command palette' },
    { key: 'Ctrl + P', action: 'Toggle settings panel' },
    { key: 'Ctrl + /', action: 'Show keyboard shortcuts' },
    { key: '↑ / ↓', action: 'Navigate messages' },
    { key: 'Enter', action: 'Send message' },
    { key: 'Shift + Enter', action: 'New line in input' },
    { key: 'Escape', action: 'Close modal/panel' },
  ];

  if (!isOpen) return null;

  return (
    <div className="search-modal-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="shortcuts-modal" onClick={e => e.stopPropagation()}>
        <div className="shortcuts-header">
          <h3 id="shortcuts-title">Keyboard Shortcuts</h3>
          <button className="search-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="shortcuts-list" role="list">
          {shortcuts.map((s, i) => (
            <div key={i} className="shortcut-item" role="listitem">
              <kbd>{s.key}</kbd>
              <span>{s.action}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function CommandPalette({ isOpen, onClose, onNavigate, onNewConversation, onSearch, onSettings, onShortcuts }) {
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef(null);

  const commands = [
    { id: 'new', label: 'New conversation', icon: 'N', action: () => { onNewConversation(); onClose(); } },
    { id: 'search', label: 'Search', icon: 'F', action: () => { onSearch(); onClose(); } },
    { id: 'query-builder', label: 'Query builder', icon: 'Q', action: () => { onNavigate('/builder'); onClose(); } },
    { id: 'ddx', label: 'Differential diagnosis', icon: 'D', action: () => { onNavigate('/ddx'); onClose(); } },
    { id: 'pathways', label: 'Clinical pathways', icon: 'P', action: () => { onNavigate('/pathways'); onClose(); } },
    { id: 'guidelines', label: 'Guidelines', icon: 'G', action: () => { onNavigate('/guidelines'); onClose(); } },
    { id: 'knowledge-base', label: 'Knowledge base', icon: 'K', action: () => { onNavigate('/kb'); onClose(); } },
    { id: 'settings', label: 'Settings', icon: 'S', action: () => { onSettings(); onClose(); } },
    { id: 'shortcuts', label: 'Keyboard shortcuts', icon: '/', action: () => { onShortcuts(); onClose(); } },
  ];

  const filteredCommands = query
    ? commands.filter(c => c.label.toLowerCase().includes(query.toLowerCase()))
    : commands;

  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isOpen]);

  const handleKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex(prev => (prev + 1) % filteredCommands.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex(prev => (prev - 1 + filteredCommands.length) % filteredCommands.length);
    } else if (e.key === 'Enter' && filteredCommands[selectedIndex]) {
      e.preventDefault();
      filteredCommands[selectedIndex].action();
    } else if (e.key === 'Escape') {
      onClose();
    }
  };

  if (!isOpen) return null;

  return (
    <div className="command-palette-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="command-palette-title">
      <div className="command-palette" onClick={e => e.stopPropagation()}>
        <input
          ref={inputRef}
          type="text"
          id="command-palette-input"
          className="command-palette-input"
          placeholder="Type a command..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          aria-label="Command search"
        />
        <div className="command-palette-results" role="listbox" aria-label="Commands">
          {filteredCommands.map((cmd, i) => (
            <button
              key={cmd.id}
              className={`command-item ${i === selectedIndex ? 'selected' : ''}`}
              onClick={() => cmd.action()}
            >
              <span className="command-icon">{cmd.icon}</span>
              {cmd.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function readinessLabel(health) {
  if (!health) return 'Unknown';
  if (health.status === 'ok') return 'Operational';
  if (health.status === 'degraded') return 'Degraded';
  return 'Unavailable';
}

export function OperatorCockpitModal({
  isOpen,
  onClose,
  health,
  diseases = [],
  patientContext,
  userRole,
  onNavigate,
  onNewConversation,
  onOpenSettings
}) {
  const indexedDiseases = diseases.filter(disease => disease.status === 'indexed');
  const blockedDiseases = diseases.filter(disease => disease.status !== 'indexed');
  const contextFields = [
    patientContext?.active_conditions?.length ? `${patientContext.active_conditions.length} active condition(s)` : null,
    patientContext?.medications?.length ? 'Medication context present' : null,
    Object.keys(patientContext?.clinical_params || {}).length ? 'Clinical parameters present' : null
  ].filter(Boolean);

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="operator-cockpit-title">
      <div className="modal operator-cockpit-modal" onClick={event => event.stopPropagation()}>
        <div className="modal-header">
          <div>
            <h3 id="operator-cockpit-title" className="modal-title">Operator cockpit</h3>
            <p className="operator-subtitle">Agent state, retrieval coverage, and next actions</p>
          </div>
          <button className="btn btn-icon" onClick={onClose} aria-label="Close operator cockpit">×</button>
        </div>

        <div className="modal-body operator-cockpit-body">
          <section className="operator-grid">
            <article className={`operator-card status-${health?.status || 'unknown'}`}>
              <span className="operator-kicker">Runtime</span>
              <strong>{readinessLabel(health)}</strong>
              <p>{health ? `Mode: ${health.mode}` : 'No health response loaded yet.'}</p>
            </article>
            <article className="operator-card">
              <span className="operator-kicker">Coverage</span>
              <strong>{indexedDiseases.length}/{diseases.length || 0}</strong>
              <p>{indexedDiseases.length ? `${indexedDiseases.map(d => d.display_name).join(', ')} indexed` : 'No indexed guideline sources reported.'}</p>
            </article>
            <article className="operator-card">
              <span className="operator-kicker">Role</span>
              <strong>{String(userRole || 'clinician').toLowerCase()}</strong>
              <p>{String(userRole).toUpperCase() === 'ADMIN' ? 'Audit tools are available.' : 'Audit tools require admin role.'}</p>
            </article>
          </section>

          <section className="operator-section">
            <h4>Component state</h4>
            <div className="operator-component-list">
              {Object.entries(health?.components || {}).map(([key, value]) => (
                <div key={key} className="operator-component-row">
                  <span>{key.replaceAll('_', ' ')}</span>
                  <strong className={value === 'ok' || value === 'ready' ? 'ok' : 'warn'}>{value}</strong>
                </div>
              ))}
              {!health?.components && <div className="operator-empty">Health has not loaded.</div>}
            </div>
          </section>

          <section className="operator-section">
            <h4>Patient context</h4>
            <div className="operator-pills">
              {(contextFields.length ? contextFields : ['No active patient context']).map(item => (
                <span key={item} className="operator-pill">{item}</span>
              ))}
            </div>
          </section>

          {blockedDiseases.length > 0 && (
            <section className="operator-section">
              <h4>Blocked retrieval sources</h4>
              <div className="operator-pills">
                {blockedDiseases.map(disease => (
                  <span key={disease.id} className="operator-pill warning">{disease.display_name}</span>
                ))}
              </div>
            </section>
          )}
        </div>

        <div className="modal-footer operator-actions">
          <button className="btn btn-secondary" onClick={() => { onNavigate('/kb'); onClose(); }}>Review sources</button>
          <button className="btn btn-secondary" onClick={() => { onNavigate('/guidelines'); onClose(); }}>Browse sources</button>
          <button className="btn btn-secondary" onClick={() => { onOpenSettings(); onClose(); }}>Settings</button>
          <button className="btn btn-primary" onClick={() => { onNewConversation(); onClose(); }}>New session</button>
        </div>
      </div>
    </div>
  );
}
