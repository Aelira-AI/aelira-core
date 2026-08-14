import { runCommand } from '@oclif/test'
import { expect } from 'chai'

describe('canvas files command', () => {
  it('shows help with --help flag', async () => {
    const { stdout } = await runCommand(['canvas:files', '--help'])
    expect(stdout).to.contain('files')
  })

  it('documents the required course argument', async () => {
    const { stdout } = await runCommand(['canvas:files', '--help'])
    expect(stdout).to.contain('COURSE_ID')
  })

  it('exposes a search filter', async () => {
    const { stdout } = await runCommand(['canvas:files', '--help'])
    expect(stdout).to.contain('--search')
  })
})
