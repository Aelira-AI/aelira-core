import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'

import { ApiClient } from '../../utils/api-client.js'
import { formatIssuesToCsv } from '../../utils/csv-formatter.js'

export default class ScanWeb extends Command {
  static args = {
    url: Args.string({
      description: 'URL to scan (single URL, batch URLs, or sitemap)',
      required: true,
    }),
  }
static description = 'Advanced web scanning with batch and sitemap support'
static examples = [
    '<%= config.bin %> <%= command.id %> https://example.com',
    '<%= config.bin %> <%= command.id %> https://example.com --batch',
    '<%= config.bin %> <%= command.id %> https://example.com/sitemap.xml --sitemap',
    '<%= config.bin %> <%= command.id %> https://example.com --max-pages 50',
  ]
static flags = {
    'api-url': Flags.string({
      default: 'http://localhost:8000',
      description: 'Aelira API URL',
    }),
    batch: Flags.boolean({
      char: 'b',
      default: false,
      description: 'Enable batch scanning (crawl linked pages)',
    }),
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console or json)',
      options: ['console', 'csv', 'json'],
    }),
    'max-pages': Flags.integer({
      char: 'm',
      default: 20,
      description: 'Maximum pages to scan (batch/sitemap mode)',
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path (for JSON or CSV format)',
    }),
    sitemap: Flags.boolean({
      char: 's',
      default: false,
      description: 'Treat URL as sitemap.xml and scan all URLs',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance timing information',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(ScanWeb)
    const startTime = Date.now()

    intro('Aelira CLI - Advanced Web Accessibility Scanner')

    try {
      if (flags.sitemap) {
        await this.scanSitemap(args.url, flags, startTime)
      } else if (flags.batch) {
        await this.batchScan(args.url, flags, startTime)
      } else {
        await this.singleScan(args.url, flags, startTime)
      }
    } catch (error: any) {
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private async batchScan(url: string, flags: any, startTime: number): Promise<void> {
    const api = new ApiClient({ apiUrl: flags['api-url'] })
    const s = spinner()
    s.start(`Starting batch scan of ${url} (max ${flags['max-pages']} pages)...`)

    const response = await api.post('/education/web/batch-scan', {
      max_pages: flags['max-pages'],
      start_url: url,
    }, {
      timeout: 300_000, // 5 minute timeout for batch scan
    })

    const result = await response.json()
    const scanDuration = Date.now() - startTime
    s.stop('Batch scan complete')

    if (flags.format === 'csv') {
      const allIssues = result.violations || result.issues_list || result.issues || []
      const csv = formatIssuesToCsv(allIssues, url)
      if (flags.output) {
        await fs.writeFile(flags.output, csv)
        outro(`✅ Batch scan complete. CSV saved to ${flags.output}`)
      } else {
        this.log(csv)
      }
    } else if (flags.format === 'json') {
      const output = { ...result, performance: { scan_time: scanDuration } }
      if (flags.output) {
        await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
        outro(`✅ Batch scan complete. Report saved to ${flags.output}`)
      } else {
        this.log(JSON.stringify(output, null, 2))
      }
    } else {
      this.displayBatchResult(result, scanDuration)
      outro('✨ Batch web scan complete!')
    }

    if (flags.timer) {
      this.log(`\n⏱️  Total execution time: ${scanDuration}ms`)
    }
  }

  private displayBatchResult(result: any, scanTime: number): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Batch Web Scan Results`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  Pages Scanned: ${result.pages_scanned || 0}`)
    this.log(`  Pages Failed: ${result.pages_failed || 0}`)

    if (result.average_score !== undefined) {
      this.log(`  Average Score: ${result.average_score?.toFixed(1) || 'N/A'}/100`)
    }

    if (result.total_issues) {
      this.log(`\n  Total Issues: ${result.total_issues}`)
      this.log(`  - Critical: ${result.issues_by_severity?.critical || 0}`)
      this.log(`  - High: ${result.issues_by_severity?.high || 0}`)
      this.log(`  - Medium: ${result.issues_by_severity?.medium || 0}`)
      this.log(`  - Low: ${result.issues_by_severity?.low || 0}`)
    }

    this.log(`\n  Processing Time: ${(scanTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    // Show worst pages
    if (result.pages && result.pages.length > 0) {
      this.log('Pages with Most Issues:\n')
      result.pages
        .sort((a: any, b: any) => (b.issue_count || 0) - (a.issue_count || 0))
        .slice(0, 5)
        .forEach((page: any, index: number) => {
          this.log(`${index + 1}. ${page.url}`)
          this.log(`   Score: ${page.compliance_score || 'N/A'}/100, Issues: ${page.issue_count || 0}`)
        })
      this.log('')
    }

    this.log('💡 Tip: Use --format json for detailed per-page breakdown')
  }

  private displaySingleResult(result: any, scanTime: number): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Web Accessibility Report`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  URL: ${result.url || 'Unknown'}`)
    this.log(`  Page Title: ${result.page_title || 'N/A'}`)

    if (result.compliance_score !== undefined) {
      this.log(`\n  Compliance Score: ${result.compliance_score}/100`)
    }

    if (result.issues) {
      const total = Object.values(result.issues).reduce((a: number, b: any) => a + (b || 0), 0)
      this.log(`\n  Issues Found: ${total}`)
      this.log(`  - Critical: ${result.issues.critical || 0}`)
      this.log(`  - High: ${result.issues.high || 0}`)
      this.log(`  - Medium: ${result.issues.medium || 0}`)
      this.log(`  - Low: ${result.issues.low || 0}`)
    }

    this.log(`\n  Processing Time: ${(scanTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')
  }

  private async scanSitemap(url: string, flags: any, startTime: number): Promise<void> {
    const api = new ApiClient({ apiUrl: flags['api-url'] })
    const s = spinner()
    s.start(`Scanning sitemap at ${url} (max ${flags['max-pages']} pages)...`)

    const response = await api.post('/education/web/scan-sitemap', {
      max_pages: flags['max-pages'],
      sitemap_url: url,
    }, {
      timeout: 300_000,
    })

    const result = await response.json()
    const scanDuration = Date.now() - startTime
    s.stop('Sitemap scan complete')

    if (flags.format === 'csv') {
      const allIssues = result.violations || result.issues_list || result.issues || []
      const csv = formatIssuesToCsv(allIssues, url)
      if (flags.output) {
        await fs.writeFile(flags.output, csv)
        outro(`✅ Sitemap scan complete. CSV saved to ${flags.output}`)
      } else {
        this.log(csv)
      }
    } else if (flags.format === 'json') {
      const output = { ...result, performance: { scan_time: scanDuration } }
      if (flags.output) {
        await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
        outro(`✅ Sitemap scan complete. Report saved to ${flags.output}`)
      } else {
        this.log(JSON.stringify(output, null, 2))
      }
    } else {
      this.displayBatchResult(result, scanDuration)
      outro('✨ Sitemap scan complete!')
    }

    if (flags.timer) {
      this.log(`\n⏱️  Total execution time: ${scanDuration}ms`)
    }
  }

  private async singleScan(url: string, flags: any, startTime: number): Promise<void> {
    const api = new ApiClient({ apiUrl: flags['api-url'] })
    const s = spinner()
    s.start(`Scanning ${url}...`)

    const response = await api.post('/education/web/scan', { url }, {
      timeout: 120_000,
    })

    const result = await response.json()
    const scanDuration = Date.now() - startTime
    s.stop('Scan complete')

    if (flags.format === 'csv') {
      const csv = formatIssuesToCsv(result.violations || result.issues_list || result.issues || [], url)
      if (flags.output) {
        await fs.writeFile(flags.output, csv)
        this.log(`CSV report saved to ${flags.output}`)
      } else {
        this.log(csv)
      }
    } else if (flags.format === 'json') {
      const output = { ...result, performance: { scan_time: scanDuration } }
      if (flags.output) {
        await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
        outro(`✅ Web scan complete. Report saved to ${flags.output}`)
      } else {
        this.log(JSON.stringify(output, null, 2))
      }
    } else {
      this.displaySingleResult(result, scanDuration)
      outro('✨ Web scan complete!')
    }

    if (flags.timer) {
      this.log(`\n⏱️  Total execution time: ${scanDuration}ms`)
    }
  }
}
