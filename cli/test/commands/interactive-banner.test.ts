import { runCommand } from '@oclif/test'
import { expect } from 'chai'
import sinon from 'sinon'

import AeliraInteractive from '../../src/commands/interactive.js'

// The logo is block and box-drawing glyphs; screen readers announce each one.
const LOGO_GLYPH = '█'
const OPT_OUT_VARS = ['AELIRA_NO_BANNER', 'CI', 'GITHUB_ACTIONS', 'GITLAB_CI', 'NO_COLOR', 'TERM'] as const

describe('interactive start-up banner', () => {
  const savedEnv: Record<string, string | undefined> = {}
  let savedTTY: PropertyDescriptor | undefined
  let clear: sinon.SinonStub

  beforeEach(() => {
    for (const name of OPT_OUT_VARS) {
      savedEnv[name] = process.env[name]
      delete process.env[name]
    }

    process.env.TERM = 'xterm-256color'
    savedTTY = Object.getOwnPropertyDescriptor(process.stdout, 'isTTY')
    clear = sinon.stub(console, 'clear')
    // The menu loop needs a real keyboard; the banner is printed before it.
    sinon.stub(AeliraInteractive.prototype as unknown as { showMainMenu(): Promise<void> }, 'showMainMenu').resolves()
  })

  afterEach(() => {
    sinon.restore()
    if (savedTTY) Object.defineProperty(process.stdout, 'isTTY', savedTTY)
    else delete (process.stdout as { isTTY?: boolean }).isTTY
    for (const name of OPT_OUT_VARS) {
      if (savedEnv[name] === undefined) delete process.env[name]
      else process.env[name] = savedEnv[name]
    }
  })

  async function start(options: { args?: string[]; env?: Record<string, string>; tty: boolean }) {
    Object.defineProperty(process.stdout, 'isTTY', { configurable: true, value: options.tty, writable: true })
    Object.assign(process.env, options.env ?? {})
    const { error, stdout } = await runCommand(['interactive', ...(options.args ?? [])])
    expect(error, error?.message).to.equal(undefined)
    expect(clear.called, 'console.clear must never be called').to.equal(false)
    expect(stdout, 'plain-text name line').to.match(/^Aelira CLI \d+\.\d+\.\d+/m)
    expect(stdout).to.contain('Accessibility scanning and supported remediation for course content')
    expect(stdout).not.to.contain('MVP')
    return stdout
  }

  it('shows the logo on a terminal with no opt-outs', async () => {
    const stdout = await start({ tty: true })
    expect(stdout).to.contain(LOGO_GLYPH)
  })

  const hidden: [string, Parameters<typeof start>[0]][] = [
    ['stdout is not a terminal', { tty: false }],
    ['NO_COLOR is set', { env: { NO_COLOR: '1' }, tty: true }],
    ['TERM is dumb', { env: { TERM: 'dumb' }, tty: true }],
    ['CI is set', { env: { CI: 'true' }, tty: true }],
    ['CI is set to 1', { env: { CI: '1' }, tty: true }],
    ['AELIRA_NO_BANNER is set', { env: { AELIRA_NO_BANNER: '1' }, tty: true }],
    ['--no-banner is passed', { args: ['--no-banner'], tty: true }],
  ]

  for (const [reason, options] of hidden) {
    it(`hides the logo but still names the tool when ${reason}`, async () => {
      const stdout = await start(options)
      expect(stdout).not.to.contain(LOGO_GLYPH)
      expect(stdout).not.to.contain('╗')
    })
  }

  it('treats AELIRA_NO_BANNER=0 as not set', async () => {
    const stdout = await start({ env: { AELIRA_NO_BANNER: '0' }, tty: true })
    expect(stdout).to.contain(LOGO_GLYPH)
  })
})
