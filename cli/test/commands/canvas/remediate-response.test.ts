import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import { createServer, Server, ServerResponse } from 'node:http'
import { AddressInfo } from 'node:net'

/**
 * Response-shape coverage for `canvas remediate`, the only Canvas command that
 * can overwrite content in a live course.
 *
 * The route returns HTTP 200 with success=false when Canvas is not connected
 * (backend/src/api/canvas_routes.py:499-503), so "the request did not throw" is
 * not the same as "the job was queued". CanvasRemediateResponse is
 * {success, scan_id, job_id, message} — canvas_routes.py:80-86.
 */

describe('canvas remediate response handling', () => {
  let server: Server
  let apiUrl: string
  let payload: Record<string, unknown>

  beforeEach(async () => {
    server = createServer((_req, res: ServerResponse) => {
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify(payload))
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

  it('fails with the API message when success is false', async () => {
    payload = {
      message: 'Canvas not connected. Please connect your Canvas account first.',
      success: false,
    }

    const { error, stdout } = await runCommand([
      'canvas:remediate',
      '101',
      '555',
      '--api-url',
      apiUrl,
    ])

    expect(stdout).to.contain('Canvas not connected')
    expect(error, 'success: false must exit non-zero').to.exist
    expect(error?.oclif?.exit).to.equal(1)
  })

  it('does not claim a completed write-back when success is false', async () => {
    payload = { message: 'Canvas not connected.', success: false }

    const { stdout } = await runCommand([
      'canvas:remediate',
      '101',
      '555',
      '--upload-back',
      '--yes',
      '--api-url',
      apiUrl,
    ])

    expect(stdout).to.not.contain('written back')
    expect(stdout).to.not.contain('Canvas will be updated')
  })

  it('describes the write-back as queued, not done, on success', async () => {
    payload = { job_id: 'job-42', message: 'Queued', scan_id: 'scan-7', success: true }

    const { stdout } = await runCommand([
      'canvas:remediate',
      '101',
      '555',
      '--upload-back',
      '--yes',
      '--api-url',
      apiUrl,
    ])

    expect(stdout).to.contain('job-42')
    expect(stdout).to.contain('Canvas will be updated when job job-42 completes')
    expect(stdout, 'the file is only queued at this point').to.not.contain('was written back')
  })
})
