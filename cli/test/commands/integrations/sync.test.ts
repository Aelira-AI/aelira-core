import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import { createServer, IncomingMessage, Server, ServerResponse } from 'node:http'
import { AddressInfo } from 'node:net'

describe('integrations sync command', () => {
  let server: Server
  let apiUrl: string
  let lastSyncRequest: {
    body?: any
    method?: string
    url?: string
  } = {}
  const syncFoldersResponse: any = {
    folders: [
      { id: 'f-1', provider: 'google', is_active: true },
      { id: 'f-2', provider: 'google', is_active: true },
    ],
    total: 2,
  }

  beforeEach(async () => {
    lastSyncRequest = {}
    server = createServer((req: IncomingMessage, res: ServerResponse) => {
      let data = ''
      req.on('data', (chunk) => {
        data += chunk
      })
      req.on('end', () => {
        if (req.url?.startsWith('/integrations/sync-folders')) {
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(syncFoldersResponse))
          return
        }

        if (req.url === '/integrations/sync') {
          lastSyncRequest.url = req.url
          lastSyncRequest.method = req.method
          try {
            lastSyncRequest.body = data ? JSON.parse(data) : {}
          } catch {
            lastSyncRequest.body = data
          }

          res.setHeader('Content-Type', 'application/json')
          res.end(
            JSON.stringify({
              folder_ids: lastSyncRequest.body.folder_ids || [],
              job_id: 'job-sync-123',
              progress: 0,
              provider: lastSyncRequest.body.provider || 'google',
              status: 'pending',
            })
          )
          return
        }

        res.statusCode = 404
        res.end()
      })
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

  it('sends explicit provider and folder_ids when specified via flags', async () => {
    const { stdout } = await runCommand([
      'integrations:sync',
      '--provider',
      'google',
      '--folder-ids',
      'f-abc,f-xyz',
      '--api-url',
      apiUrl,
    ])

    expect(lastSyncRequest.method).to.equal('POST')
    expect(lastSyncRequest.url).to.equal('/integrations/sync')
    expect(lastSyncRequest.body).to.deep.equal({
      folder_ids: ['f-abc', 'f-xyz'],
      provider: 'google',
    })
    expect(stdout).to.contain('job-sync-123')
    expect(stdout).to.contain('GOOGLE')
  })

  it('outputs single job json matching API response when --format json is passed', async () => {
    const { stdout } = await runCommand([
      'integrations:sync',
      '--provider',
      'google',
      '--folder-ids',
      'f-abc',
      '--format',
      'json',
      '--api-url',
      apiUrl,
    ])

    const parsed = JSON.parse(stdout.slice(stdout.indexOf('{')))
    expect(parsed.job_id).to.equal('job-sync-123')
    expect(parsed.provider).to.equal('google')
    expect(parsed.folder_ids).to.deep.equal(['f-abc'])
  })

  it('resolves active sync folders automatically when folder-ids is omitted', async () => {
    const { stdout } = await runCommand([
      'integrations:sync',
      '--provider',
      'google',
      '--api-url',
      apiUrl,
    ])

    expect(lastSyncRequest.method).to.equal('POST')
    expect(lastSyncRequest.body).to.deep.equal({
      folder_ids: ['f-1', 'f-2'],
      provider: 'google',
    })
    expect(stdout).to.contain('job-sync-123')
  })
})
