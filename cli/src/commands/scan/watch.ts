import type { Browser } from 'playwright'

import { AxeBuilder } from '@axe-core/playwright'
import { Args, Command, Flags } from '@oclif/core'
import FormData from 'form-data'
import * as fs from 'node:fs'
import * as fsPromises from 'node:fs/promises'
import * as path from 'node:path'
import pc from 'picocolors'
import { chromium } from 'playwright'

import { ApiClient } from '../../utils/api-client.js'
import { getApiUrl } from '../../utils/config.js'

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

/** Backends may return issues as an array or as a map of severity counts. */
function normalizeIssues(result: any): Array<{ impact?: string; severity?: string }> {
  if (Array.isArray(result.issues)) return result.issues

  const issues: Array<{ impact?: string; severity?: string }> = []
  if (result.issues && typeof result.issues === 'object') {
    for (const [sev, count] of Object.entries(result.issues)) {
      for (let i = 0; i < (count as number); i++) {
        issues.push({ severity: sev })
      }
    }
  }

  return issues
}

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
      let issues: Array<{ impact?: string; severity?: string }> = []

      if (mapping.transport === 'local-html') {
        // HTML scan with persistent Playwright browser
        const axeResults = await this.scanHtml(filePath)
        issues = axeResults.violations.map((v: any) => ({
          impact: v.impact,
          severity: v.impact,
        }))
      } else {
        issues = await this.scanViaApi(api, mapping.endpoint, filePath)
      }

      this.totalScans++
      this.totalIssues += issues.length

      this.log(`[${ts}] ${this.formatResult(issues)} — ${relativePath}`)
    } catch (error: any) {
      this.log(`[${ts}] ${pc.red('Error:')} ${relativePath} — ${error.message}`)
    }
  }

  // ---------------------------------------------------------------------------
  // HTML scan with persistent Playwright browser
  // ---------------------------------------------------------------------------

  private async scanHtml(filePath: string): Promise<any> {
    if (!this.browserRef.browser) {
      this.browserRef.browser = await chromium.launch({ headless: true })
    }

    const page = await this.browserRef.browser.newPage()
    try {
      const html = await fsPromises.readFile(filePath, 'utf8')
      await page.setContent(html)
      const results = await new AxeBuilder({ page }).analyze()
      return results
    } finally {
      await page.close()
    }
  }

  // ---------------------------------------------------------------------------
  // Upload to API
  // ---------------------------------------------------------------------------

  private async scanViaApi(
    api: ApiClient,
    endpoint: string,
    filePath: string,
  ): Promise<Array<{ impact?: string; severity?: string }>> {
    const formData = new FormData()
    formData.append('file', await fsPromises.readFile(filePath), path.basename(filePath))

    const response = await api.postForm(endpoint, formData as any, {
      timeout: 120_000,
    })

    return normalizeIssues(await response.json())
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  private formatResult(issues: Array<{ impact?: string; severity?: string }>): string {
    if (issues.length === 0) return pc.green('\u2713 0 issues \u2014 Score: 100')

    const counts: Record<string, number> = {}
    for (const issue of issues) {
      const sev = this.normalizeSeverity(issue.severity ?? issue.impact ?? 'unknown')
      counts[sev] = (counts[sev] ?? 0) + 1
    }

    const parts = Object.entries(counts).map(([sev, count]) => `${count} ${sev}`)
    const score = Math.max(0, 100 - issues.length * 5)
    return `\u2713 ${issues.length} issue${issues.length === 1 ? '' : 's'} (${parts.join(', ')}) \u2014 Score: ${score}`
  }

  private formatTimestamp(): string {
    const now = new Date()
    return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`
  }

  private normalizeSeverity(severity: string): string {
    const map: Record<string, string> = {
      high: 'serious',
      low: 'minor',
      medium: 'moderate',
    }
    return map[severity] ?? severity
  }
}
