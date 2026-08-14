import { expect } from 'chai'

import { ApiClient, ApiConnectionError, ApiError } from '../../src/utils/api-client.js'
import { cleanTestDir, createTestDir, withTestConfig } from '../helpers/setup.js'

describe('ApiClient', () => {
  let testDir: string
  let restoreEnv: () => void
  let originalFetch: typeof fetch

  beforeEach(async () => {
    testDir = await createTestDir()
    restoreEnv = withTestConfig(testDir)
    originalFetch = globalThis.fetch
  })

  afterEach(async () => {
    globalThis.fetch = originalFetch
    restoreEnv()
    delete process.env.AELIRA_API_URL
    delete process.env.AELIRA_API_KEY
    await cleanTestDir(testDir)
  })

  // ---------------------------------------------------------------------------
  // Mock helpers
  // ---------------------------------------------------------------------------

  function mockFetch(responses: Array<Error | Response>): void {
    let callIndex = 0
    globalThis.fetch = (async () => {
      const response = responses[callIndex++]
      if (response instanceof Error) throw response
      return response
    }) as typeof fetch
  }

  function mockFetchWithCounter(
    responses: Array<Error | Response>,
  ): { getCount: () => number } {
    let callIndex = 0
    let count = 0
    globalThis.fetch = (async () => {
      count++
      const response = responses[callIndex++] ?? responses.at(-1)
      if (response instanceof Error) throw response
      return response
    }) as typeof fetch
    return { getCount: () => count }
  }

  // ---------------------------------------------------------------------------
  // URL construction
  // ---------------------------------------------------------------------------

  describe('URL construction', () => {
    it('constructs URL from path and base URL', async () => {
      let capturedUrl = ''
      globalThis.fetch = (async (url: any) => {
        capturedUrl = url.toString()
        return new Response('{}', { status: 200 })
      }) as typeof fetch

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      await api.get('/api/health')

      expect(capturedUrl).to.equal('https://api.example.com/api/health')
    })

    it('appends query parameters', async () => {
      let capturedUrl = ''
      globalThis.fetch = (async (url: any) => {
        capturedUrl = url.toString()
        return new Response('{}', { status: 200 })
      }) as typeof fetch

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      await api.get('/api/issues', { query: { limit: '10', status: 'open' } })

      expect(capturedUrl).to.include('limit=10')
      expect(capturedUrl).to.include('status=open')
    })
  })

  // ---------------------------------------------------------------------------
  // Auth headers
  // ---------------------------------------------------------------------------

  describe('auth headers', () => {
    it('attaches Authorization header when API key configured', async () => {
      let capturedInit: RequestInit = {}
      globalThis.fetch = (async (_url: any, init: any) => {
        capturedInit = init
        return new Response('{}', { status: 200 })
      }) as typeof fetch

      const api = new ApiClient({
        apiKey: 'test-key-123',
        apiUrl: 'https://api.example.com',
      })
      await api.get('/api/health')

      const headers = capturedInit.headers as Record<string, string>
      expect(headers.Authorization).to.equal('Bearer test-key-123')
    })

    it('does not attach Authorization when no key', async () => {
      let capturedInit: RequestInit = {}
      globalThis.fetch = (async (_url: any, init: any) => {
        capturedInit = init
        return new Response('{}', { status: 200 })
      }) as typeof fetch

      // Ensure no key from env either
      delete process.env.AELIRA_API_KEY

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      await api.get('/api/health')

      const headers = capturedInit.headers as Record<string, string>
      expect(headers.Authorization).to.be.undefined
    })
  })

  // ---------------------------------------------------------------------------
  // Content-Type headers
  // ---------------------------------------------------------------------------

  describe('content type', () => {
    it('sets Content-Type application/json for post', async () => {
      let capturedInit: RequestInit = {}
      globalThis.fetch = (async (_url: any, init: any) => {
        capturedInit = init
        return new Response('{}', { status: 200 })
      }) as typeof fetch

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      await api.post('/api/data', { foo: 'bar' })

      const headers = capturedInit.headers as Record<string, string>
      expect(headers['Content-Type']).to.equal('application/json')
    })

    it('does not set Content-Type application/json for postForm', async () => {
      let capturedInit: RequestInit = {}
      globalThis.fetch = (async (_url: any, init: any) => {
        capturedInit = init
        return new Response('{}', { status: 200 })
      }) as typeof fetch

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      // Use a plain object that quacks like FormData (no getHeaders)
      const fakeForm = {} as any
      await api.postForm('/api/upload', fakeForm)

      const headers = capturedInit.headers as Record<string, string>
      expect(headers['Content-Type']).to.be.undefined
    })
  })

  // ---------------------------------------------------------------------------
  // Retry logic
  // ---------------------------------------------------------------------------

  describe('retry logic', () => {
    it('retries on 503 up to 3 times then throws ApiError', async () => {
      const counter = mockFetchWithCounter([
        new Response('service unavailable', { status: 503 }),
        new Response('service unavailable', { status: 503 }),
        new Response('service unavailable', { status: 503 }),
        new Response('service unavailable', { status: 503 }),
      ])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.get('/api/health')
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiError)
        expect(error.status).to.equal(503)
      }

      expect(counter.getCount()).to.equal(4) // initial + 3 retries
    })

    it('retries on 429', async () => {
      const counter = mockFetchWithCounter([
        new Response('rate limited', { status: 429 }),
        new Response('rate limited', { status: 429 }),
        new Response('rate limited', { status: 429 }),
        new Response('rate limited', { status: 429 }),
      ])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.get('/api/health')
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiError)
        expect(error.status).to.equal(429)
      }

      expect(counter.getCount()).to.equal(4)
    })

    it('retries on network error (ECONNREFUSED)', async () => {
      const connError = new Error('connect ECONNREFUSED 127.0.0.1:8000')
      ;(connError as any).code = 'ECONNREFUSED'

      const counter = mockFetchWithCounter([
        connError,
        connError,
        connError,
        connError,
      ])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.get('/api/health')
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiConnectionError)
      }

      expect(counter.getCount()).to.equal(4)
    })

    it('does not retry on 400', async () => {
      const counter = mockFetchWithCounter([
        new Response('bad request', { status: 400 }),
      ])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.get('/api/health')
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiError)
        expect(error.status).to.equal(400)
      }

      expect(counter.getCount()).to.equal(1)
    })

    it('does not retry on 401', async () => {
      const counter = mockFetchWithCounter([
        new Response('unauthorized', { status: 401 }),
      ])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.get('/api/health')
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiError)
        expect(error.status).to.equal(401)
      }

      expect(counter.getCount()).to.equal(1)
    })

    it('does not retry on 500', async () => {
      const counter = mockFetchWithCounter([
        new Response('internal server error', { status: 500 }),
      ])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.get('/api/health')
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiError)
        expect(error.status).to.equal(500)
      }

      expect(counter.getCount()).to.equal(1)
    })

    it('does not retry postForm by default', async () => {
      const counter = mockFetchWithCounter([
        new Response('service unavailable', { status: 503 }),
      ])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.postForm('/api/upload', {} as any)
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiError)
        expect(error.status).to.equal(503)
      }

      expect(counter.getCount()).to.equal(1)
    })

    it('does not retry post by default', async () => {
      const counter = mockFetchWithCounter([
        new Response('service unavailable', { status: 503 }),
      ])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.post('/api/data', { foo: 'bar' })
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiError)
        expect(error.status).to.equal(503)
      }

      // 1 call, not 4 — post() must not inherit request()'s retry-by-default.
      expect(counter.getCount()).to.equal(1)
    })

    it('retries post when retry: true', async () => {
      const counter = mockFetchWithCounter([
        new Response('service unavailable', { status: 503 }),
        new Response('service unavailable', { status: 503 }),
        new Response('service unavailable', { status: 503 }),
        new Response('service unavailable', { status: 503 }),
      ])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.post('/api/data', { foo: 'bar' }, { retry: true })
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiError)
      }

      expect(counter.getCount()).to.equal(4)
    })

    it('retries postForm when retry: true', async () => {
      const counter = mockFetchWithCounter([
        new Response('service unavailable', { status: 503 }),
        new Response('service unavailable', { status: 503 }),
        new Response('service unavailable', { status: 503 }),
        new Response('service unavailable', { status: 503 }),
      ])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.postForm('/api/upload', {} as any, { retry: true })
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiError)
      }

      expect(counter.getCount()).to.equal(4)
    })

    it('respects retry: false on get', async () => {
      const counter = mockFetchWithCounter([
        new Response('service unavailable', { status: 503 }),
      ])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.get('/api/health', { retry: false })
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiError)
        expect(error.status).to.equal(503)
      }

      expect(counter.getCount()).to.equal(1)
    })

    it('returns response on success after retry', async () => {
      mockFetch([
        new Response('service unavailable', { status: 503 }),
        new Response('service unavailable', { status: 503 }),
        new Response(JSON.stringify({ ok: true }), { status: 200 }),
      ])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      const response = await api.get('/api/health')

      expect(response.ok).to.be.true
      const body = await response.json()
      expect(body).to.deep.equal({ ok: true })
    })
  })

  // ---------------------------------------------------------------------------
  // Error types
  // ---------------------------------------------------------------------------

  describe('error types', () => {
    it('throws ApiError with correct status and body', async () => {
      mockFetch([
        new Response('{"error":"not found"}', { status: 404 }),
      ])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.get('/api/missing', { retry: false })
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiError)
        expect(error.status).to.equal(404)
        expect(error.body).to.equal('{"error":"not found"}')
        expect(error.message).to.include('404')
      }
    })

    it('throws ApiConnectionError on timeout', async () => {
      // Mock fetch that throws AbortError (simulating timeout)
      globalThis.fetch = (async () => {
        const abortError = new DOMException('The operation was aborted', 'AbortError')
        throw abortError
      }) as typeof fetch

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.get('/api/health', { retry: false, timeout: 100 })
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiConnectionError)
        expect(error.message).to.include('timed out')
      }
    })

    it('throws ApiConnectionError after retries exhausted on network error', async () => {
      const connError = new Error('connect ECONNREFUSED 127.0.0.1:8000')
      ;(connError as any).code = 'ECONNREFUSED'

      mockFetch([connError, connError, connError, connError])

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      try {
        await api.get('/api/health')
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error).to.be.instanceOf(ApiConnectionError)
        expect(error.retryCount).to.equal(3)
      }
    })
  })

  // ---------------------------------------------------------------------------
  // Timeout defaults
  // ---------------------------------------------------------------------------

  describe('timeout defaults', () => {
    it('uses default 30s timeout for GET', async () => {
      let capturedSignal: AbortSignal | undefined
      globalThis.fetch = (async (_url: any, init: any) => {
        capturedSignal = init?.signal
        return new Response('{}', { status: 200 })
      }) as typeof fetch

      const api = new ApiClient({ apiUrl: 'https://api.example.com' })
      await api.get('/api/health')

      // The signal should exist (AbortController was created)
      expect(capturedSignal).to.exist
      expect(capturedSignal!.aborted).to.be.false
    })
  })
})
