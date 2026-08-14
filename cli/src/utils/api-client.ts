/**
 * Shared API client for Aelira CLI
 * Provides retry logic, auth injection, timeout handling, and typed errors.
 */

import { getApiKey, getApiUrl } from './config.js'

// ---------------------------------------------------------------------------
// Error classes
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: string,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export class ApiConnectionError extends Error {
  constructor(
    message: string,
    public readonly retryCount: number,
  ) {
    super(message)
    this.name = 'ApiConnectionError'
  }
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RequestOptions {
  headers?: Record<string, string>
  query?: Record<string, string>
  retry?: boolean
  timeout?: number
}

// ---------------------------------------------------------------------------
// Retry helpers
// ---------------------------------------------------------------------------

const RETRYABLE_STATUSES = new Set([429, 502, 503, 504])
const RETRYABLE_ERROR_CODES = new Set(['ECONNREFUSED', 'ECONNRESET', 'ENOTFOUND', 'ETIMEDOUT'])

function isRetryableNetworkError(error: any): boolean {
  return error.name === 'AbortError' || RETRYABLE_ERROR_CODES.has(error.code)
}

/** Exponential backoff with jitter: ~1s, 2s, 4s. */
async function backoffDelay(attempt: number): Promise<void> {
  const baseDelay = 1000 * 2 ** (attempt - 1)
  const jitter = baseDelay * (0.75 + Math.random() * 0.5)
  await new Promise<void>((r) => { setTimeout(r, jitter) })
}

// ---------------------------------------------------------------------------
// ApiClient
// ---------------------------------------------------------------------------

export class ApiClient {
  private apiKey: string | undefined
  private baseUrl: string | undefined
  private readonly defaultTimeout: number | undefined
  private resolved = false

  constructor(options?: { apiKey?: string; apiUrl?: string; timeout?: number }) {
    this.baseUrl = options?.apiUrl ?? process.env.AELIRA_API_URL
    this.apiKey = options?.apiKey ?? process.env.AELIRA_API_KEY
    this.defaultTimeout = options?.timeout
  }

  // -------------------------------------------------------------------------
  // Lazy config resolution
  // -------------------------------------------------------------------------

  async delete(path: string, options?: RequestOptions): Promise<Response> {
    return this.request('DELETE', path, options)
  }

  // -------------------------------------------------------------------------
  // Core request method
  // -------------------------------------------------------------------------

  async get(path: string, options?: RequestOptions): Promise<Response> {
    return this.request('GET', path, options)
  }

  // -------------------------------------------------------------------------
  // Public convenience methods
  // -------------------------------------------------------------------------

  async getJson<T>(path: string, options?: RequestOptions): Promise<T> {
    const response = await this.get(path, options)
    return response.json() as Promise<T>
  }

  async patch(path: string, body: any, options?: RequestOptions): Promise<Response> {
    return this.request('PATCH', path, {
      ...options,
      body: JSON.stringify(body),
      formHeaders: { 'Content-Type': 'application/json' },
    })
  }

  async post(path: string, body: any, options?: RequestOptions): Promise<Response> {
    return this.request('POST', path, {
      ...options,
      body: JSON.stringify(body),
      formHeaders: { 'Content-Type': 'application/json' },
      // POST defaults to no retry — most POST call sites in this CLI trigger
      // a non-idempotent write (queue a scan job, remediate a file, send an
      // email, append a note), and ApiClient's retry adds a duplicate request
      // on top of a write whose first attempt may have already succeeded
      // server-side. Callers that are genuinely idempotent reads may opt back
      // in with `{ retry: true }`; postForm() already followed this rule.
      retry: options?.retry ?? false,
    })
  }

  async postForm(path: string, formData: FormData, options?: RequestOptions): Promise<Response> {
    const formHeaders = (formData as any).getHeaders?.() ?? {}
    return this.request('POST', path, {
      ...options,
      body: formData as any,
      formHeaders,
      retry: options?.retry ?? false,
    })
  }

  async postJson<T>(path: string, body: any, options?: RequestOptions): Promise<T> {
    const response = await this.post(path, body, options)
    return response.json() as Promise<T>
  }

  private async request(
    method: string,
    path: string,
    options: RequestOptions & { body?: any; formHeaders?: Record<string, string> } = {},
  ): Promise<Response> {
    await this.resolve()

    const url = this.buildUrl(path, options.query)
    const headers = this.buildHeaders(options)
    const timeout = this.resolveTimeout(method, options.timeout)
    const shouldRetry = options.retry ?? true
    const maxRetries = shouldRetry ? 3 : 0

    let lastError: Error | undefined
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      if (attempt > 0) await backoffDelay(attempt)

      try {
        const response = await this.fetchWithTimeout(
          url.toString(),
          { body: options.body, headers, method },
          timeout,
        )

        if (response.ok) return response

        if (shouldRetry && RETRYABLE_STATUSES.has(response.status) && attempt < maxRetries) {
          continue // retry
        }

        // Non-retryable or exhausted retries - throw ApiError
        const body = await response.text()
        throw new ApiError(response.status, body, `API error ${response.status}: ${body}`)
      } catch (error: any) {
        if (error instanceof ApiError) throw error // don't wrap ApiError

        // Network error or timeout
        if (isRetryableNetworkError(error) && attempt < maxRetries) {
          lastError = error
          continue // retry
        }

        throw this.connectionError(error, timeout, attempt)
      }
    }

    throw new ApiConnectionError(
      `All ${maxRetries} retries exhausted: ${lastError?.message}`,
      maxRetries,
    )
  }

  private buildHeaders(options: RequestOptions & { formHeaders?: Record<string, string> }): Record<string, string> {
    const headers: Record<string, string> = { ...options.formHeaders, ...options.headers }
    if (this.apiKey) {
      headers.Authorization = `Bearer ${this.apiKey}`
    }

    return headers
  }

  private buildUrl(path: string, query?: Record<string, string>): URL {
    const url = new URL(path, this.baseUrl)
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        url.searchParams.set(key, value)
      }
    }

    return url
  }

  private connectionError(error: any, timeout: number, attempt: number): ApiConnectionError {
    if (error.name === 'AbortError') {
      return new ApiConnectionError(`Request timed out after ${timeout}ms`, attempt)
    }

    return new ApiConnectionError(`Could not connect to ${this.baseUrl}: ${error.message}`, attempt)
  }

  private async fetchWithTimeout(url: string, init: RequestInit, timeout: number): Promise<Response> {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), timeout)

    try {
      return await fetch(url, { ...init, signal: controller.signal })
    } finally {
      clearTimeout(timeoutId)
    }
  }

  private resolveTimeout(method: string, override?: number): number {
    return (
      override ??
      this.defaultTimeout ??
      (method === 'GET' || method === 'DELETE' ? 30_000 : 120_000)
    )
  }

  private async resolve(): Promise<void> {
    if (this.resolved) return
    if (!this.baseUrl) {
      this.baseUrl = await getApiUrl()
    }

    if (!this.apiKey) {
      const key = await getApiKey()
      if (key) this.apiKey = key
    }

    this.resolved = true
  }
}
