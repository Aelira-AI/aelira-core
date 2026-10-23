import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import * as fs from 'node:fs/promises'
import { createServer } from 'node:http'
import * as path from 'node:path'

import { cleanTestDir, createTestDir, withTestConfig } from '../../helpers/setup.js'

// GET /education/scans/{scan_id} in src/api/education/scan_history_routes.py
// returns the file details under `scan` and the findings under `scan.result`,
// with severity counts in `scan.result.summary`. Nothing is at the top level
// except `success`.
const scanDetail = {
  scan: {
    completed_at: '2026-01-01T00:00:01',
    created_at: '2026-01-01T00:00:00',
    file_name: 'handout.pdf',
    file_size_bytes: 2_621_440,
    pages: 3,
    processing_time_ms: 900,
    remediation_eligibility: null,
    result: {
      compliance_score: 62.5,
      issues: [
        { message: 'PDF document language not set', rule: 'WCAG 3.1.1', severity: 'high' },
        { message: 'PDF has no structure tree (untagged PDF)', rule: 'WCAG 1.3.1', severity: 'critical' },
      ],
      ocr_used: false,
      ollama_used: false,
      structure: {},
      suggestions: [],
      summary: { critical: 1, high: 1, low: 0, medium: 0, total: 2 },
      wcag_level: 'AA',
    },
    scan_id: 'scan-pdf-1',
    scan_type: 'pdf',
    status: 'completed',
  },
  success: true,
}

describe('scan pdf console and CSV summaries', () => {
  const server = createServer((request, response) => {
    response.setHeader('Content-Type', 'application/json')
    const url = request.url ?? ''
    request.resume()
    if (request.method === 'POST' && url === '/education/pdf/scan') {
      response.end(JSON.stringify({ scan_id: 'scan-pdf-1', status: 'PENDING', success: true }))
      return
    }

    if (request.method === 'GET' && url === '/education/scans/scan-pdf-1/progress') {
      response.end(JSON.stringify({ progress: 100, scan_id: 'scan-pdf-1', status: 'COMPLETED', success: true }))
      return
    }

    if (request.method === 'GET' && url === '/education/scans/scan-pdf-1') {
      response.end(JSON.stringify(scanDetail))
      return
    }

    response.statusCode = 404
    response.end(JSON.stringify({ detail: 'Not Found' }))
  })
  let apiUrl: string
  let restoreConfig: () => void
  let testDir: string
  let pdfPath: string

  before(async () => {
    await new Promise<void>((resolve) => { server.listen(0, '127.0.0.1', resolve) })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Missing test listener')
    apiUrl = `http://127.0.0.1:${address.port}`
  })

  after(async () => {
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) => { server.close((error) => { error ? reject(error) : resolve() }) })
  })

  beforeEach(async () => {
    testDir = await createTestDir()
    restoreConfig = withTestConfig(testDir)
    pdfPath = path.join(testDir, 'handout.pdf')
    await fs.writeFile(pdfPath, '%PDF-1.4\n')
  })

  afterEach(async () => {
    restoreConfig()
    await cleanTestDir(testDir)
  })

  it('prints the file details and counts nested under scan and scan.result', async () => {
    const { error, stdout } = await runCommand(['scan:pdf', pdfPath, '--api-url', apiUrl])

    expect(error).to.equal(undefined)
    expect(stdout).to.contain('File: handout.pdf')
    expect(stdout).to.contain('Pages: 3')
    expect(stdout).to.contain('Size: 2.50 MB')
    expect(stdout).to.contain('OCR: No')
    expect(stdout).to.contain('Scanner score: 62.5/100')
    expect(stdout).to.contain('- Critical: 1')
    expect(stdout).to.contain('- High: 1')
    expect(stdout).to.contain('Total: 2')
    expect(stdout).to.contain('Scan ID: scan-pdf-1')
    expect(stdout).not.to.contain('Unknown')
    expect(stdout).not.to.contain('N/A')
  })

  it('omits the score line when the API masks the score as null', async () => {
    const original = scanDetail.scan.result.compliance_score
    try {
      ;(scanDetail.scan.result as any).compliance_score = null
      const { stdout } = await runCommand(['scan:pdf', pdfPath, '--api-url', apiUrl])
      expect(stdout).not.to.contain('Scanner score')
    } finally {
      scanDetail.scan.result.compliance_score = original
    }
  })

  it('writes one CSV row per finding in scan.result.issues', async () => {
    const { error, stdout } = await runCommand(['scan:pdf', pdfPath, '--api-url', apiUrl, '--format', 'csv'])

    expect(error).to.equal(undefined)
    const rows = stdout.trim().split('\n').filter((line) => line.startsWith('handout.pdf,'))
    expect(rows).to.deep.equal([
      'handout.pdf,PDF document language not set,high,WCAG 3.1.1,',
      'handout.pdf,PDF has no structure tree (untagged PDF),critical,WCAG 1.3.1,',
    ])
  })

  it('lists a scanned file with its score and issue total in the batch summary', async () => {
    const dir = path.join(testDir, 'batch')
    await fs.mkdir(dir)
    await fs.writeFile(path.join(dir, 'handout.pdf'), '%PDF-1.4\n')

    const { error, stdout } = await runCommand(['scan:pdf', dir, '--api-url', apiUrl])

    expect(error).to.equal(undefined)
    expect(stdout).to.contain('1. handout.pdf')
    expect(stdout).to.contain('Scanner score: 62.5/100')
    expect(stdout).to.contain('Issues: 2')
  })

  it('keeps the full API response in JSON output', async () => {
    const { stdout } = await runCommand(['scan:pdf', pdfPath, '--api-url', apiUrl, '--format', 'json'])
    const parsed = JSON.parse(stdout.slice(stdout.indexOf('{')))
    expect(parsed.scan.result.summary.total).to.equal(2)
  })
})
