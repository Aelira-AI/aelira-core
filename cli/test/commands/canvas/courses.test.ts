import { runCommand } from '@oclif/test'
import { expect } from 'chai'

describe('canvas courses command', () => {
  it('shows help with --help flag', async () => {
    const { stdout } = await runCommand(['canvas:courses', '--help'])
    expect(stdout).to.contain('courses')
  })

  it('exposes the standard connection flags', async () => {
    const { stdout } = await runCommand(['canvas:courses', '--help'])
    expect(stdout).to.contain('--api-url')
    expect(stdout).to.contain('--format')
    expect(stdout).to.contain('--department')
  })
})
