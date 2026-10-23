import { expect } from 'chai'
import * as fs from 'node:fs/promises'
import { createServer } from 'node:http'
import * as path from 'node:path'

import ScanWatch from '../../../src/commands/scan/watch.js'
import { ApiClient } from '../../../src/utils/api-client.js'
import { cleanTestDir, createTestDir, withTestConfig } from '../../helpers/setup.js'

// The upload routes in src/api/education/scan_routes.py only acknowledge a
// queued scan ({success, scan_id, status: "PROCESSING", progress, ...}); the
// findings, counts and score arrive later from GET /education/scans/{scan_id}.
function watcher(lines: string[]): any {
  const command = Object.create(ScanWatch.prototype) as any
  command.browserRef = { browser: null }
  command.totalIssues = 0
  command.totalScans = 0
  command.log = (line = '') => { lines.push(line) }
  return command
}

describe('scan watch results', () => {
  const server = createServer((request, response) => {
    response.setHeader('Content-Type', 'application/json')
    const url = request.url ?? ''
    request.resume()
    if (request.method === 'POST' && url === '/education/pdf/scan') {
      response.end(JSON.stringify({ progress: 0, scan_id: 'scan-watch', status: 'PROCESSING', success: true }))
      return
    }

    if (request.method === 'GET' && url === '/education/scans/scan-watch/progress') {
      response.end(JSON.stringify({ progress: 100, scan_id: 'scan-watch', status: 'COMPLETED', success: true }))
      return
    }

    if (request.method === 'GET' && url === '/education/scans/scan-watch') {
      response.end(JSON.stringify({
        scan: {
          file_name: 'handout.pdf',
          result: {
            compliance_score: 49,
            issues: [],
            summary: { critical: 1, high: 2, low: 0, medium: 3, total: 6 },
          },
          scan_id: 'scan-watch',
        },
        success: true,
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

  it('waits for an uploaded scan and reports its counts and scanner score', async () => {
    const file = path.join(testDir, 'handout.pdf')
    await fs.writeFile(file, '%PDF-1.4\n')
    const lines: string[] = []
    const command = watcher(lines)

    await command.scanFile(file, new ApiClient({ apiUrl }))

    const result = lines.find((line) => line.includes('handout.pdf') && !line.includes('Changed:'))
    expect(result).to.contain('6 issues (1 critical, 2 high, 3 medium)')
    expect(result).to.contain('Scanner score: 49/100')
    expect(command.totalIssues).to.equal(6)
    expect(command.totalScans).to.equal(1)
  })

  it('reports a local HTML scan without inventing a score', async () => {
    const file = path.join(testDir, 'page.html')
    await fs.writeFile(file, '<!doctype html><html><body><img src="a.png"></body></html>')
    const lines: string[] = []
    const command = watcher(lines)
    // The axe-core result shape the Playwright scan returns; axe reports
    // violations by impact and has no score.
    command.scanHtml = async () => ({ violations: [{ impact: 'critical' }, { impact: 'serious' }] })

    await command.scanFile(file, new ApiClient({ apiUrl }))

    const result = lines.find((line) => line.includes('page.html') && !line.includes('Changed:'))
    expect(result).to.contain('2 issues (1 critical, 1 serious)')
    expect(result).not.to.match(/score/i)
  })


  // @axe-core/playwright refuses pages opened with browser.newPage(), so this
  // needs a real browser. It runs when AELIRA_CHROMIUM_EXECUTABLE points at a
  // Chromium build (src/utils/browser.ts) and is skipped otherwise.
  it('scans a local HTML file with axe in a real browser', async function () {
    if (!process.env.AELIRA_CHROMIUM_EXECUTABLE) this.skip()
    const file = path.join(testDir, 'page.html')
    await fs.writeFile(file, '<!doctype html><html><head><title>t</title></head><body><img src="a.png"></body></html>')
    const lines: string[] = []
    const command = watcher(lines)

    try {
      await command.scanFile(file, new ApiClient({ apiUrl }))
    } finally {
      await command.browserRef.browser?.close()
    }

    const result = lines.find((line) => line.includes('page.html') && !line.includes('Changed:'))
    expect(result).to.match(/^\[[\d:]+\] \d+ issues? \(/)
    expect(result).to.contain('critical')
  })
})
