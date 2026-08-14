import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'
import pc from 'picocolors'

interface ScanResult {
  criticalCount?: number
  errorCount?: number
  passedThreshold?: boolean
  score: number
  seriousCount?: number
  target?: string
  timestamp: string
  totalIssues?: number
  url?: string
  violations: any[]
}

interface DiffSummary {
  addedIssues: any[]
  baseFile: string
  baseScan: ScanResult
  compareFile: string
  compareScan: ScanResult
  fixedIssues: any[]
  newScore: number
  oldScore: number
  regressionCount: number
  scoreChange: number
  unchangedIssues: any[]
}

export default class Diff extends Command {
  static args = {
    base: Args.string({
      description: 'Base scan result file (older scan)',
      required: true,
    }),
    compare: Args.string({
      description: 'Compare scan result file (newer scan)',
      required: true,
    }),
  }
static description = 'Compare two scan results to show accessibility changes over time'
static examples = [
    '<%= config.bin %> <%= command.id %> scan-old.json scan-new.json',
    '<%= config.bin %> <%= command.id %> baseline.json current.json --format html --output diff-report.html',
    '<%= config.bin %> <%= command.id %> v1.json v2.json --show-fixed',
  ]
static flags = {
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format',
      options: ['console', 'json', 'html', 'markdown'],
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path',
    }),
    'show-fixed': Flags.boolean({
      default: false,
      description: 'Show issues that were fixed',
    }),
    'show-unchanged': Flags.boolean({
      default: false,
      description: 'Show issues that remain unchanged',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(Diff)

    intro('Aelira CLI - Accessibility Diff')

    const s = spinner()
    s.start('Loading scan results...')

    try {
      // Load both scan files
      const baseScan = await this.loadScanResult(args.base)
      const compareScan = await this.loadScanResult(args.compare)

      s.stop('Scan results loaded')

      // Calculate diff
      const diff = this.calculateDiff(args.base, baseScan, args.compare, compareScan)

      // Output in requested format
      switch (flags.format) {
        case 'html': {
          const html = this.toHTML(diff, flags)
          if (flags.output) {
            await fs.writeFile(flags.output, html)
            this.log(`\n📄 HTML report saved to: ${flags.output}`)
          } else {
            this.log(html)
          }

          break
        }

        case 'json': {
          const json = JSON.stringify(diff, null, 2)
          if (flags.output) {
            await fs.writeFile(flags.output, json)
            this.log(`\n📄 JSON report saved to: ${flags.output}`)
          } else {
            this.log(json)
          }

          break
        }

        case 'markdown': {
          const md = this.toMarkdown(diff, flags)
          if (flags.output) {
            await fs.writeFile(flags.output, md)
            this.log(`\n📄 Markdown report saved to: ${flags.output}`)
          } else {
            this.log(md)
          }

          break
        }

        default: {
          this.displayConsole(diff, flags)
        }
      }

      // Summary
      if (diff.scoreChange > 0) {
        outro(`✅ Score improved by ${diff.scoreChange} points!`)
      } else if (diff.scoreChange < 0) {
        outro(`⚠️  Score decreased by ${Math.abs(diff.scoreChange)} points`)
      } else {
        outro('ℹ️  Score unchanged')
      }
    } catch (error: any) {
      s.stop('Error loading scan results')
      this.error(error.message)
    }
  }

  private calculateDiff(
    baseFile: string,
    baseScan: ScanResult,
    compareFile: string,
    compareScan: ScanResult,
  ): DiffSummary {
    // Create maps of violations by ID for comparison
    const baseViolationMap = new Map<string, any>()
    const compareViolationMap = new Map<string, any>()

    for (const v of baseScan.violations) {
      baseViolationMap.set(v.id, v)
    }

    for (const v of compareScan.violations) {
      compareViolationMap.set(v.id, v)
    }

    // Find added, fixed, and unchanged issues
    const addedIssues: any[] = []
    const fixedIssues: any[] = []
    const unchangedIssues: any[] = []

    // Issues in compare but not in base = added (regressions)
    for (const [id, violation] of compareViolationMap) {
      if (baseViolationMap.has(id)) {
        unchangedIssues.push({ ...violation, changeType: 'unchanged' })
      } else {
        addedIssues.push({ ...violation, changeType: 'added' })
      }
    }

    // Issues in base but not in compare = fixed
    for (const [id, violation] of baseViolationMap) {
      if (!compareViolationMap.has(id)) {
        fixedIssues.push({ ...violation, changeType: 'fixed' })
      }
    }

    return {
      addedIssues,
      baseFile,
      baseScan,
      compareFile,
      compareScan,
      fixedIssues,
      newScore: compareScan.score,
      oldScore: baseScan.score,
      regressionCount: addedIssues.length,
      scoreChange: compareScan.score - baseScan.score,
      unchangedIssues,
    }
  }

  private displayConsole(
    diff: DiffSummary,
    flags: { 'show-fixed': boolean; 'show-unchanged': boolean },
  ): void {
    this.log('\n' + pc.bold('Accessibility Diff Report'))
    this.log('━'.repeat(60))

    // File info
    this.log(`\n  ${pc.dim('Base:')}    ${path.basename(diff.baseFile)}`)
    this.log(`  ${pc.dim('Compare:')} ${path.basename(diff.compareFile)}`)

    // Score comparison
    this.log('\n' + pc.bold('Score Change'))
    this.log('─'.repeat(60))

    const oldScoreColor = diff.oldScore >= 80 ? pc.green : diff.oldScore >= 60 ? pc.yellow : pc.red
    const newScoreColor = diff.newScore >= 80 ? pc.green : diff.newScore >= 60 ? pc.yellow : pc.red

    this.log(`  Old Score: ${oldScoreColor(`${diff.oldScore}%`)}`)
    this.log(`  New Score: ${newScoreColor(`${diff.newScore}%`)}`)

    if (diff.scoreChange > 0) {
      this.log(`  Change:    ${pc.green(`+${diff.scoreChange}`)} ⬆️`)
    } else if (diff.scoreChange < 0) {
      this.log(`  Change:    ${pc.red(`${diff.scoreChange}`)} ⬇️`)
    } else {
      this.log(`  Change:    ${pc.dim('0')} ➡️`)
    }

    // Issue summary
    this.log('\n' + pc.bold('Issue Summary'))
    this.log('─'.repeat(60))
    this.log(`  🔴 New Issues (Regressions): ${pc.red(String(diff.addedIssues.length))}`)
    this.log(`  🟢 Fixed Issues:             ${pc.green(String(diff.fixedIssues.length))}`)
    this.log(`  ⚪ Unchanged Issues:         ${pc.dim(String(diff.unchangedIssues.length))}`)

    // Show regressions (always)
    if (diff.addedIssues.length > 0) {
      this.log('\n' + pc.bold(pc.red('New Issues (Regressions)')))
      this.log('─'.repeat(60))
      for (const issue of diff.addedIssues) {
        const impact = this.getImpactIcon(issue.impact)
        this.log(`  ${impact} ${pc.bold(issue.id)}`)
        this.log(`     ${pc.dim(issue.description || '')}`)
      }
    }

    // Show fixed issues (optional)
    if (flags['show-fixed'] && diff.fixedIssues.length > 0) {
      this.log('\n' + pc.bold(pc.green('Fixed Issues')))
      this.log('─'.repeat(60))
      for (const issue of diff.fixedIssues) {
        this.log(`  ✅ ${pc.strikethrough(issue.id)}`)
        this.log(`     ${pc.dim(issue.description || '')}`)
      }
    }

    // Show unchanged issues (optional)
    if (flags['show-unchanged'] && diff.unchangedIssues.length > 0) {
      this.log('\n' + pc.bold('Unchanged Issues'))
      this.log('─'.repeat(60))
      for (const issue of diff.unchangedIssues) {
        const impact = this.getImpactIcon(issue.impact)
        this.log(`  ${impact} ${issue.id}`)
      }
    }

    this.log('\n' + '━'.repeat(60))
  }

  private getImpactIcon(impact: string): string {
    switch (impact) {
      case 'critical': {
        return '🔴'
      }

      case 'minor': {
        return '🟡'
      }

      case 'moderate': {
        return '🟠'
      }

      case 'serious': {
        return '🟠'
      }

      default: {
        return '⚪'
      }
    }
  }

  private async loadScanResult(filePath: string): Promise<ScanResult> {
    const absolutePath = path.resolve(filePath)

    try {
      const content = await fs.readFile(absolutePath, 'utf8')
      const data = JSON.parse(content)

      // Handle different scan result formats
      if (data.results && Array.isArray(data.results)) {
        // Bulk scan format - aggregate results
        const violations = data.results.flatMap((r: any) => r.violations || [])
        return {
          score: data.averageScore || 0,
          timestamp: data.results[0]?.timestamp || new Date().toISOString(),
          violations,
        }
      }

      // Standard scan format
      return {
        criticalCount: data.criticalCount,
        errorCount: data.errorCount,
        passedThreshold: data.passedThreshold,
        score: data.score || 0,
        seriousCount: data.seriousCount,
        target: data.target || data.url,
        timestamp: data.timestamp || new Date().toISOString(),
        totalIssues: data.totalIssues,
        violations: data.violations || [],
      }
    } catch (error: any) {
      throw new Error(`Failed to load scan result from ${filePath}: ${error.message}`)
    }
  }

  private toHTML(
    diff: DiffSummary,
    flags: { 'show-fixed': boolean; 'show-unchanged': boolean },
  ): string {
    const timestamp = new Date().toISOString()
    const changeIcon = diff.scoreChange > 0 ? '⬆️' : diff.scoreChange < 0 ? '⬇️' : '➡️'
    const changeClass =
      diff.scoreChange > 0 ? 'positive' : diff.scoreChange < 0 ? 'negative' : 'neutral'

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Aelira Accessibility Diff Report</title>
  <style>
    :root {
      --bg: #0f172a;
      --text: #e2e8f0;
      --card: #1e293b;
      --border: #334155;
      --primary: #6366f1;
      --success: #22c55e;
      --warning: #eab308;
      --error: #ef4444;
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      margin: 0;
      padding: 2rem;
    }
    .container { max-width: 1000px; margin: 0 auto; }
    h1 { color: var(--primary); margin-bottom: 0.5rem; }
    .subtitle { color: #94a3b8; margin-bottom: 2rem; }
    .score-comparison {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 2rem;
      margin: 2rem 0;
      padding: 2rem;
      background: var(--card);
      border-radius: 12px;
    }
    .score-box {
      text-align: center;
    }
    .score-value {
      font-size: 3rem;
      font-weight: bold;
    }
    .score-label { color: #94a3b8; font-size: 0.875rem; }
    .arrow { font-size: 2rem; }
    .change { font-size: 1.5rem; font-weight: bold; padding: 0.5rem 1rem; border-radius: 8px; }
    .change.positive { background: rgba(34, 197, 94, 0.2); color: var(--success); }
    .change.negative { background: rgba(239, 68, 68, 0.2); color: var(--error); }
    .change.neutral { background: rgba(100, 116, 139, 0.2); color: #94a3b8; }
    .summary {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 1rem;
      margin: 2rem 0;
    }
    .stat {
      background: var(--card);
      border-radius: 8px;
      padding: 1.5rem;
      text-align: center;
    }
    .stat-value { font-size: 2rem; font-weight: bold; }
    .stat-label { color: #94a3b8; font-size: 0.875rem; }
    .stat-regressions .stat-value { color: var(--error); }
    .stat-fixed .stat-value { color: var(--success); }
    .section { margin: 2rem 0; }
    .section h2 { margin-bottom: 1rem; }
    .issue {
      background: var(--card);
      border-left: 4px solid var(--border);
      padding: 1rem;
      margin-bottom: 0.5rem;
      border-radius: 0 8px 8px 0;
    }
    .issue.critical { border-left-color: var(--error); }
    .issue.serious { border-left-color: var(--warning); }
    .issue.fixed { border-left-color: var(--success); opacity: 0.8; }
    .issue-id { font-weight: bold; }
    .issue-desc { color: #94a3b8; font-size: 0.875rem; }
    footer {
      margin-top: 2rem;
      padding-top: 1rem;
      border-top: 1px solid var(--border);
      color: #94a3b8;
      font-size: 0.875rem;
      text-align: center;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>Accessibility Diff Report</h1>
    <p class="subtitle">
      Generated by Aelira CLI • ${timestamp}<br>
      Comparing: ${path.basename(diff.baseFile)} → ${path.basename(diff.compareFile)}
    </p>

    <div class="score-comparison">
      <div class="score-box">
        <div class="score-value" style="color: ${diff.oldScore >= 80 ? 'var(--success)' : diff.oldScore >= 60 ? 'var(--warning)' : 'var(--error)'}">${diff.oldScore}%</div>
        <div class="score-label">Previous Score</div>
      </div>
      <div class="arrow">→</div>
      <div class="score-box">
        <div class="score-value" style="color: ${diff.newScore >= 80 ? 'var(--success)' : diff.newScore >= 60 ? 'var(--warning)' : 'var(--error)'}">${diff.newScore}%</div>
        <div class="score-label">Current Score</div>
      </div>
      <div class="change ${changeClass}">
        ${diff.scoreChange > 0 ? '+' : ''}${diff.scoreChange} ${changeIcon}
      </div>
    </div>

    <div class="summary">
      <div class="stat stat-regressions">
        <div class="stat-value">${diff.addedIssues.length}</div>
        <div class="stat-label">New Issues</div>
      </div>
      <div class="stat stat-fixed">
        <div class="stat-value">${diff.fixedIssues.length}</div>
        <div class="stat-label">Fixed Issues</div>
      </div>
      <div class="stat">
        <div class="stat-value">${diff.unchangedIssues.length}</div>
        <div class="stat-label">Unchanged</div>
      </div>
    </div>

    ${
      diff.addedIssues.length > 0
        ? `
    <div class="section">
      <h2>🔴 New Issues (Regressions)</h2>
      ${diff.addedIssues
        .map(
          (issue) => `
        <div class="issue ${issue.impact}">
          <div class="issue-id">${issue.id}</div>
          <div class="issue-desc">${issue.description || ''}</div>
        </div>
      `,
        )
        .join('')}
    </div>
    `
        : ''
    }

    ${
      flags['show-fixed'] && diff.fixedIssues.length > 0
        ? `
    <div class="section">
      <h2>🟢 Fixed Issues</h2>
      ${diff.fixedIssues
        .map(
          (issue) => `
        <div class="issue fixed">
          <div class="issue-id">✅ ${issue.id}</div>
          <div class="issue-desc">${issue.description || ''}</div>
        </div>
      `,
        )
        .join('')}
    </div>
    `
        : ''
    }

    ${
      flags['show-unchanged'] && diff.unchangedIssues.length > 0
        ? `
    <div class="section">
      <h2>⚪ Unchanged Issues</h2>
      ${diff.unchangedIssues
        .map(
          (issue) => `
        <div class="issue">
          <div class="issue-id">${issue.id}</div>
          <div class="issue-desc">${issue.description || ''}</div>
        </div>
      `,
        )
        .join('')}
    </div>
    `
        : ''
    }

    <footer>
      <p>Aelira - Higher Education Accessibility Platform</p>
    </footer>
  </div>
</body>
</html>`
  }

  private toMarkdown(
    diff: DiffSummary,
    flags: { 'show-fixed': boolean; 'show-unchanged': boolean },
  ): string {
    const changeEmoji = diff.scoreChange > 0 ? '⬆️' : diff.scoreChange < 0 ? '⬇️' : '➡️'

    let md = `# Accessibility Diff Report

**Generated:** ${new Date().toISOString()}

**Comparing:** \`${path.basename(diff.baseFile)}\` → \`${path.basename(diff.compareFile)}\`

## Score Change

| Metric | Value |
|--------|-------|
| Previous Score | ${diff.oldScore}% |
| Current Score | ${diff.newScore}% |
| Change | ${diff.scoreChange > 0 ? '+' : ''}${diff.scoreChange} ${changeEmoji} |

## Summary

- 🔴 **New Issues (Regressions):** ${diff.addedIssues.length}
- 🟢 **Fixed Issues:** ${diff.fixedIssues.length}
- ⚪ **Unchanged Issues:** ${diff.unchangedIssues.length}

`

    if (diff.addedIssues.length > 0) {
      md += `## 🔴 New Issues (Regressions)

| ID | Impact | Description |
|----|--------|-------------|
${diff.addedIssues.map((i) => `| ${i.id} | ${i.impact || '-'} | ${i.description || '-'} |`).join('\n')}

`
    }

    if (flags['show-fixed'] && diff.fixedIssues.length > 0) {
      md += `## 🟢 Fixed Issues

| ID | Impact | Description |
|----|--------|-------------|
${diff.fixedIssues.map((i) => `| ~~${i.id}~~ | ${i.impact || '-'} | ${i.description || '-'} |`).join('\n')}

`
    }

    if (flags['show-unchanged'] && diff.unchangedIssues.length > 0) {
      md += `## ⚪ Unchanged Issues

| ID | Impact | Description |
|----|--------|-------------|
${diff.unchangedIssues.map((i) => `| ${i.id} | ${i.impact || '-'} | ${i.description || '-'} |`).join('\n')}

`
    }

    md += `---
*Generated by Aelira CLI*
`

    return md
  }
}
