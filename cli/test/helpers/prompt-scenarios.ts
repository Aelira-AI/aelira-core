// Run in a subprocess so native ESM mocks cannot affect other CLI tests.
import * as prompts from '@clack/prompts'
import {Config as OclifConfig} from '@oclif/core'
import assert from 'node:assert/strict'
import {mkdtemp, readFile, rm, writeFile} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {mock} from 'node:test'

export interface Step {
  kind: 'confirm' | 'select' | 'text'
  message: string
  value: boolean | null | string
}

export interface Scenario {
  args: string[]
  command: 'config' | 'interactive'
  name: string
  seeded: boolean
  steps: Step[]
}

export interface Result {
  after: null | string
  atCancellation?: null | string
  before: null | string
  calls: {args: string[]; command: string}[]
  consumed: number
  error?: string
  exit?: number | string
  name: string
  promptErrors: string[]
  requests: string[]
}

let steps: Step[] = []
let consumed = 0
let promptErrors: string[] = []
let snapshot: () => Promise<null | string>
let atCancellation: null | string | undefined
async function answer(kind: Step['kind'], options: {message: string; options?: {value: string}[]}) {
  try {
    const step = steps[consumed++]
    assert.ok(step, `Unexpected ${kind} prompt: ${options.message}`)
    assert.equal(kind, step.kind)
    assert.ok(options.message.includes(step.message), `Expected ${step.message}; got ${options.message}`)
    if (kind === 'select' && step.value !== null) {
      assert.ok(options.options?.some((option) => option.value === step.value), 'Scripted selection must exist')
    }

    if (step.value === null) {
      atCancellation = await snapshot()
      return prompts.CANCEL_SYMBOL
    }

    return step.value
  } catch (error) {
    promptErrors.push(String(error))
    throw error
  }
}

assert.ok(prompts.isCancel(prompts.CANCEL_SYMBOL))
mock.module('@clack/prompts', {
  namedExports: {
    ...prompts,
    confirm: async (options: {message: string}) => answer('confirm', options),
    select: async (options: {message: string}) => answer('select', options),
    text: async (options: {message: string}) => answer('text', options),
  },
})
const {default: Config} = await import('../../src/commands/config.js')
const {default: Interactive} = await import('../../src/commands/interactive.js')
const runtime = await OclifConfig.load({root: process.cwd()})
const scenarios: Scenario[] = JSON.parse(process.argv[2])
const results: Result[] = []
async function contents(file: string) {
  return readFile(file, 'utf8').catch((error: NodeJS.ErrnoException) => {
    if (error.code === 'ENOENT') return null
    throw error
  })
}

class Exit extends Error {
  constructor(public code: number | string = 0) { super('command exited') }
}

for (const scenario of scenarios) {
  const dir = await mkdtemp(join(tmpdir(), 'aelira-prompts-'))
  process.env.AELIRA_CONFIG_DIR = dir
  delete process.env.AELIRA_API_KEY
  delete process.env.AELIRA_API_URL
  delete process.env.AELIRA_DEPARTMENT
  const file = join(dir, 'config.json')
  if (scenario.seeded) {
    await writeFile(file, JSON.stringify({
      activeProfile: 'default',
      profiles: {
        default: {apiKey: 'test-original-key', apiUrl: 'https://original.example.test', department: 'original', name: 'Default'},
        staging: {apiUrl: 'https://staging.example.test', name: 'staging'},
      },
      version: '1.0.0',
    }))
  }

  const result: Result = {after: null, before: await contents(file), calls: [], consumed: 0, name: scenario.name, promptErrors: [], requests: []}
  steps = scenario.steps
  consumed = 0
  promptErrors = []
  snapshot = () => contents(file)
  atCancellation = undefined
  const stdout = mock.method(process.stdout, 'write', () => true)
  const stderr = mock.method(process.stderr, 'write', () => true)
  const exit = mock.method(process, 'exit', (code?: number | string): never => { throw new Exit(code) })
  const fetch = mock.method(globalThis, 'fetch', async (input: Request | string | URL) => {
    result.requests.push(String(input))
    return Response.json({status: 'healthy'})
  })
  const dispatch = mock.method(runtime, 'runCommand', async (command: string, args: string[] = []) => {
    result.calls.push({args, command})
    // Configuration side effects use the real command and temporary disk file.
    if (command === 'config') await new Config(args, runtime).run()
  })
  try {
    if (scenario.command === 'config') await new Config(scenario.args, runtime).run()
    else await new Interactive([], runtime).run()
  } catch (error) {
    if (error instanceof Exit) result.exit = error.code
    else result.error = error instanceof Error ? error.message : String(error)
  } finally {
    result.after = await contents(file)
    result.atCancellation = atCancellation
    result.consumed = consumed
    result.promptErrors = promptErrors
    dispatch.mock.restore()
    fetch.mock.restore()
    exit.mock.restore()
    stdout.mock.restore()
    stderr.mock.restore()
    await rm(dir, {force: true, recursive: true})
  }

  results.push(result)
}

process.stdout.write(JSON.stringify(results))
