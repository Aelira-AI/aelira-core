import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import * as fs from 'node:fs/promises'
import { createServer } from 'node:http'
import * as path from 'node:path'

import { cleanTestDir, createTestDir, withTestConfig } from '../../helpers/setup.js'

// Each fixture follows GET /education/scans/{scan_id} in
// src/api/education/scan_history_routes.py: file details under `scan`,
// findings under `scan.result`, counts in `scan.result.summary`, and the
// type-specific `structure` written by the job that ran the scan:
// PowerPoint, Word and Excel in src/api/education/scan_routes.py, LaTeX via
// scan_structure() in src/education/latex_evidence.py, and code in
// process_code_background() in src/api/education/web_scan_routes.py.
const findings = [
  { message: 'Image missing alternative text', rule: 'WCAG 1.1.1', severity: 'high' },
  { message: 'Link text is not descriptive', rule: 'WCAG 2.4.4', severity: 'high' },
  { message: 'Heading levels skip from 1 to 3', rule: 'WCAG 1.3.1', severity: 'medium' },
]

function detail(scanId: string, fileName: string, pages: number, structure: Record<string, unknown>) {
  return {
    scan: {
      file_name: fileName,
      file_size_bytes: 2_621_440,
      pages,
      result: {
        compliance_score: 71,
        issues: findings,
        ocr_used: false,
        ollama_used: false,
        structure,
        suggestions: [],
        summary: { critical: 0, high: 2, low: 0, medium: 1, total: 3 },
        wcag_level: 'AA',
      },
      scan_id: scanId,
      status: 'COMPLETED',
    },
    success: true,
  }
}


interface Case {
  command: string
  console: string[]
  endpoint: string
  ext: string
  response: ReturnType<typeof detail>
}

const cases: Case[] = [
  {
    command: 'scan:docx',
    console: ['File: notes.docx', 'Paragraphs: 40', 'Tables: 1', 'Images: 2'],
    endpoint: '/education/word/scan',
    ext: '.docx',
    response: detail('scan-docx', 'notes.docx', 40, {
      heading_issues_count: 1, total_images: 2, total_links: 4, total_lists: 3, total_paragraphs: 40, total_tables: 1,
    }),
  },
  {
    command: 'scan:xlsx',
    console: ['File: grades.xlsx', 'Sheets: 2', 'Rows: 30', 'Charts: 1', 'Images: 0'],
    endpoint: '/education/excel/scan',
    ext: '.xlsx',
    response: detail('scan-xlsx', 'grades.xlsx', 2, {
      sheets: [], total_charts: 1, total_images: 0, total_rows: 30, total_sheets: 2,
    }),
  },
  {
    command: 'scan:ppt',
    console: ['File: lecture.pptx', 'Slides: 5', 'Size: 2.50 MB'],
    endpoint: '/education/powerpoint/scan',
    ext: '.pptx',
    response: detail('scan-ppt', 'lecture.pptx', 5, {
      slides: [], total_images: 3, total_shapes: 20, total_slides: 5,
    }),
  },
  {
    command: 'scan:code',
    console: ['File: page.html', 'Lines of Code: 42'],
    endpoint: '/education/code/scan',
    ext: '.html',
    response: detail('scan-code', 'page.html', 1, {
      files_analyzed: 1, images_count: 1, project_name: 'page.html', total_lines: 42,
    }),
  },
  {
    command: 'scan:latex',
    console: ['File: notes.tex', 'Equations Found: 4', 'Converted: 3', 'Success Rate: 75.0%'],
    endpoint: '/education/latex/convert',
    ext: '.tex',
    response: detail('scan-latex', 'notes.tex', 1, {
      accessibility_status: 'not_verified', conversion_success_rate: 75, equations: [],
      failed_conversions: 1, successful_conversions: 3, total_equations: 4,
    }),
  },
]

