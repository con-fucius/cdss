import React, { useRef, useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Send, 
  Plus, 
  Trash2, 
  Settings, 
  Search, 
  Keyboard, 
  Download,
  AlertCircle,
  CheckCircle2,
  Stethoscope,
  Activity,
  History
} from 'lucide-react';
import { EvidencePanel } from '../chat/EvidencePanel';
import { MarkdownContent, SourcesDisplay } from '../chat/MarkdownContent';
import { MessageActions, TypingIndicator, StreamingCursor, LoadingSkeleton } from '../chat/MessageComponents';
import { SmartSuggestions, AutoSuggestOnFocus, HITLComponent } from '../chat/Suggestions';
import { AgentActionLog } from '../common/AgentActionLog';
import { useToast } from '../../context/ToastContext';
import { sentenceLabel } from '../../lib/format';

export function QuickChat({ 
  onNavigate: _onNavigate, 
  settings, 
  patientContext: _patientContext, 
  sessionId: _sessionId, 
  patientRefHash,
  diseases,
  chatProps 
}) {
  const {
    messages,
    input, setInput,
    isLoading,
    isInitialized,
    agentActions,
    sessionStatus,
    isOfflineMode,
    health,
    hitl,
    handleSend,
    submitFeedback,
    pinnedMessages, setPinnedMessages,
    feedbackGiven, setFeedbackGiven,
    reactions, setReactions
  } = chatProps;

  const [autoScroll] = useState(true);
  const [showActionLog, setShowActionLog] = useState(false);
  const [inputFocused, setInputFocused] = useState(false);
  const [showAutoSuggest, setShowAutoSuggest] = useState(false);
  const [sampleFilter, setSampleFilter] = useState('general');
  
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const toast = useToast();

  // Auto-expand textarea
  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
      inputRef.current.style.height = `${Math.min(inputRef.current.scrollHeight, 200)}px`;
    }
  }, [input]);

  useEffect(() => {
    if (autoScroll && settings.autoScroll !== false) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, autoScroll, settings.autoScroll]);

  const handleInputFocus = () => {
    setInputFocused(true);
    if (!input.trim()) setShowAutoSuggest(true);
  };

  const handleInputBlur = () => {
    setTimeout(() => setShowAutoSuggest(false), 200);
    setInputFocused(false);
  };

  const handleFeedback = async (index, type) => {
    const message = messages[index];
    setReactions(prev => ({ ...prev, [index]: type }));
    setFeedbackGiven(prev => ({ ...prev, [index]: true }));
    try {
      await submitFeedback({
        message,
        feedbackType: type || 'cleared',
        note: type === 'not_helpful' ? 'Marked not helpful in chat UI' : ''
      });
      toast.addToast('Feedback recorded', 'success');
    } catch (error) {
      toast.addToast(error.message, 'error', 5000);
    }
  };

  const handlePin = (index) => {
    const msgId = messages[index].id;
    if (pinnedMessages.includes(msgId)) {
      setPinnedMessages(pinnedMessages.filter(id => id !== msgId));
    } else {
      setPinnedMessages([...pinnedMessages, msgId]);
      toast.addToast('Pinned to context', 'info');
    }
  };

  const handleHitlResponse = (response) => {
    const prompt = `${hitl?.text || 'Clarification requested'}\nResponse: ${response}`;
    handleSend(prompt);
  };

  const handleCiteClick = (messageId, citationNumber) => {
    const source = document.getElementById(`source-${messageId}-${citationNumber}`);
    if (!source) return;
    source.scrollIntoView({ behavior: 'smooth', block: 'center' });
    source.classList.add('source-item-highlight');
    window.setTimeout(() => source.classList.remove('source-item-highlight'), 1400);
  };

  const pinnedMsgs = messages.filter(m => pinnedMessages.includes(m.id));
  const selectedDisease = diseases.find(disease => disease.id === sampleFilter);
  const diseaseScope = selectedDisease
    ? ` for ${sentenceLabel(selectedDisease.display_name || selectedDisease.id)}`
    : ' across indexed Kenya clinical guidelines';
  const scopedQuery = (question) => `${question}${diseaseScope}.`;
  const coverageWarning = (() => {
    if (!diseases?.length) {
      return health?.components?.tables === 'partial'
        ? 'Coverage partial: some configured guideline sources are not indexed yet.'
        : '';
    }
    const indexed = diseases.filter(disease => disease.status === 'indexed');
    if (indexed.length === diseases.length) return '';
    if (indexed.length === 0) return 'Coverage limited: no configured guideline source is query-ready.';
    const indexedNames = indexed.map(disease => sentenceLabel(disease.display_name || disease.id)).join(', ');
    const legacyHiv = diseases.some(disease => disease.id === 'hiv' && disease.source_mode === 'legacy_documents');
    const legacyNote = legacyHiv ? ' HIV is available through the legacy document table.' : '';
    return `Coverage partial: ${indexedNames} ${indexed.length === 1 ? 'is' : 'are'} query-ready.${legacyNote} Other configured diseases are not query-ready.`;
  })();

  return (
    <div className="chat-page-container">
      {isOfflineMode && (
        <div className="offline-banner">
          <AlertCircle size={14} />
          <span>Retrieval-only mode: model unavailable; answers are direct extracts from indexed guidelines.</span>
        </div>
      )}
      {coverageWarning && (
        <div className="offline-banner">
          <AlertCircle size={14} />
          <span>{coverageWarning}</span>
        </div>
      )}

      <AnimatePresence>
        {pinnedMsgs.length > 0 && (
          <motion.div 
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 280, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            className="pinned-sidebar"
          >
            <div className="pinned-header">
              <History size={14} />
              <span>Pinned Context</span>
            </div>
            <div className="pinned-list">
              {pinnedMsgs.map(msg => (
                <div key={msg.id} className="pinned-item">
                  <MarkdownContent content={msg.content.substring(0, 80) + '...'} />
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="chat-main">
        <div className="chat-scroll-area">
          {!isInitialized && <LoadingSkeleton />}
          
          <AnimatePresence>
            {messages.length === 0 && isInitialized && (
              <motion.div 
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="welcome-container"
              >
                <div className="welcome-header">
                  <h1>Clinical decision support</h1>
                  <p>Guideline-grounded support based on indexed Kenya clinical sources</p>
                </div>
                
                <div className="guideline-selector">
                  <button 
                    className={`chip ${sampleFilter === 'general' ? 'active' : ''}`}
                    onClick={() => setSampleFilter('general')}
                  >
                    All guidelines
                  </button>
                  {diseases.map(d => (
                    <button 
                      key={d.id} 
                      className={`chip ${sampleFilter === d.id ? 'active' : ''}`}
                      onClick={() => setSampleFilter(d.id)}
                      disabled={d.status !== 'indexed'}
                      title={d.status === 'indexed' ? d.display_name : `${d.display_name} is not indexed yet`}
                    >
                      {sentenceLabel(d.display_name)}
                    </button>
                  ))}
                </div>

                <div className="quick-questions-grid">
                  <button className="question-card" onClick={() => handleSend(scopedQuery("What are the primary diagnostic indicators"))}>
                    <div className="card-icon"><Stethoscope size={20} /></div>
                    <div className="card-text">
                      <span className="card-title">Diagnostics</span>
                      <span className="card-desc">Identify clinical indicators</span>
                    </div>
                  </button>
                  <button className="question-card" onClick={() => handleSend(scopedQuery("What are the current recommended first-line treatment protocols"))}>
                    <div className="card-icon"><Activity size={20} /></div>
                    <div className="card-text">
                      <span className="card-title">Treatments</span>
                      <span className="card-desc">Review medical protocols</span>
                    </div>
                  </button>
                  <button className="question-card" onClick={() => handleSend(scopedQuery("What are the follow-up and monitoring requirements"))}>
                    <div className="card-icon"><History size={20} /></div>
                    <div className="card-text">
                      <span className="card-title">Monitoring</span>
                      <span className="card-desc">Plan patient follow-up</span>
                    </div>
                  </button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <div className="messages-list">
            {messages.map((msg, i) => (
              <motion.div 
                key={msg.id || i} 
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className={`message-wrapper ${msg.role}`}
                id={`message-${i}`}
              >
                <div className={`message-bubble ${msg.role === 'assistant' ? 'elevated' : ''}`}>
                  {msg.role === 'assistant' && (
                    <div className="message-header">
                      <span className="message-sender">Kini</span>
                    </div>
                  )}
                  <MarkdownContent
                    content={msg.content}
                    onCiteClick={(citationNumber) => handleCiteClick(msg.id || i, citationNumber)}
                  />

                  {msg.sources?.some(src => src.low_confidence) && (
                    <div className="offline-banner">
                      <AlertCircle size={14} />
                      <span>Retrieval confidence was low for this answer. Verify against the cited source.</span>
                    </div>
                  )}
                  
                  {msg.sources && msg.sources.length > 0 && (
                    <SourcesDisplay
                      sources={msg.sources}
                      sourceBaseId={`source-${msg.id || i}`}
                      onCiteClick={(sourceIndex) => handleCiteClick(msg.id || i, sourceIndex + 1)}
                    />
                  )}

                  <EvidencePanel concepts={msg.concepts || []} triples={msg.triples || []} interactions={msg.interactions || []} drugInteractionStatus={msg.drugInteractionStatus} reasoning={msg.reasoning || []} sessionId={_sessionId} patientRefHash={patientRefHash} />

                  <div className="message-footer">
                    {settings.showTimestamps && (
                      <span className="timestamp">{new Date(msg.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</span>
                    )}
                    <MessageActions 
                      message={msg}
                      onRegenerate={() => messages[i - 1]?.content && handleSend(messages[i - 1].content, true)}
                      onFeedback={(type) => handleFeedback(i, type)}
                      onPin={() => handlePin(i)}
                      onRetry={() => messages[i - 1]?.content && handleSend(messages[i - 1].content, true)}
                      reaction={reactions[i]}
                      isPinned={pinnedMessages.includes(msg.id)}
                      isError={msg.isError}
                      feedbackGiven={feedbackGiven[i]}
                    />
                  </div>
                </div>
                {isLoading && i === messages.length - 1 && msg.role === 'assistant' && (
                  <StreamingCursor isActive={settings.showCursor} />
                )}
              </motion.div>
            ))}
          </div>
          <div ref={messagesEndRef} />
        </div>

        <div className="input-area-container">
          <AgentActionLog 
            isOpen={showActionLog} 
            onToggle={() => setShowActionLog(!showActionLog)} 
            actions={agentActions} 
          />

          <HITLComponent hitl={hitl} onRespond={handleHitlResponse} />
          
          <div className="input-wrapper">
            <AnimatePresence>
              {showAutoSuggest && inputFocused && !input.trim() && (
                <motion.div 
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 10 }}
                  className="auto-suggest-wrapper auto-suggest-fixed"
                >
                  <AutoSuggestOnFocus onSelect={(q) => { setInput(q); setShowAutoSuggest(false); }} />
                </motion.div>
              )}
            </AnimatePresence>
            
            <div className="input-composite">
              <div className="textarea-container">
                <textarea
                  ref={inputRef}
                  className="main-chat-input"
                  placeholder="Ask a clinical question..."
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onFocus={handleInputFocus}
                  onBlur={handleInputBlur}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      handleSend(input);
                    }
                  }}
                  disabled={isLoading || !isInitialized || sessionStatus === 'disconnected'}
                  rows={1}
                  aria-label="Clinical query input"
                />
                <button 
                  className={`send-btn ${input.trim() && !isLoading ? 'ready' : ''}`}
                  onClick={() => handleSend(input)}
                  disabled={!input.trim() || isLoading || !isInitialized || sessionStatus === 'disconnected'}
                  aria-label="Send message"
                >
                  {isLoading ? (
                    <div className="loading-dots">
                      <span></span><span></span><span></span>
                    </div>
                  ) : (
                    <Send size={18} strokeWidth={2.5} />
                  )}
                </button>
              </div>
              <div className="input-meta-bar">
                <div className="status-indicator">
                  {sessionStatus === 'disconnected' ? (
                    <span className="status-text error"><AlertCircle size={10} /> Disconnected</span>
                  ) : (
                    <span className="status-text success"><CheckCircle2 size={10} /> Retrieval ready</span>
                  )}
                </div>
                <div className="input-hints">
                  <span><b>Shift + Enter</b> for new line</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
