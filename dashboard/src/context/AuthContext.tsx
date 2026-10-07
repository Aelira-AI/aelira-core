import React, {
  useState,
  useEffect,
  useCallback,
  ReactNode,
} from 'react';
import { apiClient, clearStoredApiKeyAuth, setDashboardApiKey } from '../api/client';
import type { User, Department } from '../types';
import { AuthContext } from './auth-context';
import type { AuthMethod, LoginResult } from './auth-context';

interface AuthProviderProps {
  children: ReactNode;
}

interface ValidateResponse {
  department: Department;
  user: User;
  auth_method: 'session' | 'lti';
}

export function AuthProvider({ children }: AuthProviderProps): React.ReactElement {
  const isExpiredLogin =
    window.location.pathname === '/login' && window.location.search === '?expired=1';
  // API-key compatibility sign-in is memory-only and requires re-entry after reload.
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [department, setDepartment] = useState<Department | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [authMethod, setAuthMethod] = useState<AuthMethod>(null);

  // Validate session (cookie-based auth)
  const validateSession = useCallback(async (): Promise<boolean> => {
    try {
      const response = await apiClient.get<ValidateResponse>('/auth/session/validate', {
        _skipApiKeyAuth: true,
      });
      if (response.data.auth_method === 'session') {
        clearStoredApiKeyAuth();
        setApiKey(null);
      }
      setDepartment(response.data.department);
      setUser(response.data.user);
      setAuthMethod(response.data.auth_method);
      return true;
    } catch {
      // Session not valid
      return false;
    }
  }, []);

  // Initialize auth state
  useEffect(() => {
    const initAuth = async (): Promise<void> => {
      if (isExpiredLogin) {
        clearStoredApiKeyAuth();
        setApiKey(null);
        setDepartment(null);
        setUser(null);
        setAuthMethod(null);
        setLoading(false);
        return;
      }

      // First, try session-based auth (cookies)
      const hasSession = await validateSession();

      if (hasSession) {
        setLoading(false);
        return;
      }

      setLoading(false);
    };

    initAuth();
  }, [isExpiredLogin, validateSession]);

  // Login with API key (for backwards compatibility)
  const login = async (key: string): Promise<LoginResult> => {
    setLoading(true);

    try {
      const response = await apiClient.get<ValidateResponse>('/auth/validate', {
        headers: { Authorization: `Bearer ${key}` },
      });
      setDashboardApiKey(key);
      setApiKey(key);
      setDepartment(response.data.department);
      setUser(response.data.user);
      setAuthMethod('api_key');
      setLoading(false);
      return { success: true };
    } catch (error) {
      setLoading(false);
      const axiosError = error as {
        response?: { data?: { detail?: string } };
        message?: string;
      };
      const errorMessage =
        axiosError.response?.data?.detail ||
        axiosError.message ||
        'Invalid API key. Please check your key and try again.';
      return {
        success: false,
        error: errorMessage,
      };
    }
  };

  // Refresh session tokens
  const refreshSession = async (): Promise<boolean> => {
    try {
      await apiClient.post('/auth/session/refresh', undefined, { _skipApiKeyAuth: true });
      return true;
    } catch (error) {
      const axiosError = error as { response?: { status?: number } };
      console.warn('Session refresh failed:', axiosError.response?.status);
      return false;
    }
  };

  // Logout
  const logout = async (): Promise<void> => {
    // Clear local state
    setApiKey(null);
    setDepartment(null);
    setUser(null);
    setAuthMethod(null);
    clearStoredApiKeyAuth();

    // If session-based, revoke on server
    try {
      await apiClient.post('/auth/session/logout', undefined, { _skipApiKeyAuth: true });
    } catch (error) {
      // Ignore errors - we're logging out anyway
      console.warn('Logout request failed:', error);
    }
  };

  // Check if user is authenticated
  const isAuthenticated = !!(
    user && (authMethod === 'session' || authMethod === 'lti' || authMethod === 'api_key')
  );

  return (
    <AuthContext.Provider
      value={{
        apiKey,
        department,
        user,
        loading,
        authMethod,
        isAuthenticated,
        login,
        logout,
        refreshSession,
        validateSession,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
