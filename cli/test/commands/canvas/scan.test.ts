import { runCommand } from '@oclif/test'
import { expect } from 'chai'

describe('canvas scan command', () => {
  it('shows help with --help flag', async () => {
    const { stdout } = await runCommand(['canvas:scan', '--help'])
    expect(stdout).to.contain('scan')
  })

  it('documents the required course argument', async () => {
    const { stdout } = await runCommand(['canvas:scan', '--help'])
    expect(stdout).to.contain('COURSE_ID')
  })

  it('exposes a wait flag for status polling', async () => {
    const { stdout } = await runCommand(['canvas:scan', '--help'])
    expect(stdout).to.contain('--wait')
  })
})
