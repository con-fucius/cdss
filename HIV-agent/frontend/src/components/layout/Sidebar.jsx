import React from 'react';
import { NavLink } from 'react-router-dom';
import {
  MessageSquare,
  Settings,
  BookOpen,
  Database,
  ClipboardList,
  ShieldCheck,
  Clock,
  Activity,
  Stethoscope
} from 'lucide-react';

function toSentenceRole(role) {
  const text = String(role || 'clinician').toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function Sidebar({ conversations, currentConvId, onSelectConversation, userRole }) {
  const navItems = [
    { id: 'chat', path: '/chat', icon: MessageSquare, label: 'Quick chat' },
    { id: 'builder', path: '/builder', icon: Settings, label: 'Query builder' },
    { id: 'ddx', path: '/ddx', icon: Activity, label: 'DDx workspace' },
    { id: 'pathways', path: '/pathways', icon: Stethoscope, label: 'Clinical pathways' },
    { id: 'guidelines', path: '/guidelines', icon: BookOpen, label: 'Guidelines' },
    { id: 'kb', path: '/kb', icon: Database, label: 'Knowledge base' }
  ];

  if (userRole === 'ADMIN') {
    navItems.push({ id: 'audit', path: '/audit', icon: ClipboardList, label: 'Audit log' });
    navItems.push({ id: 'admin', path: '/admin', icon: ShieldCheck, label: 'Admin' });
  }

  return (
    <aside className="sidebar" aria-label="Main Sidebar">
      <div className="sidebar-logo">
        <div className="logo-glow" />
        <span className="logo-text">Kini</span>
      </div>

      <div className="sidebar-divider" />

      <nav className="sidebar-nav" role="navigation">
        {navItems.map(item => (
          <NavLink
            key={item.id}
            to={item.path}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
            aria-label={item.label}
          >
            <item.icon size={18} strokeWidth={2} className="nav-icon" />
            <span className="nav-label">{item.label}</span>
          </NavLink>
        ))}
      </nav>

      {conversations.length > 0 && (
        <div className="sidebar-conversations">
          <div className="sidebar-section-title">
            <Clock size={12} strokeWidth={2.5} />
            <span>Recent sessions</span>
          </div>
          <div className="conversation-list">
            {conversations.slice(-8).reverse().map(conv => (
              <button
                key={conv.id}
                className={`conversation-item ${conv.id === currentConvId ? 'active' : ''}`}
                onClick={() => onSelectConversation(conv.id)}
                title={conv.title}
              >
                <MessageSquare size={14} className="conversation-icon" />
                <span className="conversation-title">{conv.title}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="sidebar-footer">
        <div className="user-profile">
          <div className="user-avatar">{userRole?.[0]}</div>
          <div className="user-info">
            <div className="user-name">{toSentenceRole(userRole)}</div>
            <div className="user-status">Online</div>
          </div>
        </div>
      </div>
    </aside>
  );
}
