import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

import { initializeConfig, setConfigValue } from '../../src/utils/config.js'
import { cleanTestDir, createTestDir, withTestConfig } from '../helpers/setup.js'

describe('API URL command precedence', () => {
  let originalApiUrl: string | undefined
  let originalFetch: typeof globalThis.fetch
  let requestedUrls: string[]
  let restoreConfig: () => void
  let testDir: string

  beforeEach(async () => {
    originalApiUrl = process.env.AELIRA_API_URL
    originalFetch = globalThis.fetch
    requestedUrls = []
    testDir = await createTestDir()
    restoreConfig = withTestConfig(testDir)
    process.env.AELIRA_API_URL = 'https://configured.example.test'
    globalThis.fetch = (async (input: Request | string | URL) => {
      requestedUrls.push(String(input))
      return Response.json({})
    }) as typeof fetch
  })

  afterEach(async () => {
    globalThis.fetch = originalFetch
    if (originalApiUrl === undefined) delete process.env.AELIRA_API_URL
    else process.env.AELIRA_API_URL = originalApiUrl
    restoreConfig()
    await cleanTestDir(testDir)
  })

  it('all network commands delegate an omitted API URL to shared resolution', async () => {
    const commandRoot = new URL('../../src/commands/', import.meta.url)
    const entries = await fs.readdir(commandRoot, { recursive: true })
    const affected: string[] = []
    const offenders: string[] = []

    for (const entry of entries) {
      if (!entry.endsWith('.ts')) continue
      const source = await fs.readFile(new URL(entry, commandRoot), 'utf8')
      const flag = source.match(/'api-url': Flags\.string\(\{([\s\S]*?)\}\)/)
      if (!flag) continue
      affected.push(entry)
      if (/\bdefault\s*:/.test(flag[1])) offenders.push(entry)
    }

    expect(affected.filter((entry) => entry !== 'config.ts')).to.have.lengthOf(28)
    expect(offenders).to.deep.equal([])
  })

  it('scan command honors the active-profile API URL', async () => {
    delete process.env.AELIRA_API_URL
    await initializeConfig()
    await setConfigValue('apiUrl', 'https://configured.example.test')

    await runCommand(['scan:web', 'https://example.test', '--format', 'json'])
    expect(requestedUrls).to.deep.equal([
      'https://configured.example.test/education/web/scan',
    ])
  })

  it('remediation command honors the configured API URL', async () => {
    await runCommand(['remediate', 'scan-123', '--format', 'json'])
    expect(requestedUrls).to.deep.equal([
      'https://configured.example.test/education/remediate/scan-123',
    ])
  })

  it('report command honors the configured API URL', async () => {
    globalThis.fetch = (async (input: Request | string | URL) => {
      requestedUrls.push(String(input))
      return new Response(Buffer.from('%PDF'), {
        headers: { 'content-type': 'application/pdf' },
      })
    }) as typeof fetch
    const output = path.join(testDir, 'evidence.pdf')

    await runCommand(['report:evidence', 'dept-123', '--output', output])

    expect(requestedUrls).to.deep.equal([
      'https://configured.example.test/analytics/evidence-report/dept-123',
    ])
  })

  it('Canvas command honors the configured API URL', async () => {
    await runCommand(['canvas:status', '--format', 'json'])
    expect(requestedUrls).to.deep.equal([
      'https://configured.example.test/canvas/status',
    ])
  })

  it('integration command honors the configured API URL', async () => {
    await runCommand(['integrations', '--format', 'json'])
    expect(requestedUrls).to.deep.equal([
      'https://configured.example.test/integrations/status',
    ])
  })

  it('documents the shared API URL precedence without command-local defaults', async () => {
    const rootReadme = await fs.readFile(new URL('../../../README.md', import.meta.url), 'utf8')
    const cliReadme = await fs.readFile(new URL('../../README.md', import.meta.url), 'utf8')

    expect(rootReadme).to.contain('1. An explicit `--api-url`')
    expect(rootReadme).to.contain('2. `AELIRA_API_URL`')
    expect(rootReadme).to.contain("3. The active profile's `apiUrl`")
    expect(rootReadme).to.contain('4. `http://localhost:8000`')
    expect(cliReadme).to.not.match(/api-url.*Default: http:\/\/localhost:8000/)
  })
})
