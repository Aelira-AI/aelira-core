import { expect } from 'chai'

import { isCI, shouldShowBanner } from '../../src/utils/terminal.js'

describe('terminal environment helpers', () => {
  it('detects the CI variables the ci command has always honoured', () => {
    expect(isCI({ CI: 'true' })).to.equal(true)
    expect(isCI({ GITHUB_ACTIONS: 'true' })).to.equal(true)
    expect(isCI({ GITLAB_CI: 'true' })).to.equal(true)
    expect(isCI({ CI: '1' })).to.equal(true)
  })

  it('does not treat empty or false-like CI values as CI', () => {
    for (const value of ['', '0', 'false', 'FALSE', 'no', 'off']) {
      expect(isCI({ CI: value }), `CI=${JSON.stringify(value)}`).to.equal(false)
    }

    expect(isCI({})).to.equal(false)
  })

  it('treats an empty NO_COLOR as unset, per no-color.org', () => {
    expect(shouldShowBanner({ env: { NO_COLOR: '' }, isTTY: true })).to.equal(true)
    expect(shouldShowBanner({ env: { NO_COLOR: '0' }, isTTY: true })).to.equal(false)
  })
})
