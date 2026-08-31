import { createHash, randomUUID } from 'node:crypto'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

import { ApiClient, ApiError } from './api-client.js'

const DEFAULT_MAX_BYTES = 20 * 1024 * 1024
const DEFAULT_POLL_INTERVAL_MS = 1000
const DEFAULT_POLL_TIMEOUT_MS = 120_000
const MAX_EVIDENCE_BYTES = 240_000
const MAX_ISSUES = 50
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export interface ReportEvidence {
  compliance_score: number
  created_at?: string
  issues: Array<Record<string, string>>
  report_kind: 'analyze' | 'scan'
  severity_totals?: Record<'critical' | 'minor' | 'moderate' | 'serious', number>
  target: string
  total_issues?: number
}

interface GenerateOptions {
  api: ApiClient
  destination: string
  evidence: ReportEvidence
  maxBytes?: number
  pollIntervalMs?: number
  pollTimeoutMs?: number
}

interface ArtifactIdentity {
  artifact_id: string
  content_type: string
  download_url: string
  filename: string
  sha256: string
  size_bytes: number
}

interface PollOptions {
  api: ApiClient
  interval: number
  jobId: string
  maxBytes: number
  statusUrl: string
  timeout: number
}

export class ReportArtifactError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ReportArtifactError'
  }
}

function boundedText(value: unknown, limit: number): string {
  return typeof value === 'string' ? value.slice(0, limit) : ''
}

function boundedTarget(value: string): string {
  const bounded = boundedText(value, 2048)
  return /^https?:\/\//i.test(bounded) ? bounded : path.basename(bounded)
}

function reportIssue(violation: any, aiResults: any[]): Record<string, string> {
  const source = violation ?? {}
  const node = Array.isArray(source.nodes) ? source.nodes[0] : undefined
  const rule = boundedText(source.id, 128)
  const ai = aiResults.find((item: any) => boundedText(item?.rule_id, 256).startsWith(`${rule}-`))
  const fix = ai?.fix?.fix_recommendation ?? ai?.classification?.explanation ?? source.help

  return {
    description: boundedText(source.description ?? source.help, 2000),
    element: boundedText(node?.target?.join?.(', ') ?? node?.html, 1000),
    fix: boundedText(fix, 4000),
    impact: boundedText(source.impact ?? 'minor', 32),
    rule,
  }
}

export function buildReportEvidence(options: {
  aiResults?: any
  axeResults: any
  reportKind: 'analyze' | 'scan'
  target: string
}): ReportEvidence {
  const allViolations = Array.isArray(options.axeResults?.violations) ? options.axeResults.violations : []
  const violations = allViolations.slice(0, MAX_ISSUES)
  const passes = Array.isArray(options.axeResults?.passes) ? options.axeResults.passes : []
  const total = allViolations.length + passes.length
  const score = total === 0 ? 100 : Math.round((passes.length / total) * 100)
  const aiResults = Array.isArray(options.aiResults?.results) ? options.aiResults.results : []
  const issues = violations.map((violation: any) => reportIssue(violation, aiResults))
  const severityTotals = { critical: 0, minor: 0, moderate: 0, serious: 0 }
  for (const violation of allViolations) {
    const impact: unknown = violation?.impact
    if (impact === 'critical' || impact === 'minor' || impact === 'moderate' || impact === 'serious') {
      severityTotals[impact]++
    }
  }

  const evidence: ReportEvidence = {
    compliance_score: score,
    created_at: new Date().toISOString(),
    issues,
    report_kind: options.reportKind,
    severity_totals: severityTotals,
    target: boundedTarget(options.target),
    total_issues: allViolations.length,
  }
  if (Buffer.byteLength(JSON.stringify(evidence)) > MAX_EVIDENCE_BYTES) {
    throw new ReportArtifactError('Report evidence exceeds the supported size limit')
  }

  return evidence
}

