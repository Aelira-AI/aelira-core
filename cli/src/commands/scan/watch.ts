import { AxeBuilder } from '@axe-core/playwright'
import { Args, Command, Flags } from '@oclif/core'
import FormData from 'form-data'
import * as fs from 'node:fs'
import * as fsPromises from 'node:fs/promises'
import * as path from 'node:path'
import pc from 'picocolors'
import { type Browser, chromium } from 'playwright'

import { ApiClient } from '../../utils/api-client.js'
import { chromiumLaunchOptions } from '../../utils/browser.js'
import { getApiUrl } from '../../utils/config.js'
import { pollForCompletion } from '../../utils/poll-progress.js'
import { summarizeScanDetail } from '../../utils/scan-detail.js'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const EXTENSION_MAP: Record<string, { endpoint: string; transport: 'form' | 'local-html' }> = {
  '.css': { endpoint: '/education/code/scan', transport: 'form' },
  '.docx': { endpoint: '/education/word/scan', transport: 'form' },
  '.htm': { endpoint: '', transport: 'local-html' },
  '.html': { endpoint: '', transport: 'local-html' },
  '.js': { endpoint: '/education/code/scan', transport: 'form' },
  '.pdf': { endpoint: '/education/pdf/scan', transport: 'form' },
  '.pptx': { endpoint: '/education/powerpoint/scan', transport: 'form' },
  '.tex': { endpoint: '/education/latex/scan', transport: 'form' },
  '.xlsx': { endpoint: '/education/excel/scan', transport: 'form' },
}

const MAX_FILE_SIZE = 50 * 1024 * 1024 // 50 MB

interface WatchResult {
  counts: Array<[string, number]>
  score: null | number
  total: number
}

const quietProgress = { message() {}, stop() {} }

export default class ScanWatch extends Command {
  static args = {
    directory: Args.string({
      description: 'Directory to watch for file changes',
      required: true,
    }),
  }
  static description = 'Watch a directory for file changes and auto-scan for accessibility issues'
  static examples = [
    '<%= config.bin %> <%= command.id %> ./course-materials',
    '<%= config.bin %> <%= command.id %> ./docs --extensions .pdf,.docx',
    '<%= config.bin %> <%= command.id %> ./src --no-recursive --debounce 1000',
  ]
  static flags = {
    'api-url': Flags.string({
      description: 'Aelira API URL',
    }),
    concurrency: Flags.integer({
      default: 3,
      description: 'Max concurrent scans',
    }),
    debounce: Flags.integer({
      default: 2000,
      description: 'Debounce delay in milliseconds',
    }),
    extensions: Flags.string({
      default: '.pdf,.docx,.pptx,.xlsx,.html,.htm,.tex,.css,.js',
      description: 'Comma-separated file extensions to watch',
    }),
    recursive: Flags.boolean({
      allowNo: true,
      default: true,
      description: 'Watch subdirectories recursively',
    }),
  }
  private browserRef: { browser: Browser | null } = { browser: null }
  private totalIssues = 0
  private totalScans = 0

  async run(): Promise<void> {
    const { args, flags } = await this.parse(ScanWatch)

    // 1. Verify directory exists
    const dirPath = path.resolve(args.directory)
    try {
      await fsPromises.access(dirPath)
    } catch {
      this.error(`Directory not found: ${dirPath}`)
    }

    // 2. Verify backend reachable
    const apiUrl = await getApiUrl(flags['api-url'])
    const api = new ApiClient({ apiUrl })
    try {
      await api.get('/health', { retry: false, timeout: 10_000 })
    } catch {
      this.error(`Backend unreachable at ${apiUrl}`)
    }

    // 3. Parse extensions
    const extensions = new Set(
      flags.extensions.split(',').map((e) => e.trim().toLowerCase()),
    )

    // 4. Check platform for recursive support
    let useRecursive = flags.recursive
    if (useRecursive && process.platform === 'linux') {
      this.warn('Recursive watching not supported on Linux — watching top-level directory only')
      useRecursive = false
    }

    // 5. State
    const changedFiles = new Set<string>()
    let debounceTimer: NodeJS.Timeout | null = null

    // 6. Startup message
    this.log(
      pc.cyan(
        `Watching ${dirPath} (${useRecursive ? 'recursive' : 'top-level'}, ${extensions.size} extensions, max ${flags.concurrency} concurrent)`,
      ),
    )
    this.log(pc.dim('Press Ctrl+C to stop.\n'))

    // 7. Start watcher
    const watcher = fs.watch(dirPath, { recursive: useRecursive }, (_eventType, filename) => {
      if (!filename) return
      const fullPath = path.resolve(dirPath, filename)
      const ext = path.extname(filename).toLowerCase()
      if (!extensions.has(ext)) return

      changedFiles.add(fullPath)
      if (debounceTimer) clearTimeout(debounceTimer)
      debounceTimer = setTimeout(() => {
        void this.processBatch(changedFiles, api, flags.concurrency)
      }, flags.debounce)
    })

    // 8. Handle Ctrl+C
    process.on('SIGINT', async () => {
      watcher.close()
      if (this.browserRef.browser) {
        await this.browserRef.browser.close()
      }

      this.log(
        `\n${pc.cyan(`Stopped watching. Scanned ${this.totalScans} file${this.totalScans === 1 ? '' : 's'}, found ${this.totalIssues} total issue${this.totalIssues === 1 ? '' : 's'}.`)}`,
      )

      process.exit(0)
    })

    // 9. Keep process alive
    await new Promise(() => {})
  }

