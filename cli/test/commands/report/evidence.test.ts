import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

import { cleanTestDir, createTestDir } from '../../helpers/setup.js'

describe('report evidence command', () => {
  let originalFetch: typeof globalThis.fetch
  let originalApiKey: string | undefined
  let requestedUrls: string[]
  let respond: (url: string) => Response
  let testDir: string

  beforeEach(async () => {
    originalFetch = globalThis.fetch
    originalApiKey = process.env.AELIRA_API_KEY
    process.env.AELIRA_API_KEY = 'test-key'
    requestedUrls = []
    testDir = await createTestDir()
    respond = () =>
      new Response(new Uint8Array([0x25, 0x50, 0x44, 0x46]), {
        headers: { 'content-type': 'application/pdf' },
        status: 200,
      })
    globalThis.fetch = (async (input: Request | string | URL) => {
      const url = String(input)
      requestedUrls.push(url)
      return respond(url)
    }) as typeof fetch
  })

  afterEach(async () => {
    globalThis.fetch = originalFetch
    if (originalApiKey === undefined) delete process.env.AELIRA_API_KEY
    else process.env.AELIRA_API_KEY = originalApiKey
    await cleanTestDir(testDir)
  })

  it('downloads the canonical evidence-report endpoint without a score gate', async () => {
    const output = path.join(testDir, 'evidence.pdf')
    const { stdout } = await runCommand([
      'report',
      'evidence',
      'dept-123',
      '--api-url',
      'https://api.example.test',
      '--output',
      output,
    ])

    expect(requestedUrls).to.deep.equal([
      'https://api.example.test/analytics/evidence-report/dept-123',
    ])
    expect(await fs.readFile(output)).to.deep.equal(Buffer.from('%PDF'))
    expect(stdout).to.contain('does not determine conformance')
  })

  it('keeps certificate only as a deprecated alias for the same PDF', async () => {
    const output = path.join(testDir, 'legacy.pdf')
    const { stderr } = await runCommand([
      'report',
      'certificate',
      'dept-legacy',
      '--api-url',
      'https://api.example.test',
      '--output',
      output,
    ])

    expect(requestedUrls).to.deep.equal([
      'https://api.example.test/analytics/evidence-report/dept-legacy',
    ])
    expect(await fs.readFile(output)).to.deep.equal(Buffer.from('%PDF'))
    expect(stderr).to.contain('deprecated')
  })

  it('keeps compliance as a deprecated, bounded scan-statistics view', async () => {
    respond = (url) => {
      const data = url.endsWith('/stats')
        ? {
            overview: { total_files_scanned: 10, compliance_rate: 90 },
            compliance_scores: { average: 82, minimum: 60, maximum: 98 },
            issues: { critical: 1, high: 2, medium: 3, low: 4, total: 10 },
            scan_types: { pdf: 10 },
            compliance_breakdown: { compliant: 9, needs_work: 1, critical: 0 },
          }
        : {
            total_issues: 1,
            severity_filter: null,
            issues: [
              {
                description: 'Heading order requires review',
                file_name: 'course.pdf',
                issue_type: 'heading_order',
                severity: 'high',
              },
            ],
          }
      return Response.json(data)
    }

    const output = path.join(testDir, 'scan-evidence.json')
    const { stderr } = await runCommand([
      'report',
      'compliance',
      'dept-stats',
      '--api-url',
      'https://api.example.test',
      '--format',
      'json',
      '--output',
      output,
    ])

    const data = JSON.parse(await fs.readFile(output, 'utf8'))
    expect(requestedUrls).to.deep.equal([
      'https://api.example.test/education/compliance/dept-stats/stats',
      'https://api.example.test/education/compliance/dept-stats/issues',
    ])
    expect(data.report_kind).to.equal('scan_evidence_statistics')
    expect(data.scan_statistics.average_scan_score).to.equal(82)
    expect(data.scan_statistics.total_files_scanned).to.equal(10)
    expect(data.scan_statistics.file_types).to.deep.equal({ pdf: 10 })
    expect(data.findings).to.have.lengthOf(1)
    expect(data.findings[0].file_name).to.equal('course.pdf')
    expect(JSON.stringify(data)).to.not.match(/compliant_files|compliance_percentage|threshold/i)
    expect(data.scope_notice).to.contain('does not determine conformance')
    expect(stderr).to.contain('deprecated')
  })

  it('routes the compliance PDF option to the canonical evidence report', async () => {
    respond = (url) => {
      if (url.endsWith('/stats')) return Response.json({ total_files: 0 })
      if (url.endsWith('/issues')) return Response.json([])
      return new Response(Buffer.from('%PDF'), {
        headers: { 'content-type': 'application/pdf' },
      })
    }

    const output = path.join(testDir, 'compatibility.pdf')
    const { stdout } = await runCommand([
      'report',
      'compliance',
      'dept-pdf',
      '--api-url',
      'https://api.example.test',
      '--pdf',
      output,
    ])

    expect(requestedUrls.at(-1)).to.equal(
      'https://api.example.test/analytics/evidence-report/dept-pdf',
    )
    expect(requestedUrls).to.not.include(
      'https://api.example.test/education/compliance/dept-pdf/report/pdf',
    )
    expect(await fs.readFile(output)).to.deep.equal(Buffer.from('%PDF'))
    expect(stdout).to.contain('does not determine conformance')
    expect(stdout).to.not.match(/meeting threshold|compliance report|maintain current compliance/i)
  })

  it('makes the interactive menu lead to the canonical evidence command', async () => {
    const source = await fs.readFile(
      new URL('../../../src/commands/interactive.ts', import.meta.url),
      'utf8',
    )

    expect(source).to.contain('Download Accessibility Evidence Report')
    expect(source).to.match(/runCommand\('report', \['evidence'/)
    expect(source).to.not.contain('Generate Compliance Report')
  })

  it('documents only the compatibility flags and carries no score policy', async () => {
    const { stdout } = await runCommand(['report', 'compliance', '--help'])
    const source = await fs.readFile(
      new URL('../../../src/commands/report/compliance.ts', import.meta.url),
      'utf8',
    )

    expect(stdout).to.contain('Deprecated scan-evidence statistics view')
    expect(stdout).to.contain('--format')
    expect(stdout).to.contain('--output')
    expect(stdout).to.contain('--pdf')
    expect(stdout).to.not.contain('--date-range')
    expect(stdout).to.not.contain('--check-eligibility')
    expect(source).to.not.match(/renderRecommendations|average_score\s*<|meeting threshold/i)
  })
})
