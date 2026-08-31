import { expect } from 'chai'
import { createHash } from 'node:crypto'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

import { ApiClient } from '../../src/utils/api-client.js'
import { buildReportEvidence, generateVerifiedPdfReport } from '../../src/utils/report-artifact.js'
import { cleanTestDir, createTestDir } from '../helpers/setup.js'

const PDF = Buffer.from('%PDF-1.7\nverified report\n%%EOF')
const SHA = createHash('sha256').update(PDF).digest('hex')
const JOB_ID = '11111111-1111-4111-8111-111111111111'
const STATUS_URL = `/education/reports/${JOB_ID}`
const DOWNLOAD_URL = `${STATUS_URL}/download`

function completed(overrides: Record<string, unknown> = {}): any {
  return {
    artifact: {
      artifact_id: JOB_ID,
      content_type: 'application/pdf',
      download_url: DOWNLOAD_URL,
      filename: `aelira-accessibility-report-${JOB_ID}.pdf`,
      sha256: SHA,
      size_bytes: PDF.length,
      ...overrides,
    },
    job_id: JOB_ID,
    progress: 100,
    status: 'completed',
  }
}

async function expectFailure(destination: string, message: string, timeout: number): Promise<void> {
  try {
    await generateVerifiedPdfReport({
      api: new ApiClient({ apiUrl: 'https://api.example.test' }), destination,
      evidence: { compliance_score: 0, issues: [], report_kind: 'scan', target: 'fixture.html' },
      pollIntervalMs: 1, pollTimeoutMs: timeout,
    })
    expect.fail('expected report failure')
  } catch (error: any) {
    expect(error.message).to.equal(message)
  }
}

async function expectMissing(file: string): Promise<void> {
  try {
    await fs.access(file)
    expect.fail('file exists')
  } catch (error: any) {
    expect(error.code).to.equal('ENOENT')
  }
}

