import { expect } from 'chai'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { chromiumLaunchOptions } from '../../src/utils/browser.js'

describe('chromiumLaunchOptions', () => {
  it('uses Playwright\'s own Chromium when nothing is configured', () => {
    expect(chromiumLaunchOptions({})).to.deep.equal({ headless: true })
    expect(chromiumLaunchOptions({ AELIRA_CHROMIUM_EXECUTABLE: '  ' })).to.deep.equal({ headless: true })
  })

  it('launches the configured executable', () => {
    const directory = mkdtempSync(join(tmpdir(), 'aelira-browser-'))
    try {
      const executable = join(directory, 'chrome')
      writeFileSync(executable, '')
      expect(chromiumLaunchOptions({ AELIRA_CHROMIUM_EXECUTABLE: executable })).to.deep.equal({
        executablePath: executable,
        headless: true,
      })
    } finally {
      rmSync(directory, { force: true, recursive: true })
    }
  })

  it('refuses a configured path that is missing instead of falling back', () => {
    expect(() => chromiumLaunchOptions({ AELIRA_CHROMIUM_EXECUTABLE: '/nonexistent/chrome' }))
      .to.throw('AELIRA_CHROMIUM_EXECUTABLE does not point to a file')
  })
})
