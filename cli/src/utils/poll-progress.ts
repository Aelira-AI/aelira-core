import { ApiClient, ApiConnectionError, ApiError } from './api-client.js'

interface ProgressSpinner {
  message: (msg: string) => void
  stop: (msg: string) => void
}

interface PollOptions {
  interval?: number
  timeout?: number
}

function updateSpinner(spinner: ProgressSpinner, data: any): void {
  const progress = data.progress ?? 0
  const progressMsg = data.progress_message || ''

  if (progressMsg) {
    spinner.message(`${progress}% — ${progressMsg}`)
  } else if (progress > 0) {
    spinner.message(`${progress}%`)
  }
}

function scanFailedError(data: any): Error {
  const err = new Error(data.error_message || data.progress_message || 'Scan failed')
  err.name = 'ScanFailedError'
  return err
}

/**
 * Re-throw errors that end the poll; return normally for transient ones so the
 * caller keeps polling on the next interval.
 * (ApiClient already retries 429/502/503/504 internally.)
 */
function rethrowIfTerminal(error: any): void {
  // ApiError with 404 means scan was deleted/cancelled
  if (error instanceof ApiError && error.status === 404) {
    throw new Error('Scan was cancelled or could not be found')
  }

  // Terminal errors (FAILED status, timeout, cancellation)
  if (
    error instanceof ApiConnectionError ||
    error.name === 'ScanFailedError' ||
    error.message?.includes('cancelled')
  ) {
    throw error
  }
}

/**
 * Poll a scan's progress endpoint until completion, updating the spinner.
 * Returns the full scan result on completion.
 */
export async function pollForCompletion(
  api: ApiClient,
  scanId: string,
  spinner: ProgressSpinner,
  options: PollOptions = {},
): Promise<any> {
  const interval = options.interval ?? 2000
  const timeout = options.timeout ?? 120_000
  const startTime = Date.now()

  while (true) {
    // Check timeout
    if (Date.now() - startTime > timeout) {
      throw new ApiConnectionError(
        `Scan timed out after ${Math.round(timeout / 1000)}s. Check scan status with \`aelira history\``,
        0,
      )
    }

    // Wait before polling (skip on first iteration)
    await new Promise<void>((r) => { setTimeout(r, interval) })

    try {
      const response = await api.get(`/education/scans/${scanId}/progress`, {
        timeout: 10_000,
        retry: false, // Don't retry progress polls — ApiClient retry adds too much latency
      })

      const data = await response.json()
      const status = (data.status || '').toUpperCase()

      updateSpinner(spinner, data)

      // Check terminal states
      if (status === 'COMPLETED') {
        // Fetch full results
        const resultResponse = await api.get(`/education/scans/${scanId}`, {
          timeout: 30_000,
        })
        return resultResponse.json()
      }

      if (status === 'FAILED') {
        throw scanFailedError(data)
      }

      // PENDING or PROCESSING — continue polling
    } catch (error: any) {
      rethrowIfTerminal(error)
    }
  }
}
