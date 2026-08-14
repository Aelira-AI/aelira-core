import React, {
  useState,
  useEffect,
  useCallback,
  ReactNode,
} from 'react';
import { apiClient } from '../api/client';
import type { User, Department } from '../types';
import { AuthContext } from './auth-context';
import type { AuthMethod, LoginResult } from './auth-context';

interface AuthProviderProps {
  children: ReactNode;
}

interface ValidateResponse {
  department: Department;
  user: User;
}

export function AuthProvider({ children }: AuthProviderProps): React.ReactElement {
  // Support both session-based auth (cookies) and API key auth (localStorage)
  const [apiKey, setApiKey] = useState<string | null>(() => localStorage.getItem('apiKey'));
  const [department, setDepartment] = useState<Department | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [authMethod, setAuthMethod] = useState<AuthMethod>(null);

  // Validate session (cookie-based auth)
  const validateSession = useCallback(async (): Promise<boolean> => {
    try {
      const response = await apiClient.get<ValidateResponse>('/auth/session/validate');
      setDepartment(response.data.department);
      setUser(response.data.user);
      setAuthMethod('session');
      return true;
    } catch {
      // Session not valid
      return false;
    }
  }, []);

  // Validate API key (legacy auth)
  const validateApiKey = useCallback(async (key: string): Promise<boolean> => {
    try {
      const response = await apiClient.get<ValidateResponse>('/auth/validate', {
        headers: { Authorization: `Bearer ${key}` },
      });
      setDepartment(response.data.department);
      setUser(response.data.user);
      setAuthMethod('api_key');
      return true;
    } catch (error) {
      const axiosError = error as { response?: { status?: number } };
      console.warn('API key validation failed:', axiosError.response?.status);
      localStorage.removeItem('apiKey');
      setApiKey(null);
      return false;
    }
  }, []);

  // Initialize auth state
  useEffect(() => {
    const initAuth = async (): Promise<void> => {
      // First, try session-based auth (cookies)
      const hasSession = await validateSession();

      if (hasSession) {
        setLoading(false);
        return;
      }

      // Fall back to API key auth
      if (apiKey) {
        await validateApiKey(apiKey);
      }

      setLoading(false);
    };

    initAuth();
  }, [apiKey, validateSession, validateApiKey]);

  // Login with API key (for backwards compatibility)
  const login = async (key: string): Promise<LoginResult> => {
    setLoading(true);

    try {
      const response = await apiClient.get<ValidateResponse>('/auth/validate', {
        headers: { Authorization: `Bearer ${key}` },
      });
      setApiKey(key);
      setDepartment(response.data.department);
      setUser(response.data.user);
      setAuthMethod('api_key');
      localStorage.setItem('apiKey', key);
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
      await apiClient.post('/auth/session/refresh');
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
    localStorage.removeItem('apiKey');

    // If session-based, revoke on server
    try {
      await apiClient.post('/auth/session/logout');
    } catch (error) {
      // Ignore errors - we're logging out anyway
      console.warn('Logout request failed:', error);
    }
  };

  // Check if user is authenticated
  const isAuthenticated = !!(user && (authMethod === 'session' || authMethod === 'api_key'));

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
