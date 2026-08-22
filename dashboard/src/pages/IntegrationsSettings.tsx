import React, { useState, useEffect, ChangeEvent, KeyboardEvent } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, Bell, Mail, Save, Plus, X, Loader2, Check } from 'lucide-react';
import { apiClient } from '../api/client';

interface AlertSettings {
  alert_on_scan_complete: boolean;
  alert_on_critical_issues: boolean;
  alert_weekly_summary: boolean;
  weekly_summary_day: number;
  weekly_summary_hour: number;
  email_addresses: string[];
  is_paused: boolean;
}

export function IntegrationsSettings(): React.ReactElement {
  const [settings, setSettings] = useState<AlertSettings>({
    alert_on_scan_complete: true,
    alert_on_critical_issues: true,
    alert_weekly_summary: true,
    weekly_summary_day: 0,
    weekly_summary_hour: 9,
    email_addresses: [],
    is_paused: false
  });
  const [loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);
  const [newEmail, setNewEmail] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const daysOfWeek: string[] = [
    'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'
  ];

  useEffect(() => {
    fetchSettings();
  }, []);

  const fetchSettings = async (): Promise<void> => {
    try {
      const response = await apiClient.get('/alerts/settings');
      setSettings(response.data);
    } catch (err) {
      console.error('Failed to fetch alert settings:', err);
      setError('Failed to load settings');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (): Promise<void> => {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      await apiClient.put('/alerts/settings', settings);
      setSuccess('Settings saved successfully');
      setTimeout(() => setSuccess(null), 3000);
    } catch {
      setError('Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const handleAddEmail = async (): Promise<void> => {
    if (!newEmail || !newEmail.includes('@')) {
      setError('Please enter a valid email address');
      return;
    }

    try {
      await apiClient.post('/alerts/emails/add', { email: newEmail });
      setSettings(prev => ({
        ...prev,
        email_addresses: [...prev.email_addresses, newEmail]
      }));
      setNewEmail('');
    } catch {
      setError('Failed to add email address');
    }
  };

  const handleRemoveEmail = async (email: string): Promise<void> => {
    try {
      await apiClient.post('/alerts/emails/remove', { email });
      setSettings(prev => ({
        ...prev,
        email_addresses: prev.email_addresses.filter(e => e !== email)
      }));
    } catch {
      setError('Failed to remove email address');
    }
  };

  const handleSendTest = async (): Promise<void> => {
    try {
      await apiClient.post('/alerts/test');
      setSuccess('Test email sent successfully');
      setTimeout(() => setSuccess(null), 3000);
    } catch {
      setError('Failed to send test email');
    }
  };

  const togglePause = async (): Promise<void> => {
    try {
      if (settings.is_paused) {
        await apiClient.post('/alerts/resume');
      } else {
        await apiClient.post('/alerts/pause');
      }
      setSettings(prev => ({ ...prev, is_paused: !prev.is_paused }));
    } catch {
      setError('Failed to update pause status');
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === 'Enter') {
      handleAddEmail();
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64" role="status" aria-label="Loading alert settings">
        <Loader2 className="w-8 h-8 animate-spin" style={{ color: 'var(--accent)' }} aria-hidden="true" />
        <span className="sr-only">Loading alert settings...</span>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      {/* Back link */}
      <Link
        to="/integrations"
        className="inline-flex items-center gap-2 text-sm mb-6 hover:underline"
        style={{ color: 'var(--content-secondary)' }}
      >
        <ArrowLeft className="w-4 h-4" aria-hidden="true" />
        Back to Integrations
      </Link>

      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <div
          className="w-12 h-12 rounded-xl flex items-center justify-center"
          style={{ backgroundColor: 'var(--accent-solid)' }}
        >
          <Bell className="w-6 h-6 text-white" aria-hidden="true" />
        </div>
        <div>
          <h1 className="text-2xl font-bold" style={{ color: 'var(--content-primary)' }}>
            Alert Settings
          </h1>
          <p style={{ color: 'var(--content-secondary)' }}>
            Configure email notifications for cloud scanning
          </p>
        </div>
      </div>

      {/* Messages */}
      {error && (
        <div
          className="mb-6 p-4 rounded-lg flex items-center gap-3"
          style={{ backgroundColor: 'var(--status-error-bg)', color: 'var(--status-error-text)' }}
        >
          <X className="w-5 h-5" aria-hidden="true" />
          {error}
          <button onClick={() => setError(null)} className="ml-auto">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {success && (
        <div
          className="mb-6 p-4 rounded-lg flex items-center gap-3"
          style={{ backgroundColor: 'var(--status-success-bg)', color: 'var(--status-success-text)' }}
        >
          <Check className="w-5 h-5" />
          {success}
        </div>
      )}

      {/* Settings form */}
      <div
        className="rounded-xl border p-6 space-y-6"
        style={{ backgroundColor: 'var(--surface-secondary)', borderColor: 'var(--border-primary)' }}
      >
        {/* Pause toggle */}
        <div className="flex items-center justify-between pb-4 border-b" style={{ borderColor: 'var(--border-primary)' }}>
          <div>
            <h3 className="font-medium" style={{ color: 'var(--content-primary)' }}>
              Pause All Alerts
            </h3>
            <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
              Temporarily stop all email notifications
            </p>
          </div>
          <button
            onClick={togglePause}
            className="relative w-12 h-6 rounded-full transition-colors"
            style={{ backgroundColor: settings.is_paused ? 'var(--content-tertiary)' : 'var(--accent-solid)' }}
          >
            <span
              className={`absolute top-1 w-4 h-4 rounded-full bg-white transition-transform ${
                settings.is_paused ? 'left-1' : 'left-7'
              }`}
            />
          </button>
        </div>

        {/* Notification types */}
        <div>
          <h3 className="font-medium mb-4" style={{ color: 'var(--content-primary)' }}>
            Notification Types
          </h3>
          <div className="space-y-4">
            <label className="flex items-center justify-between">
              <div>
                <span style={{ color: 'var(--content-primary)' }}>Scan Complete</span>
                <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                  Notify when a cloud file scan completes
                </p>
              </div>
              <input
                type="checkbox"
                checked={settings.alert_on_scan_complete}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setSettings(prev => ({ ...prev, alert_on_scan_complete: e.target.checked }))}
                className="w-5 h-5 rounded"
                style={{ accentColor: 'var(--accent)' }}
              />
            </label>

            <label className="flex items-center justify-between">
              <div>
                <span style={{ color: 'var(--content-primary)' }}>Critical Issues</span>
                <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                  Immediate alert when critical accessibility issues are found
                </p>
              </div>
              <input
                type="checkbox"
                checked={settings.alert_on_critical_issues}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setSettings(prev => ({ ...prev, alert_on_critical_issues: e.target.checked }))}
                className="w-5 h-5 rounded"
                style={{ accentColor: 'var(--accent)' }}
              />
            </label>

            <label className="flex items-center justify-between">
              <div>
                <span style={{ color: 'var(--content-primary)' }}>Weekly Summary</span>
                <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
                  Receive a weekly compliance report
                </p>
              </div>
              <input
                type="checkbox"
                checked={settings.alert_weekly_summary}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setSettings(prev => ({ ...prev, alert_weekly_summary: e.target.checked }))}
                className="w-5 h-5 rounded"
                style={{ accentColor: 'var(--accent)' }}
              />
            </label>
          </div>
        </div>

        {/* Weekly summary schedule */}
        {settings.alert_weekly_summary && (
          <div>
            <h3 className="font-medium mb-4" style={{ color: 'var(--content-primary)' }}>
              Weekly Summary Schedule
            </h3>
            <div className="flex gap-4">
              <div className="flex-1">
                <label className="block text-sm mb-1" style={{ color: 'var(--content-secondary)' }}>
                  Day
                </label>
                <select
                  value={settings.weekly_summary_day}
                  onChange={(e: ChangeEvent<HTMLSelectElement>) => setSettings(prev => ({ ...prev, weekly_summary_day: parseInt(e.target.value) }))}
                  className="w-full px-3 py-2 rounded-lg border"
                  style={{
                    backgroundColor: 'var(--surface-primary)',
                    borderColor: 'var(--border-primary)',
                    color: 'var(--content-primary)'
                  }}
                >
                  {daysOfWeek.map((day, index) => (
                    <option key={day} value={index}>{day}</option>
                  ))}
                </select>
              </div>
              <div className="flex-1">
                <label className="block text-sm mb-1" style={{ color: 'var(--content-secondary)' }}>
                  Time (UTC)
                </label>
                <select
                  value={settings.weekly_summary_hour}
                  onChange={(e: ChangeEvent<HTMLSelectElement>) => setSettings(prev => ({ ...prev, weekly_summary_hour: parseInt(e.target.value) }))}
                  className="w-full px-3 py-2 rounded-lg border"
                  style={{
                    backgroundColor: 'var(--surface-primary)',
                    borderColor: 'var(--border-primary)',
                    color: 'var(--content-primary)'
                  }}
                >
                  {Array.from({ length: 24 }, (_, i) => (
                    <option key={i} value={i}>
                      {i.toString().padStart(2, '0')}:00
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        )}

        {/* Email addresses */}
        <div>
          <h3 className="font-medium mb-4" style={{ color: 'var(--content-primary)' }}>
            Email Recipients
          </h3>
          <div className="space-y-3">
            {settings.email_addresses.map((email) => (
              <div
                key={email}
                className="flex items-center justify-between px-3 py-2 rounded-lg"
                style={{ backgroundColor: 'var(--surface-primary)' }}
              >
                <div className="flex items-center gap-2">
                  <Mail className="w-4 h-4" style={{ color: 'var(--content-secondary)' }} />
                  <span style={{ color: 'var(--content-primary)' }}>{email}</span>
                </div>
                <button
                  onClick={() => handleRemoveEmail(email)}
                  className="p-1 rounded hover:bg-opacity-50"
                  style={{ color: 'var(--status-error-text)' }}
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            ))}

            <div className="flex gap-2">
              <input
                type="email"
                value={newEmail}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setNewEmail(e.target.value)}
                placeholder="Add email address"
                className="flex-1 px-3 py-2 rounded-lg border"
                style={{
                  backgroundColor: 'var(--surface-primary)',
                  borderColor: 'var(--border-primary)',
                  color: 'var(--content-primary)'
                }}
                onKeyDown={handleKeyDown}
              />
              <button
                onClick={handleAddEmail}
                className="px-4 py-2 rounded-lg text-white"
                style={{ backgroundColor: 'var(--accent-solid)' }}
              >
                <Plus className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className="flex gap-3 mt-6">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-lg text-white font-medium disabled:opacity-50"
          style={{ backgroundColor: 'var(--accent-solid)' }}
        >
          {saving ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              Saving...
            </>
          ) : (
            <>
              <Save className="w-4 h-4" />
              Save Settings
            </>
          )}
        </button>
        <button
          onClick={handleSendTest}
          className="px-4 py-3 rounded-lg font-medium border"
          style={{
            borderColor: 'var(--border-primary)',
            color: 'var(--content-primary)',
            backgroundColor: 'var(--surface-secondary)'
          }}
        >
          Send Test Email
        </button>
      </div>
    </div>
  );
}

export default IntegrationsSettings;
