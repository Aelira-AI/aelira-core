import { runCommand } from '@oclif/test'
import { expect } from 'chai'

import ScanLatex from '../../../src/commands/scan/latex.js'

describe('scan latex help and report', () => {
  it('describes MathML conversion and a review draft, and promises no ARIA labels', async () => {
    const { stdout } = await runCommand(['scan:latex', '--help'])
    // Help wraps long descriptions across lines, so compare the words.
    const help = stdout.replaceAll(/\s+/g, ' ')
    expect(help).to.contain('Convert LaTeX equations to MathML (supports ChemFig, mhchem, physics notation, TikZ).')
    expect(help).to.contain('Model-drafted equation descriptions are kept as review drafts and never written as ARIA labels.')
    expect(help).to.not.match(/with ARIA labels/i)
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
        scan: {
          file_name: 'equations.tex',
          result: {
            structure: {
              aria_labels_generated: 3,
              conversion_success_rate: 100,
              failed_conversions: 0,
              successful_conversions: 2,
              total_equations: 2,
            },
          },
        },
      },
      1500,
    )

    const output = lines.join('\n')
    expect(output).to.contain('Converted: 2')
    expect(output).to.not.match(/ARIA/i)
  })
})
