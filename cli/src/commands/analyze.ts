import type { Browser, Page } from 'playwright'

import { AxeBuilder } from '@axe-core/playwright'
import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'
import { chromium } from 'playwright'

import { ApiClient } from '../utils/api-client.js'
import { buildReportEvidence, generateVerifiedPdfReport, ReportArtifactError } from '../utils/report-artifact.js'

interface PdfReportOptions {
  aiResults: any
  apiUrl: string
  axeResults: any
  pdfPath: string
  url: string
}

export default class Analyze extends Command {
  static args = {
    target: Args.string({
      description: 'URL or HTML file to scan',
      required: true,
    }),
  }
static description =
    'Scan for accessibility issues with AI-powered classification and fixes'
static examples = [
    '$ aelira analyze https://example.com',
    '$ aelira analyze ./index.html --api-url http://localhost:8000',
    '$ aelira analyze . --format json --output report.json',
    '$ aelira analyze ./index.html --pdf accessibility-report.pdf',
  ]
static flags = {
    'ai-timeout': Flags.integer({
      default: 0,
      description: 'AI analysis timeout in seconds (default: auto-calculated based on issue count)',
    }),
    'api-url': Flags.string({
      default: 'http://localhost:8000',
      description: 'Aelira API URL',
    }),
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console or json)',
      options: ['console', 'json'],
    }),
    'generate-fixes': Flags.boolean({
      default: true,
      description: 'Generate AI-powered code fixes (slower)',
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path (for JSON format)',
    }),
    pdf: Flags.string({
      description: 'Generate a verified server PDF report and save it atomically',
    }),
    timeout: Flags.integer({
      char: 't',
      default: 30_000,
      description: 'Page load timeout in milliseconds',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance timing information',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(Analyze)
    const startTime = Date.now()

    intro('Aelira CLI - AI-Powered Accessibility Scanner')

    let browser: Browser | null = null
    let page: null | Page = null

    try {
      // Step 1: Scan with axe-core (same as `aelira scan`)
      const s = spinner()
      s.start('Launching browser...')

      browser = await chromium.launch({ headless: true })
      const context = await browser.newContext()
      page = await context.newPage()

      s.message('Navigating to ' + args.target + '...')

      // Handle file:// URLs for local HTML files
      let targetUrl = args.target
      if (!args.target.startsWith('http')) {
        const absolutePath = path.resolve(args.target)
        await fs.access(absolutePath) // Check file exists
        targetUrl = `file://${absolutePath}`
      }

      await page.goto(targetUrl, { timeout: flags.timeout })

      s.message('Running axe-core accessibility scan...')
      const scanStart = Date.now()
      const axeResults = await new AxeBuilder({ page }).analyze()
      const scanDuration = Date.now() - scanStart

      s.stop('Scan complete')

      // Step 2: Send violations to Aelira API for AI analysis
      const {violations} = axeResults

      if (violations.length === 0) {
        if (flags.pdf) {
          await this.generatePdfReport({
            aiResults: undefined,
            apiUrl: flags['api-url'],
            axeResults,
            pdfPath: flags.pdf,
            url: args.target,
          })
        }

        outro('✅ No accessibility issues found!')
        if (flags.timer) {
          this.log(`\n⏱️  Total execution time: ${Date.now() - startTime}ms`)
        }

        return
      }

      s.start('Analyzing with AI (Llama 3.2 + Qwen 2.5 Coder)...')

      // Extract page context for context-aware fix generation
      const pageTitle = await page.title()
      const pageUrl = page.url()

      // Try to extract meta description
      let pageContext = ''
      try {
        const metaDescription = await page.locator('meta[name="description"]').getAttribute('content')
        if (metaDescription) {
          pageContext = metaDescription
        } else {
          // Fallback: extract first H1 heading
          const h1Text = await page.locator('h1').first().textContent()
          if (h1Text) {
            pageContext = h1Text
          }
        }
      } catch {
        // Ignore errors extracting context
      }

      // Prepare batch API request with page context
      const batchViolations = violations.flatMap((v: any) =>
        v.nodes.slice(0, 3).map((node: any, idx: number) => ({
          description: v.description,
          html_snippet: node.html || '',
          id: `${v.id}-${idx}`,
          impact: v.impact || 'moderate',
          page_context: pageContext,
          page_title: pageTitle,
          // Context-aware fix generation
          page_url: pageUrl,
          rule_id: v.id,
          selector: node.target.join(', '),
        }))
      )

      try {
        // Calculate timeout based on violation count (if not specified)
        // Base: 90s + 45s per violation (with code fix generation)
        // AI models (Llama 3.2 + Qwen 2.5 Coder) need time to generate fixes
        // Increased from 60s + 30s after observing real-world performance
        const violationCount = batchViolations.length
        const baseTimeout = 90_000 // 90s base (Ollama model loading + first inference)
        const perViolationTimeout = flags['generate-fixes'] ? 45_000 : 15_000 // 45s with fixes, 15s without
        const calculatedTimeout = baseTimeout + (violationCount * perViolationTimeout)
        const maxTimeout = 900_000 // 15 minute max (increased from 10 min)
        const aiTimeout = flags['ai-timeout'] > 0
          ? flags['ai-timeout'] * 1000
          : Math.min(calculatedTimeout, maxTimeout)

        s.message(`Analyzing ${violationCount} node violations (timeout: ${(aiTimeout / 1000).toFixed(0)}s)...`)

        const api = new ApiClient({ apiUrl: flags['api-url'] })

        const aiStart = Date.now()
        const apiResponse = await api.post(
          '/api/ai/batch-analyze',
          {
            generate_fixes: flags['generate-fixes'],
            violations: batchViolations,
          },
          { timeout: aiTimeout },
        )

        const aiResults = await apiResponse.json()
        const aiDuration = Date.now() - aiStart

        s.stop(`AI analysis complete in ${(aiDuration / 1000).toFixed(1)}s`)

        // Step 3: Output results
        if (flags.format === 'json') {
          const output = {
            ai_analysis: aiResults,
            axe_scan: axeResults,
            performance: {
              ai_time: aiDuration,
              scan_time: scanDuration,
              total_time: Date.now() - startTime,
            },
          }

          if (flags.output) {
            await fs.writeFile(
              flags.output,
              JSON.stringify(output, null, 2)
            )
            outro(`✅ AI-enhanced report saved to ${flags.output}`)
          } else {
            this.log(JSON.stringify(output, null, 2))
          }
        } else {
          // Console format with AI insights
          this.displayAIEnhancedResults(
            axeResults,
            aiResults,
            scanDuration,
            aiDuration
          )
          outro('✨ AI-enhanced scan complete!')
        }

        if (flags.timer) {
          this.log(`\n⏱️  Total execution time: ${Date.now() - startTime}ms`)
        }

        // Generate PDF report if requested
        if (flags.pdf) {
          await this.generatePdfReport({
            aiResults,
            apiUrl: flags['api-url'],
            axeResults,
            pdfPath: flags.pdf,
            url: args.target,
          })
        }
      } catch (aiError: any) {
        if (aiError instanceof ReportArtifactError) throw aiError
        s.stop('AI analysis failed')
        this.warn(`Could not connect to AI API: ${aiError.message}`)
        this.log('\nℹ️  Falling back to standard axe-core results...\n')

        // Fallback to standard scan output
        this.displayStandardResults(axeResults, scanDuration)
        if (flags.pdf) {
          await this.generatePdfReport({
            aiResults: undefined,
            apiUrl: flags['api-url'],
            axeResults,
            pdfPath: flags.pdf,
            url: args.target,
          })
        }

        outro('⚠️  Scan complete (without AI enhancement)')
      }
    } catch (error: any) {
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    } finally {
      if (page) await page.close()
      if (browser) await browser.close()
    }
  }

  private displayAIEnhancedResults(
    axeResults: any,
    aiResults: any,
    scanTime: number,
    aiTime: number
  ): void {
    const {violations} = axeResults
    const aiAnalyzed = aiResults.results || []

    // Calculate severity counts from AI results
    const severityCounts: Record<string, number> = {
      Critical: 0,
      High: 0,
      Low: 0,
      Medium: 0,
    }

    aiAnalyzed.forEach((result: any) => {
      const severity = result.classification?.severity || 'Low'
      severityCounts[severity] = (severityCounts[severity] || 0) + 1
    })

    // Display header
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  AI-Enhanced Accessibility Report`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  Total Issues: ${violations.length} (${aiResults.analyzed} analyzed by AI)`)
    this.log(`  - Critical: ${severityCounts.Critical}`)
    this.log(`  - High: ${severityCounts.High}`)
    this.log(`  - Medium: ${severityCounts.Medium}`)
    this.log(`  - Low: ${severityCounts.Low}\n`)

    this.log(`  Performance:`)
    this.log(`  - Axe-core scan: ${scanTime}ms`)
    this.log(`  - AI analysis: ${(aiTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    // Display ALL AI-analyzed issues
    this.log('🤖 AI-Powered Insights:\n')

    aiAnalyzed.forEach((result: any, idx: number) => {
      const classification = result.classification || {}
      this.log(`${idx + 1}. ${result.rule_id} (${classification.severity || 'Unknown'})`)
      this.log(`   ${classification.explanation || 'No explanation available'}\n`)

      if (result.fix && ['Critical', 'High'].includes(classification.severity)) {
        this.log(`   💡 Suggested Fix:`)
        const fixPreview = result.fix.fix_recommendation
          .split('\n')
          .slice(0, 5)
          .join('\n')
        this.log(`   ${fixPreview}...\n`)
      }

      this.log(`   Business Impact: ${classification.business_impact || 'N/A'}\n`)
    })

    this.log('\n💡 Tip: Use --format json to get complete fixes with full code (not truncated)')
  }

  private displayStandardResults(axeResults: any, scanTime: number): void {
    // Fallback to standard scan output (same as `aelira scan`)
    const {violations} = axeResults

    const critical = violations.filter((v: any) => v.impact === 'critical').length
    const high = violations.filter((v: any) => v.impact === 'serious').length
    const medium = violations.filter((v: any) => v.impact === 'moderate').length
    const low = violations.filter((v: any) => v.impact === 'minor').length

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Accessibility Report (Standard)`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  Total Issues: ${violations.length}`)
    this.log(`  - Critical: ${critical}`)
    this.log(`  - High: ${high}`)
    this.log(`  - Medium: ${medium}`)
    this.log(`  - Low: ${low}\n`)

    this.log(`  Scan Time: ${scanTime}ms`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')
  }

  private async generatePdfReport(options: PdfReportOptions): Promise<void> {
    const { aiResults, apiUrl, axeResults, pdfPath, url } = options
    const s = spinner()
    s.start('Generating AI-enhanced PDF report...')

    try {
      const api = new ApiClient({ apiUrl })

      await generateVerifiedPdfReport({
        api,
        destination: pdfPath,
        evidence: buildReportEvidence({
          aiResults,
          axeResults,
          reportKind: 'analyze',
          target: url,
        }),
      })

      s.stop(`✅ PDF report saved to ${pdfPath}`)
    } catch (error: any) {
      s.stop('PDF generation failed')
      throw error
    }
  }
}
