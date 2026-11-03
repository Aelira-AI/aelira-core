import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import { createServer, Server, IncomingMessage, ServerResponse } from 'node:http'
import { AddressInfo } from 'node:net'

describe('integrations connect command', () => {
  let server: Server
  let apiUrl: string
  let lastRequest: {
    url?: string
    method?: string
    body?: any
  } = {}

  beforeEach(async () => {
    lastRequest = {}
    server = createServer((req: IncomingMessage, res: ServerResponse) => {
      let data = ''
      req.on('data', (chunk) => {
        data += chunk
      })
      req.on('end', () => {
        lastRequest.url = req.url
        lastRequest.method = req.method
        try {
          lastRequest.body = data ? JSON.parse(data) : {}
        } catch {
          lastRequest.body = data
        }

        res.setHeader('Content-Type', 'application/json')
        res.end(
          JSON.stringify({
            authorization_url: 'https://example.com/oauth/authorize?state=xyz',
          })
        )
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

  it('sends department_id and /blackboard/oauth/callback redirect_uri for blackboard', async () => {
    const { error, stderr, stdout } = await runCommand([
      'integrations:connect',
      'blackboard',
      '--instance-url',
      'https://blackboard.example.edu',
      '--department',
      'dept-science',
      '--api-url',
      apiUrl,
    ])
    if (error) {
      console.log('TEST STDOUT:', stdout)
      console.log('TEST STDERR:', stderr)
      console.log('TEST ERROR:', error)
    }

    expect(lastRequest.method).to.equal('POST')
    expect(lastRequest.url).to.equal('/blackboard/connect')
    expect(lastRequest.body).to.deep.equal({
      blackboard_instance_url: 'https://blackboard.example.edu',
      department_id: 'dept-science',
      redirect_uri: `${apiUrl}/blackboard/oauth/callback`,
    })
    expect(stdout).to.contain('https://example.com/oauth/authorize')
  })

  it('sends department_id and /moodle/callback redirect_uri for moodle', async () => {
    const { stdout } = await runCommand([
      'integrations:connect',
      'moodle',
      '--instance-url',
      'https://moodle.example.edu',
      '--department',
      'dept-arts',
      '--api-url',
      apiUrl,
    ])

    expect(lastRequest.method).to.equal('POST')
    expect(lastRequest.url).to.equal('/moodle/connect')
    expect(lastRequest.body).to.deep.equal({
      moodle_instance_url: 'https://moodle.example.edu',
      department_id: 'dept-arts',
      redirect_uri: `${apiUrl}/moodle/callback`,
    })
    expect(stdout).to.contain('https://example.com/oauth/authorize')
  })

  it('falls back to default department when not explicitly passed', async () => {
    await runCommand([
      'integrations:connect',
      'blackboard',
      '--instance-url',
      'https://blackboard.example.edu',
      '--api-url',
      apiUrl,
    ])

    expect(lastRequest.body?.department_id).to.be.a('string')
    expect(lastRequest.body?.department_id.length).to.be.greaterThan(0)
  })
})
