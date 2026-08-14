import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import { createServer, IncomingMessage, Server, ServerResponse } from 'node:http'
import { AddressInfo } from 'node:net'

/**
 * Response-shape coverage for `canvas scan`.
 *
 * These exist because the wrong response key (`data.file_ids`, which the API
 * never returns) survived three code-reading reviews: reading TypeScript cannot
 * see a contract that lives in Python in another directory. A canned response
 * off a real socket can.
 *
 * The canned payloads mirror the backend models verbatim:
 *   CanvasBulkScanResponse  — backend/src/api/canvas_scan_routes.py:88-93
 *   CourseScanStatusResponse — backend/src/api/canvas_scan_routes.py:474-476
 */

interface RecordedRequest {
  method: string
  url: string
}

describe('canvas scan response handling', () => {
  describe('with jobs returned', () => {
    let server: Server
    let apiUrl: string
    let requests: RecordedRequest[]

    beforeEach(async () => {
      requests = []
      server = createServer((req: IncomingMessage, res: ServerResponse) => {
        requests.push({ method: req.method ?? '', url: req.url ?? '' })
        res.setHeader('Content-Type', 'application/json')

        if (req.url?.startsWith('/canvas/scan/bulk')) {
          res.end(
            JSON.stringify({
              jobs: [
                { file_id: '555', file_name: 'syllabus.pdf', job_id: 'job-1' },
                { file_id: '556', file_name: 'week1.pptx', job_id: 'job-2' },
              ],
              skipped: 0,
              total: 2,
            }),
          )
          return
        }

        if (req.url?.includes('/scan-status')) {
          res.end(
            JSON.stringify({
              files: [
                { file_name: 'syllabus.pdf', provider_file_id: '555', status: 'completed' },
                { file_name: 'week1.pptx', provider_file_id: '556', status: 'completed' },
              ],
            }),
          )
          return
        }

        res.statusCode = 404
        res.end('{}')
      })

      await new Promise<void>((resolve) => {
        server.listen(0, '127.0.0.1', resolve)
      })
      apiUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    })

    afterEach(async () => {
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()))
      })
    })

    it('extracts file ids from the jobs list, so --wait actually polls', async () => {
      await runCommand(['canvas:scan', '101', '--wait', '--api-url', apiUrl])

      const statusRequests = requests.filter((r) => r.url.includes('/scan-status'))
      expect(statusRequests, 'expected --wait to poll scan-status').to.have.lengthOf(1)
    })

    it('sends file_ids as one comma-separated query parameter', async () => {
      await runCommand(['canvas:scan', '101', '--wait', '--api-url', apiUrl])

      const statusRequest = requests.find((r) => r.url.includes('/scan-status'))
      const query = new URLSearchParams(statusRequest!.url.split('?')[1])
      expect(query.getAll('file_ids')).to.deep.equal(['555,556'])
    })
  })

  describe('with no files returned', () => {
    let server: Server
    let apiUrl: string

    // The suppression branch at canvas/scan.ts:73 only runs when the bulk-scan
    // response yields zero file ids — an empty `jobs` array is the only fixture
    // that reaches it. The two-job fixture above never exercises this branch.
    beforeEach(async () => {
      server = createServer((req: IncomingMessage, res: ServerResponse) => {
        res.setHeader('Content-Type', 'application/json')

        if (req.url?.startsWith('/canvas/scan/bulk')) {
          res.end(JSON.stringify({ jobs: [], skipped: 0, total: 0 }))
          return
        }

        res.statusCode = 404
        res.end('{}')
      })

      await new Promise<void>((resolve) => {
        server.listen(0, '127.0.0.1', resolve)
      })
      apiUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    })

    afterEach(async () => {
      await new Promise<void>((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()))
      })
    })

    it('keeps --format json parseable on the wait path when there are no files to poll', async () => {
      const { stdout } = await runCommand([
        'canvas:scan',
        '101',
        '--wait',
        '--format',
        'json',
        '--api-url',
        apiUrl,
      ])

      expect(stdout).to.not.contain('Skipping status polling')
    })

    it('tells the console user status polling was skipped when there are no files', async () => {
      const { stdout } = await runCommand(['canvas:scan', '101', '--wait', '--api-url', apiUrl])

      expect(stdout).to.contain('Skipping status polling')
    })
  })
})
