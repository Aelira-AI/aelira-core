import React, { useState, useEffect, useCallback, ChangeEvent, MouseEvent as ReactMouseEvent } from 'react';
import { useAuth } from '../context/auth-context';
import { useToast } from '../context/toast-context';
import { useFeatureAccess } from '../hooks/useFeatureAccess';
import { llmProvidersApi } from '../api/llmProviders';
import { accountApi } from '../api/account';
import type { DeletionStatusResponse } from '../api/account';
import { AccountDeletionModal } from '../components/AccountDeletionModal';
import {
  User,
  Building2,
  Shield,
  LogOut,
  AlertCircle,
  CheckCircle,
  XCircle,
  Loader2,
  Trash2,
  Save,
  Mail,
  Globe,
  BadgeCheck,
  Download,
  AlertTriangle,
} from 'lucide-react';
import { apiClient } from '../api/client';
import EmailPreferencesCard from '../components/EmailPreferencesCard';
import { ActiveSessionsCard } from '../components/settings/ActiveSessionsCard';
import { AIProvidersCard } from '../components/settings/AIProvidersCard';
import { APIKeyManagementCard } from '../components/settings/APIKeyManagementCard';
import { trackEvent } from '../utils/analytics';

// Type definitions
type ProviderKey = 'ollama' | 'gemini' | 'openai' | 'anthropic' | 'xai';

interface FeatureItemProps {
  label: string;
  available: boolean;
}

interface Provider {
  is_available?: boolean;
  text_model?: string;
  code_model?: string;
  vision_model?: string;
}

interface Profile {
  email: string;
  name?: string;
  timezone?: string;
  email_notifications?: boolean;
  email_verified?: boolean;
  auth_provider?: string;
  created_at: string;
}

interface ProfileForm {
  name: string;
  timezone: string;
}

interface Session {
  id: string;
  user_agent?: string;
  ip_address?: string;
  is_current?: boolean;
  last_used_at?: string;
  created_at: string;
}

// Feature item for plan features display
const FeatureItem = ({ label, available }: FeatureItemProps): React.ReactElement => (
  <div className="flex items-center gap-2">
    {available ? (
      <CheckCircle className="w-4 h-4 shrink-0" style={{ color: 'var(--content-success)' }} />
    ) : (
      <XCircle className="w-4 h-4 shrink-0" style={{ color: 'var(--content-tertiary)' }} />
    )}
    <span className={`text-sm ${available ? 'text-primary' : 'text-tertiary'}`}>
      {label}
    </span>
  </div>
);



