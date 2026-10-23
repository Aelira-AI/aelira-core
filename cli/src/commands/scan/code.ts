import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import FormData from 'form-data'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

import { ApiClient } from '../../utils/api-client.js'
import { formatIssuesToCsv } from '../../utils/csv-formatter.js'
import { pollForCompletion } from '../../utils/poll-progress.js'
import { numberOrNull, scoreAndCountLines, summarizeScanDetail } from '../../utils/scan-detail.js'

export default class ScanCode extends Command {
  static args = {
    file: Args.string({
      description: 'HTML/CSS/JS file or directory to scan for accessibility issues',
      required: true,
    }),
  }
static description =
    'Scan source code for accessibility issues (ARIA attributes, semantic HTML, keyboard navigation)'
static examples = [
    '<%= config.bin %> <%= command.id %> index.html',
    '<%= config.bin %> <%= command.id %> ./src/',
    '<%= config.bin %> <%= command.id %> component.jsx --format json',
    '<%= config.bin %> <%= command.id %> ./frontend/ --api-url http://localhost:8000',
  ]
static flags = {
    'api-url': Flags.string({
      description: 'Aelira API URL',
    }),
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console or json)',
      options: ['console', 'csv', 'json'],
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path (for JSON or CSV format)',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance timing information',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(ScanCode)
    const startTime = Date.now()

    intro('Aelira CLI - Code Accessibility Scanner')

    try {
      const targetPath = path.resolve(args.file)
      const stats = await fs.stat(targetPath)

      if (stats.isDirectory()) {
        // Batch scan directory
        await this.scanDirectory(targetPath, flags, startTime)
      } else {
        // Single file scan
        await this.scanFile(targetPath, flags, startTime)
      }
    } catch (error: any) {
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private displayBatchResults(results: any[], totalTime: number): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Batch Code Scan Results`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    const successful = results.filter((r) => !r.error).length
    const failed = results.filter((r) => r.error).length

    const summaries = results
      .filter((r) => !r.error)
      .map((r) => ({ file: r.file, summary: summarizeScanDetail(r) }))
    const totalIssues = summaries.reduce((sum, { summary }) => sum + (summary.issueCounts?.total ?? 0), 0)

    this.log(`  Total Files: ${results.length}`)
    this.log(`  Successful: ${successful}`)
    this.log(`  Failed: ${failed}`)
    this.log(`  Total Issues: ${totalIssues}`)
    this.log(`  Processing Time: ${(totalTime / 1000).toFixed(1)}s\n`)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    if (summaries.length > 0) {
      const sorted = [...summaries].sort(
        (a, b) => (b.summary.issueCounts?.total ?? 0) - (a.summary.issueCounts?.total ?? 0),
      )
      this.log('Files Needing Most Attention:\n')
      for (const [index, { file, summary }] of sorted.slice(0, 5).entries()) {
        this.log(`${index + 1}. ${file}`)
        if (summary.complianceScore !== null) this.log(`   Scanner score: ${summary.complianceScore}/100`)
        if (summary.issueCounts) {
          this.log(`   Issues: ${summary.issueCounts.total} (Critical: ${summary.issueCounts.critical})`)
        }

        this.log('')
      }
    }

    if (failed > 0) {
      this.log('Failed Scans:\n')
      for (const result of results
        .filter((r) => r.error)) {
          this.log(`  ✗ ${result.file}: ${result.error}`)
        }

      this.log('')
    }

    this.log('💡 Tip: Use --format json --output report.json for full results')
  }

  private displaySingleResult(result: any, scanTime: number): void {
    const summary = summarizeScanDetail(result)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Code Accessibility Report`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  File: ${summary.fileName ?? 'Unknown'}`)
    this.log(`  Lines of Code: ${numberOrNull(summary.structure.total_lines) ?? 'N/A'}\n`)

    for (const line of scoreAndCountLines(summary)) this.log(line)

    this.log(`\n  Processing Time: ${(scanTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    if (summary.scanId) {
      this.log(`  Scan ID: ${summary.scanId}\n`)
    }

    this.log('💡 Tip: Use --format json for detailed issue breakdown with line numbers')
    this.log('💡 Tip: Use directory path to batch scan multiple code files')
  }

  private async findCodeFiles(dirPath: string): Promise<string[]> {
    const files: string[] = []
    const codeExtensions = new Set(['.css', '.htm', '.html', '.js', '.jsx', '.svelte', '.ts', '.tsx', '.vue'])

    const entries = await fs.readdir(dirPath, { withFileTypes: true })

    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name)

      if (entry.isDirectory()) {
        // Skip node_modules and common build directories
        if (
          entry.name === 'node_modules' ||
          entry.name === 'dist' ||
          entry.name === 'build' ||
          entry.name === '.next' ||
          entry.name === '.git'
        ) {
          continue
        }

        // Recursively scan subdirectories
        files.push(...(await this.findCodeFiles(fullPath)))
      } else if (entry.isFile()) {
        const ext = path.extname(entry.name).toLowerCase()
        if (codeExtensions.has(ext)) {
          files.push(fullPath)
        }
      }
    }

    return files
  }

