import { expect } from 'chai'
import LegacyFormData from 'form-data'
import { createServer } from 'node:http'

import { ApiClient } from '../../src/utils/api-client.js'

// A real HTTP parser catches malformed wire bodies that a fetch stub accepts.
describe('ApiClient multipart wire format', () => {
  const bytes = Buffer.from([0, 255, 80, 75, 3, 4, 10, 13, 128])
  const server = createServer(async (request, response) => {
    try {
      const chunks: Buffer[] = []
      for await (const chunk of request) chunks.push(Buffer.from(chunk))
      const parsed = await new Request('http://localhost/upload', {
        method: 'POST', headers: request.headers as Record<string, string>, body: Buffer.concat(chunks),
      }).formData()
      const files = await Promise.all(parsed.getAll('files').map(async (entry) => {
        if (typeof entry === 'string') throw new Error('Expected uploaded file')
        return { name: entry.name, bytes: [...new Uint8Array(await entry.arrayBuffer())] }
      }))
      response.setHeader('Content-Type', 'application/json')
      response.end(JSON.stringify({ files, skip_ocr: parsed.get('skip_ocr') }))
    } catch {
      response.statusCode = 400
      response.end('Invalid multipart body')
    }
  })
  let url: string

  before(async () => {
    await new Promise<void>((resolve) => { server.listen(0, '127.0.0.1', resolve) })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Missing test listener')
    url = `http://127.0.0.1:${address.port}`
  })

  after(async () => {
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) => { server.close((error) => { error ? reject(error) : resolve() }) })
  })

  it('sends buffered form-data files and scalar fields as multipart bytes', async () => {
    const form = new LegacyFormData()
    form.append('files', bytes, 'one.xlsx')
    form.append('files', bytes, 'two.pdf')
    form.append('skip_ocr', 'true')
    const api = new ApiClient({ apiUrl: url, apiKey: 'synthetic-multipart-key' })
    const response = await api.postForm('/upload', form as any)
    expect(await response.json()).to.deep.equal({
      files: [{ name: 'one.xlsx', bytes: [...bytes] }, { name: 'two.pdf', bytes: [...bytes] }],
      skip_ocr: 'true',
    })
  })

  it('retains native fetch FormData boundary generation', async () => {
    const form = new FormData()
    form.append('files', new Blob([new Uint8Array(bytes).buffer]), 'native.docx')
    form.append('skip_ocr', 'false')
    const api = new ApiClient({ apiUrl: url, apiKey: 'synthetic-multipart-key' })
    const response = await api.postForm('/upload', form)
    expect(await response.json()).to.deep.equal({
      files: [{ name: 'native.docx', bytes: [...bytes] }], skip_ocr: 'false',
    })
  })
})
