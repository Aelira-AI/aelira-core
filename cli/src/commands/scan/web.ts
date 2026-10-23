import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'

import { ApiClient } from '../../utils/api-client.js'
import { formatIssuesToCsv } from '../../utils/csv-formatter.js'
import { pollForCompletion } from '../../utils/poll-progress.js'
import { numberOrNull, scoreAndCountLines, summarizeScanDetail } from '../../utils/scan-detail.js'

export default class ScanWeb extends Command {
  static args = {
    url: Args.string({
      description: 'URL to scan, a sitemap URL with --sitemap, or one or more URLs with --batch',
      required: false,
    }),
  }
static description = 'Advanced web scanning with batch and sitemap support'
static examples = [
    '<%= config.bin %> <%= command.id %> https://example.com',
    '<%= config.bin %> <%= command.id %> https://example.com https://example.com/about --batch',
    '<%= config.bin %> <%= command.id %> --batch --urls-file urls.txt --depth 0',
    '<%= config.bin %> <%= command.id %> https://example.com/sitemap.xml --sitemap --max-pages 50',
  ]
static strict = false
static flags = {
    'api-url': Flags.string({
      description: 'Aelira API URL',
    }),
    batch: Flags.boolean({
      char: 'b',
      default: false,
      description: 'Scan one or more URLs as one batch (up to 50)',
    }),
    depth: Flags.integer({
      default: 1,
      description: 'Batch: levels of links to follow from each URL (0 = only the given URLs; 1 also scans pages they link to)',
      min: 0,
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
      description: 'Maximum sitemap URLs to scan (sitemap mode)',
      min: 1,
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
    'urls-file': Flags.string({
      description: 'File with one URL per line to scan as a batch (blank lines and # comments are ignored)',
    }),
  }

  async run(): Promise<void> {
    const { argv, flags } = await this.parse(ScanWeb)
    const startTime = Date.now()
    const urls = await this.collectUrls(argv as string[], flags)

    intro('Aelira CLI - Advanced Web Accessibility Scanner')

    try {
      if (flags.sitemap) {
        await this.runScan('sitemap', urls, flags, startTime)
      } else if (flags.batch) {
        await this.runScan('batch', urls, flags, startTime)
      } else {
        await this.runScan('single', urls, flags, startTime)
      }
    } catch (error: any) {
      outro(`Error: ${error.message}`)
      this.error(error)
    }
  }

  /** URLs from the arguments and --urls-file, checked against the chosen mode. */
  private async collectUrls(argv: string[], flags: any): Promise<string[]> {
    const urls = [...argv]
    if (flags['urls-file']) {
      if (!flags.batch) this.error('--urls-file needs --batch')
      const text = await fs.readFile(flags['urls-file'], 'utf8')
      urls.push(...text.split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith('#')))
    }

    if (urls.length === 0) this.error('Missing URL. Give a URL, or URLs with --batch.')
    if (flags.batch && flags.sitemap) this.error('Use either --batch or --sitemap, not both')
    if (!flags.batch && urls.length > 1) this.error('Several URLs need --batch')
    if (flags.batch && urls.length > 50) this.error('A batch can scan at most 50 URLs')
    return urls
  }

  private displayAggregateResult(detail: any, scanTime: number, kind: 'batch' | 'sitemap'): void {
    const summary = summarizeScanDetail(detail)
    const { structure } = summary

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(kind === 'sitemap' ? '  Sitemap Web Scan Results' : '  Batch Web Scan Results')
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    if (kind === 'sitemap') {
      this.log(`  Sitemap: ${structure.sitemap_url ?? summary.fileName ?? 'Unknown'}`)
      this.log(`  URLs Discovered: ${numberOrNull(structure.total_urls_discovered) ?? 'N/A'}`)
    }

    this.log(`  URLs Scanned: ${numberOrNull(structure.total_urls_scanned) ?? 'N/A'}`)
    this.log(`  Pages Scanned: ${numberOrNull(structure.total_pages_scanned) ?? 'N/A'}\n`)

    for (const line of scoreAndCountLines(summary)) this.log(line)

    this.log(`\n  Processing Time: ${(scanTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    if (summary.scanId) {
      this.log(`  Scan ID: ${summary.scanId}\n`)
    }

    this.log('Tip: Use --format json or --format csv to list each finding with its page')
  }

  private displaySingleResult(detail: any, scanTime: number): void {
    const summary = summarizeScanDetail(detail)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Web Accessibility Report`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  URL: ${summary.structure.root_url ?? summary.fileName ?? 'Unknown'}`)
    this.log(`  Pages Scanned: ${numberOrNull(summary.structure.pages_scanned) ?? 'N/A'}\n`)

    for (const line of scoreAndCountLines(summary)) this.log(line)

    this.log(`\n  Processing Time: ${(scanTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    if (summary.scanId) {
      this.log(`  Scan ID: ${summary.scanId}\n`)
    }
  }

  /**
   * Queue a scan, wait for the worker, and return GET /education/scans/{id}.
   * The three routes in src/api/education/web_scan_routes.py only acknowledge
   * the queued job; single and sitemap scans return scan_id, batch scans
   * return batch_scan_id.
   */
  private async queueAndWait(kind: 'batch' | 'single' | 'sitemap', urls: string[], flags: any, s: ReturnType<typeof spinner>): Promise<any> {
    const api = new ApiClient({ apiUrl: flags['api-url'] })
    let response: Response
    if (kind === 'sitemap') {
      response = await api.post('/education/web/scan-sitemap', { max_pages: flags['max-pages'], sitemap_url: urls[0] }, { timeout: 60_000 })
    } else if (kind === 'batch') {
      // BatchWebScanRequest takes the URL list and a crawl depth per URL.
      response = await api.post('/education/web/batch-scan', { max_depth: flags.depth, urls }, { timeout: 60_000 })
    } else {
      response = await api.post('/education/web/scan', { url: urls[0] }, { timeout: 60_000 })
    }

    const queued = await response.json()
    const scanId = queued.scan_id ?? queued.batch_scan_id
    if (!scanId) throw new Error('Unexpected response: no scan_id returned')

    return pollForCompletion(api, scanId, s, { timeout: kind === 'single' ? 300_000 : 1_800_000 })
  }

  private async runScan(kind: 'batch' | 'single' | 'sitemap', urls: string[], flags: any, startTime: number): Promise<void> {
    const s = spinner()
    s.start(
      kind === 'sitemap'
        ? `Scanning sitemap at ${urls[0]} (max ${flags['max-pages']} pages)...`
        : kind === 'batch'
          ? `Starting batch scan of ${urls.length} URL${urls.length === 1 ? '' : 's'} (depth ${flags.depth})...`
          : `Scanning ${urls[0]}...`,
    )

    let detail: any
    try {
      detail = await this.queueAndWait(kind, urls, flags, s)
    } catch (error) {
      s.stop('Scan failed')
      throw error
    }

    const scanDuration = Date.now() - startTime
    s.stop('Scan complete')
    const label = kind === 'sitemap' ? 'Sitemap scan' : kind === 'batch' ? 'Batch scan' : 'Web scan'

    if (flags.format === 'csv') {
      const csv = formatIssuesToCsv(summarizeScanDetail(detail).issues, kind === 'batch' ? 'batch' : urls[0])
      if (flags.output) {
        await fs.writeFile(flags.output, csv)
        outro(`${label} complete. CSV saved to ${flags.output}`)
      } else {
        this.log(csv)
      }
    } else if (flags.format === 'json') {
      const output = { ...detail, performance: { scan_time: scanDuration } }
      if (flags.output) {
        await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
        outro(`${label} complete. Report saved to ${flags.output}`)
      } else {
        this.log(JSON.stringify(output, null, 2))
      }
    } else {
      if (kind === 'single') this.displaySingleResult(detail, scanDuration)
      else this.displayAggregateResult(detail, scanDuration, kind)
      outro(`${label} complete.`)
    }

    if (flags.timer) {
      this.log(`\nTotal execution time: ${scanDuration}ms`)
    }
  }
}
