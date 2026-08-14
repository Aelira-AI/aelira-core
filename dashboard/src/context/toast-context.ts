/**
 * Toast context object, types, and hook.
 *
 * Kept separate from ToastContext.tsx (the provider component) so that file
 * only exports a component — a requirement for Vite fast refresh.
 */

import { createContext, useContext } from 'react';

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

export const ToastContext = createContext<ToastContextType | null>(null);

export function useToast(): ToastContextType {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}
