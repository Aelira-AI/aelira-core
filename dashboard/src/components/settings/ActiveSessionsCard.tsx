import React from 'react';
import {
  Shield,
  LogOut,
  Loader2,
  Monitor,
  Smartphone,
  Clock,
  Trash2,
} from 'lucide-react';

interface Session {
  id: string;
  user_agent?: string;
  ip_address?: string;
  is_current?: boolean;
  last_used_at?: string;
  created_at: string;
}

interface ActiveSessionsCardProps {
  sessions: Session[];
  loadingSessions: boolean;
  revokingSession: string | null;
  revokingAll: boolean;
  onRevokeSession: (sessionId: string) => void;
  onRevokeAllOther: () => void;
}

function parseUserAgent(userAgent: string | undefined): { device: string; browser: string } {
  if (!userAgent) return { device: 'Unknown', browser: 'Unknown' };

  let browser = 'Unknown Browser';
  let device = 'Desktop';

  if (userAgent.includes('Chrome')) browser = 'Chrome';
  else if (userAgent.includes('Firefox')) browser = 'Firefox';
  else if (userAgent.includes('Safari')) browser = 'Safari';
  else if (userAgent.includes('Edge')) browser = 'Edge';

  if (userAgent.includes('Mobile') || userAgent.includes('Android') || userAgent.includes('iPhone')) {
    device = 'Mobile';
  } else if (userAgent.includes('Tablet') || userAgent.includes('iPad')) {
    device = 'Tablet';
  }

  return { device, browser };
}

export function ActiveSessionsCard({
  sessions,
  loadingSessions,
  revokingSession,
  revokingAll,
  onRevokeSession,
  onRevokeAllOther,
}: ActiveSessionsCardProps): React.ReactElement | null {
  if (sessions.length === 0) return null;

  return (
    <div className="card mb-6">
      <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border-subtle)' }}>
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-semibold text-primary flex items-center gap-2">
            <Shield className="w-5 h-5" />
            Active Sessions
          </h2>
          {sessions.filter(s => !s.is_current).length > 0 && (
            <button
              onClick={onRevokeAllOther}
              disabled={revokingAll}
              className="btn-secondary px-3 py-1.5 text-sm flex items-center gap-1"
              style={{ color: 'var(--content-error)' }}
            >
              {revokingAll ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <LogOut className="w-3.5 h-3.5" />
              )}
              Sign out all other devices
            </button>
          )}
        </div>
        <p className="text-sm text-tertiary mt-1">
          Manage devices where you're currently signed in
        </p>
      </div>
      <div className="px-6 py-4">
        {loadingSessions ? (
          <div className="flex items-center justify-center py-4">
            <Loader2 className="w-5 h-5 animate-spin text-tertiary" />
          </div>
        ) : (
          <div className="space-y-3">
            {sessions.map((session) => {
              const { device, browser } = parseUserAgent(session.user_agent);
              const DeviceIcon = device === 'Mobile' ? Smartphone : Monitor;

              return (
                <div
                  key={session.id}
                  className="flex items-center justify-between p-4 rounded-lg border"
                  style={{
                    backgroundColor: session.is_current ? 'var(--surface-success-subtle)' : 'var(--surface-secondary)',
                    borderColor: session.is_current ? 'var(--content-success)' : 'var(--border-primary)',
                  }}
                >
                  <div className="flex items-center gap-3">
                    <div
                      className="p-2 rounded-lg"
                      style={{ backgroundColor: 'var(--surface-tertiary)' }}
                    >
                      <DeviceIcon className="w-5 h-5 text-secondary" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-primary">{browser}</span>
                        <span className="text-sm text-tertiary">on {device}</span>
                        {session.is_current && (
                          <span
                            className="px-2 py-0.5 text-xs font-medium rounded"
                            style={{
                              backgroundColor: 'var(--content-success)',
                              color: 'var(--content-inverse)',
                            }}
                          >
                            Current
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 mt-1 text-xs text-tertiary">
                        {session.ip_address && (
                          <span>IP: {session.ip_address}</span>
                        )}
                        <span className="flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {session.last_used_at
                            ? `Last active ${new Date(session.last_used_at).toLocaleDateString()}`
                            : `Created ${new Date(session.created_at).toLocaleDateString()}`
                          }
                        </span>
                      </div>
                    </div>
                  </div>
                  {!session.is_current && (
                    <button
                      onClick={() => onRevokeSession(session.id)}
                      disabled={revokingSession === session.id}
                      className="btn-secondary px-3 py-1.5 text-sm flex items-center gap-1"
                      title="Sign out this device"
                    >
                      {revokingSession === session.id ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Trash2 className="w-4 h-4" style={{ color: 'var(--content-error)' }} />
                      )}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
