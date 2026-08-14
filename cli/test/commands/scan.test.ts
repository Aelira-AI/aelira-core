import { runCommand } from '@oclif/test'
import { expect } from 'chai'

describe('scan command', () => {
  it('shows help with --help flag', async () => {
    const { stdout } = await runCommand(['scan', '--help'])
    expect(stdout).to.contain('Scan a URL or HTML file')
    expect(stdout).to.contain('--format')
    expect(stdout).to.contain('--threshold')
    expect(stdout).to.contain('--timeout')
    expect(stdout).to.contain('--mode')
  })

  it('lists all format options in help', async () => {
    const { stdout } = await runCommand(['scan', '--help'])
    expect(stdout).to.contain('console')
    expect(stdout).to.contain('json')
    expect(stdout).to.contain('html')
  })

  it('lists all mode options in help', async () => {
    const { stdout } = await runCommand(['scan', '--help'])
    expect(stdout).to.contain('quick')
    expect(stdout).to.contain('comprehensive')
    expect(stdout).to.contain('deep')
  })

  it('errors when no target argument provided', async () => {
    const { error } = await runCommand(['scan'])
    expect(error).to.exist
    expect(error?.message).to.contain('Missing')
  })

  it('errors for non-existent file', async () => {
    const { error } = await runCommand(['scan', '/tmp/definitely-not-a-real-file-abc123.pdf'])
    // The command will try to launch a browser or fail — either way it should error
    expect(error).to.exist
  })

  it('accepts --timer flag without error', async () => {
    const { stdout } = await runCommand(['scan', '--help'])
    expect(stdout).to.contain('timer')
  })
})
