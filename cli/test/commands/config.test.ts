import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import { restore } from 'sinon'

import * as configUtils from '../../src/utils/config.js'
import { cleanTestDir, createTestDir, withTestConfig } from '../helpers/setup.js'

describe('config command', () => {
  let testDir: string
  let restoreEnv: () => void

  beforeEach(async () => {
    testDir = await createTestDir()
    restoreEnv = withTestConfig(testDir)
    // Pre-initialize config so commands don't hit interactive prompts
    await configUtils.initializeConfig()
  })

  afterEach(async () => {
    restoreEnv()
    restore()
    await cleanTestDir(testDir)
  })

  // NOTE: `config init` is NOT tested via runCommand because it uses
  // interactive @clack/prompts (text, confirm, select) which hang in
  // non-TTY test environments. initializeConfig() is tested directly
  // in the config utility tests instead.

  it('config show outputs configuration', async () => {
    const { stdout } = await runCommand(['config', 'show'])
    expect(stdout).to.contain('default')
  })

  it('config set api-url updates the URL', async () => {
    await runCommand(['config', 'set', 'api-url', 'https://new-api.test'])
    const config = await configUtils.readConfig()
    expect(config.profiles.default.apiUrl).to.equal('https://new-api.test')
  })

  it('config set api-key updates the key', async () => {
    await runCommand(['config', 'set', 'api-key', 'sk_test_123'])
    const config = await configUtils.readConfig()
    expect(config.profiles.default.apiKey).to.equal('sk_test_123')
  })

  it('config profile list shows profiles', async () => {
    const { stdout } = await runCommand(['config', 'profile', 'list'])
    expect(stdout).to.contain('default')
  })

  it('config profile use switches active profile', async () => {
    await configUtils.createProfile('staging', { apiUrl: 'https://staging.test' })
    await runCommand(['config', 'profile', 'use', 'staging'])
    const config = await configUtils.readConfig()
    expect(config.activeProfile).to.equal('staging')
  })

  it('config validate reports success when connected', async () => {
    // Reassign globalThis.fetch (sinon can't stub ES module exports)
    const originalFetch = globalThis.fetch
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ status: 'ok' }), { status: 200 })
    ) as typeof fetch
    try {
      const { stdout } = await runCommand(['config', 'validate'])
      expect(stdout).to.contain('Connected')
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('config validate reports failure when unreachable', async () => {
    const originalFetch = globalThis.fetch
    globalThis.fetch = (async () => {
      throw new Error('ECONNREFUSED')
    }) as typeof fetch
    try {
      const { stdout } = await runCommand(['config', 'validate'])
      expect(stdout).to.contain('Could not connect')
    } finally {
      globalThis.fetch = originalFetch
    }
  })
})
