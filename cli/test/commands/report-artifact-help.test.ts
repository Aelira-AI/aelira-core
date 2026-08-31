import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import * as fs from 'node:fs/promises'

import Scan from '../../src/commands/scan.js'

describe('scan and analyze PDF report help', () => {
  for (const command of ['scan', 'analyze']) {
    it(`${command} documents the supported verified PDF behavior`, async () => {
      const { stdout } = await runCommand([command, '--help'])
      expect(stdout).to.contain('--pdf')
      expect(stdout).to.contain('verified server PDF report')
      expect(stdout).to.match(/--pdf accessibility-report\.pdf/)
      expect(stdout).to.not.contain('temporarily unavailable')
    })
  }

  it('routes both commands through the shared verified artifact client', async () => {
    for (const source of ['scan.ts', 'analyze.ts']) {
      const text = await fs.readFile(new URL(`../../src/commands/${source}`, import.meta.url), 'utf8')
      expect(text).to.contain('generateVerifiedPdfReport')
      expect(text).to.not.contain('/education/scans/generate-pdf')
    }
  })

  it('still generates a PDF when scan output is JSON', async () => {
    const command = Object.create(Scan.prototype) as any
    command.log = () => {}
    let generationArguments: any[] | undefined
    command.generatePdfReport = async (...args: any[]) => {
      generationArguments = args
    }

    await command.printResults(
      { passes: [], url: 'about:blank', violations: [] },
      {
        'api-url': 'https://api.example.test',
        format: 'json',
        pdf: 'report.pdf',
        timer: false,
      },
      Date.now(),
      '/private/source/fixture.html',
    )

    expect(generationArguments).to.deep.equal([
      { passes: [], url: 'about:blank', violations: [] },
      'report.pdf',
      'https://api.example.test',
      '/private/source/fixture.html',
    ])
  })
})
