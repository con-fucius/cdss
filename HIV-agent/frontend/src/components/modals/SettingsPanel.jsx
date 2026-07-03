import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  X, 
  Settings, 
  Type, 
  Layout, 
  Eye, 
  Monitor, 
  History,
  Shield,
  LogOut
} from 'lucide-react';

export function SettingsPanel({
  isOpen,
  onClose,
  settings,
  userRole,
  onRoleChange,
  onSettingsChange
}) {
  const defaultSettings = {
    fontSize: '13',
    density: 'compact',
    showCursor: false,
    autoScroll: true,
    showTimestamps: false,
  };

  const updateSetting = (key, value) => {
    onSettingsChange({ ...settings, [key]: value });
  };

  const resetSettings = () => {
    onSettingsChange(defaultSettings);
    sessionStorage.setItem('kini_settings', JSON.stringify(defaultSettings));
    sessionStorage.removeItem('kiniq_settings');
    localStorage.removeItem('kini_settings');
    localStorage.removeItem('kiniq_settings');
  };

  const clearLocalSession = () => {
    sessionStorage.removeItem('kini_session_id');
    sessionStorage.removeItem('kiniq_session_id');
    [
      'kini_messages',
      'kini_reactions',
      'kini_feedback',
      'kini_feedback_given',
      'kini_pinned',
      'kini_conversations',
      'kiniq_messages',
      'kiniq_reactions',
      'kiniq_feedback',
      'kiniq_feedback_given',
      'kiniq_pinned',
      'kiniq_conversations',
      'kini_settings',
      'kini_user_role',
      'kiniq_settings'
    ].forEach(key => {
      localStorage.removeItem(key);
      sessionStorage.removeItem(key);
    });
    window.location.reload();
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="settings-overlay"
            onClick={onClose}
          />
          <motion.div 
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', damping: 25, stiffness: 200 }}
            className="settings-panel-modern"
          >
            <header className="settings-header">
              <div className="header-title">
                <Settings size={20} />
                <h2>Settings</h2>
              </div>
              <button className="close-btn" onClick={onClose}><X size={20} /></button>
            </header>

            <div className="settings-body">
              <section className="settings-section">
                <div className="section-title">
                  <Type size={16} />
                  <span>Typography and display</span>
                </div>
                <div className="setting-control">
                  <label>Font size</label>
                  <div className="font-size-stepper">
                    <button onClick={() => updateSetting('fontSize', Math.max(11, parseInt(settings.fontSize) - 1))}>-</button>
                    <span>{settings.fontSize}px</span>
                    <button onClick={() => updateSetting('fontSize', Math.min(18, parseInt(settings.fontSize) + 1))}>+</button>
                  </div>
                </div>
                <div className="setting-control">
                  <label>Interface density</label>
                  <div className="density-toggle">
                    <button 
                      className={settings.density === 'compact' ? 'active' : ''} 
                      onClick={() => updateSetting('density', 'compact')}
                    >Compact</button>
                    <button 
                      className={settings.density === 'spacious' ? 'active' : ''} 
                      onClick={() => updateSetting('density', 'spacious')}
                    >Spacious</button>
                  </div>
                </div>
              </section>

              <section className="settings-section">
                <div className="section-title">
                  <Monitor size={16} />
                  <span>Chat experience</span>
                </div>
                <div className="setting-item-toggle">
                  <div className="toggle-text">
                    <span className="label">Streaming cursor</span>
                    <span className="desc">Show a streaming indicator while text arrives</span>
                  </div>
                  <input 
                    type="checkbox" 
                    checked={settings.showCursor} 
                    onChange={e => updateSetting('showCursor', e.target.checked)}
                  />
                </div>
                <div className="setting-item-toggle">
                  <div className="toggle-text">
                    <span className="label">Auto-scroll</span>
                    <span className="desc">Follow new messages automatically</span>
                  </div>
                  <input 
                    type="checkbox" 
                    checked={settings.autoScroll} 
                    onChange={e => updateSetting('autoScroll', e.target.checked)}
                  />
                </div>
              </section>

              <section className="settings-section">
                <div className="section-title">
                  <Shield size={16} />
                  <span>Clinical compliance</span>
                </div>
                <div className="compliance-info">
                  <p>All interactions are logged for clinical audit purposes. Data is encrypted at rest.</p>
                  <div className="badge-verified">Hipaa / gdpr ready</div>
                </div>
              </section>

              <section className="settings-section">
                <div className="section-title">
                  <Shield size={16} />
                  <span>Operator context</span>
                </div>
                <div className="setting-control">
                  <label>Active role header</label>
                  <select
                    value={String(userRole || 'CLINICIAN').toUpperCase()}
                    onChange={event => onRoleChange?.(event.target.value)}
                  >
                    <option value="CLINICIAN">Clinician</option>
                    <option value="ADMIN">Admin</option>
                  </select>
                </div>
                <div className="compliance-info">
                  <p>This is tab-scoped and sent as the live <code>X-User-Role</code> header. There is no hidden auth layer behind it.</p>
                </div>
              </section>
            </div>

            <footer className="settings-footer">
              <button className="btn-logout" onClick={clearLocalSession}>
                <LogOut size={16} />
                <span>Clear session</span>
              </button>
              <button className="btn-logout" onClick={resetSettings}>
                <History size={16} />
                <span>Reset settings</span>
              </button>
              <div className="version-tag">Kini v1.0.4 stable</div>
            </footer>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
