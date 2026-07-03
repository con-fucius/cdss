import React, { useState } from 'react';

export function MessageActions({ message, onRegenerate, onFeedback, onPin, onRetry, reaction, isPinned, isError, feedbackGiven }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="message-actions" role="group" aria-label="Message actions">
      <button className="action-btn" onClick={handleCopy} title="Copy" aria-label="Copy message">
        {copied ? 'Copied' : 'Copy'}
      </button>
      {message.role === 'assistant' && (
        <>
          {isError && (
            <button className="action-btn" onClick={() => onRetry()} title="Retry" aria-label="Retry message">
              Retry
            </button>
          )}
          {!isError && (
            <button className="action-btn" onClick={() => onRegenerate()} title="Regenerate" aria-label="Regenerate response">
              Regenerate
            </button>
          )}
          <button
            className={`action-btn ${isPinned ? 'active' : ''}`}
            onClick={() => onPin()}
            title={isPinned ? 'Unpin' : 'Pin'}
            aria-label={isPinned ? 'Unpin message' : 'Pin message'}
          >
            {isPinned ? 'Pinned' : 'Pin'}
          </button>
          {feedbackGiven ? (
            <span className="feedback-thanks" aria-label="Feedback given">✓ Thanks!</span>
          ) : (
            <>
              <button
                className={`action-btn ${reaction === 'helpful' ? 'active' : ''}`}
                onClick={() => onFeedback(reaction === 'helpful' ? null : 'helpful')}
                title="Helpful"
                aria-label="Mark as helpful"
              >
                Helpful
              </button>
              <button
                className={`action-btn ${reaction === 'not_helpful' ? 'active' : ''}`}
                onClick={() => onFeedback(reaction === 'not_helpful' ? null : 'not_helpful')}
                title="Not helpful"
                aria-label="Mark as not helpful"
              >
                Not helpful
              </button>
            </>
          )}
        </>
      )}
    </div>
  );
}

export function StreamingCursor({ isActive }) {
  if (!isActive) return null;
  return <span className="streaming-cursor" aria-label="Kini is typing">▊</span>;
}

export function TypingIndicator() {
  return (
    <div className="typing-indicator" role="status" aria-live="polite">
      <span className="typing-dot"></span>
      <span className="typing-dot"></span>
      <span className="typing-dot"></span>
    </div>
  );
}

export function LoadingSkeleton() {
  return (
    <div className="loading-skeleton" role="status" aria-live="polite">
      <div className="skeleton-line skeleton-short"></div>
      <div className="skeleton-line skeleton-medium"></div>
      <div className="skeleton-line skeleton-long"></div>
      <div className="skeleton-line skeleton-medium"></div>
      <div className="skeleton-line skeleton-short"></div>
    </div>
  );
}
