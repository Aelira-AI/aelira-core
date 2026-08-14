import { expect } from 'chai'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'
import * as sinon from 'sinon'

import {
  configExists,
  createProfile,
  DEFAULT_CONFIG,
  deleteProfile,
  getActiveProfile,
  getApiKey,
  getApiUrl,
  getConfigPath,
  getDepartment,
  initializeConfig,
  listProfiles,
  readConfig,
  setActiveProfile,
  setConfigValue,
  validateConnection,
  writeConfig,
} from '../../src/utils/config.js'
import { cleanTestDir, createTestDir, withTestConfig } from '../helpers/setup.js'

describe('config utility', () => {
  let testDir: string
  let restoreEnv: () => void

  beforeEach(async () => {
    testDir = await createTestDir()
    restoreEnv = withTestConfig(testDir)
  })

  afterEach(async () => {
    restoreEnv()
    sinon.restore()
    await cleanTestDir(testDir)
  })

  describe('configExists', () => {
    it('returns false when no config file exists', async () => {
      expect(await configExists()).to.be.false
    })

    it('returns true after config is written', async () => {
      await initializeConfig()
      expect(await configExists()).to.be.true
    })
  })

  describe('readConfig', () => {
    it('returns default values when no file exists', async () => {
      const config = await readConfig()
      expect(config.activeProfile).to.equal('default')
      expect(config.profiles.default.apiUrl).to.equal('http://localhost:8000')
    })

    it('parses valid config JSON correctly', async () => {
      await initializeConfig()
      await setConfigValue('apiUrl', 'https://api.test.com')
      const config = await readConfig()
      expect(config.profiles.default.apiUrl).to.equal('https://api.test.com')
    })

    it('handles corrupt JSON gracefully', async () => {
      const configFile = getConfigPath()
      await fs.mkdir(path.dirname(configFile), { recursive: true })
      await fs.writeFile(configFile, '{invalid json!!!')
      const config = await readConfig()
      // Falls back to defaults
      expect(config.activeProfile).to.equal('default')
    })
  })

  describe('writeConfig', () => {
    it('creates directory if it does not exist', async () => {
      const config = await readConfig()
      await writeConfig(config)
      const exists = await configExists()
      expect(exists).to.be.true
    })

    it('writes valid JSON that can be read back', async () => {
      await initializeConfig()
      const written = await readConfig()
      written.profiles.default.apiUrl = 'https://roundtrip.test'
      await writeConfig(written)
      const readBack = await readConfig()
      expect(readBack.profiles.default.apiUrl).to.equal('https://roundtrip.test')
    })

    it('preserves fields not being updated', async () => {
      await initializeConfig()
      await setConfigValue('apiKey', 'keep-this-key')
      await setConfigValue('apiUrl', 'https://new-url.test')
      const config = await readConfig()
      expect(config.profiles.default.apiKey).to.equal('keep-this-key')
      expect(config.profiles.default.apiUrl).to.equal('https://new-url.test')
    })
  })

  describe('initializeConfig', () => {
    it('creates config with default structure', async () => {
      const created = await initializeConfig()
      expect(created).to.be.true
      const config = await readConfig()
      expect(config.version).to.exist
      expect(config.profiles.default).to.exist
    })

    it('does not overwrite existing config', async () => {
      await initializeConfig()
      await setConfigValue('apiUrl', 'https://keep-me.test')
      const created = await initializeConfig()
      expect(created).to.be.false
      const config = await readConfig()
      expect(config.profiles.default.apiUrl).to.equal('https://keep-me.test')
    })
  })

  describe('profile management', () => {
    beforeEach(async () => {
      await initializeConfig()
    })

    it('createProfile adds a new profile', async () => {
      await createProfile('staging', { apiUrl: 'https://staging.test' })
      const profiles = await listProfiles()
      const names = profiles.map(p => p.name)
      expect(names).to.include('staging')
    })

    it('createProfile rejects duplicate names', async () => {
      try {
        await createProfile('default', { apiUrl: 'https://dup.test' })
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error.message).to.contain('already exists')
      }
    })

    it('deleteProfile removes a profile', async () => {
      await createProfile('temp', { apiUrl: 'https://temp.test' })
      await deleteProfile('temp')
      const profiles = await listProfiles()
      const names = profiles.map(p => p.name)
      expect(names).to.not.include('temp')
    })

    it('deleteProfile throws for non-existent profile', async () => {
      try {
        await deleteProfile('nonexistent')
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error.message).to.contain('does not exist')
      }
    })

    it('deleteProfile rejects deleting default', async () => {
      try {
        await deleteProfile('default')
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error.message).to.contain('Cannot delete the default')
      }
    })

    it('listProfiles returns all profiles', async () => {
      await createProfile('staging', { apiUrl: 'https://staging.test' })
      const profiles = await listProfiles()
      expect(profiles.length).to.equal(2)
    })

    it('setActiveProfile switches profile', async () => {
      await createProfile('staging', { apiUrl: 'https://staging.test' })
      await setActiveProfile('staging')
      const profile = await getActiveProfile()
      expect(profile.apiUrl).to.equal('https://staging.test')
    })

    it('setActiveProfile throws for non-existent profile', async () => {
      try {
        await setActiveProfile('nonexistent')
        expect.fail('should have thrown')
      } catch (error: any) {
        expect(error.message).to.contain('does not exist')
      }
    })
  })

  describe('env var overrides', () => {
    beforeEach(async () => {
      await initializeConfig()
    })

    afterEach(() => {
      delete process.env.AELIRA_API_URL
      delete process.env.AELIRA_API_KEY
      delete process.env.AELIRA_DEPARTMENT
    })

    it('getApiUrl returns env var over config', async () => {
      process.env.AELIRA_API_URL = 'https://env-override.test'
      const url = await getApiUrl()
      expect(url).to.equal('https://env-override.test')
    })

    it('getApiKey returns env var over config', async () => {
      process.env.AELIRA_API_KEY = 'env-key-123'
      const key = await getApiKey()
      expect(key).to.equal('env-key-123')
    })

    it('getDepartment returns env var over config', async () => {
      process.env.AELIRA_DEPARTMENT = 'env-dept'
      const dept = await getDepartment()
      expect(dept).to.equal('env-dept')
    })
  })

  // ---------------------------------------------------------------------------
  // Mutation safety — DEFAULT_CONFIG must never be corrupted by callers that
  // mutate the object readConfig() hands back. Regression coverage for the
  // shallow-spread bug: readConfig()'s fallback branch (`{ ...DEFAULT_CONFIG }`)
  // and merge branch shared `profiles` (and the nested profile objects) by
  // reference with the module-level default, so setConfigValue() — called
  // before any config file exists — silently mutated DEFAULT_CONFIG itself
  // for the rest of the process. All prior tests call initializeConfig()
  // first, which masked this because the merge branch's shallow spread of
  // `config.profiles` (with a real `default` key) shadowed the shared
  // reference before anything wrote into it.
  // ---------------------------------------------------------------------------
  describe('mutation safety', () => {
    it('setConfigValue without initializeConfig first does not mutate DEFAULT_CONFIG', async () => {
      const snapshotBefore = structuredClone(DEFAULT_CONFIG)

      // Deliberately no initializeConfig() call: no config file exists yet,
      // so readConfig() inside setConfigValue() takes the catch-branch
      // fallback that used to return `{ ...DEFAULT_CONFIG }`.
      await setConfigValue('apiKey', 'leaked-key')

      expect(DEFAULT_CONFIG).to.deep.equal(snapshotBefore)
      expect(DEFAULT_CONFIG.profiles.default.apiKey).to.be.undefined

      // And a fresh read (from the file setConfigValue just wrote) should
      // still see the change — the fix isn't supposed to break persistence.
      const config = await readConfig()
      expect(config.profiles.default.apiKey).to.equal('leaked-key')
    })

    it('readConfig with a config file missing the default profile does not mutate DEFAULT_CONFIG', async () => {
      const configFile = getConfigPath()
      await fs.mkdir(path.dirname(configFile), { recursive: true })
      await fs.writeFile(
        configFile,
        JSON.stringify({ activeProfile: 'default', profiles: {}, version: '1.0.0' }),
      )

      const snapshotBefore = structuredClone(DEFAULT_CONFIG)

      const config = await readConfig()
      // Before the fix, config.profiles.default was the same object as
      // DEFAULT_CONFIG.profiles.default (merged in via shallow spread).
      config.profiles.default.apiUrl = 'https://mutate-test.example'

      expect(DEFAULT_CONFIG).to.deep.equal(snapshotBefore)
      expect(DEFAULT_CONFIG.profiles.default.apiUrl).to.not.equal('https://mutate-test.example')
    })
  })

  describe('validateConnection', () => {
    it('returns success on 200 response', async () => {
      // Node 18+ native fetch is non-configurable — reassign globalThis.fetch directly
      const originalFetch = globalThis.fetch
      globalThis.fetch = (async () =>
        new Response(JSON.stringify({ status: 'ok' }), { status: 200 })
      ) as typeof fetch
      try {
        const result = await validateConnection('http://test-api.local')
        expect(result.success).to.be.true
        expect(result.message).to.contain('Connected')
      } finally {
        globalThis.fetch = originalFetch
      }
    })

    it('returns failure on network error', async () => {
      const originalFetch = globalThis.fetch
      globalThis.fetch = (async () => {
        throw new Error('ECONNREFUSED')
      }) as typeof fetch
      try {
        const result = await validateConnection('http://test-api.local')
        expect(result.success).to.be.false
        expect(result.message).to.contain('Could not connect')
      } finally {
        globalThis.fetch = originalFetch
      }
    })
  })
})
