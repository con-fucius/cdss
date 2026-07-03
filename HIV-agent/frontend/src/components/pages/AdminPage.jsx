import React, { useCallback, useEffect, useState } from 'react';
import { AlertCircle, Database, RefreshCw, ShieldCheck, Trash2, Users } from 'lucide-react';
import { request } from '../../lib/api';

function StatCard({ label, value }) {
  return (
    <div className="admin-stat-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

async function settleAdminRequest(label, promise) {
  try {
    return { label, data: await promise, error: '' };
  } catch (error) {
    return {
      label,
      data: null,
      error: error instanceof Error ? error.message : `Unable to load ${label}`
    };
  }
}

export function AdminPage({ userRole }) {
  const [stats, setStats] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [users, setUsers] = useState([]);
  const [evidence, setEvidence] = useState(null);
  const [nodes, setNodes] = useState([]);
  const [pendingMemory, setPendingMemory] = useState([]);
  const [approvedMemory, setApprovedMemory] = useState([]);
  const [terminology, setTerminology] = useState(null);
  const [terminologyCoverage, setTerminologyCoverage] = useState([]);
  const [terminologyCoverageLoading, setTerminologyCoverageLoading] = useState(false);
  const [terminologyCoverageError, setTerminologyCoverageError] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isSeeding, setIsSeeding] = useState(false);
  const [approvingMemoryId, setApprovingMemoryId] = useState('');
  const [savingUserId, setSavingUserId] = useState('');
  const [deletingUserId, setDeletingUserId] = useState('');
  const [newUser, setNewUser] = useState({ external_id: '', display_name: '', role: 'CLINICIAN' });

  const loadAdmin = useCallback(async () => {
    setIsLoading(true);
    setError('');
    try {
      const adminHeaders = { 'X-User-Role': userRole || 'ADMIN' };
      const results = await Promise.all([
        settleAdminRequest('stats', request('/admin/stats', { headers: adminHeaders })),
        settleAdminRequest('sessions', request('/admin/sessions', { headers: adminHeaders })),
        settleAdminRequest('users', request('/admin/users', { headers: adminHeaders })),
        settleAdminRequest('evidence stats', request('/evidence/stats', { headers: adminHeaders })),
        settleAdminRequest('evidence preview', request('/evidence/nodes', {
          method: 'POST',
          headers: adminHeaders,
          body: JSON.stringify({ limit: 25 })
        })),
        settleAdminRequest('pending memory', request('/memory/pending/all', { headers: adminHeaders })),
        settleAdminRequest('approved memory', request('/memory/long-term/all', { headers: adminHeaders })),
        settleAdminRequest('terminology health', request('/terminology/health', { headers: adminHeaders }))
      ]);
      const [statsData, sessionData, userData, evidenceData, nodeData, pendingData, memoryData, terminologyData] = results.map(result => result.data || {});
      setStats(statsData.stats || {});
      setSessions(sessionData.sessions || []);
      setUsers(userData.users || []);
      setEvidence(evidenceData || {});
      setNodes(nodeData.nodes || []);
      setPendingMemory(pendingData.pending || []);
      setApprovedMemory(memoryData.memory || []);
      setTerminology(terminologyData || {});
      const failures = results.filter(result => result.error);
      if (failures.length) {
        setError(failures.map(result => `${result.label}: ${result.error}`).join(' | '));
      }
    } catch (adminError) {
      setError(adminError instanceof Error ? adminError.message : 'Unable to load admin data');
    } finally {
      setIsLoading(false);
    }
  }, [userRole]);

  useEffect(() => {
    loadAdmin();
  }, [loadAdmin]);

  const seedAllEvidence = async () => {
    setIsSeeding(true);
    setError('');
    try {
      await request('/evidence/seed-all', {
        method: 'POST',
        headers: { 'X-User-Role': userRole || 'ADMIN' }
      });
      await loadAdmin();
    } catch (seedError) {
      setError(seedError instanceof Error ? seedError.message : 'Unable to seed evidence graph');
    } finally {
      setIsSeeding(false);
    }
  };

  const loadTerminologyCoverage = async () => {
    setTerminologyCoverageLoading(true);
    setTerminologyCoverageError('');
    try {
      const data = await request('/terminology/coverage', {
        headers: { 'X-User-Role': userRole || 'ADMIN' }
      });
      setTerminologyCoverage(data.coverage || []);
    } catch (coverageError) {
      setTerminologyCoverage([]);
      setTerminologyCoverageError(coverageError instanceof Error ? coverageError.message : 'Unable to load terminology coverage');
    } finally {
      setTerminologyCoverageLoading(false);
    }
  };

  const approveMemory = async (memoryId) => {
    setApprovingMemoryId(memoryId);
    setError('');
    try {
      await request(`/memory/pending/${memoryId}/approve`, {
        method: 'POST',
        headers: { 'X-User-Role': userRole || 'ADMIN' }
      });
      await loadAdmin();
    } catch (approveError) {
      setError(approveError instanceof Error ? approveError.message : 'Unable to approve memory');
    } finally {
      setApprovingMemoryId('');
    }
  };

  const createUser = async () => {
    const externalId = newUser.external_id.trim();
    if (!externalId) {
      setError('External user id is required');
      return;
    }
    setSavingUserId('new');
    setError('');
    try {
      await request('/admin/users', {
        method: 'POST',
        headers: { 'X-User-Role': userRole || 'ADMIN' },
        body: JSON.stringify({
          external_id: externalId,
          display_name: newUser.display_name.trim(),
          role: newUser.role
        })
      });
      setNewUser({ external_id: '', display_name: '', role: 'CLINICIAN' });
      await loadAdmin();
    } catch (userError) {
      setError(userError instanceof Error ? userError.message : 'Unable to create user');
    } finally {
      setSavingUserId('');
    }
  };

  const updateUser = async (user) => {
    setSavingUserId(user.id);
    setError('');
    try {
      await request(`/admin/users/${user.id}`, {
        method: 'PATCH',
        headers: { 'X-User-Role': userRole || 'ADMIN' },
        body: JSON.stringify({
          display_name: user.display_name,
          role: user.role
        })
      });
      await loadAdmin();
    } catch (userError) {
      setError(userError instanceof Error ? userError.message : 'Unable to update user');
    } finally {
      setSavingUserId('');
    }
  };

  const deleteUser = async (userId) => {
    setDeletingUserId(userId);
    setError('');
    try {
      await request(`/admin/users/${userId}`, {
        method: 'DELETE',
        headers: { 'X-User-Role': userRole || 'ADMIN' }
      });
      await loadAdmin();
    } catch (userError) {
      setError(userError instanceof Error ? userError.message : 'Unable to delete user');
    } finally {
      setDeletingUserId('');
    }
  };

  if (String(userRole).toUpperCase() !== 'ADMIN') {
    return (
      <div className="page-wrapper">
        <div className="audit-empty-note">
          <AlertCircle size={14} />
          <span>Admin page requires admin role.</span>
        </div>
      </div>
    );
  }

  return (
    <div className="page-wrapper">
      <div className="admin-page">
        <header className="page-header-minimal">
          <div className="header-info">
            <h1>Admin control plane</h1>
            <p>Operational visibility across sessions, users, audit, and evidence graph</p>
          </div>
          <div className="header-actions">
            <button className="btn-outline-sm" onClick={seedAllEvidence} disabled={isSeeding || isLoading}>
              <span>{isSeeding ? 'Seeding...' : 'Seed evidence graph'}</span>
            </button>
            <button className="btn-outline-sm" onClick={loadTerminologyCoverage} disabled={terminologyCoverageLoading || isLoading}>
              <span>{terminologyCoverageLoading ? 'Loading coverage...' : 'Terminology coverage'}</span>
            </button>
            <button className="btn-outline-sm" onClick={loadAdmin} disabled={isLoading}>
              <RefreshCw size={14} className={isLoading ? 'spinner' : ''} />
              <span>Refresh</span>
            </button>
          </div>
        </header>

        {error && (
          <div className="audit-empty-note">
            <AlertCircle size={14} />
            <span>{error}</span>
          </div>
        )}

        <section className="admin-grid">
          <StatCard label="Active sessions" value={stats?.active_sessions ?? 0} />
          <StatCard label="Users" value={stats?.users_total ?? users.length} />
          <StatCard label="Audit events" value={stats?.audit_events_total ?? 0} />
          <StatCard label="Indexed diseases" value={`${stats?.indexed_diseases_total ?? 0}/${stats?.configured_diseases_total ?? 0}`} />
          <StatCard label="PageIndex rows" value={stats?.pageindex_rows_total ?? 0} />
          <StatCard label="Evidence nodes" value={evidence?.nodes ?? 0} />
          <StatCard label="Evidence edges" value={evidence?.edges ?? 0} />
          <StatCard label="Pending memory" value={pendingMemory.length} />
          <StatCard label="Approved memory" value={approvedMemory.length} />
          <StatCard label="Terminology concepts" value={terminology?.terminology_concepts ?? 0} />
        </section>

        <section className="admin-panel">
          <h2><Database size={16} /> Runtime status</h2>
          <div className="admin-list">
            <div className="admin-row">
              <span>Database</span>
              <code>{stats?.database || 'unknown'}</code>
            </div>
            <div className="admin-row">
              <span>Session storage</span>
              <code>{stats?.session_storage_backend || 'unknown'}</code>
            </div>
            <div className="admin-row">
              <span>Audit storage</span>
              <code>{stats?.audit_storage_backend || 'unknown'}</code>
            </div>
            <div className="admin-row">
              <span>Missing diseases</span>
              <span>{(stats?.missing_diseases || []).join(', ') || 'None'}</span>
            </div>
          </div>
        </section>

        <section className="admin-panel">
          <h2><ShieldCheck size={16} /> Sessions</h2>
          <div className="admin-list">
            {sessions.map(session => (
              <div className="admin-row" key={session.session_id}>
                <code>{session.session_id}</code>
                <span>{session.message_count} messages</span>
                <span>{session.last_seen_at || 'No timestamp'}</span>
              </div>
            ))}
            {sessions.length === 0 && <p>No sessions found.</p>}
          </div>
        </section>

        <section className="admin-panel">
          <h2><Users size={16} /> Users</h2>
          <div className="admin-list">
            <div className="admin-row">
              <input
                type="text"
                placeholder="External user id"
                value={newUser.external_id}
                onChange={event => setNewUser(prev => ({ ...prev, external_id: event.target.value }))}
              />
              <input
                type="text"
                placeholder="Display name"
                value={newUser.display_name}
                onChange={event => setNewUser(prev => ({ ...prev, display_name: event.target.value }))}
              />
              <select
                value={newUser.role}
                onChange={event => setNewUser(prev => ({ ...prev, role: event.target.value }))}
              >
                <option value="CLINICIAN">Clinician</option>
                <option value="ADMIN">Admin</option>
              </select>
              <button className="btn-ghost-sm" onClick={createUser} disabled={savingUserId === 'new'}>
                {savingUserId === 'new' ? 'Creating...' : 'Create'}
              </button>
            </div>
            {users.map(user => (
              <div className="admin-row" key={user.id}>
                <input
                  type="text"
                  value={user.display_name || ''}
                  placeholder={user.external_id}
                  onChange={event => setUsers(prev => prev.map(row => row.id === user.id ? { ...row, display_name: event.target.value } : row))}
                />
                <code>{user.external_id}</code>
                <select
                  value={user.role}
                  onChange={event => setUsers(prev => prev.map(row => row.id === user.id ? { ...row, role: event.target.value } : row))}
                >
                  <option value="CLINICIAN">Clinician</option>
                  <option value="ADMIN">Admin</option>
                </select>
                <span>{user.created_at || 'No timestamp'}</span>
                <button
                  className="btn-ghost-sm"
                  onClick={() => updateUser(user)}
                  disabled={savingUserId === user.id}
                >
                  {savingUserId === user.id ? 'Saving...' : 'Save'}
                </button>
                <button
                  className="btn-ghost-sm"
                  onClick={() => deleteUser(user.id)}
                  disabled={deletingUserId === user.id}
                >
                  <Trash2 size={14} />
                  <span>{deletingUserId === user.id ? 'Deleting...' : 'Delete'}</span>
                </button>
              </div>
            ))}
            {users.length === 0 && <p>No users found.</p>}
          </div>
        </section>

        <section className="admin-panel">
          <h2><ShieldCheck size={16} /> Pending clinical memory</h2>
          <div className="admin-list">
            {pendingMemory.map(memory => (
              <div className="admin-row" key={memory.id}>
                <span>{memory.fact_text}</span>
                <code>{memory.fact_type}</code>
                <button
                  className="btn-ghost-sm"
                  onClick={() => approveMemory(memory.id)}
                  disabled={approvingMemoryId === memory.id}
                >
                  {approvingMemoryId === memory.id ? 'Approving...' : 'Approve'}
                </button>
              </div>
            ))}
            {pendingMemory.length === 0 && <p>No pending memory candidates.</p>}
          </div>
        </section>

        <section className="admin-panel">
          <h2><ShieldCheck size={16} /> Approved clinical memory</h2>
          <div className="admin-list">
            {approvedMemory.map(memory => (
              <div className="admin-row" key={memory.id}>
                <span>{memory.fact_text}</span>
                <code>{memory.fact_type}</code>
                <span>{memory.approved_by || 'No approver'}</span>
              </div>
            ))}
            {approvedMemory.length === 0 && <p>No approved memory facts.</p>}
          </div>
        </section>

        <section className="admin-panel">
          <h2><Database size={16} /> Terminology coverage</h2>
          <div className="admin-list">
            <div className="admin-row">
              <span>Status</span>
              <code>{terminology?.status || 'unknown'}</code>
            </div>
            <div className="admin-row">
              <span>Concepts</span>
              <span>{terminology?.terminology_concepts ?? 0}</span>
            </div>
            <div className="admin-row">
              <span>Aliases</span>
              <span>{terminology?.terminology_aliases ?? 0}</span>
            </div>
            <div className="admin-row">
              <span>Relations</span>
              <span>{terminology?.terminology_relations ?? 0}</span>
            </div>
            <div className="admin-row">
              <span>Annotated chunks</span>
              <span>{terminology?.guideline_chunk_concepts ?? 0}</span>
            </div>
          </div>
          {terminologyCoverageError && (
            <div className="audit-empty-note">
              <AlertCircle size={14} />
              <span>{terminologyCoverageError}</span>
            </div>
          )}
          <div className="admin-list">
            {terminologyCoverage.map(row => (
              <div className="admin-row" key={row.disease}>
                <code>{row.disease}</code>
                <span>{row.annotated_chunks ?? 0}/{row.total_chunks ?? 0} chunks</span>
                <span>{Number(row.coverage_pct || 0).toFixed(1)}%</span>
                <span>{row.unique_cuis ?? 0} CUIs</span>
              </div>
            ))}
            {terminologyCoverage.length === 0 && <p>Terminology coverage has not been loaded yet.</p>}
          </div>
        </section>

        <section className="admin-panel">
          <h2>Evidence graph coverage by disease</h2>
          <div className="admin-list">
            {Object.entries(evidence?.by_disease_detail || {}).map(([disease, counts]) => (
              <div className="admin-row" key={disease}>
                <code>{disease}</code>
                <span>{counts.nodes} nodes</span>
                <span>{counts.edges} edges</span>
              </div>
            ))}
            {Object.keys(evidence?.by_disease_detail || {}).length === 0 && <p>No graph coverage found.</p>}
          </div>
        </section>

        <section className="admin-panel">
          <h2><Database size={16} /> Evidence graph preview</h2>
          <div className="admin-list">
            {nodes.map(node => (
              <div className="admin-row" key={node.id}>
                <span>{node.label}</span>
                <code>{node.node_type}</code>
                <span>{node.disease}</span>
              </div>
            ))}
            {nodes.length === 0 && <p>No evidence nodes found.</p>}
          </div>
        </section>
      </div>
    </div>
  );
}
