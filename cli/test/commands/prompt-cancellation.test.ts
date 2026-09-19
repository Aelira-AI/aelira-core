import {expect} from 'chai'
import {execFile} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import {promisify} from 'node:util'

import type {Result, Scenario, Step} from '../helpers/prompt-scenarios.js'

interface Case extends Scenario {
  calls: Result['calls']
  requests: number
  unchanged: boolean
  verify?: (result: Result) => void
}

const cases: Case[] = []
const text = (message: string, value: Step['value']): Step => ({kind: 'text', message, value})
const select = (message: string, value: Step['value']): Step => ({kind: 'select', message, value})
const confirm = (message: string, value: Step['value']): Step => ({kind: 'confirm', message, value})
const main = (value: Step['value']) => select('What would you like to do?', value)
const call = (command: string, ...args: string[]) => ({args, command})
const values = [
  text('Enter the Aelira API URL:', 'https://new.example.test'),
  text('Enter your API key', 'test-new-key'),
  text('Enter your department ID', 'engineering'),
]
const overwrite = confirm('Do you want to reconfigure?', true)
const connection = (value: Step['value']) => confirm('test the API connection?', value)
function config(name: string, args: string[], steps: Step[], overrides: Partial<Case> = {}) {
  cases.push({args, calls: [], command: 'config', name, requests: 0, seeded: true, steps, unchanged: true, ...overrides})
}

function interactive(name: string, steps: Step[], overrides: Partial<Case> = {}) {
  cases.push({args: [], calls: [], command: 'interactive', name, requests: 0, seeded: true, steps: [...steps, main('exit')], unchanged: true, ...overrides})
}

const saved = (result: Result) => {
  expect(result.after).not.to.equal(null)
  return JSON.parse(result.after!)
}

const verifyWizard = (result: Result) => expect(saved(result).profiles.default).to.include({
  apiKey: 'test-new-key', apiUrl: 'https://new.example.test', department: 'engineering',
})

for (const seeded of [false, true]) {
  for (const [index, field] of ['URL', 'key', 'department'].entries()) {
    config(`init cancels ${field} with ${seeded ? 'existing' : 'no'} config`, ['init'], [
      ...(seeded ? [overwrite] : []), ...values.slice(0, index), {...values[index], value: null},
    ], {seeded})
  }
}

for (const value of [null, false]) {
  config(`init ${value === null ? 'cancels' : 'declines'} overwrite`, ['init'], [confirm('reconfigure?', value)])
}

for (const value of [null, false, true]) {
  config(`init connection confirmation ${String(value)}`, ['init'], [...values, connection(value)], {
    requests: value === true ? 1 : 0, seeded: false, unchanged: false, verify: verifyWizard,
  })
}

config('init accepts reconfiguration', ['init'], [overwrite, ...values, connection(false)], {
  unchanged: false, verify: verifyWizard,
})
config('set cancels field selection', ['set'], [select('What would you like to configure?', null)])
config('set cancels field selection without creating config', ['set'], [select('What would you like to configure?', null)], {seeded: false})
for (const [key, stored] of [['api-url', 'apiUrl'], ['api-key', 'apiKey'], ['department', 'department']]) {
  config(`set cancels ${key} value`, ['set', key], [text(`Enter new value for ${key}`, null)])
  config(`set cancels ${key} without creating config`, ['set', key], [text(`Enter new value for ${key}`, null)], {seeded: false})
  config(`set saves selected ${key}`, ['set'], [select('What would you like to configure?', key), text(`Enter new value for ${key}`, 'updated-value')], {
    unchanged: false, verify: (result) => expect(saved(result).profiles.default[stored]).to.equal('updated-value'),
  })
}

cases.push({args: [], calls: [], command: 'interactive', name: 'main menu cancellation exits cleanly', requests: 0, seeded: true, steps: [main(null)], unchanged: true})
interactive('main menu explicit exit', [])

const menus = [
  {action: 'scan_website', message: 'Choose website scan type:', options: ['basic', 'ai']},
  {action: 'scan_documents', message: 'Choose document type:', options: ['pdf', 'ppt', 'latex']},
  {action: 'scan_media', message: 'Choose media type:', options: ['image', 'video']},
]
for (const menu of menus) {
  for (const value of [null, 'back']) {
    interactive(`${menu.action} ${value === null ? 'cancel' : 'back'} selection`, [main(menu.action), select(menu.message, value)])
  }

  for (const option of menu.options) {
    const message = menu.action === 'scan_website' ? 'Enter URL to scan' : 'Enter file or directory path:'
    const input = menu.action === 'scan_website' ? 'https://example.test/page' : 'fixture.html'
    const prefix = [main(menu.action), select(menu.message, option)]
    interactive(`${menu.action} ${option} cancels input`, [...prefix, text(message, null)])
    const dispatched = menu.action === 'scan_website'
      ? call(option === 'ai' ? 'analyze' : 'scan', input) : call('scan', option, input)
    interactive(`${menu.action} ${option} accepts input`, [...prefix, text(message, input)], {calls: [dispatched]})
  }
}

