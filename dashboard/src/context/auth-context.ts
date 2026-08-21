/**
 * Auth context object and hook.
 *
 * Kept separate from AuthContext.tsx (the provider component) so that file
 * only exports a component — a requirement for Vite fast refresh.
 */

import { createContext, useContext } from 'react';
import type { User, Department } from '../types';

export type AuthMethod = 'session' | 'lti' | 'api_key' | null;

export interface LoginResult {
  success: boolean;
  error?: string;
}

export interface AuthContextType {
  apiKey: string | null;
  department: Department | null;
  user: User | null;
  loading: boolean;
  authMethod: AuthMethod;
  isAuthenticated: boolean;
  login: (key: string) => Promise<LoginResult>;
  logout: () => Promise<void>;
  refreshSession: () => Promise<boolean>;
  validateSession: () => Promise<boolean>;
}

export const AuthContext = createContext<AuthContextType | null>(null);

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}