  private async scanDirectory(
    dirPath: string,
    flags: any,
    startTime: number
  ): Promise<void> {
    const s = spinner()
    s.start('Finding code files...')

    const files = await this.findCodeFiles(dirPath)

    if (files.length === 0) {
      s.stop('No code files found')
      outro('⚠️  No code files to scan')
      return
    }

    s.stop(`Found ${files.length} code file${files.length > 1 ? 's' : ''}`)

    const results: any[] = []

    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      s.start(`Scanning ${path.basename(file)} (${i + 1}/${files.length})...`)

      try {
        const result = await this.uploadAndScan(file, flags['api-url'], s)
        results.push({ file: path.basename(file), ...result })
        s.stop(`✓ ${path.basename(file)}`)
      } catch (error: any) {
        s.stop(`✗ ${path.basename(file)}: ${error.message}`)
        results.push({ error: error.message, file: path.basename(file) })
      }
    }

    // Output results
    if (flags.format === 'csv') {
      const allIssues = results.flatMap((r) => summarizeScanDetail(r).issues)
      const csv = formatIssuesToCsv(allIssues, 'batch')
      if (flags.output) {
        await fs.writeFile(flags.output, csv)
        outro(`✅ Batch scan complete. CSV saved to ${flags.output}`)
      } else {
        this.log(csv)
      }
    } else if (flags.format === 'json') {
      const output = {
        performance: {
          files_scanned: files.length,
          total_time: Date.now() - startTime,
        },
        results,
      }

      if (flags.output) {
        await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
        outro(`✅ Batch scan complete. Report saved to ${flags.output}`)
      } else {
        this.log(JSON.stringify(output, null, 2))
      }
    } else {
      this.displayBatchResults(results, Date.now() - startTime)
      outro(`✨ Batch scan complete! Scanned ${files.length} code files`)
    }

    if (flags.timer) {
      this.log(`\n⏱️  Total execution time: ${Date.now() - startTime}ms`)
    }
  }

  private async scanFile(
    filePath: string,
    flags: any,
    startTime: number
  ): Promise<void> {
    const s = spinner()
    s.start('Uploading code to Aelira API...')

    try {
      const result = await this.uploadAndScan(filePath, flags['api-url'], s)
      const scanDuration = Date.now() - startTime

      s.stop('Scan complete')

      if (flags.format === 'csv') {
        const csv = formatIssuesToCsv(summarizeScanDetail(result).issues, path.basename(filePath))
        if (flags.output) {
          await fs.writeFile(flags.output, csv)
          this.log(`CSV report saved to ${flags.output}`)
        } else {
          this.log(csv)
        }
      } else if (flags.format === 'json') {
        const output = {
          ...result,
          performance: {
            scan_time: scanDuration,
          },
        }

        if (flags.output) {
          await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
          outro(`✅ Code scan complete. Report saved to ${flags.output}`)
        } else {
          this.log(JSON.stringify(output, null, 2))
        }
      } else {
        this.displaySingleResult(result, scanDuration)
        outro('✨ Code scan complete!')
      }

      if (flags.timer) {
        this.log(`\n⏱️  Total execution time: ${scanDuration}ms`)
      }
    } catch (error: any) {
      s.stop('Upload failed')
      throw error
    }
  }

  private async uploadAndScan(
    filePath: string,
    apiUrl: string | undefined,
    s: ReturnType<typeof spinner>,
  ): Promise<any> {
    const api = new ApiClient({ apiUrl })
    const formData = new FormData()
    formData.append('file', await fs.readFile(filePath), path.basename(filePath))

    // Upload with shorter timeout (just file transfer)
    const response = await api.postForm('/education/code/scan', formData as any, {
      timeout: 30_000,
    })
    const uploadResult = await response.json()

    if (!uploadResult.scan_id) {
      throw new Error('Unexpected response: no scan_id returned')
    }

    // Poll for completion with progress updates
    return pollForCompletion(api, uploadResult.scan_id, s, { timeout: 60_000 })
  }
}
