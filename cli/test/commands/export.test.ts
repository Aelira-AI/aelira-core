import { runCommand } from '@oclif/test'
import { expect } from 'chai'

describe('export command', () => {
  it('shows help with --help flag', async () => {
    const { stdout } = await runCommand(['export', '--help'])
    expect(stdout).to.contain('Export scan history')
    expect(stdout).to.contain('--format')
    expect(stdout).to.contain('--output')
    expect(stdout).to.contain('--limit')
  })

  it('does not show --department flag', async () => {
    const { stdout } = await runCommand(['export', '--help'])
    expect(stdout).to.not.contain('--department')
  })

  it('shows default format is csv', async () => {
    const { stdout } = await runCommand(['export', '--help'])
    expect(stdout).to.contain('csv')
  })

  it('shows default limit of 50', async () => {
    const { stdout } = await runCommand(['export', '--help'])
    expect(stdout).to.contain('50')
  })
})
