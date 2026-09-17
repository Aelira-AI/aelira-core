import { Command, Parser } from '@oclif/core'
import { expect } from 'chai'
import { rejects } from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'

const guides = ['COMMANDS.md', 'EXAMPLES.md', 'TROUBLESHOOTING.md']
const commandRoot = new URL('../../src/commands/', import.meta.url)
const definitions = new Map<string, typeof Command>()

async function loadDefinitions(directory = commandRoot, prefix = ''): Promise<void> {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      await loadDefinitions(new URL(`${entry.name}/`, directory), `${prefix}${entry.name} `)
    } else if (entry.name.endsWith('.ts')) {
      const { default: definition } = await import(new URL(entry.name, directory).href)
      definitions.set(`${prefix}${entry.name.slice(0, -3)}`, definition)
    }
  }
}

// Deliberately support only separate, single-line commands and quoted arguments.
// These examples are parsed as literal argv; no shell expansion or command runs.
function tokenize(line: string): string[] {
  const tokens = line.match(/"[^"]*"|'[^']*'|[^\s"']+/g) ?? []
  expect(tokens.join(' '), `unsupported example syntax: ${line}`).to.equal(line.trim())
  expect(line, 'put shell composition outside aelira example lines').not.to.match(/[|;&<>`]/)
  return tokens.map((token) => token.replace(/^(["'])(.*)\1$/, '$2'))
}

async function parseExample(line: string): Promise<void> {
  const [bin, ...tokens] = tokenize(line)
  expect(bin).to.equal('aelira')
  const id = [...definitions.keys()]
    .sort((left, right) => right.split(' ').length - left.split(' ').length)
    .find((candidate) => candidate.split(' ').every((part, index) => tokens[index] === part))
  if (!id) throw new Error(`Unknown command: ${line}`)

  const definition = definitions.get(id)!
  const parsed = await Parser.parse(tokens.slice(id.split(' ').length), {
    args: definition.args,
    flags: definition.flags,
    strict: true,
  })

  // config's key is a free string in oclif; run() enforces this small public set.
  // Validate it here without invoking run() or writing a configuration file.
  if (id === 'config' && parsed.args.action === 'set') {
    expect(parsed.args.key, line).to.be.oneOf(['api-url', 'api-key', 'department'])
  }
}

describe('operator guide command examples', () => {
  before(async () => {
    await loadDefinitions()
  })

  for (const guide of guides) {
    it(`${guide} uses current command arguments and flags`, async () => {
      const markdown = await readFile(new URL(`../../docs/${guide}`, import.meta.url), 'utf8')
      const examples: string[] = []
      for (const [, block] of markdown.matchAll(/^```bash\n([\s\S]*?)^```/gm)) {
        for (const line of block.split('\n')) {
          if (!line.includes('aelira ')) continue
          expect(line, `${guide}: keep CLI examples on their own lines`).to.match(/^aelira /)
          examples.push(line)
        }
      }

      expect(examples.length, `${guide} should exercise real examples`).to.be.greaterThan(0)
      for (const example of examples) {
        try {
          await parseExample(example)
        } catch (error) {
          throw new Error(`${guide}: ${example}`, { cause: error })
        }
      }
    })
  }

  it('links every installed command to its source definition', async () => {
    const index = await readFile(new URL('../../docs/COMMANDS.md', import.meta.url), 'utf8')
    for (const id of definitions.keys()) {
      expect(index, id).to.include(`../src/commands/${id.replaceAll(' ', '/')}.ts`)
    }
  })

  for (const invalid of [
    'aelira watch ./uploads',
    'aelira config --init',
    'aelira config --validate',
    'aelira config set apiKey example-key',
    'aelira auth login --email user@example.edu',
    'aelira scan pdf document.pdf --batch',
    'aelira scan pdf document.pdf --generate-alt-text',
    'aelira scan pdf document.pdf --export-html accessible.html',
    'aelira scan pdf document.pdf --ocr',
    'aelira scan pdf document.pdf --pdf report.pdf',
    'aelira scan ppt presentation.pptx --fix-contrast',
    'aelira scan video lecture.mp4 --format vtt',
    'aelira scan pdf',
  ]) {
    it(`rejects stale syntax: ${invalid}`, async () => {
      await rejects(parseExample(invalid))
    })
  }

  it('accepts quoted file paths without shell execution', async () => {
    await parseExample('aelira scan pdf "My document.pdf" --format json')
  })
})
