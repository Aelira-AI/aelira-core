import React, { createContext, useContext, useState, useCallback, useMemo, ReactNode } from 'react';
import { X, CheckCircle, AlertCircle, AlertTriangle, Info, LucideIcon } from 'lucide-react';

// ============================================================================
// Types
// ============================================================================

export type ToastType = 'success' | 'error' | 'warning' | 'info';

export interface Toast {
  id: number;
  type: ToastType;
  title?: string;
  message: string;
}

export interface ToastOptions {
  type?: ToastType;
  title?: string;
  message: string;
  duration?: number;
}

export interface ToastContextType {
  success: (message: string, title?: string) => number;
  error: (message: string, title?: string) => number;
  warning: (message: string, title?: string) => number;
  info: (message: string, title?: string) => number;
  custom: (options: ToastOptions) => number;
  /** Legacy convenience: showToast(message, type) */
  showToast: (message: string, type?: 'success' | 'error' | 'warning' | 'info') => number;
}

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
      role="alert"
      aria-live="polite"
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

// ============================================================================
// Context & Provider
// ============================================================================

const ToastContext = createContext<ToastContextType | null>(null);

export function ToastProvider({ children }: ToastProviderProps): React.ReactElement {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const removeToast = useCallback((id: number): void => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  }, []);

  const addToast = useCallback(
    ({ type = 'info', title, message, duration = 5000 }: ToastOptions): number => {
      const id = Date.now() + Math.random();

      setToasts((prev) => [...prev, { id, type, title, message }]);

      // Auto-remove after duration
      if (duration > 0) {
        setTimeout(() => {
          removeToast(id);
        }, duration);
      }

      return id;
    },
    [removeToast]
  );

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
      >
        {toasts.map((t) => (
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
      `}</style>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextType {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}
