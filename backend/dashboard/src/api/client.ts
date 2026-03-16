/// <reference types="vite/client" />

import axios, {
  AxiosInstance,
  AxiosError,
  InternalAxiosRequestConfig,
  AxiosResponse,
} from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'https://api.aelira.ai';

export const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 30000, // 30 second timeout for long-running operations
  withCredentials: true, // Enable cookies for session-based auth
});

// Track if we're currently refreshing to prevent multiple refresh calls
let isRefreshing = false;

interface QueuedRequest {
  resolve: () => void;
  reject: (error: unknown) => void;
}

let failedQueue: QueuedRequest[] = [];

const processQueue = (error: unknown): void => {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve();
    }
  });
  failedQueue = [];
};

// Extend InternalAxiosRequestConfig to include our _retry flag
interface RetryableRequestConfig extends InternalAxiosRequestConfig {
  _retry?: boolean;
}

// Add auth token to all requests (for API key auth fallback)
apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const apiKey = localStorage.getItem('apiKey');
  if (apiKey && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${apiKey}`;
  }
  return config;
});

// Handle 401 errors with automatic token refresh
apiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as RetryableRequestConfig | undefined;

    // If no config or error is not 401 or request has already been retried, reject
    if (!originalRequest || error.response?.status !== 401 || originalRequest._retry) {
      return Promise.reject(error);
    }

    // Don't try to refresh if we're on auth endpoints
    const authEndpoints = [
      '/auth/session/validate', // Prevent redirect loop on initial auth check
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

    if (authEndpoints.some((endpoint) => originalRequest.url?.includes(endpoint))) {
      return Promise.reject(error);
    }

    // If we're already refreshing, queue this request
    if (isRefreshing) {
      return new Promise<void>((resolve, reject) => {
        failedQueue.push({ resolve, reject });
      })
        .then(() => apiClient(originalRequest))
        .catch((err) => Promise.reject(err));
    }

    originalRequest._retry = true;
    isRefreshing = true;

    try {
      // Try to refresh the session
      await apiClient.post('/auth/session/refresh');
      processQueue(null);
      isRefreshing = false;

      // Retry the original request
      return apiClient(originalRequest);
    } catch (refreshError) {
      processQueue(refreshError);
      isRefreshing = false;

      // Clear API key if present (session refresh failed)
      localStorage.removeItem('apiKey');

      // Redirect to login
      window.location.href = '/login';
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
