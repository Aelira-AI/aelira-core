import React, { useState, useEffect, useCallback } from 'react';
import { useToast } from '../context/toast-context';
import { apiClient } from '../api/client';
import {
  Mail,
  Bell,
  AlertTriangle,
  FileCheck2,
  Wrench,
  Calendar,
  Clock,
  Loader2,
  Save,
} from 'lucide-react';

interface EmailPreferences {
  email_scan_complete: boolean;
  email_remediation_complete: boolean;
  email_critical_alerts: boolean;
  email_weekly_summary: boolean;
  weekly_summary_day: number;
  weekly_summary_hour: number;
}

interface ToggleSwitchProps {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  disabled?: boolean;
}

const ToggleSwitch: React.FC<ToggleSwitchProps> = ({ enabled, onChange, disabled }) => (
  <button
    type="button"
    role="switch"
    aria-checked={enabled}
    disabled={disabled}
    onClick={() => onChange(!enabled)}
    className={`
      relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent
      transition-colors duration-200 ease-in-out focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2
      ${disabled ? 'opacity-50 cursor-not-allowed' : ''}
    `}
    style={{
      backgroundColor: enabled ? 'var(--content-accent)' : 'var(--surface-tertiary)',
    }}
  >
    <span
      className={`
        pointer-events-none inline-block h-5 w-5 transform rounded-full shadow-lg ring-0
        transition duration-200 ease-in-out
        ${enabled ? 'translate-x-5' : 'translate-x-0'}
      `}
      style={{ backgroundColor: 'var(--surface-primary)' }}
    />
  </button>
);

interface PreferenceItemProps {
  icon: React.ReactNode;
  label: string;
  description: string;
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  disabled?: boolean;
  children?: React.ReactNode;
}

const PreferenceItem: React.FC<PreferenceItemProps> = ({
  icon,
  label,
  description,
  enabled,
  onChange,
  disabled,
  children,
}) => (
  <div
    className="p-4 rounded-lg border transition-colors"
    style={{
      backgroundColor: enabled ? 'var(--surface-accent)' : 'var(--surface-secondary)',
      borderColor: enabled ? 'var(--border-accent)' : 'var(--border-primary)',
    }}
  >
    <div className="flex items-start justify-between gap-4">
      <div className="flex items-start gap-3">
        <div
          className="p-2 rounded-lg shrink-0"
          style={{
            backgroundColor: enabled ? 'var(--surface-accent)' : 'var(--surface-tertiary)',
          }}
        >
          {icon}
        </div>
        <div>
          <h4 className="font-medium text-primary">{label}</h4>
          <p className="text-sm text-tertiary mt-0.5">{description}</p>
        </div>
      </div>
      <ToggleSwitch enabled={enabled} onChange={onChange} disabled={disabled} />
    </div>
    {children && enabled && (
      <div className="mt-4 ml-12">{children}</div>
    )}
  </div>
);

const DAYS_OF_WEEK = [
  { value: 0, label: 'Monday' },
  { value: 1, label: 'Tuesday' },
  { value: 2, label: 'Wednesday' },
  { value: 3, label: 'Thursday' },
  { value: 4, label: 'Friday' },
  { value: 5, label: 'Saturday' },
  { value: 6, label: 'Sunday' },
];

const HOURS = Array.from({ length: 24 }, (_, i) => ({
  value: i,
  label: i === 0 ? '12:00 AM' : i < 12 ? `${i}:00 AM` : i === 12 ? '12:00 PM' : `${i - 12}:00 PM`,
}));

