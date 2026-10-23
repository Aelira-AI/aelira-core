import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import * as fs from 'node:fs/promises'
import { createServer, type IncomingMessage } from 'node:http'
import * as path from 'node:path'

import { cleanTestDir, createTestDir, withTestConfig } from '../../helpers/setup.js'

interface ConvertRequest {
  contentType: string
  fieldNames: string[]
  file?: { bytes: number[]; name: string }
}

async function readBody(request: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = []
  for await (const chunk of request) chunks.push(Buffer.from(chunk))
  return Buffer.concat(chunks)
}

// The server route is `file: UploadFile = File(...)` in
// src/api/education/scan_routes.py, so a JSON body is rejected there. A real
// HTTP listener parses the wire body the same way a multipart server would.
describe('scan latex upload contract', () => {
  const source = '\\documentclass{article}\n\\begin{document}\n$E = mc^2$\n\\end{document}\n'
  const convertRequests: ConvertRequest[] = []
  const server = createServer(async (request, response) => {
    response.setHeader('Content-Type', 'application/json')
    const url = request.url ?? ''
    try {
      if (request.method === 'POST' && url === '/education/latex/convert') {
        const contentType = request.headers['content-type'] ?? ''
        const body = await readBody(request)
        const record: ConvertRequest = { contentType, fieldNames: [] }
        if (contentType.startsWith('multipart/form-data')) {
          const parsed = await new Request('http://localhost/upload', {
            body: new Uint8Array(body),
            headers: { 'content-type': contentType },
            method: 'POST',
          }).formData()
          record.fieldNames = [...parsed.keys()]
          const entry = parsed.get('file')
          if (entry && typeof entry !== 'string') {
            record.file = { bytes: [...new Uint8Array(await entry.arrayBuffer())], name: entry.name }
          }
        }

        convertRequests.push(record)
        if (!record.file) {
          response.statusCode = 422
          response.end(JSON.stringify({ detail: [{ loc: ['body', 'file'], msg: 'Field required' }] }))
          return
        }

        response.end(JSON.stringify({ progress: 0, scan_id: 'scan-latex-1', status: 'PROCESSING', success: true }))
        return
      }

      if (request.method === 'GET' && url === '/education/scans/scan-latex-1/progress') {
        response.end(JSON.stringify({ progress: 100, scan_id: 'scan-latex-1', status: 'COMPLETED', success: true }))
        return
      }

      if (request.method === 'GET' && url === '/education/scans/scan-latex-1') {
        response.end(JSON.stringify({ scan: { file_name: 'equations.tex', scan_id: 'scan-latex-1' }, success: true }))
        return
      }

      response.statusCode = 404
      response.end(JSON.stringify({ detail: 'Not Found' }))
    } catch {
      response.statusCode = 400
      response.end(JSON.stringify({ detail: 'Invalid body' }))
    }
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
    convertRequests.length = 0
    testDir = await createTestDir()
    restoreConfig = withTestConfig(testDir)
  })

  afterEach(async () => {
    restoreConfig()
    await cleanTestDir(testDir)
  })

  it('uploads the .tex source as the multipart file field the server reads', async () => {
    const texPath = path.join(testDir, 'equations.tex')
    await fs.writeFile(texPath, source)

    const { error, stdout } = await runCommand(['scan:latex', texPath, '--api-url', apiUrl, '--format', 'json'])

    expect(error).to.equal(undefined)
    expect(convertRequests).to.have.length(1)
    const [sent] = convertRequests
    expect(sent.contentType).to.match(/^multipart\/form-data; boundary=/)
    expect(sent.fieldNames).to.deep.equal(['file'])
    expect(sent.file).to.deep.equal({ bytes: [...Buffer.from(source)], name: 'equations.tex' })
    expect(stdout).to.contain('"scan_id": "scan-latex-1"')
  })

  it('documents every accepted output format in the help text', async () => {
    const { stdout } = await runCommand(['scan:latex', '--help'])
    const help = stdout.replaceAll(/\s+/g, ' ')
    expect(help).to.contain('Output format (console, csv or json)')
    expect(help).to.match(/<options: console\|csv\|json>/)
  })
})