  // ---------------------------------------------------------------------------
  // Batch processing
  // ---------------------------------------------------------------------------

  private async processBatch(
    changedFiles: Set<string>,
    api: ApiClient,
    concurrency: number,
  ): Promise<void> {
    const files = [...changedFiles]
    changedFiles.clear()

    // Process in chunks of `concurrency` size
    for (let i = 0; i < files.length; i += concurrency) {
      const chunk = files.slice(i, i + concurrency)
      await Promise.all(chunk.map((file) => this.scanFile(file, api)))
    }
  }

  // ---------------------------------------------------------------------------
  // Single file scan
  // ---------------------------------------------------------------------------

  private async scanFile(filePath: string, api: ApiClient): Promise<void> {
    const ts = this.formatTimestamp()
    const relativePath = path.relative(process.cwd(), filePath)

    // Check file still exists
    try {
      await fsPromises.access(filePath)
    } catch {
      this.log(`[${ts}] ${pc.dim('Skipped (deleted):')} ${relativePath}`)
      return
    }

    // Check file size
    const stat = await fsPromises.stat(filePath)
    if (stat.size > MAX_FILE_SIZE) {
      this.log(
        `[${ts}] ${pc.yellow('Skipped (>50 MB):')} ${relativePath} (${(stat.size / 1024 / 1024).toFixed(1)} MB)`,
      )
      return
    }

    const ext = path.extname(filePath).toLowerCase()
    const mapping = EXTENSION_MAP[ext]
    if (!mapping) return

    this.log(`[${ts}] ${pc.blue('Changed:')} ${relativePath}`)

    try {
      let result: WatchResult

      if (mapping.transport === 'local-html') {
        // HTML scan with persistent Playwright browser. axe reports
        // violations only, so there is no scanner score for this path.
        const axeResults = await this.scanHtml(filePath)
        const counts: Record<string, number> = {}
        for (const violation of axeResults.violations) {
          const impact = violation.impact ?? 'unknown'
          counts[impact] = (counts[impact] ?? 0) + 1
        }

        result = { counts: Object.entries(counts), score: null, total: axeResults.violations.length }
      } else {
        result = await this.scanViaApi(api, mapping.endpoint, filePath)
      }

      this.totalScans++
      this.totalIssues += result.total

      this.log(`[${ts}] ${this.formatResult(result)} — ${relativePath}`)
    } catch (error: any) {
      this.log(`[${ts}] ${pc.red('Error:')} ${relativePath} — ${error.message}`)
    }
  }

  // ---------------------------------------------------------------------------
  // HTML scan with persistent Playwright browser
  // ---------------------------------------------------------------------------

  private async scanHtml(filePath: string): Promise<any> {
    if (!this.browserRef.browser) {
      this.browserRef.browser = await chromium.launch(chromiumLaunchOptions())
    }

    // @axe-core/playwright needs a page from an explicit browser context.
    const context = await this.browserRef.browser.newContext()
    try {
      const page = await context.newPage()
      const html = await fsPromises.readFile(filePath, 'utf8')
      await page.setContent(html)
      return await new AxeBuilder({ page }).analyze()
    } finally {
      await context.close()
    }
  }

  // ---------------------------------------------------------------------------
  // Upload to API
  // ---------------------------------------------------------------------------

  /**
   * Upload a file, wait for the queued scan, and read its counts and score
   * from GET /education/scans/{scan_id}. The upload routes only acknowledge
   * the job with a scan_id.
   */
  private async scanViaApi(api: ApiClient, endpoint: string, filePath: string): Promise<WatchResult> {
    const formData = new FormData()
    formData.append('file', await fsPromises.readFile(filePath), path.basename(filePath))

    const response = await api.postForm(endpoint, formData as any, {
      timeout: 120_000,
    })
    const queued = await response.json()
    if (!queued.scan_id) throw new Error('Unexpected response: no scan_id returned')

    const summary = summarizeScanDetail(await pollForCompletion(api, queued.scan_id, quietProgress, { timeout: 300_000 }))
    const counts = summary.issueCounts
    if (!counts) throw new Error('Scan finished without issue counts')

    return {
      counts: (['critical', 'high', 'medium', 'low'] as const).map((severity) => [severity, counts[severity]]),
      score: summary.complianceScore,
      total: counts.total,
    }
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  private formatResult(result: WatchResult): string {
    const parts = result.counts.filter(([, count]) => count > 0).map(([severity, count]) => `${count} ${severity}`)
    const issues = `${result.total} issue${result.total === 1 ? '' : 's'}${parts.length > 0 ? ` (${parts.join(', ')})` : ''}`
    const text = result.score === null ? issues : `${issues} \u2014 Scanner score: ${result.score}/100`
    return result.total === 0 ? pc.green(text) : text
  }

  private formatTimestamp(): string {
    const now = new Date()
    return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`
  }
}