export default function EmailPreferencesCard(): React.ReactElement {
  const { showToast } = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [preferences, setPreferences] = useState<EmailPreferences>({
    email_scan_complete: true,
    email_remediation_complete: true,
    email_critical_alerts: true,
    email_weekly_summary: true,
    weekly_summary_day: 0,
    weekly_summary_hour: 9,
  });
  const [hasChanges, setHasChanges] = useState(false);
  const [originalPrefs, setOriginalPrefs] = useState<EmailPreferences | null>(null);

  const fetchPreferences = useCallback(async () => {
    try {
      setLoading(true);
      const response = await apiClient.get('/auth/profile/email-preferences');
      setPreferences(response.data);
      setOriginalPrefs(response.data);
    } catch (error) {
      console.error('Failed to fetch email preferences:', error);
      showToast('Failed to load email preferences', 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    fetchPreferences();
  }, [fetchPreferences]);

  const updatePreference = <K extends keyof EmailPreferences>(key: K, value: EmailPreferences[K]) => {
    const newPrefs = { ...preferences, [key]: value };
    setPreferences(newPrefs);
    setHasChanges(JSON.stringify(newPrefs) !== JSON.stringify(originalPrefs));
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      const response = await apiClient.patch('/auth/profile/email-preferences', preferences);
      setPreferences(response.data);
      setOriginalPrefs(response.data);
      setHasChanges(false);
      showToast('Email preferences saved', 'success');
    } catch (error) {
      console.error('Failed to save email preferences:', error);
      const err = error as { response?: { data?: { detail?: string } } };
      showToast(err.response?.data?.detail || 'Failed to save preferences', 'error');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="card">
        <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border-subtle)' }}>
          <h2 className="text-xl font-semibold text-primary flex items-center gap-2">
            <Mail className="w-5 h-5" />
            Email Notifications
          </h2>
        </div>
        <div className="px-6 py-8 flex items-center justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-tertiary" />
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border-subtle)' }}>
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold text-primary flex items-center gap-2">
              <Mail className="w-5 h-5" />
              Email Notifications
            </h2>
            <p className="text-sm text-tertiary mt-1">
              Control which emails you receive and when
            </p>
          </div>
          {hasChanges && (
            <button
              onClick={handleSave}
              disabled={saving}
              className="btn-primary px-4 py-2 text-sm flex items-center gap-2"
            >
              {saving ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              Save Changes
            </button>
          )}
        </div>
      </div>

      <div className="px-6 py-4 space-y-4">
        <div>
          <h3 className="text-sm font-medium text-secondary mb-3 flex items-center gap-2">
            <Bell className="w-4 h-4" />
            Scan & Remediation Alerts
          </h3>
          <div className="space-y-3">
            <PreferenceItem
              icon={<FileCheck2 className="w-5 h-5" style={{ color: 'var(--content-success)' }} />}
              label="Scan Complete"
              description="Get notified when a document scan finishes"
              enabled={preferences.email_scan_complete}
              onChange={(v) => updatePreference('email_scan_complete', v)}
            />
            <PreferenceItem
              icon={<Wrench className="w-5 h-5" style={{ color: 'var(--content-info)' }} />}
              label="Remediation Complete"
              description="Get notified when auto-remediation completes"
              enabled={preferences.email_remediation_complete}
              onChange={(v) => updatePreference('email_remediation_complete', v)}
            />
            <PreferenceItem
              icon={<AlertTriangle className="w-5 h-5" style={{ color: 'var(--content-error)' }} />}
              label="Critical Issue Alerts"
              description="Immediate alerts for critical accessibility issues"
              enabled={preferences.email_critical_alerts}
              onChange={(v) => updatePreference('email_critical_alerts', v)}
            />
          </div>
        </div>

        <div
          className="border-t pt-4"
          style={{ borderColor: 'var(--border-subtle)' }}
        >
          <h3 className="text-sm font-medium text-secondary mb-3 flex items-center gap-2">
            <Calendar className="w-4 h-4" />
            Scheduled Reports
          </h3>
          <div className="space-y-3">
            <PreferenceItem
              icon={<Calendar className="w-5 h-5" style={{ color: 'var(--content-accent)' }} />}
              label="Weekly Summary"
              description="Weekly compliance digest with scan statistics"
              enabled={preferences.email_weekly_summary}
              onChange={(v) => updatePreference('email_weekly_summary', v)}
            >
              <div className="flex flex-wrap items-center gap-4">
                <div className="flex items-center gap-2">
                  <Clock className="w-4 h-4 text-tertiary" />
                  <span className="text-sm text-secondary">Send every</span>
                </div>
                <select
                  value={preferences.weekly_summary_day}
                  onChange={(e) => updatePreference('weekly_summary_day', parseInt(e.target.value))}
                  className="input py-1.5 text-sm"
                  style={{ minWidth: '140px' }}
                >
                  {DAYS_OF_WEEK.map((day) => (
                    <option key={day.value} value={day.value}>
                      {day.label}
                    </option>
                  ))}
                </select>
                <span className="text-sm text-secondary">at</span>
                <select
                  value={preferences.weekly_summary_hour}
                  onChange={(e) => updatePreference('weekly_summary_hour', parseInt(e.target.value))}
                  className="input py-1.5 text-sm"
                  style={{ minWidth: '120px' }}
                >
                  {HOURS.map((hour) => (
                    <option key={hour.value} value={hour.value}>
                      {hour.label}
                    </option>
                  ))}
                </select>
                <span className="text-xs text-tertiary">(UTC)</span>
              </div>
            </PreferenceItem>
          </div>
        </div>

        <div
          className="mt-4 p-3 rounded-lg text-xs text-tertiary"
          style={{ backgroundColor: 'var(--surface-tertiary)' }}
        >
          <p>
            Transactional emails (password resets, security alerts) cannot be disabled.
          </p>
        </div>
      </div>
    </div>
  );
}
