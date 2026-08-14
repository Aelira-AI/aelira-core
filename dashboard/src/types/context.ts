/**
 * Context Provider Types
 * Types for React context providers
 */

import type { Department, User } from './api';

// ============================================================================
// Auth Context
// ============================================================================

export type AuthMethod = 'session' | 'api_key' | null;

export interface AuthState {
  apiKey: string | null;
  department: Department | null;
  user: User | null;
  loading: boolean;
  authMethod: AuthMethod;
  isAuthenticated: boolean;
}

export interface LoginResult {
  success: boolean;
  error?: string;
}

export interface AuthContextType extends AuthState {
  login: (key: string) => Promise<LoginResult>;
  logout: () => Promise<void>;
  refreshSession: () => Promise<boolean>;
  validateSession: () => Promise<boolean>;
}

// ============================================================================
// Theme Context
// ============================================================================

export type Theme = 'dark' | 'light';

export interface ThemeContextType {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
}

// ============================================================================
// Toast Context
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
  showToast: (message: string, type?: 'success' | 'error' | 'warning' | 'info') => number;
}
