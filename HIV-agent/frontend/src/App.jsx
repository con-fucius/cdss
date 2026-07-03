import React, { useState, useEffect } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { AnimatePresence, motion } from 'framer-motion';
import { Search, Terminal, Settings as SettingsIcon, Info } from 'lucide-react';
import { useChat } from './hooks/useChat';

// Components
import { Sidebar } from './components/layout/Sidebar';
import { QuickChat } from './components/pages/QuickChat';
import { QueryBuilderPage } from './components/pages/QueryBuilder';
import { GuidelinesBrowserPage } from './components/pages/GuidelinesBrowser';
import { KnowledgeBasePage } from './components/pages/KnowledgeBase';
import { AuditLogPage } from './components/pages/AuditLog';
import { AdminPage } from './components/pages/AdminPage';
import DDxWorkspace from './pages/DDxWorkspace';
import PathwayExplorer from './pages/PathwayExplorer';
import { PatientContextPanel } from './components/panels/PatientContextPanel';
import { ScoringPanel } from './components/panels/ScoringPanel';
import { getStoredUserRole, request, setStoredUserRole } from './lib/api';

// Modals
import { SettingsPanel } from './components/modals/SettingsPanel';
import { SearchModal, ShortcutsModal, CommandPalette, OperatorCockpitModal } from './components/modals/Modals';

const pageVariants = {
  initial: { opacity: 0, y: 10 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -10 }
};

function loadSessionSettings() {
  try {
    const saved =
      sessionStorage.getItem('kini_settings') ||
      sessionStorage.getItem('kiniq_settings') ||
      localStorage.getItem('kini_settings') ||
      localStorage.getItem('kiniq_settings');
    if (!saved) {
      return {
        fontSize: '13',
        density: 'compact',
        showCursor: true,
        autoScroll: true,
        showTimestamps: false,
      };
    }
    if (!sessionStorage.getItem('kini_settings')) {
      sessionStorage.setItem('kini_settings', saved);
    }
    localStorage.removeItem('kini_settings');
    localStorage.removeItem('kiniq_settings');
    sessionStorage.removeItem('kiniq_settings');
    return JSON.parse(saved);
  } catch (_error) {
    return {
      fontSize: '13',
      density: 'compact',
      showCursor: true,
      autoScroll: true,
      showTimestamps: false,
    };
  }
}

