import React, { useState, useCallback, useMemo, useRef, useEffect, ReactNode, KeyboardEvent } from 'react';
import { X, CheckCircle, AlertCircle, AlertTriangle, Info, LucideIcon } from 'lucide-react';
import { ToastContext } from './toast-context';
import type { Toast, ToastType, ToastOptions, ToastContextType } from './toast-context';

interface ToastProviderProps {
  children: ReactNode;
}

interface ToastConfig {
  icon: LucideIcon;
  bgColor: string;
  textColor: string;
  borderColor: string;
}

interface ToastComponentProps {
  id: number;
  type: ToastType;
  title?: string;
  message: string;
  onClose: (id: number) => void;
}

// ============================================================================
// Toast Configuration
// ============================================================================

const TOAST_TYPES: Record<ToastType, ToastConfig> = {
  success: {
    icon: CheckCircle,
    bgColor: 'var(--feature-success-surface)',
    textColor: 'var(--feature-success-content)',
    borderColor: 'var(--feature-success-content)',
  },
  error: {
    icon: AlertCircle,
    bgColor: 'var(--feature-error-surface)',
    textColor: 'var(--feature-error-content)',
    borderColor: 'var(--feature-error-content)',
  },
  warning: {
    icon: AlertTriangle,
    bgColor: 'var(--feature-warning-surface)',
    textColor: 'var(--feature-warning-content)',
    borderColor: 'var(--feature-warning-content)',
  },
  info: {
    icon: Info,
    bgColor: 'var(--feature-info-surface)',
    textColor: 'var(--feature-info-content)',
    borderColor: 'var(--feature-info-content)',
  },
};

// ============================================================================
// Toast Component
// ============================================================================

function ToastComponent({ id, type, title, message, onClose }: ToastComponentProps): React.ReactElement {
  const config = TOAST_TYPES[type] || TOAST_TYPES.info;
  const Icon = config.icon;

  return (
    <div
      className="flex items-start gap-3 p-4 rounded-lg shadow-lg min-w-[320px] max-w-[420px] animate-slide-in"
      style={{
        backgroundColor: config.bgColor,
        borderLeft: `4px solid ${config.borderColor}`,
      }}
      // Errors interrupt (role="alert"); everything else is announced politely
      // by the persistent status region this toast is rendered inside.
      role={type === 'error' ? 'alert' : undefined}
      data-toast-id={id}
    >
      <Icon
        className="w-5 h-5 flex-shrink-0 mt-0.5"
        style={{ color: config.textColor }}
        aria-hidden="true"
      />
      <div className="flex-1 min-w-0">
        {title && (
          <p className="font-semibold text-sm" style={{ color: config.textColor }}>
            {title}
          </p>
        )}
        <p className="text-sm mt-0.5" style={{ color: config.textColor, opacity: 0.9 }}>
          {message}
        </p>
      </div>
      <button
        onClick={() => onClose(id)}
        className="flex-shrink-0 p-1 rounded hover:opacity-70 transition-opacity"
        style={{ color: config.textColor }}
        aria-label="Close notification"
      >
        <X className="w-4 h-4" aria-hidden="true" />
      </button>
    </div>
  );
}

interface ToastTimer {
  timeout: ReturnType<typeof setTimeout> | null;
  remaining: number;
  startedAt: number;
}

