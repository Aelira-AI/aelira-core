import { expect } from 'chai'

import { ApiClient } from '../../src/utils/api-client.js'
import { pollForCompletion } from '../../src/utils/poll-progress.js'
import { cleanTestDir, createTestDir, withTestConfig } from '../helpers/setup.js'

describe('pollForCompletion', () => {
  let testDir: string
  let restoreEnv: () => void
  let originalFetch: typeof fetch
  let spinnerMessages: string[]

  const mockSpinner = {
    message(msg: string) { spinnerMessages.push(msg) },
    stop(msg: string) { spinnerMessages.push(`STOP: ${msg}`) },
  }

  beforeEach(async () => {
    testDir = await createTestDir()
    restoreEnv = withTestConfig(testDir)
    originalFetch = globalThis.fetch
    spinnerMessages = []
  })

  afterEach(async () => {
    globalThis.fetch = originalFetch
    restoreEnv()
    await cleanTestDir(testDir)
  })

  function mockFetchSequence(responses: Array<{ body: any; status?: number }>): void {
    let callIndex = 0
    globalThis.fetch = (async (_url: any) => {
      const resp = responses[callIndex] ?? responses.at(-1)
      callIndex++
      return new Response(JSON.stringify(resp.body), {
        status: resp.status ?? 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }) as typeof fetch
  }

  it('polls until COMPLETED and returns full result', async () => {
    mockFetchSequence([
      // Progress poll 1: PROCESSING
      { body: { status: 'PROCESSING', progress: 50, progress_message: 'Extracting text' } },
      // Progress poll 2: COMPLETED
      { body: { status: 'COMPLETED', progress: 100, progress_message: 'Done' } },
      // Full result fetch
      { body: { scan_id: 'test-123', issues: [], score: 100 } },
    ])

    const api = new ApiClient({ apiUrl: 'http://test:8000' })
    const result = await pollForCompletion(api, 'test-123', mockSpinner, { interval: 10 })
    expect(result.score).to.equal(100)
  })

  it('updates spinner message with progress', async () => {
    mockFetchSequence([
      { body: { status: 'PROCESSING', progress: 30, progress_message: 'Analyzing images' } },
      { body: { status: 'COMPLETED', progress: 100 } },
      { body: { scan_id: 'test-123', issues: [] } },
    ])

    const api = new ApiClient({ apiUrl: 'http://test:8000' })
    await pollForCompletion(api, 'test-123', mockSpinner, { interval: 10 })
    expect(spinnerMessages).to.include('30% — Analyzing images')
  })

  it('throws on FAILED status with error_message', async () => {
    mockFetchSequence([
      { body: { status: 'FAILED', progress: 0, error_message: 'PDF is encrypted' } },
    ])

    const api = new ApiClient({ apiUrl: 'http://test:8000' })
    try {
      await pollForCompletion(api, 'test-123', mockSpinner, { interval: 10 })
      expect.fail('should have thrown')
    } catch (error: any) {
      expect(error.message).to.contain('PDF is encrypted')
    }
  })

  it('throws on timeout', async () => {
    // Always return PROCESSING
    mockFetchSequence([
      { body: { status: 'PROCESSING', progress: 10 } },
    ])

    const api = new ApiClient({ apiUrl: 'http://test:8000' })
    try {
      await pollForCompletion(api, 'test-123', mockSpinner, { interval: 10, timeout: 50 })
      expect.fail('should have thrown')
    } catch (error: any) {
      expect(error.message).to.contain('timed out')
    }
  })

  it('handles 404 as scan cancelled', async () => {
    mockFetchSequence([
      { body: { detail: 'Not found' }, status: 404 },
    ])

    const api = new ApiClient({ apiUrl: 'http://test:8000' })
    try {
      await pollForCompletion(api, 'test-123', mockSpinner, { interval: 10 })
      expect.fail('should have thrown')
    } catch (error: any) {
      expect(error.message).to.contain('cancelled')
    }
  })

  it('continues polling on transient errors', async () => {
    mockFetchSequence([
      // First poll: server error (transient)
      { body: { error: 'temporary' }, status: 500 },
      // Second poll: back to normal
      { body: { status: 'COMPLETED', progress: 100 } },
      // Full result fetch
      { body: { scan_id: 'test-123', issues: [] } },
    ])

    const api = new ApiClient({ apiUrl: 'http://test:8000' })
    const result = await pollForCompletion(api, 'test-123', mockSpinner, { interval: 10 })
    expect(result.scan_id).to.equal('test-123')
  })

  it('handles case-insensitive status', async () => {
    mockFetchSequence([
      { body: { status: 'completed', progress: 100 } },
      { body: { scan_id: 'test-123', issues: [] } },
    ])

    const api = new ApiClient({ apiUrl: 'http://test:8000' })
    const result = await pollForCompletion(api, 'test-123', mockSpinner, { interval: 10 })
    expect(result.scan_id).to.equal('test-123')
  })

  it('shows percentage only when no progress_message', async () => {
    mockFetchSequence([
      { body: { status: 'PROCESSING', progress: 60 } },
      { body: { status: 'COMPLETED', progress: 100 } },
      { body: { scan_id: 'test-123', issues: [] } },
    ])

    const api = new ApiClient({ apiUrl: 'http://test:8000' })
    await pollForCompletion(api, 'test-123', mockSpinner, { interval: 10 })
    expect(spinnerMessages).to.include('60%')
  })
})