describe('verified report artifact', () => {
  let originalFetch: typeof globalThis.fetch
  let originalKey: string | undefined
  let testDir: string

  beforeEach(async () => {
    originalFetch = globalThis.fetch
    originalKey = process.env.AELIRA_API_KEY
    process.env.AELIRA_API_KEY = 'test-key'
    testDir = await createTestDir()
  })

  afterEach(async () => {
    globalThis.fetch = originalFetch
    if (originalKey === undefined) delete process.env.AELIRA_API_KEY
    else process.env.AELIRA_API_KEY = originalKey
    await cleanTestDir(testDir)
  })

  function responseSequence(states: any[], download = PDF): void {
    let poll = 0
    globalThis.fetch = (async (input: Request | string | URL) => {
      const url = String(input)
      if (url.endsWith('/education/reports')) {
        return Response.json(
          { job_id: JOB_ID, status: 'pending', status_url: STATUS_URL },
          { status: 202 },
        )
      }

      if (url.endsWith(DOWNLOAD_URL)) {
        return new Response(download, {
          headers: {
            'content-length': String(download.length),
            'content-type': 'application/pdf',
            'x-artifact-id': JOB_ID,
            'x-checksum-sha256': SHA,
          },
        })
      }

      return Response.json(states[Math.min(poll++, states.length - 1)])
    }) as typeof fetch
  }

  it('waits through queued states and atomically publishes exact verified bytes', async () => {
    responseSequence([
      { job_id: JOB_ID, progress: 0, status: 'pending' },
      { job_id: JOB_ID, progress: 50, status: 'processing' },
      completed(),
    ])
    const destination = path.join(testDir, 'report.pdf')

    await generateVerifiedPdfReport({
      api: new ApiClient({ apiUrl: 'https://api.example.test' }),
      destination,
      evidence: { compliance_score: 100, issues: [], report_kind: 'scan', target: 'fixture.html' },
      pollIntervalMs: 1,
      pollTimeoutMs: 1000,
    })

    expect(await fs.readFile(destination)).to.deep.equal(PDF)
    expect((await fs.readdir(testDir)).filter((name) => name.endsWith('.tmp'))).to.deep.equal([])
  })

  it('rejects malformed content and leaves no output', async () => {
    responseSequence([completed()], Buffer.from('<html>proxy error</html>'))
    const destination = path.join(testDir, 'report.pdf')
    try {
      await generateVerifiedPdfReport({
        api: new ApiClient({ apiUrl: 'https://api.example.test' }), destination,
        evidence: { compliance_score: 0, issues: [], report_kind: 'scan', target: 'fixture.html' },
        pollIntervalMs: 1,
      })
      expect.fail('expected malformed artifact rejection')
    } catch (error: any) {
      expect(error.message).to.equal('Downloaded report failed artifact verification')
    }

    await expectMissing(destination)
  })

  it('rejects checksum and size identity mismatches', async () => {
    for (const artifact of [{ sha256: '0'.repeat(64) }, { size_bytes: PDF.length + 1 }]) {
      responseSequence([completed(artifact)])
      const destination = path.join(testDir, `bad-${Object.keys(artifact)[0]}.pdf`)
      try {
        await generateVerifiedPdfReport({
          api: new ApiClient({ apiUrl: 'https://api.example.test' }), destination,
          evidence: { compliance_score: 0, issues: [], report_kind: 'scan', target: 'fixture.html' },
          pollIntervalMs: 1,
        })
        expect.fail('expected identity rejection')
      } catch (error: any) {
        expect(error.message).to.equal('Downloaded report failed artifact verification')
      }

      await expectMissing(destination)
    }
  })

  it('bounds failed jobs and polling timeouts without partial output', async () => {
    const destination = path.join(testDir, 'report.pdf')
    responseSequence([{ error_code: 'report_generation_failed', status: 'failed' }])
    await expectFailure(destination, 'Report generation failed (report_generation_failed)', 100)

    responseSequence([{ progress: 1, status: 'processing' }])
    await expectFailure(destination, 'Report generation timed out', 5)
  })

  it('preserves an existing destination and removes temp bytes after interruption', async () => {
    const destination = path.join(testDir, 'existing.pdf')
    await fs.writeFile(destination, 'original')
    responseSequence([completed()])
    let calls = 0
    const baseFetch = globalThis.fetch
    globalThis.fetch = (async (input: Request | string | URL, init?: RequestInit) => {
      if (!String(input).endsWith('/download')) return baseFetch(input, init)
      const stream = new ReadableStream({
        pull(controller) {
          if (calls++ === 0) controller.enqueue(PDF.subarray(0, 8))
          else controller.error(new Error('/private/server/path interrupted'))
        },
      })
      return new Response(stream, {
        headers: {
          'content-length': String(PDF.length), 'content-type': 'application/pdf',
          'x-artifact-id': JOB_ID, 'x-checksum-sha256': SHA,
        },
      })
    }) as typeof fetch

    await expectFailure(destination, 'Report download was interrupted', 100)
    expect(await fs.readFile(destination, 'utf8')).to.equal('original')
    expect((await fs.readdir(testDir)).filter((name) => name.endsWith('.tmp'))).to.deep.equal([])
  })

  it('builds bounded evidence from scan and AI results', () => {
    const evidence = buildReportEvidence({
      axeResults: {
        passes: Array.from({ length: 4 }),
        violations: [{ description: 'Missing name', help: 'Name buttons', id: 'button-name', impact: 'serious', nodes: [{ html: '<button>', target: ['#save'] }] }],
      },
      aiResults: { results: [{ classification: { explanation: 'Button needs a name' }, rule_id: 'button-name-0' }] },
      reportKind: 'analyze',
      target: '/Users/example/private/fixture.html',
    })
    expect(evidence.report_kind).to.equal('analyze')
    expect(evidence.compliance_score).to.equal(80)
    expect(evidence.issues).to.have.length(1)
    expect(evidence.total_issues).to.equal(1)
    expect(evidence.target).to.equal('fixture.html')
    expect(JSON.stringify(evidence)).to.not.contain('/Users/example/private')
    expect(evidence.severity_totals).to.deep.equal({ critical: 0, minor: 0, moderate: 0, serious: 1 })
    expect(JSON.stringify(evidence).length).to.be.lessThan(240_000)
  })

  it('keeps whole-scan score and severity totals when detail rows are bounded', () => {
    const violations = Array.from({ length: 60 }, (_, index) => ({
      description: `Issue ${index}`,
      id: `rule-${index}`,
      impact: index === 0 ? 'critical' : 'minor',
      nodes: [],
    }))
    const evidence = buildReportEvidence({
      axeResults: { passes: Array.from({ length: 40 }), violations },
      reportKind: 'scan',
      target: 'fixture.html',
    })

    expect(evidence.compliance_score).to.equal(40)
    expect(evidence.total_issues).to.equal(60)
    expect(evidence.issues).to.have.length(50)
    expect(evidence.severity_totals).to.deep.equal({ critical: 1, minor: 59, moderate: 0, serious: 0 })
  })

})