function assertAccepted(value: any): { jobId: string; statusUrl: string } {
  if (
    typeof value?.job_id !== 'string' ||
    !UUID.test(value.job_id) ||
    typeof value?.status_url !== 'string' ||
    value.status_url !== `/education/reports/${value.job_id}`
  ) {
    throw new ReportArtifactError('Report service returned an invalid job identity')
  }

  return { jobId: value.job_id, statusUrl: value.status_url }
}

function assertArtifact(value: any, jobId: string, maxBytes: number): ArtifactIdentity {
  const artifact = value?.artifact
  if (
    value?.job_id !== jobId ||
    artifact?.artifact_id !== jobId ||
    artifact?.content_type !== 'application/pdf' ||
    typeof artifact?.download_url !== 'string' ||
    artifact.download_url !== `/education/reports/${jobId}/download` ||
    typeof artifact?.filename !== 'string' ||
    !Number.isSafeInteger(artifact?.size_bytes) ||
    artifact.size_bytes < 5 ||
    artifact.size_bytes > maxBytes ||
    typeof artifact?.sha256 !== 'string' ||
    !/^[0-9a-f]{64}$/.test(artifact.sha256)
  ) {
    throw new ReportArtifactError('Report service returned an invalid artifact identity')
  }

  return artifact as ArtifactIdentity
}

async function waitForArtifact(options: PollOptions): Promise<ArtifactIdentity> {
  const started = Date.now()
  while (true) {
    const remaining = options.timeout - (Date.now() - started)
    if (remaining <= 0) {
      throw new ReportArtifactError('Report generation timed out')
    }

    let response: Response
    try {
      response = await options.api.get(options.statusUrl, {
        retry: false,
        timeout: Math.min(remaining, 10_000),
      })
    } catch (error) {
      if (error instanceof ApiError) {
        throw new ReportArtifactError(`Report status request failed (HTTP ${error.status})`)
      }

      throw new ReportArtifactError('Report status service is unavailable')
    }

    let state: any
    try {
      state = await response.json()
    } catch {
      throw new ReportArtifactError('Report service returned an invalid job state')
    }

    const status = typeof state?.status === 'string' ? state.status.toLowerCase() : ''
    if (status === 'completed') return assertArtifact(state, options.jobId, options.maxBytes)
    if (status === 'failed') {
      const code = typeof state?.error_code === 'string' && /^[a-z0-9_]{1,128}$/.test(state.error_code)
        ? state.error_code
        : 'report_generation_failed'
      throw new ReportArtifactError(`Report generation failed (${code})`)
    }

    if (status !== 'pending' && status !== 'processing') {
      throw new ReportArtifactError('Report service returned an invalid job state')
    }

    const waitRemaining = options.timeout - (Date.now() - started)
    if (waitRemaining <= 0) {
      throw new ReportArtifactError('Report generation timed out')
    }

    await new Promise<void>((resolve) => {
      setTimeout(resolve, Math.min(options.interval, waitRemaining))
    })
  }
}

function assertDownloadHeaders(
  response: Response,
  artifact: ArtifactIdentity,
  maxBytes: number,
): ReadableStream<Uint8Array> {
  const contentType = (response.headers.get('content-type') ?? '').split(';', 1)[0].trim().toLowerCase()
  const contentLength = Number(response.headers.get('content-length'))
  const artifactId = response.headers.get('x-artifact-id')
  const checksum = response.headers.get('x-checksum-sha256')
  if (
    contentType !== 'application/pdf' ||
    !Number.isSafeInteger(contentLength) ||
    contentLength !== artifact.size_bytes ||
    contentLength > maxBytes ||
    artifactId !== artifact.artifact_id ||
    checksum !== artifact.sha256 ||
    response.body === null
  ) {
    throw new ReportArtifactError('Downloaded report failed artifact verification')
  }

  return response.body
}

