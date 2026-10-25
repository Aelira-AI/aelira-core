import { expect } from 'chai'
import { createServer, IncomingMessage, Server } from 'node:http'
import { AddressInfo } from 'node:net'

import { createApiKeyFromSession } from '../../src/utils/session-api-key.js'

const SESSION = 'session-value'
const TOKEN = 'csrf-value'

function cookies(request: IncomingMessage): Record<string, string> {
  return Object.fromEntries((request.headers.cookie ?? '').split(';').filter(Boolean).map((part) => {
    const [key, ...value] = part.trim().split('=')
    return [key, value.join('=')]
  }))
}

// Mirrors the API's CSRF middleware: a cookie-authenticated POST needs a
// csrf_token cookie and a matching X-CSRF-Token header.
function csrfApi(csrfToken: unknown = TOKEN): Server {
  return createServer((request, response) => {
    const jar = cookies(request)
    if (request.method === 'GET' && request.url === '/api/csrf-token') {
      response.setHeader('Set-Cookie', `csrf_token=${TOKEN}; HttpOnly; SameSite=Lax`)
      response.setHeader('Content-Type', 'application/json')
      response.end(JSON.stringify({ csrf_token: csrfToken }))
      return
    }

    if (request.method === 'POST' && request.url === '/auth/keys') {
      const header = request.headers['x-csrf-token']
      if (!jar.csrf_token || !header || jar.csrf_token !== header) {
        response.statusCode = 403
        response.end(JSON.stringify({ detail: 'CSRF token missing. Include X-CSRF-Token header.' }))
        return
      }

      if (jar.aelira_access !== SESSION) {
        response.statusCode = 401
        response.end()
        return
      }

      let body = ''
      request.on('data', (chunk) => { body += chunk })
      request.on('end', () => {
        response.setHeader('Content-Type', 'application/json')
        response.end(JSON.stringify({ full_key: 'ak_created', name: JSON.parse(body).name }))
      })
      return
    }

    response.statusCode = 404
    response.end()
  })
}

describe('createApiKeyFromSession', () => {
  let server: Server
  let apiUrl: string

  async function start(api: Server): Promise<void> {
    server = api
    await new Promise<void>((resolve) => { server.listen(0, '127.0.0.1', resolve) })
    apiUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  }

  afterEach(async () => {
    await new Promise<void>((resolve) => { server.close(() => resolve()) })
  })

  it('creates the key with the session cookie and a matching CSRF token', async () => {
    await start(csrfApi())
    const response = await createApiKeyFromSession(apiUrl, SESSION, 'CLI — test')
    expect(response.status).to.equal(200)
    expect(await response.json()).to.deep.equal({ full_key: 'ak_created', name: 'CLI — test' })
  })

  it('is refused by the same API when the key request carries only the session cookie', async () => {
    await start(csrfApi())
    const response = await fetch(`${apiUrl}/auth/keys`, {
      body: JSON.stringify({ name: 'CLI — test' }),
      headers: { 'Content-Type': 'application/json', 'Cookie': `aelira_access=${SESSION}` },
      method: 'POST',
    })
    expect(response.status).to.equal(403)
  })

  it('throws when the API returns no CSRF token', async () => {
    await start(csrfApi(''))
    let error: unknown
    try {
      await createApiKeyFromSession(apiUrl, SESSION, 'CLI — test')
    } catch (error_) {
      error = error_
    }

    expect(error).to.be.instanceOf(Error)
    expect((error as Error).message).to.equal('The API did not return a CSRF token.')
  })
})
