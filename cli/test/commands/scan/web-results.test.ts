import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import * as fs from 'node:fs/promises'
import { createServer, type IncomingMessage } from 'node:http'
import * as path from 'node:path'

import { cleanTestDir, createTestDir, withTestConfig } from '../../helpers/setup.js'

// POST /education/web/scan, /web/batch-scan and /web/scan-sitemap in
// src/api/education/web_scan_routes.py only queue a durable job and
// acknowledge it ({scan_id | batch_scan_id, status: "processing", ...}).
// Results are read later from GET /education/scans/{scan_id}: file_name is
// the URL (or a batch label), the worker's structure is under scan.result,
// and the stored {"details": [...]} issue list is projected as a plain list.
const issues = [
  { criterion: '1.1.1', description: 'Images must have alternate text', element: '<img src="chart.png">', impact: 'critical' },
  { criterion: '3.1.1', description: '<html> element must have a lang attribute', element: '<html>', impact: 'serious' },
]

function detail(scanId: string, fileName: string, structure: Record<string, unknown>) {
  return {
    scan: {
      file_name: fileName,
      file_size_bytes: 0,
      pages: 1,
      result: {
        compliance_score: 49,
        issues,
        ocr_used: false,
        ollama_used: false,
        structure,
        suggestions: null,
        summary: { critical: 1, high: 1, low: 0, medium: 0, total: 2 },
        wcag_level: 'AA',
      },
      scan_id: scanId,
      scan_type: 'website',
      status: 'COMPLETED',
    },
    success: true,
  }
}

const details: Record<string, ReturnType<typeof detail>> = {
  'scan-batch': detail('scan-batch', 'Batch scan (1 URLs)', {
    total_pages_scanned: 1, total_urls_scanned: 1, urls_scanned: ['https://example.edu/'],
  }),
  'scan-sitemap': detail('scan-sitemap', 'https://example.edu/sitemap.xml', {
    sitemap_url: 'https://example.edu/sitemap.xml', total_pages_scanned: 2, total_urls_discovered: 3, total_urls_scanned: 2,
  }),
  'scan-web': detail('scan-web', 'https://example.edu/', {
    pages_scanned: 1, root_url: 'https://example.edu/', scan_time: 4.2,
  }),
}

const acknowledgements: Record<string, Record<string, unknown>> = {
  '/education/web/batch-scan': { batch_scan_id: 'scan-batch', status: 'processing', total_urls: 1 },
  '/education/web/scan': { scan_id: 'scan-web', status: 'processing' },
  '/education/web/scan-sitemap': { scan_id: 'scan-sitemap', status: 'processing' },
}

async function readJson(request: IncomingMessage): Promise<any> {
  const chunks: Buffer[] = []
  for await (const chunk of request) chunks.push(Buffer.from(chunk))
  return chunks.length > 0 ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {}
}

