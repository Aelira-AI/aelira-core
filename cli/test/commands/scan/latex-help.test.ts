import { runCommand } from '@oclif/test'
import { expect } from 'chai'

import ScanLatex from '../../../src/commands/scan/latex.js'

describe('scan latex help and report', () => {
  it('describes MathML conversion and a review draft, and promises no ARIA labels', async () => {
    const { stdout } = await runCommand(['scan:latex', '--help'])
    expect(stdout).to.contain('Convert LaTeX equations to MathML')
    expect(stdout).to.contain('review draft')
    expect(stdout).to.contain('never written as an ARIA label')
    expect(stdout).to.not.match(/with ARIA labels/i)
    expect(ScanLatex.description).to.not.match(/with ARIA labels/i)
  })

  it('prints no ARIA label line, even when a response carries a count', () => {
    const lines: string[] = []
    const command = Object.create(ScanLatex.prototype) as any
    command.log = (line = '') => {
      lines.push(line)
    }

    command.displaySingleResult(
      {
        aria_labels_generated: 3,
        equations_converted: 2,
        filename: 'equations.tex',
        mathml_content: '<math></math>',
        success_rate: 100,
      },
      1500,
    )

    const output = lines.join('\n')
    expect(output).to.contain('MathML generated successfully')
    expect(output).to.not.match(/ARIA/i)
  })
})