export default function Settings(): React.ReactElement {
  const { authMethod, department, logout } = useAuth();
  const { showToast } = useToast();
  const {
    showAIProviderSettings,
    showCustomAPIKeys,
    departmentLabel,
    tierDisplayName,
    isIndividualTier,
    showVideoProcessing,
    showBulkAPI,
    showEvidenceReports,
    showIntegrations,
    showBulkUpload,
    showTeamFeatures,
  } = useFeatureAccess();


  // LLM Provider state
  const [providers, setProviders] = useState<Record<string, Provider>>({});
  const [primaryProvider, setPrimaryProvider] = useState<ProviderKey | null>(null);
  const [fallbackProvider, setFallbackProvider] = useState<ProviderKey | null>(null);
  const [loadingProviders, setLoadingProviders] = useState<boolean>(true);
  const [testingProvider, setTestingProvider] = useState<ProviderKey | null>(null);

  // Profile state
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loadingProfile, setLoadingProfile] = useState<boolean>(true);
  const [editingProfile, setEditingProfile] = useState<boolean>(false);
  const [profileForm, setProfileForm] = useState<ProfileForm>({ name: '', timezone: 'UTC' });
  const [savingProfile, setSavingProfile] = useState<boolean>(false);

  // Session management state
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loadingSessions, setLoadingSessions] = useState<boolean>(true);
  const [revokingSession, setRevokingSession] = useState<string | null>(null);
  const [revokingAll, setRevokingAll] = useState<boolean>(false);

  // Account deletion state
  const [showDeleteModal, setShowDeleteModal] = useState<boolean>(false);
  const [deletionStatus, setDeletionStatus] = useState<DeletionStatusResponse | null>(null);
  const [deactivating, setDeactivating] = useState<boolean>(false);
  const [exporting, setExporting] = useState<boolean>(false);

  useEffect(() => {
    fetchProfile();
    fetchSessions();
    fetchDeletionStatus();
  }, []);

  const fetchDeletionStatus = async (): Promise<void> => {
    try {
      const status = await accountApi.getDeletionStatus();
      setDeletionStatus(status);
    } catch {
      // Silently ignore — endpoint may 404 if feature not deployed yet
    }
  };

  const fetchProfile = async (): Promise<void> => {
    try {
      setLoadingProfile(true);
      const response = await apiClient.get('/auth/profile');
      setProfile(response.data);
      setProfileForm({
        name: response.data.name || '',
        timezone: response.data.timezone || 'UTC',
      });
    } catch (error) {
      console.error('Failed to fetch profile:', error);
    } finally {
      setLoadingProfile(false);
    }
  };

  const fetchSessions = async (): Promise<void> => {
    try {
      setLoadingSessions(true);
      const response = await apiClient.get('/auth/sessions');
      setSessions(response.data.sessions || []);
    } catch (error) {
      console.error('Failed to fetch sessions:', error);
    } finally {
      setLoadingSessions(false);
    }
  };

  const handleSaveProfile = async (): Promise<void> => {
    try {
      setSavingProfile(true);
      const response = await apiClient.patch('/auth/profile', profileForm);
      setProfile(response.data);
      setEditingProfile(false);
      showToast('Profile updated successfully', 'success');
    } catch (error) {
      console.error('Failed to save profile:', error);
      const err = error as { response?: { data?: { detail?: string } } };
      showToast(err.response?.data?.detail || 'Failed to save profile', 'error');
    } finally {
      setSavingProfile(false);
    }
  };

  const handleRevokeSession = async (sessionId: string): Promise<void> => {
    try {
      setRevokingSession(sessionId);
      await apiClient.delete(`/auth/sessions/${sessionId}`);
      setSessions(sessions.filter(s => s.id !== sessionId));
      showToast('Session revoked', 'success');
    } catch (error) {
      console.error('Failed to revoke session:', error);
      const err = error as { response?: { data?: { detail?: string } } };
      showToast(err.response?.data?.detail || 'Failed to revoke session', 'error');
    } finally {
      setRevokingSession(null);
    }
  };

  const handleRevokeAllOtherSessions = async (): Promise<void> => {
    if (!window.confirm('Are you sure you want to sign out all other devices?')) {
      return;
    }
    try {
      setRevokingAll(true);
      const response = await apiClient.delete('/auth/sessions');
      await fetchSessions();
      showToast(`Revoked ${response.data.revoked_count} session(s)`, 'success');
    } catch (error) {
      console.error('Failed to revoke sessions:', error);
      const err = error as { response?: { data?: { detail?: string } } };
      showToast(err.response?.data?.detail || 'Failed to revoke sessions', 'error');
    } finally {
      setRevokingAll(false);
    }
  };


  const fetchProviders = useCallback(async (): Promise<void> => {
    try {
      setLoadingProviders(true);
      const data = await llmProvidersApi.listProviders();
      const providerRecord: Record<string, Provider> = {};
      for (const p of data.providers || []) {
        providerRecord[p.name] = {
          is_available: p.is_available,
          text_model: p.text_model,
          code_model: p.code_model,
          vision_model: p.vision_model,
        };
      }
      setProviders(providerRecord);
      setPrimaryProvider(data.primary_provider);
      setFallbackProvider(data.fallback_provider);
    } catch (error) {
      console.error('Failed to fetch providers:', error);
      showToast('Failed to load LLM providers', 'error');
    } finally {
      setLoadingProviders(false);
    }
  }, [showToast]);

  useEffect(() => {
    if (showAIProviderSettings) {
      fetchProviders();
    } else {
      setLoadingProviders(false);
    }
  }, [showAIProviderSettings, fetchProviders]);

  const handleSetPrimary = async (providerKey: ProviderKey): Promise<void> => {
    try {
      await llmProvidersApi.setPrimaryProvider(providerKey, false);
      setPrimaryProvider(providerKey);
      showToast(`Set ${providerKey} as primary provider`, 'success');
    } catch (error) {
      console.error('Failed to set primary provider:', error);
      showToast('Failed to set primary provider', 'error');
    }
  };

  const handleTestProvider = async (providerKey: ProviderKey): Promise<void> => {
    try {
      setTestingProvider(providerKey);
      const result = await llmProvidersApi.testProvider(providerKey);
      if (result.success) {
        showToast(`${providerKey} is working (${(result.response_time_ms / 1000).toFixed(2)}s)`, 'success');
      } else {
        showToast(`Test failed: ${result.error || 'Unknown error'}`, 'error');
      }
    } catch (error) {
      console.error('Failed to test provider:', error);
      const err = error as { response?: { data?: { detail?: string } }; message?: string };
      showToast(`Test failed: ${err.response?.data?.detail || err.message}`, 'error');
    } finally {
      setTestingProvider(null);
    }
  };


  const handleDeactivate = async (): Promise<void> => {
    if (!window.confirm('Are you sure you want to deactivate your account? This will immediately sign you out and block re-registration for 90 days.')) {
      return;
    }
    try {
      setDeactivating(true);
      await accountApi.deactivateAccount();
      showToast('Account deactivated. You will be signed out.', 'warning');
      setTimeout(() => logout(), 1500);
    } catch (error) {
      const err = error as { response?: { data?: { detail?: string } } };
      showToast(err.response?.data?.detail || 'Failed to deactivate account.', 'error');
    } finally {
      setDeactivating(false);
    }
  };

  const handleExportData = async (): Promise<void> => {
    try {
      setExporting(true);
      const data = await accountApi.exportData();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `aelira-data-export-${new Date().toISOString().split('T')[0]}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showToast('Data export downloaded.', 'success');
    } catch (error) {
      const err = error as { response?: { data?: { detail?: string } } };
      showToast(err.response?.data?.detail || 'Failed to export data.', 'error');
    } finally {
      setExporting(false);
    }
  };

  const handleCancelDeletion = async (): Promise<void> => {
    if (!window.confirm('Cancel your pending account deletion? Your account will be fully restored.')) {
      return;
    }
    try {
      await accountApi.cancelDeletion();
      setDeletionStatus(null);
      showToast('Account deletion cancelled. Your account has been restored.', 'success');
    } catch (error) {
      const err = error as { response?: { data?: { detail?: string } } };
      showToast(err.response?.data?.detail || 'Failed to cancel deletion.', 'error');
    }
  };

  const handleLogout = (): void => {
    if (window.confirm('Are you sure you want to log out?')) {
      logout();
    }
  };

  return (
    <div className="p-8">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-3xl font-bold text-primary mb-8">Settings</h1>

        {/* Profile Section */}
        {profile && (
          <div className="card mb-6">
            <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border-subtle)' }}>
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-semibold text-primary flex items-center gap-2">
                  <User className="w-5 h-5" />
                  Your Profile
                </h2>
                {!editingProfile && (
                  <button
                    onClick={() => setEditingProfile(true)}
                    className="btn-secondary px-3 py-1.5 text-sm"
                  >
                    Edit
                  </button>
                )}
              </div>
            </div>
            <div className="px-6 py-4">
              {loadingProfile ? (
                <div className="flex items-center justify-center py-4">
                  <Loader2 className="w-5 h-5 animate-spin text-tertiary" />
                </div>
              ) : editingProfile ? (
                <div className="space-y-4">
                  <div>
                    <label className="text-sm font-medium text-tertiary block mb-1">Name</label>
                    <input
                      type="text"
                      value={profileForm.name}
                      onChange={(e: ChangeEvent<HTMLInputElement>) => setProfileForm({ ...profileForm, name: e.target.value })}
                      className="input w-full max-w-md"
                      placeholder="Your name"
                    />
                  </div>
                  <div>
                    <label className="text-sm font-medium text-tertiary block mb-1">Timezone</label>
                    <select
                      value={profileForm.timezone}
                      onChange={(e: ChangeEvent<HTMLSelectElement>) => setProfileForm({ ...profileForm, timezone: e.target.value })}
                      className="input w-full max-w-md"
                    >
                      <option value="UTC">UTC</option>
                      <option value="America/New_York">Eastern Time (ET)</option>
                      <option value="America/Chicago">Central Time (CT)</option>
                      <option value="America/Denver">Mountain Time (MT)</option>
                      <option value="America/Los_Angeles">Pacific Time (PT)</option>
                      <option value="Europe/London">London (GMT)</option>
                      <option value="Europe/Paris">Paris (CET)</option>
                      <option value="Asia/Tokyo">Tokyo (JST)</option>
                    </select>
                  </div>
                  <div className="flex gap-2 pt-2">
                    <button
                      onClick={handleSaveProfile}
                      disabled={savingProfile}
                      className="btn-primary px-4 py-2 text-sm flex items-center gap-2"
                    >
                      {savingProfile ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Save className="w-4 h-4" />
                      )}
                      Save Changes
                    </button>
                    <button
                      onClick={() => {
                        setEditingProfile(false);
                        setProfileForm({
                          name: profile.name || '',
                          timezone: profile.timezone || 'UTC',
                        });
                      }}
                      className="btn-secondary px-4 py-2 text-sm"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <label className="text-sm font-medium text-tertiary flex items-center gap-1">
                        <Mail className="w-3.5 h-3.5" /> Email
                      </label>
                      <p className="text-base text-primary mt-1">{profile.email}</p>
                    </div>
                    <div>
                      <label className="text-sm font-medium text-tertiary flex items-center gap-1">
                        <User className="w-3.5 h-3.5" /> Name
                      </label>
                      <p className="text-base text-primary mt-1">{profile.name || 'Not set'}</p>
                    </div>
                    <div>
                      <label className="text-sm font-medium text-tertiary flex items-center gap-1">
                        <Globe className="w-3.5 h-3.5" /> Timezone
                      </label>
                      <p className="text-base text-primary mt-1">{profile.timezone || 'UTC'}</p>
                    </div>
                    <div>
                      <label className="text-sm font-medium text-tertiary flex items-center gap-1">
                        <BadgeCheck className="w-3.5 h-3.5" /> Account Status
                      </label>
                      <div className="flex items-center gap-2 mt-1">
                        {profile.email_verified ? (
                          <span
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium"
                            style={{ backgroundColor: 'var(--surface-success-subtle)', color: 'var(--content-success)' }}
                          >
                            <CheckCircle className="w-3 h-3" /> Verified
                          </span>
                        ) : (
                          <span
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium"
                            style={{ backgroundColor: 'var(--surface-warning-subtle)', color: 'var(--content-warning)' }}
                          >
                            <AlertCircle className="w-3 h-3" /> Unverified
                          </span>
                        )}
                        <span className="text-xs text-tertiary capitalize">
                          via {profile.auth_provider?.replace('_', ' ') || 'magic link'}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div className="pt-2 text-xs text-tertiary">
                    Member since {new Date(profile.created_at).toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Email Preferences */}
        <div className="mb-6">
          <EmailPreferencesCard />
        </div>

        {/* Account/Department Information */}
        <div className="card mb-6">
          <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border-subtle)' }}>
            <h2 className="text-xl font-semibold text-primary flex items-center gap-2">
              <Building2 className="w-5 h-5" />
              {departmentLabel} Information
            </h2>
          </div>
          <div className="px-6 py-4 space-y-4">
            <div>
              <label className="text-sm font-medium text-tertiary">{departmentLabel} Name</label>
              <p className="text-base text-primary mt-1">{department?.name || 'N/A'}</p>
            </div>
            {!isIndividualTier && (
              <div>
                <label className="text-sm font-medium text-tertiary">Institution</label>
                <p className="text-base text-primary mt-1">{department?.institution || 'N/A'}</p>
              </div>
            )}
            <div>
              <label className="text-sm font-medium text-tertiary">Workspace Type</label>
              <div className="mt-1">
                <span
                  className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium"
                  style={{
                    backgroundColor: 'var(--surface-accent)',
                    color: 'var(--content-accent)',
                  }}
                >
                  <Shield className="w-4 h-4 mr-1" />
                  {tierDisplayName}
                </span>
              </div>
            </div>
            {!isIndividualTier && (
              <div>
                <label className="text-sm font-medium text-tertiary">{departmentLabel} ID</label>
                <p className="text-sm text-secondary mt-1 font-mono">{department?.id || 'N/A'}</p>
              </div>
            )}
          </div>
        </div>

        {/* Workspace Features */}
        <div className="card mb-6">
          <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border-subtle)' }}>
            <h2 className="text-xl font-semibold text-primary flex items-center gap-2">
              <Shield className="w-5 h-5" />
              Features
            </h2>
            <p className="text-sm text-tertiary mt-1">
              Features available in this {departmentLabel.toLowerCase()}
            </p>
          </div>
          <div className="px-6 py-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <h3 className="text-sm font-medium text-secondary mb-3">Core Features</h3>
                <div className="space-y-2">
                  <FeatureItem label="Document Scanning" available={true} />
                  <FeatureItem label="Auto-Remediation" available={true} />
                  <FeatureItem label="Bulk Upload" available={showBulkUpload} />
                  <FeatureItem label="LMS Integrations" available={showIntegrations} />
                  <FeatureItem label="Team Collaboration" available={showTeamFeatures} />
                  <FeatureItem label="Accessibility Evidence Reports" available={showEvidenceReports} />
                </div>
              </div>
              <div>
                <h3 className="text-sm font-medium text-secondary mb-3">Advanced Features</h3>
                <div className="space-y-2">
                  <FeatureItem label="Video/Audio Processing" available={showVideoProcessing} />
                  <FeatureItem label="Bulk API Access" available={showBulkAPI} />
                  <FeatureItem label="Bring Your Own API Keys" available={showCustomAPIKeys} />
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* API Key Management */}
        {authMethod === 'lti' ? (
          <section className="card mb-6" aria-labelledby="api-key-management-unavailable-title">
            <div className="px-6 py-5">
              <h2 id="api-key-management-unavailable-title" className="text-xl font-semibold text-primary">
                API Key Management
              </h2>
              <p className="text-sm text-tertiary mt-2">
                API key management is unavailable in an LTI launch session.
              </p>
            </div>
          </section>
        ) : <APIKeyManagementCard />}

        {/* Security - Active Sessions */}
        <ActiveSessionsCard
          sessions={sessions}
          loadingSessions={loadingSessions}
          revokingSession={revokingSession}
          revokingAll={revokingAll}
          onRevokeSession={handleRevokeSession}
          onRevokeAllOther={handleRevokeAllOtherSessions}
        />

        {/* AI Provider Settings */}
        <AIProvidersCard
          showAIProviderSettings={showAIProviderSettings}
          providers={providers}
          primaryProvider={primaryProvider}
          fallbackProvider={fallbackProvider}
          loadingProviders={loadingProviders}
          testingProvider={testingProvider}
          onTestProvider={handleTestProvider}
          onSetPrimary={handleSetPrimary}
        />

        {/* Account Actions */}
        <div className="card">
          <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border-subtle)' }}>
            <h2 className="text-xl font-semibold text-primary flex items-center gap-2">
              <User className="w-5 h-5" />
              Account Actions
            </h2>
          </div>
          <div className="px-6 py-4">
            <button
              onClick={handleLogout}
              className="flex items-center gap-2 px-4 py-2 rounded-lg transition-colors"
              style={{
                backgroundColor: 'var(--content-error)',
                color: 'var(--content-inverse)'
              }}
              onMouseEnter={(e: ReactMouseEvent<HTMLButtonElement>) => {
                e.currentTarget.style.opacity = '0.9';
              }}
              onMouseLeave={(e: ReactMouseEvent<HTMLButtonElement>) => {
                e.currentTarget.style.opacity = '1';
              }}
            >
              <LogOut className="w-4 h-4" />
              Sign Out
            </button>
            <p className="text-xs text-tertiary mt-2">
              {authMethod === 'session'
                ? 'You can sign in again with a new magic link.'
                : 'You will need a valid API key to access the dashboard again.'}
            </p>
          </div>
        </div>

        {/* Danger Zone */}
        <div className="card mt-6">
          <div
            className="px-6 py-4 border-b"
            style={{ borderColor: 'var(--content-error)' }}
          >
            <h2
              className="text-xl font-semibold flex items-center gap-2"
              style={{ color: 'var(--content-error)' }}
            >
              <AlertTriangle className="w-5 h-5" />
              Danger Zone
            </h2>
          </div>
          <div className="px-6 py-5 space-y-4">
            {/* Pending deletion banner */}
            {deletionStatus?.deletion_pending && (
              <div
                className="rounded-lg p-4 border"
                style={{
                  backgroundColor: 'var(--surface-error-subtle, #fef2f2)',
                  borderColor: 'var(--content-error)',
                }}
              >
                <div className="flex items-start justify-between">
                  <div>
                    <p
                      className="text-sm font-medium"
                      style={{ color: 'var(--content-error)' }}
                    >
                      Account deletion scheduled
                    </p>
                    <p className="text-xs text-secondary mt-1">
                      Your data will be permanently deleted
                      {deletionStatus.days_remaining != null && (
                        <> in <strong>{deletionStatus.days_remaining} day{deletionStatus.days_remaining !== 1 ? 's' : ''}</strong></>
                      )}
                      {deletionStatus.scheduled_for && (
                        <> on {new Date(deletionStatus.scheduled_for).toLocaleDateString()}</>
                      )}.
                    </p>
                  </div>
                  {deletionStatus.can_cancel && (
                    <button
                      onClick={handleCancelDeletion}
                      className="px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors"
                      style={{
                        borderColor: 'var(--content-error)',
                        color: 'var(--content-error)',
                      }}
                    >
                      Cancel Deletion
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Export data */}
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-primary">Export My Data</p>
                <p className="text-xs text-tertiary">
                  Download all your data as JSON (GDPR Article 20).
                </p>
              </div>
              <button
                onClick={handleExportData}
                disabled={exporting}
                className="flex items-center gap-2 px-4 py-2 rounded-lg border text-sm font-medium transition-colors disabled:opacity-50"
                style={{
                  borderColor: 'var(--border-primary)',
                  color: 'var(--content-primary)',
                }}
              >
                {exporting ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Download className="w-4 h-4" />
                )}
                Export
              </button>
            </div>

            <hr style={{ borderColor: 'var(--border-subtle)' }} />

            {/* Deactivate account */}
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-primary">Deactivate Account</p>
                <p className="text-xs text-tertiary">
                  Disable your account and revoke all sessions. Re-registration blocked for 90 days.
                </p>
              </div>
              <button
                onClick={handleDeactivate}
                disabled={deactivating || deletionStatus?.deletion_pending}
                className="flex items-center gap-2 px-4 py-2 rounded-lg border text-sm font-medium transition-colors disabled:opacity-50"
                style={{
                  borderColor: 'var(--content-error)',
                  color: 'var(--content-error)',
                }}
              >
                {deactivating ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Shield className="w-4 h-4" />
                )}
                Deactivate
              </button>
            </div>

            <hr style={{ borderColor: 'var(--border-subtle)' }} />

            {/* Delete account */}
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-primary">Delete Account</p>
                <p className="text-xs text-tertiary">
                  Permanently delete all your data after a 30-day grace period. This cannot be undone.
                </p>
              </div>
              <button
                onClick={() => {
                  trackEvent('dash-account-delete-initiated', {});
                  setShowDeleteModal(true);
                }}
                disabled={deletionStatus?.deletion_pending}
                className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
                style={{
                  backgroundColor: 'var(--content-error)',
                  color: 'var(--content-inverse)',
                }}
              >
                <Trash2 className="w-4 h-4" />
                Delete Account
              </button>
            </div>
          </div>
        </div>

        {/* Usage Information */}
        <div
          className="rounded-lg p-4 mt-6 border"
          style={{
            backgroundColor: 'var(--surface-info-subtle)',
            borderColor: 'var(--content-info)',
            color: 'var(--content-info)'
          }}
        >
          <div className="flex items-start gap-2">
            <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium">Need Help?</p>
              <p className="text-xs mt-1 opacity-90">
                Contact your system administrator or visit our documentation for support with API keys and account management.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Account Deletion Modal */}
      <AccountDeletionModal
        isOpen={showDeleteModal}
        onClose={() => setShowDeleteModal(false)}
        onDeleted={() => {
          setShowDeleteModal(false);
          fetchDeletionStatus();
        }}
      />
    </div>
  );
}