describe('document scan console, CSV and batch summaries', () => {
  const byEndpoint = new Map(cases.map((c) => [c.endpoint, c]))
  const byScanId = new Map(cases.map((c) => [c.response.scan.scan_id, c]))
  const server = createServer((request, response) => {
    response.setHeader('Content-Type', 'application/json')
    const url = request.url ?? ''
    request.resume()
    const upload = request.method === 'POST' ? byEndpoint.get(url) : undefined
    if (upload) {
      response.end(JSON.stringify({ scan_id: upload.response.scan.scan_id, status: 'PROCESSING', success: true }))
      return
    }

    const progress = url.match(/^\/education\/scans\/([\w-]+)\/progress$/)
    if (request.method === 'GET' && progress && byScanId.has(progress[1])) {
      response.end(JSON.stringify({ progress: 100, scan_id: progress[1], status: 'COMPLETED', success: true }))
      return
    }

    const scanDetail = url.match(/^\/education\/scans\/([\w-]+)$/)
    if (request.method === 'GET' && scanDetail && byScanId.has(scanDetail[1])) {
      response.end(JSON.stringify(byScanId.get(scanDetail[1])!.response))
      return
    }

    if (request.method === 'GET' && url.startsWith('/education/scans?')) {
      response.end(JSON.stringify({
        scans: [{ created_at: '2026-01-01T12:00:00', file_name: 'notes.docx', scan_id: 'scan-docx', total_issues: 3 }],
        success: true,
        total_returned: 1,
      }))
      return
    }

    response.statusCode = 404
    response.end(JSON.stringify({ detail: 'Not Found' }))
  })
  let apiUrl: string
  let restoreConfig: () => void
  let testDir: string

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
  })

  afterEach(async () => {
    restoreConfig()
    await cleanTestDir(testDir)
  })

  for (const c of cases) {
    const fileName = c.response.scan.file_name

    describe(c.command, () => {
      it('prints the file details, scanner score and counts from the nested response', async () => {
        const file = path.join(testDir, fileName)
        await fs.writeFile(file, 'x')
        const { error, stdout } = await runCommand([c.command, file, '--api-url', apiUrl])

        expect(error).to.equal(undefined)
        for (const line of c.console) expect(stdout).to.contain(line)
        if (c.command !== 'scan:latex') {
          expect(stdout).to.contain('Scanner score: 71/100')
          expect(stdout).to.contain('- High: 2')
          expect(stdout).to.contain('- Medium: 1')
          expect(stdout).to.contain('Total: 3')
        }

        expect(stdout).to.contain(`Scan ID: ${c.response.scan.scan_id}`)
        expect(stdout).not.to.contain('Unknown')
        expect(stdout).not.to.contain('N/A')
      })

      it('writes one CSV row per finding in scan.result.issues', async () => {
        const file = path.join(testDir, fileName)
        await fs.writeFile(file, 'x')
        const { error, stdout } = await runCommand([c.command, file, '--api-url', apiUrl, '--format', 'csv'])

        expect(error).to.equal(undefined)
        const rows = stdout.split('\n').filter((line) => line.startsWith(`${fileName},`))
        expect(rows).to.have.length(3)
        expect(rows[0]).to.equal(`${fileName},Image missing alternative text,high,WCAG 1.1.1,`)
      })

      it('lists the scanned file in the batch summary', async () => {
        const dir = path.join(testDir, 'batch')
        await fs.mkdir(dir)
        await fs.writeFile(path.join(dir, fileName), 'x')
        const { error, stdout } = await runCommand([c.command, dir, '--api-url', apiUrl])

        expect(error).to.equal(undefined)
        expect(stdout).to.contain(`1. ${fileName}`)
        if (c.command === 'scan:latex') {
          expect(stdout).to.contain('Total Equations Converted: 3')
          expect(stdout).to.contain('Equations: 3')
        } else {
          expect(stdout).to.contain('Scanner score: 71/100')
          expect(stdout).to.contain('Issues: 3')
        }
      })
    })
  }

  describe('export', () => {
    it('exports the findings nested under scan.result', async () => {
      const output = path.join(testDir, 'history.csv')
      const { error } = await runCommand(['export', '--api-url', apiUrl, '--output', output])

      expect(error).to.equal(undefined)
      const rows = (await fs.readFile(output, 'utf8')).split('\n')
      expect(rows).to.have.length(4)
      expect(rows[1]).to.equal('scan-docx,2026-01-01,notes.docx,Image missing alternative text,high,WCAG 1.1.1,')
    })
  })
})
