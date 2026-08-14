import { runCommand } from '@oclif/test'
import { expect } from 'chai'

describe('canvas remediate command', () => {
  it('shows help with --help flag', async () => {
    const { stdout } = await runCommand(['canvas:remediate', '--help'])
    expect(stdout).to.contain('remediate')
  })

  it('documents both required arguments', async () => {
    const { stdout } = await runCommand(['canvas:remediate', '--help'])
    expect(stdout).to.contain('COURSE_ID')
    expect(stdout).to.contain('FILE_ID')
  })

  it('describes write-back as replacing course content', async () => {
    const { stdout } = await runCommand(['canvas:remediate', '--help'])
    expect(stdout).to.contain('--upload-back')
    expect(stdout.toLowerCase()).to.contain('replace')
  })
})
