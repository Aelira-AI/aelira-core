import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import { createHash } from 'node:crypto'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

import { cleanTestDir, createTestDir } from '../helpers/setup.js'

const PDF = Buffer.from('%PDF-1.7\nverified command report\n%%EOF')
const SHA = createHash('sha256').update(PDF).digest('hex')
const JOB_ID = '11111111-1111-4111-8111-111111111111'
const STATUS_URL = `/education/reports/${JOB_ID}`
const DOWNLOAD_URL = `${STATUS_URL}/download`

describe('scan and analyze verified PDF journey', () => {
  let originalFetch: typeof globalThis.fetch
  let originalKey: string | undefined
  let testDir: string
  let submittedEvidence: any[]

  beforeEach(async () => {
    originalFetch = globalThis.fetch
    originalKey = process.env.AELIRA_API_KEY
    process.env.AELIRA_API_KEY = 'test-key'
    submittedEvidence = []
    testDir = await createTestDir()
    globalThis.fetch = (async (input: Request | string | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/ai/batch-analyze')) {
        return Response.json({
          results: [{
            classification: { explanation: 'Provide meaningful alternate text' },
            rule_id: 'image-alt-0',
          }],
        })
      }

      if (url.endsWith('/education/reports')) {
        submittedEvidence.push(JSON.parse(String(init?.body)))
        return Response.json(
          { job_id: JOB_ID, status: 'pending', status_url: STATUS_URL },
          { status: 202 },
        )
      }

      if (url.endsWith(DOWNLOAD_URL)) {
        return new Response(PDF, {
          headers: {
            'content-length': String(PDF.length),
            'content-type': 'application/pdf',
            'x-artifact-id': JOB_ID,
            'x-checksum-sha256': SHA,
          },
        })
      }

      if (url.endsWith(STATUS_URL)) {
        return Response.json({
          artifact: {
            artifact_id: JOB_ID,
            content_type: 'application/pdf',
            download_url: DOWNLOAD_URL,
            filename: `aelira-accessibility-report-${JOB_ID}.pdf`,
            sha256: SHA,
            size_bytes: PDF.length,
          },
          job_id: JOB_ID,
          progress: 100,
          status: 'completed',
        })
      }

      throw new Error(`Unexpected request: ${url}`)
    }) as typeof fetch
  })

  afterEach(async () => {
    globalThis.fetch = originalFetch
    if (originalKey === undefined) delete process.env.AELIRA_API_KEY
    else process.env.AELIRA_API_KEY = originalKey
    await cleanTestDir(testDir)
  })

  for (const command of ['scan', 'analyze'] as const) {
    it(`${command} publishes the exact verified server artifact`, async () => {
      const fixture = path.join(testDir, 'private-source.html')
      const destination = path.join(testDir, `${command}-report.pdf`)
      await fs.writeFile(
        fixture,
        '<!doctype html><html><body><main><button></button></main></body></html>',
      )

      const { error } = await runCommand([
        command,
        fixture,
        '--api-url',
        'https://api.example.test',
        '--format',
        'json',
        '--pdf',
        destination,
      ])

      expect(error).to.equal(undefined)
      expect(await fs.readFile(destination)).to.deep.equal(PDF)
      expect(submittedEvidence).to.have.length(1)
      expect(submittedEvidence[0].report_kind).to.equal(command)
      expect(submittedEvidence[0].target).to.equal('private-source.html')
      expect(JSON.stringify(submittedEvidence[0])).to.not.contain(testDir)
    })
  }
})
