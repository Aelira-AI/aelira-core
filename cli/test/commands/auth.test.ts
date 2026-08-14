import { runCommand } from '@oclif/test'
import { expect } from 'chai'

import * as configUtils from '../../src/utils/config.js'
import { cleanTestDir, createTestDir, withTestConfig } from '../helpers/setup.js'

describe('auth command', () => {
  it('shows help with --help flag', async () => {
    const { stdout } = await runCommand(['auth', '--help'])
    expect(stdout).to.contain('Authenticate')
    expect(stdout).to.contain('login')
    expect(stdout).to.contain('logout')
  })

  it('shows api-url flag in help', async () => {
    const { stdout } = await runCommand(['auth', '--help'])
    expect(stdout).to.contain('--api-url')
  })

  it('lists login and logout as valid actions', async () => {
    const { stdout } = await runCommand(['auth', '--help'])
    expect(stdout).to.contain('login')
    expect(stdout).to.contain('logout')
  })

  describe('auth logout', () => {
    let testDir: string
    let restoreEnv: () => void

    beforeEach(async () => {
      testDir = await createTestDir()
      restoreEnv = withTestConfig(testDir)
      await configUtils.initializeConfig()
    })

    afterEach(async () => {
      restoreEnv()
      await cleanTestDir(testDir)
    })

    it('prints not logged in when no API key', async () => {
      const { stdout } = await runCommand(['auth', 'logout'])
      expect(stdout).to.contain('Not currently logged in')
    })

    it('removes API key from config', async () => {
      await configUtils.setConfigValue('apiKey', 'test-key-123')
      await runCommand(['auth', 'logout'])
      const config = await configUtils.readConfig()
      expect(config.profiles.default.apiKey).to.equal('')
    })

    it('prints confirmation after logout', async () => {
      await configUtils.setConfigValue('apiKey', 'test-key-123')
      const { stdout } = await runCommand(['auth', 'logout'])
      expect(stdout).to.contain('Logged out')
    })
  })
})
