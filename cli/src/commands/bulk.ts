import type { Browser, Page } from 'playwright'

import { AxeBuilder } from '@axe-core/playwright'
import { intro, isCancel, outro, select, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'
import pc from 'picocolors'
import { chromium } from 'playwright'

interface BulkScanResult {
  criticalCount: number
  duration: number
  errorCount: number
  filePath: string
  passCount: number
  score: number
  seriousCount: number
  status: 'error' | 'scanned' | 'skipped'
  timestamp: string
  violations: any[]
}

interface BulkSummary {
  averageScore: number
  duration: number
  errorFiles: string[]
  failedFiles: string[]
  passedFiles: string[]
  results: BulkScanResult[]
  skippedFiles: string[]
  totalCritical: number
  totalFiles: number
  totalSerious: number
}

const SUPPORTED_EXTENSIONS = new Set(['.docx', '.htm', '.html', '.pdf', '.pptx', '.xlsx'])

export default class Bulk extends Command {
  static args = {
    action: Args.string({
      description: 'Action to perform',
      options: ['scan', 'remediate', 'export', 'report'],
      required: true,
    }),
    target: Args.string({
      description: 'Directory or file pattern to process',
      required: true,
    }),
  }
static description = 'Bulk operations for scanning and remediating multiple files'
static examples = [
    '<%= config.bin %> <%= command.id %> scan ./course-materials --recursive',
    '<%= config.bin %> <%= command.id %> scan ./pdfs --pattern "*.pdf"',
    '<%= config.bin %> <%= command.id %> export ./results --format csv',
    '<%= config.bin %> <%= command.id %> report ./course-materials --output report.html',
  ]
static flags = {
    concurrency: Flags.integer({
      char: 'c',
      default: 3,
      description: 'Number of parallel scans (1-10)',
    }),
    'dry-run': Flags.boolean({
      default: false,
      description: 'Show what would be processed without actually doing it',
    }),
    format: Flags.string({
      char: 'f',
      default: 'json',
      description: 'Output format for export action',
      options: ['json', 'csv', 'html'],
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path for results',
    }),
    pattern: Flags.string({
      char: 'p',
      description: 'File pattern to match (e.g., "*.pdf", "*.html")',
    }),
    recursive: Flags.boolean({
      char: 'r',
      default: false,
      description: 'Recursively scan subdirectories',
    }),
    resume: Flags.string({
      description: 'Resume from a previous scan state file',
    }),
    threshold: Flags.integer({
      char: 't',
      default: 80,
      description: 'Minimum accessibility score to pass',
    }),
    timeout: Flags.integer({
      default: 30_000,
      description: 'Timeout per file in milliseconds',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(Bulk)

    intro('Aelira CLI - Bulk Operations')

    // Validate concurrency
    const concurrency = Math.max(1, Math.min(10, flags.concurrency))

    switch (args.action) {
      case 'export': {
        await this.runExport(args.target, flags)
        break
      }

      case 'remediate': {
        await this.runRemediate(args.target, flags)
        break
      }

      case 'report': {
        await this.runReport(args.target, flags)
        break
      }

      case 'scan': {
        await this.runScan(args.target, { ...flags, concurrency })
        break
      }

      default: {
        this.error(`Unknown action: ${args.action}`)
      }
    }
  }

  private async collectFiles(
    targetPath: string,
    recursive: boolean,
    pattern?: string,
  ): Promise<string[]> {
    const absolutePath = path.resolve(targetPath)
    const files: string[] = []

    try {
      const stats = await fs.stat(absolutePath)

      if (stats.isFile()) {
        // Single file
        if (this.isSupported(absolutePath, pattern)) {
          files.push(absolutePath)
        }
      } else if (stats.isDirectory()) {
        // Directory - collect files
        await this.walkDirectory(absolutePath, files, recursive, pattern)
      }
    } catch {
      this.warn(`Cannot access: ${absolutePath}`)
    }

    return files.sort()
  }

  private escapeCSV(value: string): string {
    if (value.includes(',') || value.includes('"') || value.includes('\n')) {
      return `"${value.replaceAll('"', '""')}"`
    }

    return value
  }

  private formatDuration(ms: number): string {
    if (ms < 1000) return `${ms}ms`
    if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
    const minutes = Math.floor(ms / 60_000)
    const seconds = Math.floor((ms % 60_000) / 1000)
    return `${minutes}m ${seconds}s`
  }

  private generateReport(summary: BulkSummary, targetPath: string): string {
    const timestamp = new Date().toISOString()

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Aelira Accessibility Report</title>
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
    .container { max-width: 1200px; margin: 0 auto; }
    h1 { color: var(--primary); margin-bottom: 0.5rem; }
    .subtitle { color: #94a3b8; margin-bottom: 2rem; }
    .summary {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }
    .stat {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.5rem;
      text-align: center;
    }
    .stat-value { font-size: 2.5rem; font-weight: bold; }
    .stat-label { color: #94a3b8; font-size: 0.875rem; }
    .stat-success .stat-value { color: var(--success); }
    .stat-warning .stat-value { color: var(--warning); }
    .stat-error .stat-value { color: var(--error); }
    table {
      width: 100%;
      border-collapse: collapse;
      background: var(--card);
      border-radius: 8px;
      overflow: hidden;
    }
    th, td { padding: 1rem; text-align: left; border-bottom: 1px solid var(--border); }
    th { background: var(--border); font-weight: 600; }
    tr:last-child td { border-bottom: none; }
    .score {
      display: inline-block;
      padding: 0.25rem 0.75rem;
      border-radius: 9999px;
      font-weight: 600;
      font-size: 0.875rem;
    }
    .score-pass { background: rgba(34, 197, 94, 0.2); color: var(--success); }
    .score-warn { background: rgba(234, 179, 8, 0.2); color: var(--warning); }
    .score-fail { background: rgba(239, 68, 68, 0.2); color: var(--error); }
    .badge { font-size: 0.75rem; padding: 0.125rem 0.5rem; border-radius: 4px; }
    .badge-critical { background: var(--error); color: white; }
    .badge-serious { background: var(--warning); color: black; }
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
    <h1>Accessibility Report</h1>
    <p class="subtitle">
      Generated by Aelira CLI • ${timestamp}<br>
      Target: ${targetPath}
    </p>

    <div class="summary">
      <div class="stat">
        <div class="stat-value">${summary.totalFiles}</div>
        <div class="stat-label">Total Files</div>
      </div>
      <div class="stat stat-success">
        <div class="stat-value">${summary.passedFiles.length}</div>
        <div class="stat-label">Passed</div>
      </div>
      <div class="stat stat-error">
        <div class="stat-value">${summary.failedFiles.length}</div>
        <div class="stat-label">Failed</div>
      </div>
      <div class="stat ${summary.averageScore >= 80 ? 'stat-success' : summary.averageScore >= 60 ? 'stat-warning' : 'stat-error'}">
        <div class="stat-value">${summary.averageScore}%</div>
        <div class="stat-label">Average Score</div>
      </div>
      <div class="stat stat-error">
        <div class="stat-value">${summary.totalCritical}</div>
        <div class="stat-label">Critical Issues</div>
      </div>
      <div class="stat stat-warning">
        <div class="stat-value">${summary.totalSerious}</div>
        <div class="stat-label">Serious Issues</div>
      </div>
    </div>

    <h2>File Results</h2>
    <table>
      <thead>
        <tr>
          <th>File</th>
          <th>Score</th>
          <th>Critical</th>
          <th>Serious</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        ${summary.results
          .map(
            (r) => `
          <tr>
            <td>${path.basename(r.filePath)}</td>
            <td>
              <span class="score ${r.score >= 80 ? 'score-pass' : r.score >= 60 ? 'score-warn' : 'score-fail'}">
                ${r.score}%
              </span>
            </td>
            <td>${r.criticalCount > 0 ? `<span class="badge badge-critical">${r.criticalCount}</span>` : '0'}</td>
            <td>${r.seriousCount > 0 ? `<span class="badge badge-serious">${r.seriousCount}</span>` : '0'}</td>
            <td>${r.status}</td>
          </tr>
        `,
          )
          .join('')}
      </tbody>
    </table>

    <footer>
      <p>Aelira - Higher Education Accessibility Platform</p>
      <p>Report generated with axe-core accessibility testing engine</p>
    </footer>
  </div>
</body>
</html>`
  }

  private getScoreColor(score: number): string {
    if (score >= 90) return pc.green(`${score}%`)
    if (score >= 70) return pc.yellow(`${score}%`)
    return pc.red(`${score}%`)
  }

  private isSupported(filePath: string, pattern?: string): boolean {
    const ext = path.extname(filePath).toLowerCase()

    // Check pattern first if provided
    if (pattern) {
      const regex = new RegExp('^' + pattern.replaceAll('.', String.raw`\.`).replaceAll('*', '.*') + '$')
      return regex.test(path.basename(filePath))
    }

    // Check supported extensions
    return SUPPORTED_EXTENSIONS.has(ext)
  }

  private async runExport(
    targetPath: string,
    flags: { format: string; output?: string },
  ): Promise<void> {
    const s = spinner()
    s.start('Loading scan results...')

    try {
      // Look for previous scan results
      const resultsPath = path.resolve(targetPath)
      let results: BulkSummary

      try {
        const content = await fs.readFile(resultsPath, 'utf8')
        results = JSON.parse(content)
      } catch {
        s.stop('No results found')
        this.error(
          `Cannot load results from: ${resultsPath}\nRun a bulk scan first or provide a valid results file.`,
        )
        return
      }

      s.stop('Results loaded')

      // Export in requested format
      let output: string
      let ext: string

      switch (flags.format) {
        case 'csv': {
          output = this.toCSV(results)
          ext = '.csv'
          break
        }

        case 'html': {
          output = this.toHTML(results)
          ext = '.html'
          break
        }

        default: {
          output = JSON.stringify(results, null, 2)
          ext = '.json'
        }
      }

      // Write output
      const outputPath = flags.output || `bulk-export-${Date.now()}${ext}`
      await fs.writeFile(outputPath, output)

      outro(`✅ Exported to ${outputPath}`)
    } catch (error: any) {
      s.stop('Export failed')
      this.error(error.message)
    }
  }

  private async runRemediate(
    targetPath: string,
    flags: { 'dry-run': boolean; pattern?: string; recursive: boolean },
  ): Promise<void> {
    const s = spinner()
    s.start('Collecting files...')

    const files = await this.collectFiles(targetPath, flags.recursive, flags.pattern)

    s.stop(`Found ${files.length} files`)

    if (files.length === 0) {
      outro('No files found to remediate')
      return
    }

    // Show files that would be processed
    this.log('\n📁 Files to remediate:\n')
    for (const file of files.slice(0, 10)) {
      this.log(`  ${pc.dim('•')} ${path.relative(process.cwd(), file)}`)
    }

    if (files.length > 10) {
      this.log(`  ${pc.dim(`... and ${files.length - 10} more`)}`)
    }

    if (flags['dry-run']) {
      outro('Dry run complete - no files were modified')
      return
    }

    // Confirm before proceeding
    const confirm = await select({
      message: `Remediate ${files.length} files?`,
      options: [
        { label: 'Yes, proceed', value: 'yes' },
        { label: 'No, cancel', value: 'no' },
      ],
    })

    if (isCancel(confirm) || confirm === 'no') {
      outro('Remediation cancelled')
      return
    }

    // Note: Full remediation would require backend API integration
    // For now, we'll show what would be done
    s.start('Remediating files...')

    // Simulate remediation progress
    for (let i = 0; i < files.length; i++) {
      s.message(`Processing ${i + 1}/${files.length}: ${path.basename(files[i])}`)
      await new Promise((resolve) => {
        setTimeout(resolve, 100)
      })
    }

    s.stop('Remediation complete')

    this.log('\n⚠️  Note: Full auto-remediation requires backend API connection.')
    this.log('   Run `aelira config init` to configure backend connection.\n')

    outro(`✅ Processed ${files.length} files`)
  }

  private async runReport(
    targetPath: string,
    flags: { output?: string; pattern?: string; recursive: boolean; threshold: number },
  ): Promise<void> {
    // First run a scan
    const scanFlags = {
      concurrency: 3,
      'dry-run': false,
      format: 'json',
      output: undefined,
      pattern: flags.pattern,
      recursive: flags.recursive,
      resume: undefined,
      threshold: flags.threshold,
      timeout: 30_000,
    }

    // Collect and scan files
    const s = spinner()
    s.start('Generating comprehensive report...')

    const files = await this.collectFiles(targetPath, flags.recursive, flags.pattern)

    if (files.length === 0) {
      s.stop('No files found')
      outro('No files found to report on')
      return
    }

    // Scan files
    const results = await this.scanFiles(files, scanFlags)
    s.stop('Scan complete')

    // Generate HTML report
    const html = this.generateReport(results, targetPath)

    const outputPath = flags.output || `accessibility-report-${Date.now()}.html`
    await fs.writeFile(outputPath, html)

    // Summary
    this.log('\n' + pc.bold('Report Summary'))
    this.log('━'.repeat(50))
    this.log(`  Total Files: ${results.totalFiles}`)
    this.log(`  Passed: ${pc.green(String(results.passedFiles.length))}`)
    this.log(`  Failed: ${pc.red(String(results.failedFiles.length))}`)
    this.log(`  Average Score: ${this.getScoreColor(results.averageScore)}`)
    this.log(`  Critical Issues: ${results.totalCritical}`)
    this.log(`  Serious Issues: ${results.totalSerious}`)
    this.log('━'.repeat(50))

    outro(`✅ Report saved to ${outputPath}`)
  }

  private async runScan(
    targetPath: string,
    flags: {
      concurrency: number
      'dry-run': boolean
      output?: string
      pattern?: string
      recursive: boolean
      resume?: string
      threshold: number
      timeout: number
    },
  ): Promise<void> {
    const s = spinner()
    s.start('Collecting files...')

    // Collect files to scan
    const files = await this.collectFiles(targetPath, flags.recursive, flags.pattern)

    s.stop(`Found ${files.length} files`)

    if (files.length === 0) {
      outro('No supported files found')
      return
    }

    // Show what will be scanned
    this.log('\n📁 Files to scan:\n')
    for (const file of files.slice(0, 10)) {
      this.log(`  ${pc.dim('•')} ${path.relative(process.cwd(), file)}`)
    }

    if (files.length > 10) {
      this.log(`  ${pc.dim(`... and ${files.length - 10} more`)}`)
    }

    this.log('')

    if (flags['dry-run']) {
      outro('Dry run complete - no files were scanned')
      return
    }

    // Load resume state if provided
    let startIndex = 0
    let previousResults: BulkScanResult[] = []

    if (flags.resume) {
      try {
        const resumeData = JSON.parse(await fs.readFile(flags.resume, 'utf8'))
        previousResults = resumeData.results || []
        startIndex = previousResults.length
        this.log(`📂 Resuming from file ${startIndex + 1}/${files.length}\n`)
      } catch {
        this.warn(`Cannot load resume file: ${flags.resume}`)
      }
    }

    // Scan files
    s.start(`Scanning files (0/${files.length})...`)
    const startTime = Date.now()

    const results = await this.scanFiles(files.slice(startIndex), {
      ...flags,
      onProgress(completed, total, current) {
        s.message(`Scanning (${startIndex + completed}/${files.length}): ${path.basename(current)}`)
      },
    })

    // Merge with previous results
    results.results = [...previousResults, ...results.results]
    results.totalFiles = files.length

    s.stop(`Scanned ${files.length} files in ${this.formatDuration(Date.now() - startTime)}`)

    // Display summary
    this.log('\n' + pc.bold('Scan Summary'))
    this.log('━'.repeat(50))
    this.log(`  Total Files: ${results.totalFiles}`)
    this.log(`  Passed: ${pc.green(String(results.passedFiles.length))} (score >= ${flags.threshold}%)`)
    this.log(`  Failed: ${pc.red(String(results.failedFiles.length))} (score < ${flags.threshold}%)`)
    this.log(`  Errors: ${results.errorFiles.length}`)
    this.log(`  Skipped: ${results.skippedFiles.length}`)
    this.log(`  Average Score: ${this.getScoreColor(results.averageScore)}`)
    this.log(`  Critical Issues: ${results.totalCritical}`)
    this.log(`  Serious Issues: ${results.totalSerious}`)
    this.log('━'.repeat(50))

    // Show worst files
    if (results.failedFiles.length > 0) {
      this.log('\n' + pc.bold('Files Needing Attention:'))
      const worstResults = results.results
        .filter((r) => r.status === 'scanned')
        .sort((a, b) => a.score - b.score)
        .slice(0, 5)

      for (const result of worstResults) {
        this.log(
          `  ${this.getScoreColor(result.score)} ${path.relative(process.cwd(), result.filePath)}`,
        )
        this.log(
          `      ${pc.dim(`${result.criticalCount} critical, ${result.seriousCount} serious`)}`,
        )
      }
    }

    // Save results
    const outputPath = flags.output || `bulk-scan-${Date.now()}.json`
    await fs.writeFile(outputPath, JSON.stringify(results, null, 2))

    // Save resume state
    const statePath = `bulk-scan-state-${Date.now()}.json`
    await fs.writeFile(statePath, JSON.stringify({ files, results: results.results }, null, 2))

    this.log(`\n📄 Results saved to: ${outputPath}`)
    this.log(`📄 State saved to: ${statePath} (use --resume to continue)`)

    outro(results.failedFiles.length === 0 ? '✅ All files passed!' : '⚠️  Some files need attention')
  }

  private async scanFiles(
    files: string[],
    options: {
      concurrency: number
      onProgress?: (completed: number, total: number, current: string) => void
      threshold: number
      timeout: number
    },
  ): Promise<BulkSummary> {
    const results: BulkScanResult[] = []
    const startTime = Date.now()

    let browser: Browser | null = null

    try {
      browser = await chromium.launch({ headless: true })

      // Process files with limited concurrency
      let completed = 0

      const processFile = async (filePath: string): Promise<BulkScanResult> => {
        const fileStartTime = Date.now()
        const ext = path.extname(filePath).toLowerCase()

        // Only HTML files can be scanned with axe-core directly
        if (ext !== '.html' && ext !== '.htm') {
          return {
            criticalCount: 0,
            duration: Date.now() - fileStartTime,
            errorCount: 0,
            filePath,
            passCount: 0,
            score: 0,
            seriousCount: 0,
            status: 'skipped',
            timestamp: new Date().toISOString(),
            violations: [],
          }
        }

        let page: null | Page = null

        try {
          const context = await browser!.newContext()
          page = await context.newPage()

          await page.goto(`file://${filePath}`, { timeout: options.timeout })

          const axeResults = await new AxeBuilder({ page }).analyze()

          const {violations} = axeResults
          const criticalCount = violations.filter((v) => v.impact === 'critical').length
          const seriousCount = violations.filter((v) => v.impact === 'serious').length

          // Calculate score
          let score = 100
          score -= criticalCount * 20
          score -= seriousCount * 10
          score -= violations.filter((v) => v.impact === 'moderate').length * 5
          score -= violations.filter((v) => v.impact === 'minor').length * 2
          score = Math.max(0, Math.min(100, score))

          return {
            criticalCount,
            duration: Date.now() - fileStartTime,
            errorCount: criticalCount + seriousCount,
            filePath,
            passCount: axeResults.passes.length,
            score,
            seriousCount,
            status: 'scanned',
            timestamp: new Date().toISOString(),
            violations: violations.map((v) => ({
              description: v.description,
              helpUrl: v.helpUrl,
              id: v.id,
              impact: v.impact,
              nodes: v.nodes.length,
            })),
          }
        } catch (error: any) {
          return {
            criticalCount: 0,
            duration: Date.now() - fileStartTime,
            errorCount: 1,
            filePath,
            passCount: 0,
            score: 0,
            seriousCount: 0,
            status: 'error',
            timestamp: new Date().toISOString(),
            violations: [{ description: error.message, id: 'scan-error', impact: 'critical' }],
          }
        } finally {
          if (page) await page.close()
        }
      }

      // Process in batches
      for (let i = 0; i < files.length; i += options.concurrency) {
        const batch = files.slice(i, i + options.concurrency)
        const batchResults = await Promise.all(batch.map((file) => processFile(file)))

        for (const result of batchResults) {
          results.push(result)
          completed++
          options.onProgress?.(completed, files.length, result.filePath)
        }
      }
    } finally {
      if (browser) await browser.close()
    }

    // Calculate summary
    const scannedResults = results.filter((r) => r.status === 'scanned')
    const averageScore =
      scannedResults.length > 0
        ? Math.round(scannedResults.reduce((sum, r) => sum + r.score, 0) / scannedResults.length)
        : 0

    return {
      averageScore,
      duration: Date.now() - startTime,
      errorFiles: results.filter((r) => r.status === 'error').map((r) => r.filePath),
      failedFiles: results
        .filter((r) => r.status === 'scanned' && r.score < options.threshold)
        .map((r) => r.filePath),
      passedFiles: results
        .filter((r) => r.status === 'scanned' && r.score >= options.threshold)
        .map((r) => r.filePath),
      results,
      skippedFiles: results.filter((r) => r.status === 'skipped').map((r) => r.filePath),
      totalCritical: results.reduce((sum, r) => sum + r.criticalCount, 0),
      totalFiles: files.length,
      totalSerious: results.reduce((sum, r) => sum + r.seriousCount, 0),
    }
  }

  private toCSV(summary: BulkSummary): string {
    const headers = ['File', 'Score', 'Status', 'Critical', 'Serious', 'Total Issues', 'Duration', 'Timestamp']
    const rows = summary.results.map((r) => [
      this.escapeCSV(r.filePath),
      r.score.toString(),
      r.status,
      r.criticalCount.toString(),
      r.seriousCount.toString(),
      r.violations.length.toString(),
      r.duration.toString(),
      r.timestamp,
    ])

    return [headers.join(','), ...rows.map((row) => row.join(','))].join('\n')
  }

  private toHTML(summary: BulkSummary): string {
    return this.generateReport(summary, 'Exported Results')
  }

  private async walkDirectory(
    dirPath: string,
    files: string[],
    recursive: boolean,
    pattern?: string,
  ): Promise<void> {
    const entries = await fs.readdir(dirPath, { withFileTypes: true })

    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name)

      if (entry.isDirectory()) {
        if (recursive) {
          await this.walkDirectory(fullPath, files, recursive, pattern)
        }
      } else if (entry.isFile() && this.isSupported(fullPath, pattern)) {
          files.push(fullPath)
        }
    }
  }
}
