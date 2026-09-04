import {createHash} from 'node:crypto'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

const SARIF_SCHEMA_URI =
  'https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/schemas/sarif-schema-2.1.0.json'

export interface AxeNode {
  failureSummary?: string
  html?: string
  target?: unknown[]
}

export interface AxeViolation {
  description: string
  help: string
  helpUrl: string
  id: string
  impact: null | string
  nodes?: AxeNode[]
  tags?: string[]
}

export interface SarifSource {
  text: string
  uri: string
}

interface SarifLocation {
  physicalLocation: {
    artifactLocation: {uri: string}
    region: {
      endColumn: number
      endLine: number
      startColumn: number
      startLine: number
    }
  }
}

interface SarifResult {
  level: 'error' | 'none' | 'note' | 'warning'
  locations?: SarifLocation[]
  message: {text: string}
  partialFingerprints: Record<string, string>
  properties: {
    helpUri: string
    impact: null | string
    target: unknown[]
  }
  ruleId: string
  ruleIndex: number
}

interface SarifRule {
  defaultConfiguration: {level: SarifResult['level']}
  fullDescription: {text: string}
  helpUri: string
  id: string
  name: string
  properties: {tags: string[]}
  shortDescription: {text: string}
}

export interface SarifLog {
  $schema: string
  runs: Array<{
    properties: {
      sourceMapping: 'repository' | 'unavailable'
      target: string
    }
    results: SarifResult[]
    tool: {
      driver: {
        informationUri: string
        name: string
        rules: SarifRule[]
        semanticVersion: string
      }
    }
  }>
  version: '2.1.0'
}

interface BuildSarifOptions {
  axeResults: {violations: AxeViolation[]}
  source?: SarifSource
  target: string
  toolVersion: string
}

interface CiExitInput {
  score: number
  violations: Array<{impact: null | string}>
}

function sarifLevel(impact: null | string): SarifResult['level'] {
  switch (impact) {
    case 'critical':
    case 'serious': {
      return 'error'
    }

    case 'minor': {
      return 'note'
    }

    case 'moderate': {
      return 'warning'
    }

    default: {
      return 'none'
    }
  }
}

function failSeverities(failOn: string): string[] {
  switch (failOn) {
    case 'critical': {
      return ['critical']
    }

    case 'minor': {
      return ['critical', 'serious', 'moderate', 'minor']
    }

    case 'moderate': {
      return ['critical', 'serious', 'moderate']
    }

    default: {
      return ['critical', 'serious']
    }
  }
}

export function calculateCiExitCode(result: CiExitInput, threshold: number, failOn: string): 0 | 1 {
  const failingImpacts = failSeverities(failOn)
  return result.score < threshold || result.violations.some(({impact}) => impact && failingImpacts.includes(impact))
    ? 1
    : 0
}

function normalizedUri(uri: string): string {
  return uri
    .replaceAll('\\', '/')
    .replace(/^\.\/+/, '')
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/')
}

function lineColumn(text: string, index: number): {column: number; line: number} {
  const before = text.slice(0, index)
  const lastNewline = before.lastIndexOf('\n')
  return {
    column: index - lastNewline,
    line: before.split('\n').length,
  }
}

function sourceLocation(source: SarifSource | undefined, html: string | undefined): SarifLocation[] | undefined {
  if (!source || !html) return undefined

  const startIndex = source.text.indexOf(html)
  if (startIndex === -1 || source.text.includes(html, startIndex + html.length)) return undefined

  const start = lineColumn(source.text, startIndex)
  const end = lineColumn(source.text, startIndex + html.length)
  return [
    {
      physicalLocation: {
        artifactLocation: {uri: normalizedUri(source.uri)},
        region: {
          endColumn: end.column,
          endLine: end.line,
          startColumn: start.column,
          startLine: start.line,
        },
      },
    },
  ]
}

function stableNodeKey(node: AxeNode): string {
  return JSON.stringify([node.target ?? [], node.html ?? '', node.failureSummary ?? ''])
}

function fingerprint(ruleId: string, target: string, source: SarifSource | undefined, node: AxeNode): string {
  return createHash('sha256')
    .update(JSON.stringify([ruleId, source ? normalizedUri(source.uri) : target, node.target ?? [], node.html ?? '']))
    .digest('hex')
}

export function buildSarifLog(options: BuildSarifOptions): SarifLog {
  const violations = [...options.axeResults.violations].sort((left, right) => left.id.localeCompare(right.id))
  const rules: SarifRule[] = violations.map((violation) => ({
    defaultConfiguration: {level: sarifLevel(violation.impact)},
    fullDescription: {text: violation.description},
    helpUri: violation.helpUrl,
    id: violation.id,
    name: violation.id,
    properties: {tags: [...(violation.tags ?? [])].sort()},
    shortDescription: {text: violation.help},
  }))
  const ruleIndexes = new Map(rules.map((rule, index) => [rule.id, index]))
  const results = violations.flatMap((violation) => {
    const nodes = violation.nodes && violation.nodes.length > 0 ? [...violation.nodes] : [{}]
    return nodes.sort((left, right) => stableNodeKey(left).localeCompare(stableNodeKey(right))).map((node) => {
      const result: SarifResult = {
        level: sarifLevel(violation.impact),
        message: {text: node.failureSummary || violation.help || violation.description},
        partialFingerprints: {
          'aelira/v1': fingerprint(violation.id, options.target, options.source, node),
        },
        properties: {
          helpUri: violation.helpUrl,
          impact: violation.impact,
          target: node.target ?? [],
        },
        ruleId: violation.id,
        ruleIndex: ruleIndexes.get(violation.id) ?? 0,
      }
      const locations = sourceLocation(options.source, node.html)
      if (locations) result.locations = locations
      return result
    })
  })

  return {
    $schema: SARIF_SCHEMA_URI,
    runs: [
      {
        properties: {
          sourceMapping: options.source ? 'repository' : 'unavailable',
          target: options.target,
        },
        results,
        tool: {
          driver: {
            informationUri: 'https://github.com/Aelira-AI/aelira-core',
            name: 'Aelira',
            rules,
            semanticVersion: options.toolVersion,
          },
        },
      },
    ],
    version: '2.1.0',
  }
}

async function findRepositoryRoot(start: string): Promise<string | undefined> {
  let candidate = path.resolve(start)
  while (true) {
    try {
      await fs.stat(path.join(candidate, '.git'))
      return candidate
    } catch {
      const parent = path.dirname(candidate)
      if (parent === candidate) return undefined
      candidate = parent
    }
  }
}

export async function resolveSarifSource(target: string, cwd: string): Promise<SarifSource | undefined> {
  if (/^https?:\/\//i.test(target)) return undefined

  const repositoryRoot = await findRepositoryRoot(cwd)
  if (!repositoryRoot) return undefined

  const canonicalRoot = await fs.realpath(repositoryRoot)
  const targetPath = path.resolve(cwd, target)
  const targetStats = await fs.stat(targetPath)
  const sourcePath = targetStats.isDirectory() ? path.join(targetPath, 'index.html') : targetPath
  const canonicalSource = await fs.realpath(sourcePath)
  const relativePath = path.relative(canonicalRoot, canonicalSource)
  if (relativePath.startsWith(`..${path.sep}`) || relativePath === '..' || path.isAbsolute(relativePath)) {
    return undefined
  }

  return {
    text: await fs.readFile(canonicalSource, 'utf8'),
    uri: relativePath,
  }
}
