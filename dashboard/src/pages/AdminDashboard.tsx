import React, { useState, useEffect, FormEvent, ChangeEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Users,
  UserPlus,
  Mail,
  Loader,
  Trash2,
  RefreshCw,
  Shield,
  ShieldCheck,
  X,
  FileSpreadsheet,
  FileDown,
  Archive,
} from 'lucide-react';
import { adminApi } from '../api/admin';
import { unwrapResponse } from '../utils/apiUnwrap';
import { useAuth } from '../context/auth-context';
import { useToast } from '../context/toast-context';
import { LMSAIPolicyCard } from '../components/admin/LMSAIPolicyCard';

// Type definitions
type UserRole = 'faculty' | 'admin' | 'super_admin';
type ExportType = 'csv' | 'excel' | 'bulk';

interface DepartmentStats {
  total_users: number;
  active_users: number;
  total_scans: number;
  historical_scan_count: number;
  enrolled_document_count: number;
  verified_document_count: number;
  unverified_document_count: number;
  scans_this_month: number;
  avg_compliance_score: number | null;
  pending_invitations: number;
}

interface User {
  id: string;
  email: string;
  name?: string;
  role: UserRole;
  picture_url?: string;
  last_login_at?: string;
  scan_count?: number;
}

interface Invitation {
  id: string;
  email: string;
  role: UserRole;
  expires_at: string;
}

