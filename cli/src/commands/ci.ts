import type { Browser, Page } from 'playwright'

import { AxeBuilder } from '@axe-core/playwright'
import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'
import pc from 'picocolors'
import { chromium } from 'playwright'

interface CIResult {
  criticalCount: number
  errorCount: number
  passedThreshold: boolean
  score: number
  seriousCount: number
  target: string
  timestamp: string
  totalIssues: number
  violations: any[]
}

export default class CI extends Command {
  static args = {
    target: Args.string({
      description: 'URL, HTML file, or directory to check',
      required: true,
    }),
  }
static description = 'Run accessibility checks for CI/CD pipelines - returns exit codes and generates reports'
static examples = [
    '<%= config.bin %> <%= command.id %> https://example.com --threshold 85',
    '<%= config.bin %> <%= command.id %> ./dist --threshold 90 --fail-on critical',
    '<%= config.bin %> <%= command.id %> https://example.com --format junit --output results.xml',
    '<%= config.bin %> <%= command.id %> https://example.com --badge badge.svg',
  ]
static flags = {
    badge: Flags.string({
      description: 'Generate accessibility score badge (SVG file path)',
    }),
    'fail-on': Flags.string({
      default: 'serious',
      description: 'Fail on issue severity level (critical, serious, moderate, minor)',
      options: ['critical', 'serious', 'moderate', 'minor'],
    }),
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console, json, junit)',
      options: ['console', 'json', 'junit'],
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path for reports',
    }),
    threshold: Flags.integer({
      char: 't',
      default: 80,
      description: 'Minimum accessibility score (0-100) to pass',
    }),
    timeout: Flags.integer({
      default: 30_000,
      description: 'Page load timeout in milliseconds',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(CI)

    // CI mode - minimal output unless verbose
    const isCI = process.env.CI === 'true' || process.env.GITHUB_ACTIONS === 'true' || process.env.GITLAB_CI === 'true'

    if (!isCI) {
      intro('Aelira CLI - CI/CD Accessibility Check')
    }

    let browser: Browser | null = null
    let page: null | Page = null
    let exitCode = 0

    try {
      const s = isCI ? null : spinner()
      s?.start('Running accessibility checks...')

      browser = await chromium.launch({ headless: true })
      const context = await browser.newContext()
      page = await context.newPage()

      const targetUrl = await this.resolveTargetUrl(args.target)

      await page.goto(targetUrl, { timeout: flags.timeout })

      // Run axe-core scan
      const axeResults = await new AxeBuilder({ page }).analyze()

      s?.stop('Scan complete')

      // Calculate results
      const result = this.calculateResults(args.target, axeResults, flags.threshold)

      // Determine if we should fail
      const failSeverities = this.getFailSeverities(flags['fail-on'])
      const hasFailingIssues = result.violations.some((v: any) => failSeverities.includes(v.impact))

      if (!result.passedThreshold || hasFailingIssues) {
        exitCode = 1
      }

      // Output results in requested format
      switch (flags.format) {
        case 'json': {
          await this.outputJson(result, flags.output)
          break
        }

        case 'junit': {
          await this.outputJunit(result, flags.output)
          break
        }

        default: {
          this.outputConsole(result, flags.threshold, flags['fail-on'], isCI)
        }
      }

      // Generate badge if requested
      await this.writeBadge(result.score, flags.badge, isCI)

      if (!isCI) {
        outro(exitCode === 0 ? '✅ CI check passed!' : '❌ CI check failed')
      }
    } catch (error: any) {
      if (isCI) {
        this.log(`::error::${error.message}`)
      } else {
        outro(`❌ Error: ${error.message}`)
      }

      exitCode = 2
    } finally {
      if (page) await page.close()
      if (browser) await browser.close()
    }

    // Exit with appropriate code for CI
    process.exit(exitCode)
  }

  private calculateResults(target: string, axeResults: any, threshold: number): CIResult {
    const { violations } = axeResults

    // Count issues by severity
    const criticalCount = violations.filter((v: any) => v.impact === 'critical').length
    const seriousCount = violations.filter((v: any) => v.impact === 'serious').length
    const errorCount = criticalCount + seriousCount

    // Calculate score (start at 100, deduct for issues)
    let score = 100
    score -= criticalCount * 20
    score -= seriousCount * 10
    score -= violations.filter((v: any) => v.impact === 'moderate').length * 5
    score -= violations.filter((v: any) => v.impact === 'minor').length * 2
    score = Math.max(0, Math.min(100, score))

    return {
      criticalCount,
      errorCount,
      passedThreshold: score >= threshold,
      score,
      seriousCount,
      target,
      timestamp: new Date().toISOString(),
      totalIssues: violations.length,
      violations,
    }
  }

  /** Local files and directories are scanned over file:// URLs. */
  private async resolveTargetUrl(target: string): Promise<string> {
    if (target.startsWith('http')) return target

    const absolutePath = path.resolve(target)
    const stats = await fs.stat(absolutePath)

    // Find index.html in directory
    return stats.isDirectory()
      ? `file://${path.join(absolutePath, 'index.html')}`
      : `file://${absolutePath}`
  }

  private async writeBadge(score: number, badgePath: string | undefined, isCI: boolean): Promise<void> {
    if (!badgePath) return

    await this.generateBadge(score, badgePath)
    if (!isCI) {
      this.log(`\n✅ Badge saved to ${badgePath}`)
    }
  }

  private escapeXml(text: string): string {
    return text
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll('\'', '&apos;')
  }

  private async generateBadge(score: number, outputPath: string): Promise<void> {
    const color = score >= 90 ? '4c1' : score >= 70 ? 'a3c51c' : score >= 50 ? 'dfb317' : 'e05d44'
    const label = 'accessibility'
    const message = `${score}%`

    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="130" height="20">
  <linearGradient id="b" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="a">
    <rect width="130" height="20" rx="3" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#a)">
    <path fill="#555" d="M0 0h79v20H0z"/>
    <path fill="#${color}" d="M79 0h51v20H79z"/>
    <path fill="url(#b)" d="M0 0h130v20H0z"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
    <text x="40.5" y="15" fill="#010101" fill-opacity=".3">${label}</text>
    <text x="40.5" y="14">${label}</text>
    <text x="103.5" y="15" fill="#010101" fill-opacity=".3">${message}</text>
    <text x="103.5" y="14">${message}</text>
  </g>
</svg>`

    await fs.writeFile(outputPath, svg)
  }

  private getFailSeverities(failOn: string): string[] {
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

      case 'serious': {
        return ['critical', 'serious']
      }

      default: {
        return ['critical', 'serious']
      }
    }
  }

  private getScoreDisplay(score: number): string {
    if (score >= 90) {
      return pc.green(`${score}%`)
    }

    if (score >= 70) {
      return pc.yellow(`${score}%`)
    }

    return pc.red(`${score}%`)
  }

  private outputConsole(result: CIResult, threshold: number, failOn: string, isCI: boolean): void {
    if (isCI) {
      // GitHub Actions/GitLab CI compatible output
      this.log(`::group::Accessibility Report`)
      this.log(`Target: ${result.target}`)
      this.log(`Score: ${result.score}%`)
      this.log(`Threshold: ${threshold}%`)
      this.log(`Total Issues: ${result.totalIssues}`)
      this.log(`Critical: ${result.criticalCount}`)
      this.log(`Serious: ${result.seriousCount}`)

      if (result.totalIssues > 0) {
        this.log(`\nViolations:`)
        for (const v of result.violations.slice(0, 10)) {
          this.log(`  - [${v.impact}] ${v.id}: ${v.description}`)
        }

        if (result.violations.length > 10) {
          this.log(`  ... and ${result.violations.length - 10} more`)
        }
      }

      this.log(`::endgroup::`)

      // Set outputs for GitHub Actions
      if (process.env.GITHUB_OUTPUT) {
        const outputs = [
          `score=${result.score}`,
          `passed=${result.passedThreshold}`,
          `total_issues=${result.totalIssues}`,
          `critical_count=${result.criticalCount}`,
          `serious_count=${result.seriousCount}`,
        ]
        fs.appendFile(process.env.GITHUB_OUTPUT, outputs.join('\n') + '\n').catch(() => {})
      }

      // Emit warnings/errors for CI
      for (const v of result.violations) {
        if (v.impact === 'critical') {
          this.log(`::error title=${v.id}::${v.description}`)
        } else if (v.impact === 'serious') {
          this.log(`::warning title=${v.id}::${v.description}`)
        }
      }
    } else {
      // Human-readable output
      this.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
      this.log('  CI/CD Accessibility Check Report')
      this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

      this.log(`  Target: ${result.target}`)
      this.log(`  Timestamp: ${result.timestamp}`)
      this.log(`  Score: ${this.getScoreDisplay(result.score)} (threshold: ${threshold}%)`)
      this.log(`  Status: ${result.passedThreshold ? pc.green('PASS') : pc.red('FAIL')}\n`)

      this.log('  Issue Summary:')
      this.log(`  - Critical: ${result.criticalCount > 0 ? pc.red(String(result.criticalCount)) : '0'}`)
      this.log(`  - Serious: ${result.seriousCount > 0 ? pc.yellow(String(result.seriousCount)) : '0'}`)
      this.log(`  - Total: ${result.totalIssues}\n`)

      if (result.violations.length > 0) {
        this.log('  Top Issues:')
        for (const v of result.violations.slice(0, 5)) {
          const icon = v.impact === 'critical' ? '🔴' : v.impact === 'serious' ? '🟠' : '🟡'
          this.log(`  ${icon} [${v.impact}] ${v.id}`)
          this.log(`     ${v.description}\n`)
        }
      }

      this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

      this.log(`  Fail on: ${failOn} severity or above`)
      this.log(`  Exit code: ${result.passedThreshold ? '0 (pass)' : '1 (fail)'}\n`)
    }
  }

  private async outputJson(result: CIResult, outputPath?: string): Promise<void> {
    const json = JSON.stringify(result, null, 2)

    if (outputPath) {
      await fs.writeFile(outputPath, json)
    } else {
      this.log(json)
    }
  }

  private async outputJunit(result: CIResult, outputPath?: string): Promise<void> {
    const {timestamp} = result
    const failures = result.violations.filter((v: any) => ['critical', 'serious'].includes(v.impact)).length
    const errors = result.criticalCount

    let xml = `<?xml version="1.0" encoding="UTF-8"?>
<testsuites name="Aelira Accessibility" tests="${result.totalIssues}" failures="${failures}" errors="${errors}" time="0">
  <testsuite name="Accessibility Scan" tests="${result.violations.length}" failures="${failures}" errors="${errors}" timestamp="${timestamp}">
`

    for (const v of result.violations) {
      const status = ['critical', 'serious'].includes(v.impact) ? 'failure' : 'skipped'
      const nodes = v.nodes?.length || 1

      xml += `    <testcase name="${this.escapeXml(v.id)}" classname="accessibility.${v.impact}">\n`

      if (status === 'failure') {
        xml += `      <failure message="${this.escapeXml(v.description)}" type="${v.impact}">
${this.escapeXml(v.help || '')}
Affected elements: ${nodes}
Help URL: ${v.helpUrl || 'N/A'}
      </failure>\n`
      }

      xml += `    </testcase>\n`
    }

    // Add a passing test case for the score
    xml += `    <testcase name="accessibility-score" classname="accessibility.score">
      <system-out>Score: ${result.score}%</system-out>
    </testcase>
`

    xml += `  </testsuite>
</testsuites>`

    if (outputPath) {
      await fs.writeFile(outputPath, xml)
    } else {
      this.log(xml)
    }
  }
}