async function streamToFile(
  body: ReadableStream<Uint8Array>,
  handle: fs.FileHandle,
  artifact: ArtifactIdentity,
  maxBytes: number,
): Promise<{ bytes: number; digest: string; signature: Buffer }> {
  const hash = createHash('sha256')
  let bytes = 0
  let signature = Buffer.alloc(0)
  try {
    for await (const rawChunk of body as any) {
      const chunk = Buffer.from(rawChunk)
      bytes += chunk.length
      if (bytes > maxBytes || bytes > artifact.size_bytes) {
        throw new ReportArtifactError('Downloaded report failed artifact verification')
      }

      if (signature.length < 5) signature = Buffer.concat([signature, chunk]).subarray(0, 5)
      hash.update(chunk)
      let offset = 0
      while (offset < chunk.length) {
        const { bytesWritten } = await handle.write(chunk, offset)
        if (bytesWritten <= 0) {
          throw new ReportArtifactError('Report download was interrupted')
        }

        offset += bytesWritten
      }
    }
  } catch (error) {
    if (error instanceof ReportArtifactError) throw error
    throw new ReportArtifactError('Report download was interrupted')
  }

  return { bytes, digest: hash.digest('hex'), signature }
}

async function downloadArtifact(
  api: ApiClient,
  artifact: ArtifactIdentity,
  destination: string,
  maxBytes: number,
): Promise<void> {
  let response: Response
  try {
    response = await api.get(artifact.download_url, {
      headers: { Accept: 'application/pdf' },
      retry: false,
      timeout: 120_000,
    })
  } catch (error) {
    if (error instanceof ApiError) {
      throw new ReportArtifactError(`Report download failed (HTTP ${error.status})`)
    }

    throw new ReportArtifactError('Report download service is unavailable')
  }

  const body = assertDownloadHeaders(response, artifact, maxBytes)

  const temporary = path.join(
    path.dirname(destination),
    `.${path.basename(destination)}.${process.pid}.${randomUUID()}.tmp`,
  )
  let handle: fs.FileHandle | undefined
  try {
    handle = await fs.open(temporary, 'wx', 0o600)
    const streamed = await streamToFile(body, handle, artifact, maxBytes)
    await handle.sync()
    await handle.close()
    handle = undefined
    if (
      streamed.bytes !== artifact.size_bytes ||
      !streamed.signature.equals(Buffer.from('%PDF-')) ||
      streamed.digest !== artifact.sha256
    ) {
      throw new ReportArtifactError('Downloaded report failed artifact verification')
    }

    await fs.rename(temporary, destination)
  } catch (error) {
    if (error instanceof ReportArtifactError) throw error
    throw new ReportArtifactError('Could not write report output file')
  } finally {
    await handle?.close().catch(() => {})
    await fs.unlink(temporary).catch(() => {})
  }
}

export async function generateVerifiedPdfReport(options: GenerateOptions): Promise<void> {
  const maxBytes = options.maxBytes ?? DEFAULT_MAX_BYTES
  let acceptedResponse: Response
  try {
    acceptedResponse = await options.api.post('/education/reports', options.evidence, {
      retry: false,
      timeout: 30_000,
    })
  } catch (error) {
    if (error instanceof ApiError) {
      throw new ReportArtifactError(`Report request failed (HTTP ${error.status})`)
    }

    throw new ReportArtifactError('Report service is unavailable')
  }

  let acceptedBody: any
  try {
    acceptedBody = await acceptedResponse.json()
  } catch {
    throw new ReportArtifactError('Report service returned an invalid response')
  }

  const accepted = assertAccepted(acceptedBody)
  const artifact = await waitForArtifact({
    api: options.api,
    interval: options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS,
    jobId: accepted.jobId,
    maxBytes,
    statusUrl: accepted.statusUrl,
    timeout: options.pollTimeoutMs ?? DEFAULT_POLL_TIMEOUT_MS,
  })
  await downloadArtifact(options.api, artifact, options.destination, maxBytes)
}
