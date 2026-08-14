import { runCommand } from '@oclif/test'
import { expect } from 'chai'

describe('scan watch command', () => {
  it('shows description and all flags in help', async () => {
    const { stdout } = await runCommand(['scan', 'watch', '--help'])
    expect(stdout).to.contain('Watch a directory')
    expect(stdout).to.contain('--extensions')
    expect(stdout).to.contain('--debounce')
    expect(stdout).to.contain('--concurrency')
    expect(stdout).to.contain('--recursive')
  })

  it('lists default extensions in help', async () => {
    const { stdout } = await runCommand(['scan', 'watch', '--help'])
    expect(stdout).to.contain('.pdf')
    expect(stdout).to.contain('.docx')
    expect(stdout).to.contain('.html')
  })

  it('shows default debounce value in help', async () => {
    const { stdout } = await runCommand(['scan', 'watch', '--help'])
    expect(stdout).to.contain('2000')
  })

  it('shows default concurrency value in help', async () => {
    const { stdout } = await runCommand(['scan', 'watch', '--help'])
    expect(stdout).to.contain('3')
  })

  it('errors when no directory argument provided', async () => {
    const { error } = await runCommand(['scan', 'watch'])
    expect(error).to.exist
    expect(error?.message).to.contain('Missing')
  })

  it('errors for non-existent directory', async () => {
    const { error } = await runCommand(['scan', 'watch', '/tmp/definitely-not-a-real-dir-xyz123'])
    expect(error).to.exist
  })
})
