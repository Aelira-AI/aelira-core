import type { Browser, Page } from 'playwright'

import { AxeBuilder } from '@axe-core/playwright'
import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'
import { chromium } from 'playwright'

import { ApiClient } from '../utils/api-client.js'
import { buildReportEvidence, generateVerifiedPdfReport } from '../utils/report-artifact.js'

export default class Scan extends Command {
  static args = {
    target: Args.string({
      description: 'URL or local HTML file to scan',
      required: true,
    }),
  }
static description = 'Scan a URL or HTML file for accessibility issues'
static examples = [
    '<%= config.bin %> <%= command.id %> https://example.com',
    '<%= config.bin %> <%= command.id %> https://example.com --mode comprehensive',
    '<%= config.bin %> <%= command.id %> https://example.com --mode deep',
    '<%= config.bin %> <%= command.id %> ./index.html',
    '<%= config.bin %> <%= command.id %> http://localhost:3000',
    '<%= config.bin %> <%= command.id %> https://example.com --format json --mode comprehensive',
    '<%= config.bin %> <%= command.id %> . --threshold 80',
    '<%= config.bin %> <%= command.id %> ./index.html --pdf accessibility-report.pdf',
  ]
static flags = {
    'api-url': Flags.string({
      description: 'Aelira API URL for PDF generation',
    }),
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console, json, html)',
      options: ['console', 'html', 'json'],
    }),
    'load-delay': Flags.integer({
      default: 0,
      description: 'Wait time in ms after page load (for SPAs)',
    }),
    local: Flags.boolean({
      default: false,
      description: 'Skip AI analysis (free tier, axe-core only)',
    }),
    mode: Flags.string({
      char: 'm',
      default: 'quick',
      description: 'Scan thoroughness (quick, comprehensive, deep)',
      options: ['quick', 'comprehensive', 'deep'],
    }),
    output: Flags.string({
      char: 'o',
      description: 'Save report to file',
    }),
    pdf: Flags.string({
      description: 'Generate a verified server PDF report and save it atomically',
    }),
    threshold: Flags.integer({
      char: 't',
      description: 'Exit with code 1 if score below threshold (CI mode)',
    }),
    timeout: Flags.integer({
      default: 30_000,
      description: 'Timeout for page load in ms',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance metrics',
    }),
  }

  public async run(): Promise<void> {
    const { args, flags } = await this.parse(Scan)
    const startTime = Date.now()

    intro('Aelira CLI - Accessibility Scanner')

    let browser: Browser | undefined
    let page: Page | undefined
    let axeResults: any

    try {
      // Determine if target is URL or file
      const isUrl = this.isUrl(args.target)

      if (isUrl) {
        // URL scanning with Playwright
        const s = spinner()
        s.start('Launching browser...')
        let scanDuration = 0
        let succeeded = false

        try {
          browser = await chromium.launch({ headless: true })
          const context = await browser.newContext()
          page = await context.newPage()

          s.message('Navigating to ' + args.target + '...')
          await page.goto(args.target, { timeout: flags.timeout })

          if (flags['load-delay'] > 0) {
            s.message(`Waiting ${flags['load-delay']}ms for page to settle...`)
            await page.waitForTimeout(flags['load-delay'])
          }

          // Show scan mode info
          const modeDescriptions = {
            comprehensive: 'axe-core + Pa11y (~95%+ coverage)',
            deep: 'All engines + AI vision (maximum confidence)',
            quick: 'axe-core only (~90% coverage)'
          }
          s.message(`Running ${flags.mode} scan: ${modeDescriptions[flags.mode as keyof typeof modeDescriptions]}`)
          const scanStart = Date.now()

          axeResults = await new AxeBuilder({ page }).analyze()

          scanDuration = Date.now() - scanStart

          // CLI uses axe-core only for local scanning (fast, no backend required).
          // For Pa11y + AI vision modes, use the backend API via 'aelira analyze' command.
          // This is intentional: CLI prioritizes speed and offline capability.
          if (flags.mode !== 'quick') {
            s.message(`Note: CLI uses axe-core only for speed. Use 'aelira analyze' for ${flags.mode} mode with AI.`)
          }

          succeeded = true
        } finally {
          // The spinner interval keeps the event loop alive until stop() runs,
          // so it must fire on every path — including when goto/analyze throws.
          s.stop(succeeded ? `Scan complete in ${scanDuration}ms` : 'Scan failed')
        }

        await this.printResults(axeResults, flags, startTime, args.target)
      } else {
        // Local HTML file scanning
        const s = spinner()
        s.start('Reading HTML file...')
        let scanDuration = 0
        let succeeded = false

        try {
          const htmlContent = await this.readHtmlFile(args.target)

          s.message('Launching browser...')
          browser = await chromium.launch({ headless: true })
          const context = await browser.newContext()
          page = await context.newPage()

          s.message('Loading HTML content...')
          await page.setContent(htmlContent)

          s.message('Running axe-core accessibility scan...')
          const scanStart = Date.now()

          axeResults = await new AxeBuilder({ page }).analyze()

          scanDuration = Date.now() - scanStart
          succeeded = true
        } finally {
          // Same rationale as the URL branch: readHtmlFile can throw for a
          // missing file before the spinner is ever stopped, which otherwise
          // leaves its interval running and the process hanging.
          s.stop(succeeded ? `Scan complete in ${scanDuration}ms` : 'Scan failed')
        }

        await this.printResults(axeResults, flags, startTime, args.target)
      }

      // Check threshold for CI/CD (reuse cached scan results)
      if (flags.threshold !== undefined && axeResults) {
        const score = this.calculateScore(axeResults)
        if (score < flags.threshold) {
          this.error(`Score ${score} is below threshold ${flags.threshold}`, { exit: 1 })
        }
      }

      outro('✓ Scan complete!')
    } catch (error) {
      if (error instanceof Error) {
        this.error(error.message, { exit: 1 })
      }

      throw error
    } finally {
      if (page) await page.close()
      if (browser) await browser.close()
    }
  }

  private calculateScore(results: any): number {
    const total = results.violations.length + results.passes.length
    if (total === 0) return 100
    const passRate = results.passes.length / total
    return Math.round(passRate * 100)
  }

  private async generatePdfReport(
    results: any,
    pdfPath: string,
    apiUrl: string | undefined,
    target: string,
  ): Promise<void> {
    const s = spinner()
    s.start('Generating PDF report...')

    try {
      const api = new ApiClient({ apiUrl })
      await generateVerifiedPdfReport({
        api,
        destination: pdfPath,
        evidence: buildReportEvidence({
          axeResults: results,
          reportKind: 'scan',
          target,
        }),
      })

      s.stop(`PDF report saved to ${pdfPath}`)
    } catch (error: any) {
      s.stop('PDF generation failed')
      throw error
    }
  }

  private isUrl(target: string): boolean {
    return /^https?:\/\//i.test(target)
  }

  private async printResults(
    results: any,
    flags: any,
    startTime: number,
    target: string,
  ): Promise<void> {
    if (flags.format === 'json') {
      const output = JSON.stringify(results, null, 2)
      if (flags.output) {
        await fs.writeFile(flags.output, output)
        this.log(`Report saved to ${flags.output}`)
      } else {
        this.log(output)
      }

    } else if (flags.format === 'console') {
      // Calculate score
      const score = this.calculateScore(results)

      this.log('')
      this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
      this.log(`  Accessibility Score: ${score}/100`)
      this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
      this.log('')

      // Summary
      const {violations} = results
      const critical = violations.filter((v: any) => v.impact === 'critical').length
      const high = violations.filter((v: any) => v.impact === 'serious').length
      const medium = violations.filter((v: any) => v.impact === 'moderate').length
      const low = violations.filter((v: any) => v.impact === 'minor').length

      this.log(`  Total Issues: ${violations.length}`)
      this.log(`  - Critical: ${critical}`)
      this.log(`  - High: ${high}`)
      this.log(`  - Medium: ${medium}`)
      this.log(`  - Low: ${low}`)
      this.log('')

      // Show top 5 issues
      if (violations.length > 0) {
        this.log('Top Issues:')
        this.log('')
        violations.slice(0, 5).forEach((violation: any, index: number) => {
          this.log(`  ${index + 1}. ${violation.help}`)
          this.log(`     Impact: ${violation.impact}`)
          this.log(`     Occurrences: ${violation.nodes.length}`)
          this.log(`     Rule: ${violation.id}`)
          this.log('')
        })
      }

      if (flags.timer) {
        const totalTime = Date.now() - startTime
        this.log(`⏱️  Total execution time: ${totalTime}ms`)
      }

      this.log('💡 Tip: Use --format json for CI/CD integration')
    }

    // Generate PDF report if requested
    if (flags.pdf) {
      await this.generatePdfReport(results, flags.pdf, flags['api-url'], target)
    }
  }

  private async readHtmlFile(filePath: string): Promise<string> {
    try {
      const resolvedPath = path.resolve(filePath)
      const content = await fs.readFile(resolvedPath, 'utf8')
      return content
    } catch {
      throw new Error(`Failed to read HTML file: ${filePath}`)
    }
  }
}
