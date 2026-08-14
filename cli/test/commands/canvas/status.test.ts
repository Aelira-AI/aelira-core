import { runCommand } from '@oclif/test'
import { expect } from 'chai'

describe('canvas status command', () => {
  it('shows help with --help flag', async () => {
    const { stdout } = await runCommand(['canvas:status', '--help'])
    expect(stdout).to.contain('Canvas')
  })

  it('exposes the standard connection flags', async () => {
    const { stdout } = await runCommand(['canvas:status', '--help'])
    expect(stdout).to.contain('--api-url')
    expect(stdout).to.contain('--api-key')
    expect(stdout).to.contain('--format')
  })

  it('exposes a department override', async () => {
    const { stdout } = await runCommand(['canvas:status', '--help'])
    expect(stdout).to.contain('--department')
  })
})
