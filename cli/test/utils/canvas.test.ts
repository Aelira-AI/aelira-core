import { expect } from 'chai'

import { resolveDepartment } from '../../src/utils/canvas.js'
import { initializeConfig, setConfigValue } from '../../src/utils/config.js'
import { cleanTestDir, createTestDir, withTestConfig } from '../helpers/setup.js'

describe('resolveDepartment', () => {
  let testDir: string
  let restoreEnv: () => void

  beforeEach(async () => {
    testDir = await createTestDir()
    restoreEnv = withTestConfig(testDir)
  })

  afterEach(async () => {
    restoreEnv()
    await cleanTestDir(testDir)
  })

  it('returns the explicit flag value when given, even with a configured default', async () => {
    await initializeConfig()
    await setConfigValue('department', 'configured-dept')
    expect(await resolveDepartment('flag-dept')).to.equal('flag-dept')
  })

  it('falls back to the configured department when no flag value is given', async () => {
    await initializeConfig()
    await setConfigValue('department', 'configured-dept')
    expect(await resolveDepartment()).to.equal('configured-dept')
  })

  it('returns undefined when neither a flag value nor a configured department is set', async () => {
    await initializeConfig()
    expect(await resolveDepartment()).to.be.undefined
  })
})