function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [initialQuery, setInitialQuery] = useState(null);

  // Global Session State
  const [sessionId] = useState(() => {
    let id = sessionStorage.getItem('kini_session_id') || sessionStorage.getItem('kiniq_session_id');
    if (!id) {
      id = crypto.randomUUID();
    }
    sessionStorage.setItem('kini_session_id', id);
    sessionStorage.removeItem('kiniq_session_id');
    return id;
  });

  // Global Context State
  const [patientContext, setPatientContext] = useState({
    active_conditions: [],
    clinical_params: {},
    medications: []
  });
  const [patientRefHash, setPatientRefHash] = useState(null);

  // Global App Settings
  const [settings, setSettings] = useState(loadSessionSettings);

  // Global Data
  const [availableDiseases, setAvailableDiseases] = useState([]);
  const [userRole, setUserRole] = useState(() => getStoredUserRole() || 'CLINICIAN');

  // UI State
  const [showSettings, setShowSettings] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [showCommandPalette, setShowCommandPalette] = useState(false);
  const [showOperatorCockpit, setShowOperatorCockpit] = useState(false);

  // Initialize Chat Hook
  const chat = useChat(sessionId, initialQuery, patientContext, settings, patientRefHash);

  useEffect(() => {
    request('/health')
      .then(data => {
        if (data.role) {
          setUserRole(String(data.role).toUpperCase());
          setStoredUserRole(data.role);
        }
      })
      .catch(err => console.error('Failed to connect to health endpoint', err));

    request('/diseases')
      .then(data => setAvailableDiseases(data.diseases || []))
      .catch(err => console.error('Failed to fetch disease list', err));
  }, []);

  useEffect(() => {
    const handleGlobalShortcut = (event) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      if (key === 'k') {
        event.preventDefault();
        setShowCommandPalette(true);
      } else if (key === 'f') {
        event.preventDefault();
        setShowSearch(true);
      } else if (key === '/') {
        event.preventDefault();
        setShowShortcuts(true);
      } else if (key === 'p') {
        event.preventDefault();
        setShowSettings(true);
      } else if (key === 'n') {
        event.preventDefault();
        chat.handleNewConversation();
        navigate('/chat');
      }
    };

    window.addEventListener('keydown', handleGlobalShortcut);
    return () => window.removeEventListener('keydown', handleGlobalShortcut);
  }, [chat, navigate]);

  const handleNavigate = (path, query = null) => {
    navigate(path);
    if (query) {
      setInitialQuery(query);
      chat.handleSend(query);
      setTimeout(() => setInitialQuery(null), 100);
    }
  };

  const getPageTitle = (pathname) => {
    const titles = {
      '/chat': 'Quick chat',
      '/builder': 'Query builder',
      '/ddx': 'Differential Diagnosis',
      '/pathways': 'Clinical Pathways',
      '/guidelines': 'Guidelines',
      '/kb': 'Knowledge base',
      '/audit': 'Audit log',
      '/admin': 'Admin'
    };
    return titles[pathname] || 'Clinical decision support';
  };

  return (
    <div 
        className={`app settings-${settings.density}`}
        style={{ '--font-size': `${settings.fontSize}px` }}
      >
        <Sidebar 
          conversations={chat.conversations}
          currentConvId={chat.currentConvId}
          onSelectConversation={(id) => {
            chat.setCurrentConvId(id);
            navigate('/chat');
          }}
          userRole={userRole}
        />

        <main className="main-content">
          <header className="main-header">
             <div className="header-branding">
               <h2>{getPageTitle(location.pathname)}</h2>
               <span className="session-id">Session: {sessionId.substring(0, 8)}</span>
             </div>
             <div className="header-tools">
               <button className="tool-btn" onClick={() => setShowSearch(true)} title="Search (Ctrl+F)">
                 <Search size={18} />
               </button>
               <button className="tool-btn" onClick={() => setShowCommandPalette(true)} title="Commands (Ctrl+K)">
                 <Terminal size={18} />
               </button>
               <button className="tool-btn" onClick={() => setShowOperatorCockpit(true)} title="Operator cockpit">
                 <Info size={18} />
               </button>
               <button className="tool-btn" onClick={() => setShowSettings(true)} title="Settings">
                 <SettingsIcon size={18} />
               </button>
             </div>
          </header>

          <div className="page-scroller">
            <AnimatePresence mode="wait">
              <motion.div
                key={location.pathname}
                initial="initial"
                animate="animate"
                exit="exit"
                variants={pageVariants}
                transition={{ duration: 0.2 }}
                className="page-motion-wrapper"
              >
                <Routes location={location}>
                  <Route path="/" element={<Navigate to="/chat" replace />} />
                  <Route path="/chat" element={
                    <QuickChat 
                      onNavigate={handleNavigate}
                      settings={settings}
                      patientContext={patientContext}
                      sessionId={sessionId}
                      patientRefHash={patientRefHash}
                      diseases={availableDiseases}
                      chatProps={chat}
                    />
                  } />
                  <Route path="/builder" element={
                    <QueryBuilderPage 
                      onNavigate={handleNavigate}
                      sessionId={sessionId}
                      patientContext={patientContext}
                      setPatientContext={setPatientContext}
                      diseases={availableDiseases}
                      chatProps={chat}
                    />
                  } />
                  <Route path="/ddx" element={
                    <DDxWorkspace />
                  } />
                  <Route path="/pathways" element={
                    <PathwayExplorer patientRefHash={patientRefHash} />
                  } />
                  <Route path="/guidelines" element={
                    <GuidelinesBrowserPage 
                      onNavigate={handleNavigate}
                      diseases={availableDiseases}
                    />
                  } />
                  <Route path="/kb" element={
                    <KnowledgeBasePage 
                      onNavigate={handleNavigate}
                      diseases={availableDiseases}
                      userRole={userRole}
                    />
                  } />
                  <Route path="/audit" element={
                    <AuditLogPage 
                      onNavigate={handleNavigate}
                      availableDiseases={availableDiseases}
                      userRole={userRole}
                    />
                  } />
                  <Route path="/admin" element={
                    <AdminPage userRole={userRole} />
                  } />
                </Routes>
              </motion.div>
            </AnimatePresence>
          </div>

          <PatientContextPanel 
            context={patientContext}
            onContextChange={setPatientContext}
            diseases={availableDiseases}
            sessionId={sessionId}
            userRole={userRole}
            patientRefHash={patientRefHash}
            onPatientRefHashChange={setPatientRefHash}
          />

          <ScoringPanel
            sessionId={sessionId}
            patientRefHash={patientRefHash}
            chatMessages={chat.messages}
          />
        </main>

        <SettingsPanel 
          isOpen={showSettings}
          onClose={() => setShowSettings(false)}
          settings={settings}
          userRole={userRole}
          onRoleChange={(role) => {
            setUserRole(role);
            setStoredUserRole(role);
          }}
          onSettingsChange={(s) => {
            setSettings(s);
            sessionStorage.setItem('kini_settings', JSON.stringify(s));
            sessionStorage.removeItem('kiniq_settings');
            localStorage.removeItem('kini_settings');
            localStorage.removeItem('kiniq_settings');
          }}
        />

        <SearchModal 
          isOpen={showSearch}
          onClose={() => setShowSearch(false)}
          conversations={chat.conversations}
          currentMessages={chat.messages}
          onJump={(cid, _idx) => {
             chat.setCurrentConvId(cid);
             navigate('/chat');
          }}
        />

        <ShortcutsModal 
          isOpen={showShortcuts}
          onClose={() => setShowShortcuts(false)}
        />

        <CommandPalette 
          isOpen={showCommandPalette}
          onClose={() => setShowCommandPalette(false)}
          onNavigate={handleNavigate}
          onNewConversation={chat.handleNewConversation}
          onSearch={() => setShowSearch(true)}
          onSettings={() => setShowSettings(true)}
          onShortcuts={() => setShowShortcuts(true)}
        />

        <OperatorCockpitModal
          isOpen={showOperatorCockpit}
          onClose={() => setShowOperatorCockpit(false)}
          health={chat.health}
          diseases={availableDiseases}
          patientContext={patientContext}
          userRole={userRole}
          onNavigate={handleNavigate}
          onNewConversation={chat.handleNewConversation}
          onOpenSettings={() => setShowSettings(true)}
        />
      </div>
  );
}

export default App;