describe('scan web results', () => {
  const sent: Array<{ body: any; url: string }> = []
  const server = createServer(async (request, response) => {
    response.setHeader('Content-Type', 'application/json')
    const url = request.url ?? ''
    const body = request.method === 'POST' ? await readJson(request) : undefined
    if (request.method !== 'POST') request.resume()
    if (request.method === 'POST') sent.push({ body, url })
    // BatchWebScanRequest requires a urls list; FastAPI answers 422 without one.
    if (request.method === 'POST' && url === '/education/web/batch-scan' && !Array.isArray(body?.urls)) {
      response.statusCode = 422
      response.end(JSON.stringify({ detail: [{ loc: ['body', 'urls'], msg: 'Field required', type: 'missing' }] }))
      return
    }

    if (request.method === 'POST' && acknowledgements[url]) {
      response.end(JSON.stringify(acknowledgements[url]))
      return
    }

    const progress = url.match(/^\/education\/scans\/([\w-]+)\/progress$/)
    if (request.method === 'GET' && progress && details[progress[1]]) {
      response.end(JSON.stringify({ progress: 100, scan_id: progress[1], status: 'COMPLETED', success: true }))
      return
    }

    const scan = url.match(/^\/education\/scans\/([\w-]+)$/)
    if (request.method === 'GET' && scan && details[scan[1]]) {
      response.end(JSON.stringify(details[scan[1]]))
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
    sent.length = 0
    testDir = await createTestDir()
    restoreConfig = withTestConfig(testDir)
  })

  afterEach(async () => {
    restoreConfig()
    await cleanTestDir(testDir)
  })

  it('polls a single-page scan and prints its URL, pages, score and counts', async () => {
    const { error, stdout } = await runCommand(['scan:web', 'https://example.edu/', '--api-url', apiUrl])

    expect(error).to.equal(undefined)
    expect(stdout).to.contain('URL: https://example.edu/')
    expect(stdout).to.contain('Pages Scanned: 1')
    expect(stdout).to.contain('Scanner score: 49/100')
    expect(stdout).to.contain('- Critical: 1')
    expect(stdout).to.contain('- High: 1')
    expect(stdout).to.contain('Total: 2')
    expect(stdout).to.contain('Scan ID: scan-web')
    expect(stdout).not.to.contain('Unknown')
    expect(stdout).not.to.contain('N/A')
  })

  it('writes one CSV row per finding of a single-page scan', async () => {
    const { error, stdout } = await runCommand(['scan:web', 'https://example.edu/', '--api-url', apiUrl, '--format', 'csv'])

    expect(error).to.equal(undefined)
    const rows = stdout.split('\n').filter((line) => line.startsWith('https://example.edu/,'))
    expect(rows).to.deep.equal([
      'https://example.edu/,Images must have alternate text,critical,1.1.1,"<img src=""chart.png"">"',
      'https://example.edu/,<html> element must have a lang attribute,serious,3.1.1,<html>',
    ])
  })

  it('keeps the finished scan detail in JSON output', async () => {
    const { stdout } = await runCommand(['scan:web', 'https://example.edu/', '--api-url', apiUrl, '--format', 'json'])
    const parsed = JSON.parse(stdout.slice(stdout.indexOf('{')))
    expect(parsed.scan.result.summary.total).to.equal(2)
  })

  it('polls a sitemap scan and prints the URLs discovered and scanned', async () => {
    const { error, stdout } = await runCommand(['scan:web', 'https://example.edu/sitemap.xml', '--sitemap', '--max-pages', '2', '--api-url', apiUrl])

    expect(error).to.equal(undefined)
    expect(sent).to.deep.equal([{ body: { max_pages: 2, sitemap_url: 'https://example.edu/sitemap.xml' }, url: '/education/web/scan-sitemap' }])
    expect(stdout).to.contain('Sitemap: https://example.edu/sitemap.xml')
    expect(stdout).to.contain('URLs Discovered: 3')
    expect(stdout).to.contain('URLs Scanned: 2')
    expect(stdout).to.contain('Pages Scanned: 2')
    expect(stdout).to.contain('Scanner score: 49/100')
    expect(stdout).to.contain('Total: 2')
    expect(stdout).to.contain('Scan ID: scan-sitemap')
  })

  it('sends a batch as the urls list and depth the API accepts, then prints the aggregate', async () => {
    const { error, stdout } = await runCommand(['scan:web', 'https://example.edu/', 'https://example.edu/about', '--batch', '--api-url', apiUrl])

    expect(error).to.equal(undefined)
    expect(sent).to.deep.equal([{ body: { max_depth: 1, urls: ['https://example.edu/', 'https://example.edu/about'] }, url: '/education/web/batch-scan' }])
    expect(stdout).to.contain('URLs Scanned: 1')
    expect(stdout).to.contain('Pages Scanned: 1')
    expect(stdout).to.contain('Scanner score: 49/100')
    expect(stdout).to.contain('Scan ID: scan-batch')
  })

  it('reads batch URLs from --urls-file and passes --depth 0', async () => {
    const file = path.join(testDir, 'urls.txt')
    await fs.writeFile(file, '# course pages\nhttps://example.edu/\n\nhttps://example.edu/about\n')
    const { error } = await runCommand(['scan:web', '--batch', '--urls-file', file, '--depth', '0', '--api-url', apiUrl])

    expect(error).to.equal(undefined)
    expect(sent[0].body).to.deep.equal({ max_depth: 0, urls: ['https://example.edu/', 'https://example.edu/about'] })
  })

  it('refuses several URLs without --batch', async () => {
    const { error } = await runCommand(['scan:web', 'https://example.edu/', 'https://example.edu/about', '--api-url', apiUrl])

    expect(error?.message).to.contain('Several URLs need --batch')
    expect(sent).to.have.length(0)
  })
})
