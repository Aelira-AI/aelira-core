/// <reference types="vite/client" />

import axios, {
  AxiosInstance,
  AxiosError,
  InternalAxiosRequestConfig,
  AxiosResponse,
} from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 30000, // 30 second timeout for long-running operations
  withCredentials: true, // Enable cookies for session-based auth
});

let refreshPromise: Promise<void> | null = null;
let terminalLogoutPromise: Promise<void> | null = null;
let credentialsCleared = false;
let terminalRedirected = false;

// Extend InternalAxiosRequestConfig to include our _retry flag
interface RetryableRequestConfig extends InternalAxiosRequestConfig {
  _retry?: boolean;
  _apiKeyAuth?: boolean;
}

// Add auth token to all requests (for API key auth fallback)
const CSRF_SAFE_METHODS = new Set(['get', 'head', 'options', 'trace']);

function readCookie(name: string): string | null {
  const match = document.cookie.match(
    new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()[\]\\/+^])/g, '\\$1') + '=([^;]*)')
  );
  return match ? decodeURIComponent(match[1]) : null;
}

apiClient.interceptors.request.use((config: RetryableRequestConfig) => {
  const apiKey = localStorage.getItem('apiKey');
  if (apiKey && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${apiKey}`;
    config._apiKeyAuth = true;
  }

  // CSRF: on cookie-authenticated (no Bearer) state-changing requests, echo
  // the double-submit token the server set. The server enforces this on all
  // cookie-authed dashboard mutations; Bearer/API-key requests are exempt
  // server-side, so we skip the header when an Authorization header is set.
  const method = (config.method || 'get').toLowerCase();
  if (!CSRF_SAFE_METHODS.has(method) && !config.headers.Authorization) {
    const csrfToken = readCookie('csrf_token');
    if (csrfToken) {
      config.headers['X-CSRF-Token'] = csrfToken;
    }
  }
  return config;
});

const AUTH_ENDPOINTS = [
  '/auth/session/validate',
  '/auth/session/refresh',
  '/auth/session/logout',
  '/auth/validate',
  '/auth/magic-link/request',
  '/auth/magic-link/check',
  '/auth/magic-link/verify',
  '/auth/google/login',
  '/auth/google/callback',
  '/auth/microsoft/login',
  '/auth/microsoft/callback',
];

function isAuthEndpoint(url: string | undefined): boolean {
  return AUTH_ENDPOINTS.some((endpoint) => url?.includes(endpoint));
}

function terminateSession(): Promise<void> {
  if (terminalLogoutPromise) {
    return terminalLogoutPromise;
  }

  if (!credentialsCleared) {
    localStorage.removeItem('apiKey');
    credentialsCleared = true;
  }

  const csrfToken = readCookie('csrf_token');
  terminalLogoutPromise = axios
    .post('/auth/session/logout', undefined, {
      baseURL: API_BASE_URL,
      withCredentials: true,
      timeout: 5000,
      headers: csrfToken ? { 'X-CSRF-Token': csrfToken } : undefined,
    })
    .catch(() => undefined)
    .then(() => {
      if (!terminalRedirected) {
        terminalRedirected = true;
        window.location.replace('/login?expired=1');
      }
    });

  return terminalLogoutPromise;
}

function refreshSession(): Promise<void> {
  if (!refreshPromise) {
    refreshPromise = apiClient
      .post('/auth/session/refresh')
      .then(() => undefined)
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

// Handle 401 errors with automatic token refresh
apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as RetryableRequestConfig | undefined;

    if (!originalRequest || error.response?.status !== 401) {
      return Promise.reject(error);
    }

    // Auth endpoints must reject directly so they cannot recursively refresh or terminate.
    if (isAuthEndpoint(originalRequest.url)) {
      return Promise.reject(error);
    }

    // A stored API key is dashboard authentication: a 401 is terminal. Other
    // explicit Bearer tokens (for example LTI) belong to their caller and must
    // not clear dashboard credentials or redirect the whole application.
    if (originalRequest._apiKeyAuth) {
      void terminateSession();
      return Promise.reject(error);
    }
    if (originalRequest.headers.Authorization) {
      return Promise.reject(error);
    }

    // Once terminal logout starts, later cookie failures join that terminal
    // path. Clearing the API key must not make them start a new refresh while
    // the redirect is still pending.
    if (terminalLogoutPromise) {
      void terminateSession();
      return Promise.reject(error);
    }

    // Set this before joining the shared refresh. Every waiter may retry once,
    // but none can start a second refresh if that retry is also unauthorized.
    if (originalRequest._retry) {
      void terminateSession();
      return Promise.reject(error);
    }
    originalRequest._retry = true;

    try {
      await refreshSession();
      return apiClient(originalRequest);
    } catch (refreshError) {
      void terminateSession();
      return Promise.reject(refreshError);
    }
  }
);

// ============================================================================
// Typed API Helper Functions
// ============================================================================

/**
 * Make a typed GET request
 */
export async function get<T>(url: string, config?: InternalAxiosRequestConfig): Promise<T> {
  const response = await apiClient.get<T>(url, config);
  return response.data;
}

/**
 * Make a typed POST request
 */
export async function post<T, D = unknown>(
  url: string,
  data?: D,
  config?: InternalAxiosRequestConfig
): Promise<T> {
  const response = await apiClient.post<T>(url, data, config);
  return response.data;
}

/**
 * Make a typed PUT request
 */
export async function put<T, D = unknown>(
  url: string,
  data?: D,
  config?: InternalAxiosRequestConfig
): Promise<T> {
  const response = await apiClient.put<T>(url, data, config);
  return response.data;
}

/**
 * Make a typed DELETE request
 */
export async function del<T>(url: string, config?: InternalAxiosRequestConfig): Promise<T> {
  const response = await apiClient.delete<T>(url, config);
  return response.data;
}

/**
 * Make a typed PATCH request
 */
export async function patch<T, D = unknown>(
  url: string,
  data?: D,
  config?: InternalAxiosRequestConfig
): Promise<T> {
  const response = await apiClient.patch<T>(url, data, config);
  return response.data;
}

export default apiClient;