export function AdminDashboard(): React.ReactElement {
  const [stats, setStats] = useState<DepartmentStats | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState<string>('');
  const [inviteRole, setInviteRole] = useState<UserRole>('faculty');
  const [inviting, setInviting] = useState<boolean>(false);
  const [showInviteModal, setShowInviteModal] = useState<boolean>(false);
  const [showRemoveModal, setShowRemoveModal] = useState<boolean>(false);
  const [userToRemove, setUserToRemove] = useState<User | null>(null);
  const [removing, setRemoving] = useState<boolean>(false);
  const [exporting, setExporting] = useState<ExportType | null>(null);

  const { department, user: currentUser } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();

  const departmentId = department?.id || 'test-dept-456';
  const currentUserRole = (currentUser?.role as UserRole) || 'admin';

  // Check if user has admin access
  useEffect(() => {
    if (currentUser && !['admin', 'super_admin'].includes(currentUser.role || '')) {
      navigate('/dashboard');
      toast.error('Admin access required');
    }
  }, [currentUser, navigate, toast]);

  const fetchData = async (): Promise<void> => {
    try {
      setLoading(true);
      setError(null);

      const [statsData, usersData, invitationsData] = await Promise.all([
        adminApi.getDepartmentStats(),
        adminApi.listUsers(),
        adminApi.listInvitations('pending'),
      ]);

      setStats(unwrapResponse<DepartmentStats>(statsData, 'stats'));
      setUsers(usersData.users || []);
      setInvitations(invitationsData.invitations || []);
    } catch (err) {
      console.error('Failed to fetch admin data:', err);
      const error = err as { response?: { data?: { detail?: string } }; message?: string };
      setError(error.response?.data?.detail || error.message || 'Failed to load admin data');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleInvite = async (e: FormEvent<HTMLFormElement>): Promise<void> => {
    e.preventDefault();
    if (!inviteEmail) return;

    setInviting(true);
    try {
      await adminApi.inviteUser(inviteEmail, inviteRole);
      toast.success(`Invitation sent to ${inviteEmail}`);
      setInviteEmail('');
      setInviteRole('faculty');
      setShowInviteModal(false);
      // Refresh invitations
      const invitationsData = await adminApi.listInvitations('pending');
      setInvitations(invitationsData.invitations || []);
    } catch (err) {
      const error = err as { response?: { data?: { detail?: string } } };
      toast.error(error.response?.data?.detail || 'Failed to send invitation');
    } finally {
      setInviting(false);
    }
  };

  const handleRemoveUser = async (): Promise<void> => {
    if (!userToRemove) return;

    setRemoving(true);
    try {
      await adminApi.removeUser(userToRemove.id);
      toast.success(`${userToRemove.email} has been removed`);
      setShowRemoveModal(false);
      setUserToRemove(null);
      // Refresh users
      const usersData = await adminApi.listUsers();
      setUsers(usersData.users || []);
    } catch (err) {
      const error = err as { response?: { data?: { detail?: string } } };
      toast.error(error.response?.data?.detail || 'Failed to remove user');
    } finally {
      setRemoving(false);
    }
  };

  const handleRoleChange = async (userId: string, newRole: string): Promise<void> => {
    try {
      await adminApi.updateUserRole(userId, newRole as UserRole);
      toast.success('Role updated successfully');
      // Refresh users
      const usersData = await adminApi.listUsers();
      setUsers(usersData.users || []);
    } catch (err) {
      const error = err as { response?: { data?: { detail?: string } } };
      toast.error(error.response?.data?.detail || 'Failed to update role');
    }
  };

  const handleRevokeInvitation = async (invitationId: string, email: string): Promise<void> => {
    try {
      await adminApi.revokeInvitation(invitationId);
      toast.success(`Invitation to ${email} revoked`);
      // Refresh invitations
      const invitationsData = await adminApi.listInvitations('pending');
      setInvitations(invitationsData.invitations || []);
    } catch (err) {
      const error = err as { response?: { data?: { detail?: string } } };
      toast.error(error.response?.data?.detail || 'Failed to revoke invitation');
    }
  };

  const handleResendInvitation = async (invitationId: string, email: string): Promise<void> => {
    try {
      await adminApi.resendInvitation(invitationId);
      toast.success(`Invitation resent to ${email}`);
    } catch (err) {
      const error = err as { response?: { data?: { detail?: string } } };
      toast.error(error.response?.data?.detail || 'Failed to resend invitation');
    }
  };

  const handleExport = async (type: ExportType): Promise<void> => {
    setExporting(type);
    try {
      let blob: Blob;
      let filename: string;

      switch (type) {
        case 'csv':
          blob = await adminApi.exportCSV(departmentId);
          filename = `scans_export_${departmentId}_${new Date().toISOString().split('T')[0]}.csv`;
          break;
        case 'excel':
          blob = await adminApi.exportExcel(departmentId);
          filename = `scans_export_${departmentId}_${new Date().toISOString().split('T')[0]}.xlsx`;
          break;
        case 'bulk':
          blob = await adminApi.bulkExport(departmentId, { include_evidence_report: true });
          filename = `aelira_export_${departmentId}_${new Date().toISOString().split('T')[0]}.zip`;
          break;
        default:
          throw new Error('Unknown export type');
      }

      // Create download link
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);

      toast.success(`${type.toUpperCase()} export downloaded`);
    } catch (err) {
      const error = err as { response?: { data?: { detail?: string } } };
      toast.error(error.response?.data?.detail || `Failed to export ${type}`);
    } finally {
      setExporting(null);
    }
  };

  return (
    <div className="p-8">
      <div className="max-w-7xl mx-auto">
        {['admin', 'super_admin'].includes(currentUserRole) && <LMSAIPolicyCard />}
        <section aria-label="Dashboard users and statistics" aria-busy={loading}>
          {loading ? (
            <div className="flex items-center justify-center h-64" role="status" aria-label="Loading admin dashboard">
              <Loader className="w-8 h-8 animate-spin text-accent" aria-hidden="true" />
              <span className="sr-only">Loading admin dashboard...</span>
            </div>
          ) : error ? (
            <div
              className="rounded-lg p-4"
              style={{
                backgroundColor: 'var(--surface-error-subtle)',
                borderColor: 'var(--content-error)',
                border: '1px solid',
                color: 'var(--content-error)',
              }}
              role="alert"
            >
              Error: {error}
            </div>
          ) : (
            <>
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-3xl font-bold text-primary">Admin Dashboard</h1>
          <button
            onClick={() => setShowInviteModal(true)}
            className="btn-primary flex items-center space-x-2"
            aria-label="Invite new user"
          >
            <UserPlus className="w-4 h-4" aria-hidden="true" />
            <span>Invite User</span>
          </button>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
          <div className="card">
            <div className="text-sm font-medium text-secondary mb-1">Total Users</div>
            <div className="text-3xl font-bold text-primary">{stats?.total_users || 0}</div>
            <div className="text-sm text-tertiary mt-1">
              {stats?.active_users || 0} active this month
            </div>
          </div>

          <div className="card">
            <div className="text-sm font-medium text-secondary mb-1">Current Documents</div>
            <div className="text-3xl font-bold text-primary">{stats?.enrolled_document_count ?? 0}</div>
            <div className="text-sm text-tertiary mt-1">
              {stats?.verified_document_count ?? 0} verified · {stats?.historical_scan_count ?? 0} attempts
            </div>
          </div>

          <div className="card">
            <div className="text-sm font-medium text-secondary mb-1">Avg Compliance</div>
            <div className="text-3xl font-bold text-primary">
              {stats?.avg_compliance_score == null ? '--' : Math.round(stats.avg_compliance_score)}
              {stats?.avg_compliance_score != null && <span className="text-lg">/100</span>}
            </div>
            <div className="text-sm text-tertiary mt-1">
              {stats?.unverified_document_count ?? 0} documents awaiting results
            </div>
          </div>

          <div className="card">
            <div className="text-sm font-medium text-secondary mb-1">Pending Invites</div>
            <div className="text-3xl font-bold text-primary">{stats?.pending_invitations || 0}</div>
            <div className="text-sm text-tertiary mt-1">Awaiting acceptance</div>
          </div>
        </div>

        {/* Export Actions */}
        <div className="card mb-8">
          <h2 className="text-xl font-semibold text-primary mb-4">Export Data</h2>
          <div className="flex flex-wrap gap-4">
            <button
              onClick={() => handleExport('csv')}
              disabled={exporting !== null}
              className="btn-secondary flex items-center space-x-2"
              aria-label="Export as CSV"
            >
              {exporting === 'csv' ? (
                <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />
              ) : (
                <FileDown className="w-4 h-4" aria-hidden="true" />
              )}
              <span>Export CSV</span>
            </button>

            <button
              onClick={() => handleExport('excel')}
              disabled={exporting !== null}
              className="btn-secondary flex items-center space-x-2"
              aria-label="Export as Excel"
            >
              {exporting === 'excel' ? (
                <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />
              ) : (
                <FileSpreadsheet className="w-4 h-4" aria-hidden="true" />
              )}
              <span>Export Excel</span>
            </button>

            <button
              onClick={() => handleExport('bulk')}
              disabled={exporting !== null}
              className="btn-secondary flex items-center space-x-2"
              aria-label="Bulk export as ZIP"
            >
              {exporting === 'bulk' ? (
                <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />
              ) : (
                <Archive className="w-4 h-4" aria-hidden="true" />
              )}
              <span>Bulk Export (ZIP)</span>
            </button>
          </div>
        </div>

        {/* Faculty Members */}
        <div className="card mb-8">
          <h2 className="text-xl font-semibold text-primary mb-4">
            Faculty Members ({users.length})
          </h2>

          {users.length === 0 ? (
            <div className="text-center py-8">
              <Users className="w-12 h-12 text-tertiary mx-auto mb-4" aria-hidden="true" />
              <p className="text-tertiary">No users yet. Invite your first faculty member!</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full" role="grid">
                <thead>
                  <tr className="border-b border-primary">
                    <th className="text-left py-3 px-4 text-sm font-medium text-secondary">User</th>
                    <th className="text-left py-3 px-4 text-sm font-medium text-secondary">Role</th>
                    <th className="text-left py-3 px-4 text-sm font-medium text-secondary">
                      Last Login
                    </th>
                    <th className="text-left py-3 px-4 text-sm font-medium text-secondary">Scans</th>
                    <th className="text-right py-3 px-4 text-sm font-medium text-secondary">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <tr
                      key={user.id}
                      className="border-b border-subtle hover:bg-surface-tertiary transition-colors"
                    >
                      <td className="py-3 px-4">
                        <div className="flex items-center space-x-3">
                          {user.picture_url ? (
                            <img
                              src={user.picture_url}
                              alt=""
                              className="w-8 h-8 rounded-full"
                            />
                          ) : (
                            <div
                              className="w-8 h-8 rounded-full flex items-center justify-center"
                              style={{ backgroundColor: 'var(--surface-tertiary)' }}
                            >
                              <Users className="w-4 h-4 text-tertiary" aria-hidden="true" />
                            </div>
                          )}
                          <div>
                            <div className="font-medium text-primary">{user.name || 'Unnamed'}</div>
                            <div className="text-sm text-secondary">{user.email}</div>
                          </div>
                        </div>
                      </td>
                      <td className="py-3 px-4">
                        <select
                          value={user.role}
                          onChange={(e: ChangeEvent<HTMLSelectElement>) => handleRoleChange(user.id, e.target.value)}
                          disabled={user.id === currentUser?.id}
                          className="text-sm rounded-md border border-primary px-2 py-1 bg-surface-primary text-primary"
                          aria-label={`Change role for ${user.email}`}
                        >
                          <option value="faculty">Faculty</option>
                          <option value="admin">Admin</option>
                          {currentUserRole === 'super_admin' && (
                            <option value="super_admin">Super Admin</option>
                          )}
                        </select>
                      </td>
                      <td className="py-3 px-4 text-sm text-secondary">
                        {user.last_login_at
                          ? new Date(user.last_login_at).toLocaleDateString()
                          : 'Never'}
                      </td>
                      <td className="py-3 px-4 text-sm text-primary">{user.scan_count || 0}</td>
                      <td className="py-3 px-4 text-right">
                        {user.id !== currentUser?.id && (
                          <button
                            onClick={() => {
                              setUserToRemove(user);
                              setShowRemoveModal(true);
                            }}
                            className="text-[var(--feature-danger-content)] hover:underline text-sm"
                            aria-label={`Remove ${user.email}`}
                          >
                            <Trash2 className="w-4 h-4 inline" aria-hidden="true" />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Pending Invitations */}
        {invitations.length > 0 && (
          <div className="card">
            <h2 className="text-xl font-semibold text-primary mb-4">
              Pending Invitations ({invitations.length})
            </h2>

            <div className="space-y-3">
              {invitations.map((invite) => (
                <div
                  key={invite.id}
                  className="flex items-center justify-between p-4 rounded-lg bg-surface-tertiary border border-primary"
                >
                  <div className="flex items-center space-x-4">
                    <Mail className="w-5 h-5 text-secondary" aria-hidden="true" />
                    <div>
                      <div className="font-medium text-primary">{invite.email}</div>
                      <div className="text-sm text-secondary">
                        {invite.role === 'admin' ? (
                          <span className="flex items-center">
                            <ShieldCheck className="w-3 h-3 mr-1" aria-hidden="true" /> Admin
                          </span>
                        ) : (
                          <span className="flex items-center">
                            <Shield className="w-3 h-3 mr-1" aria-hidden="true" /> Faculty
                          </span>
                        )}
                        {' '}
                        · Expires{' '}
                        {new Date(invite.expires_at).toLocaleDateString()}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center space-x-2">
                    <button
                      onClick={() => handleResendInvitation(invite.id, invite.email)}
                      className="text-accent hover:underline text-sm flex items-center"
                      aria-label={`Resend invitation to ${invite.email}`}
                    >
                      <RefreshCw className="w-4 h-4 mr-1" aria-hidden="true" />
                      Resend
                    </button>
                    <button
                      onClick={() => handleRevokeInvitation(invite.id, invite.email)}
                      className="text-[var(--feature-danger-content)] hover:underline text-sm"
                      aria-label={`Revoke invitation for ${invite.email}`}
                    >
                      Revoke
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
            </>
          )}
        </section>
      </div>

      {/* Invite User Modal */}
      {showInviteModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50"
          role="dialog"
          aria-modal="true"
          aria-labelledby="invite-modal-title"
        >
          <div
            className="rounded-lg p-6 max-w-md w-full mx-4"
            style={{ backgroundColor: 'var(--surface-primary)' }}
          >
            <div className="flex items-center justify-between mb-4">
              <h3 id="invite-modal-title" className="text-lg font-semibold text-primary">
                Invite New User
              </h3>
              <button
                onClick={() => setShowInviteModal(false)}
                className="text-tertiary hover:text-primary"
                aria-label="Close invite modal"
              >
                <X className="w-5 h-5" aria-hidden="true" />
              </button>
            </div>

            <form onSubmit={handleInvite}>
              <div className="mb-4">
                <label
                  htmlFor="invite-email"
                  className="block text-sm font-medium text-secondary mb-1"
                >
                  Email Address
                </label>
                <input
                  type="email"
                  id="invite-email"
                  value={inviteEmail}
                  onChange={(e: ChangeEvent<HTMLInputElement>) => setInviteEmail(e.target.value)}
                  placeholder="faculty@university.edu"
                  className="w-full px-3 py-2 rounded-lg border border-primary bg-surface-primary text-primary"
                  required
                />
              </div>

              <div className="mb-6">
                <label
                  htmlFor="invite-role"
                  className="block text-sm font-medium text-secondary mb-1"
                >
                  Role
                </label>
                <select
                  id="invite-role"
                  value={inviteRole}
                  onChange={(e: ChangeEvent<HTMLSelectElement>) => setInviteRole(e.target.value as UserRole)}
                  className="w-full px-3 py-2 rounded-lg border border-primary bg-surface-primary text-primary"
                >
                  <option value="faculty">Faculty</option>
                  <option value="admin">Admin</option>
                </select>
              </div>

              <div className="flex justify-end space-x-3">
                <button
                  type="button"
                  onClick={() => setShowInviteModal(false)}
                  className="btn-secondary"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={inviting || !inviteEmail}
                  className="btn-primary flex items-center space-x-2"
                >
                  {inviting && <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />}
                  <span>Send Invitation</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Remove User Confirmation Modal */}
      {showRemoveModal && userToRemove && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="remove-modal-title"
          aria-describedby="remove-modal-description"
        >
          <div
            className="rounded-lg p-6 max-w-md w-full mx-4"
            style={{ backgroundColor: 'var(--surface-primary)' }}
          >
            <h3 id="remove-modal-title" className="text-lg font-semibold text-primary mb-2">
              Remove User
            </h3>
            <p id="remove-modal-description" className="text-secondary mb-6">
              Are you sure you want to remove <strong>{userToRemove.email}</strong> from your
              department? They will lose access to all department resources.
            </p>

            <div className="flex justify-end space-x-3">
              <button
                onClick={() => {
                  setShowRemoveModal(false);
                  setUserToRemove(null);
                }}
                className="btn-secondary"
              >
                Cancel
              </button>
              <button
                onClick={handleRemoveUser}
                disabled={removing}
                className="px-4 py-2 rounded-lg text-white flex items-center space-x-2"
                style={{ backgroundColor: 'var(--feature-danger-content)' }}
              >
                {removing && <Loader className="w-4 h-4 animate-spin" aria-hidden="true" />}
                <span>Remove User</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
