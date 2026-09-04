import { runCommand } from '@oclif/test'
import { expect } from 'chai'

describe('ci command', () => {
  it('shows help with --help flag', async () => {
    const { stdout } = await runCommand(['ci', '--help'])
    expect(stdout).to.contain('CI/CD')
    expect(stdout).to.contain('--format')
    expect(stdout).to.contain('--threshold')
    expect(stdout).to.contain('--badge')
    expect(stdout).to.contain('--fail-on')
  })

  it('lists all format options in help', async () => {
    const { stdout } = await runCommand(['ci', '--help'])
    expect(stdout).to.contain('console')
    expect(stdout).to.contain('json')
    expect(stdout).to.contain('junit')
    expect(stdout).to.contain('sarif')
  })

  it('lists all fail-on severity options in help', async () => {
    const { stdout } = await runCommand(['ci', '--help'])
    expect(stdout).to.contain('critical')
    expect(stdout).to.contain('serious')
    expect(stdout).to.contain('moderate')
    expect(stdout).to.contain('minor')
  })

  it('errors when no target argument provided', async () => {
    const { error } = await runCommand(['ci'])
    expect(error).to.exist
    expect(error?.message).to.contain('Missing')
  })

  it('shows default threshold of 80 in help', async () => {
    const { stdout } = await runCommand(['ci', '--help'])
    expect(stdout).to.contain('80')
  })

  it('shows timeout option in help', async () => {
    const { stdout } = await runCommand(['ci', '--help'])
    expect(stdout).to.contain('timeout')
  })
})