interactive('code scan cancels input', [main('scan_code'), text('Enter file or directory path:', null)])
interactive('code scan accepts input', [main('scan_code'), text('Enter file or directory path:', 'source.ts')], {calls: [call('scan', 'code', 'source.ts')]})
for (const value of [null, '', 'engineering']) {
  interactive(`report department ${JSON.stringify(value)}`, [main('evidence_report'), text('Enter department ID', value)], {
    calls: value === null ? [] : [call('report', 'evidence', ...(value ? [value] : []))],
  })
}

const settings = (value: Step['value']) => [main('settings'), select('Settings & Configuration:', value)]
for (const value of [null, 'back']) {
  interactive(`settings ${String(value)}`, settings(value))
  interactive(`profiles ${String(value)}`, [...settings('profiles'), select('Profile Management:', value)])
}

for (const [action, key, message, stored] of [
  ['set-api-url', 'api-url', 'Enter API URL', 'apiUrl'],
  ['set-api-key', 'api-key', 'Enter your API key:', 'apiKey'],
  ['set-department', 'department', 'Enter department ID:', 'department'],
]) {
  interactive(`${action} cancels input`, [...settings(action), text(message, null)])
  interactive(`${action} saves input`, [...settings(action), text(message, 'updated-value')], {
    calls: [call('config', 'set', key, 'updated-value')], unchanged: false,
    verify: (result) => expect(saved(result).profiles.default[stored]).to.equal('updated-value'),
  })
}

const profiles = (value: string) => [...settings('profiles'), select('Profile Management:', value)]
for (const action of ['create', 'delete', 'use']) {
  interactive(`profile ${action} cancels name`, [...profiles(action), text('Enter profile name', null)])
}

for (const value of [null, '', 'https://research.example.test']) {
  interactive(`profile create URL ${JSON.stringify(value)}`, [...profiles('create'), text('Enter profile name:', 'research'), text('Enter API URL for this profile:', value)], {
    calls: value === null ? [] : [call('config', 'profile', 'create', 'research', '--api-url', value || 'http://localhost:8000')],
    unchanged: value === null,
    verify(result) {
      if (value === null) expect(saved(result).profiles).not.to.have.property('research')
      else expect(saved(result).profiles.research.apiUrl).to.equal(value || 'http://localhost:8000')
    },
  })
}

interactive('profile use accepts name', [...profiles('use'), text('Enter profile name', 'staging')], {
  calls: [call('config', 'profile', 'use', 'staging')], unchanged: false,
  verify: (result) => expect(saved(result).activeProfile).to.equal('staging'),
})
interactive('profile delete accepts name', [...profiles('delete'), text('Enter profile name', 'staging')], {
  calls: [call('config', 'profile', 'delete', 'staging')], unchanged: false,
  verify: (result) => expect(saved(result).profiles).not.to.have.property('staging'),
})

for (const value of [null, '']) {
  interactive(`profile list acknowledgement ${String(value)}`, [...profiles('list'), text('Press Enter to continue...', value)], {calls: [call('config', 'profile', 'list')]})
  interactive(`show config acknowledgement ${String(value)}`, [...settings('show'), text('Press Enter to continue...', value)], {calls: [call('config', 'show')]})
  interactive(`validate acknowledgement ${String(value)}`, [...settings('validate'), text('Press Enter to continue...', value)], {calls: [call('config', 'validate')], requests: 1})
  interactive(`help acknowledgement ${String(value)}`, [main('help'), text('Press Enter to continue...', value)])
}

interactive('settings setup delegates cancellation safely', [...settings('init'), confirm('reconfigure?', null)], {calls: [call('config', 'init')]})
interactive('settings setup saves accepted values', [...settings('init'), overwrite, ...values, connection(false)], {
  calls: [call('config', 'init')], unchanged: false, verify: verifyWizard,
})

describe('prompt cancellation contracts', () => {
  let results: Result[]

  before(async () => {
    const {stdout} = await promisify(execFile)(process.execPath, [
      '--experimental-test-module-mocks', '--loader', 'ts-node/esm',
      fileURLToPath(new URL('../helpers/prompt-scenarios.ts', import.meta.url)), JSON.stringify(cases),
    ], {maxBuffer: 4 * 1024 * 1024, timeout: 30_000})
    results = JSON.parse(stdout)
    expect(results).to.have.length(cases.length)
  })

  for (const [index, scenario] of cases.entries()) {
    it(scenario.name, () => {
      const result = results[index]
      expect(result.name).to.equal(scenario.name)
      expect(result.error).to.equal(undefined)
      expect(result.promptErrors).to.deep.equal([])
      expect(result.consumed).to.equal(scenario.steps.length)
      expect(result.calls).to.deep.equal(scenario.calls)
      expect(result.requests).to.have.length(scenario.requests)
      if (scenario.command === 'interactive') expect(result.exit).to.equal(0)
      if (scenario.unchanged) expect(result.after).to.equal(result.before)
      else expect(result.after).not.to.equal(result.before)
      if (scenario.steps.some((step) => step.value === null)) expect(result.after).to.equal(result.atCancellation)
      scenario.verify?.(result)
    })
  }
})
