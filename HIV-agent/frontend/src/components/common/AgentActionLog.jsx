import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { CheckCircle2, Loader2, ChevronDown, ChevronUp } from 'lucide-react';

export function AgentActionLog({ isOpen, onToggle, actions = [] }) {
  return (
    <div className="agent-action-log-container">
      <button
        className={`action-log-toggle ${isOpen ? 'open' : ''}`}
        onClick={onToggle}
        aria-expanded={isOpen}
      >
        <div className="toggle-left">
          <span>Agent activity</span>
          {actions.length > 0 && <span className="action-count">{actions.length}</span>}
        </div>
        {isOpen ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
      </button>

      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="action-log-content-modern"
          >
            {actions.length === 0 ? (
              <div className="action-item-empty">No activity recorded yet.</div>
            ) : (
              <div className="action-items-list">
                {actions.map((action, idx) => (
                  <div key={idx} className={`action-item-modern ${action.done ? 'done' : 'running'}`}>
                    <div className="action-status-icon">
                      {action.done ? (
                        <CheckCircle2 size={12} />
                      ) : (
                        <Loader2 size={12} className="spinner" />
                      )}
                    </div>
                    <div className="action-details">
                      <span className="action-text">{action.text}</span>
                      {action.detail && <span className="action-detail">{action.detail}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