export function ToastProvider({ children }: ToastProviderProps): React.ReactElement {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef<Map<number, ToastTimer>>(new Map());
  const paused = useRef(false);

  const removeToast = useCallback((id: number): void => {
    const timer = timers.current.get(id);
    if (timer?.timeout) clearTimeout(timer.timeout);
    timers.current.delete(id);
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  }, []);

  const startTimer = useCallback((id: number, timer: ToastTimer): void => {
    timer.startedAt = Date.now();
    timer.timeout = setTimeout(() => removeToast(id), timer.remaining);
  }, [removeToast]);

  // WCAG 2.2.1: a notification must not disappear while someone is reading it
  // or reaching for its close button. Hover and keyboard focus pause every
  // countdown; leaving resumes it with the time that was left.
  const pauseTimers = useCallback((): void => {
    if (paused.current) return;
    paused.current = true;
    timers.current.forEach((timer) => {
      if (timer.timeout) clearTimeout(timer.timeout);
      timer.timeout = null;
      timer.remaining = Math.max(timer.remaining - (Date.now() - timer.startedAt), 2000);
    });
  }, []);

  const resumeTimers = useCallback((): void => {
    if (!paused.current) return;
    paused.current = false;
    timers.current.forEach((timer, id) => startTimer(id, timer));
  }, [startTimer]);

  useEffect(() => {
    const active = timers.current;
    return () => {
      active.forEach((timer) => {
        if (timer.timeout) clearTimeout(timer.timeout);
      });
      active.clear();
    };
  }, []);

  const addToast = useCallback(
    ({ type = 'info', title, message, duration = 5000 }: ToastOptions): number => {
      const id = Date.now() + Math.random();

      setToasts((prev) => [...prev, { id, type, title, message }]);

      // Auto-remove after duration
      if (duration > 0) {
        const timer: ToastTimer = { timeout: null, remaining: duration, startedAt: Date.now() };
        timers.current.set(id, timer);
        if (!paused.current) startTimer(id, timer);
      }

      return id;
    },
    [startTimer]
  );

  const handleRegionKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== 'Escape') return;
    const toastElement = (event.target as HTMLElement).closest<HTMLElement>('[data-toast-id]');
    if (toastElement) {
      event.preventDefault();
      removeToast(Number(toastElement.dataset.toastId));
    }
  };

  // Convenience methods — memoised so consumers get a stable reference
  // (prevents re-render loops when showToast appears in useEffect deps)
  const toast: ToastContextType = useMemo(() => ({
    success: (message: string, title?: string) => addToast({ type: 'success', title, message }),
    error: (message: string, title?: string) =>
      addToast({ type: 'error', title, message, duration: 8000 }),
    warning: (message: string, title?: string) => addToast({ type: 'warning', title, message }),
    info: (message: string, title?: string) => addToast({ type: 'info', title, message }),
    custom: addToast,
    showToast: (message: string, type: 'success' | 'error' | 'warning' | 'info' = 'info') =>
      addToast({ type, message }),
  }), [addToast]);

  return (
    <ToastContext.Provider value={toast}>
      {children}

      {/* Toast Container */}
      <div
        className="fixed bottom-4 right-4 z-50 flex flex-col gap-2"
        role="region"
        aria-label="Notifications"
        onMouseEnter={pauseTimers}
        onMouseLeave={resumeTimers}
        onFocus={pauseTimers}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) resumeTimers();
        }}
        onKeyDown={handleRegionKeyDown}
      >
        {/* Persistent polite live region: present before any toast is added so
            screen readers announce the insertions. Errors announce themselves
            through their own role="alert". */}
        <div role="status" aria-live="polite" className="flex flex-col gap-2">
          {toasts.filter((t) => t.type !== 'error').map((t) => (
            <ToastComponent
              key={t.id}
              id={t.id}
              type={t.type}
              title={t.title}
              message={t.message}
              onClose={removeToast}
            />
          ))}
        </div>
        <div className="flex flex-col gap-2">
          {toasts.filter((t) => t.type === 'error').map((t) => (
            <ToastComponent
              key={t.id}
              id={t.id}
              type={t.type}
              title={t.title}
              message={t.message}
              onClose={removeToast}
            />
          ))}
        </div>
      </div>

      {/* Animation styles */}
      <style>{`
        @keyframes slide-in {
          from {
            transform: translateX(100%);
            opacity: 0;
          }
          to {
            transform: translateX(0);
            opacity: 1;
          }
        }
        .animate-slide-in {
          animation: slide-in 0.3s ease-out forwards;
        }
        @media (prefers-reduced-motion: reduce) {
          .animate-slide-in {
            animation: none;
          }
        }
      `}</style>
    </ToastContext.Provider>
  );
}
