// DEPRECATED: Phase 3.2 autocomplete is implemented in PatientContextPanel.jsx.
import React, { useEffect, useState } from 'react';
import { request } from '../../lib/api';

export function TerminologyAutocomplete({ query, enabled, onSelect }) {
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!enabled) {
      setSuggestions([]);
      setLoading(false);
      return;
    }

    const term = String(query || '').trim();
    if (term.length < 2) {
      setSuggestions([]);
      setLoading(false);
      return;
    }

    let active = true;
    setLoading(true);
    const timeout = window.setTimeout(async () => {
      try {
        const data = await request('/terminology/autocomplete', {
          method: 'POST',
          body: JSON.stringify({ query: term, top_k: 8 }),
        });
        if (!active) return;
        setSuggestions(Array.isArray(data.results) ? data.results : []);
      } catch (_error) {
        if (active) setSuggestions([]);
      } finally {
        if (active) setLoading(false);
      }
    }, 220);

    return () => {
      active = false;
      window.clearTimeout(timeout);
    };
  }, [enabled, query]);

  if (!enabled || (!loading && suggestions.length === 0)) return null;

  return (
    <div className="terminology-autocomplete" role="listbox" aria-label="Terminology suggestions">
      {loading && <div className="terminology-autocomplete-empty">Searching terminology...</div>}
      {suggestions.map((item, index) => (
        <button
          key={`${item.cui}-${index}`}
          className="terminology-autocomplete-item"
          role="option"
          onClick={() => onSelect(item.preferred_name || item.cui)}
        >
          <span>{item.preferred_name || item.cui}</span>
          {item.cui && <small>{item.cui}</small>}
        </button>
      ))}
    </div>
  );
}
