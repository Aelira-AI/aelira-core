import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import FormData from 'form-data'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

import { ApiClient } from '../../utils/api-client.js'
import { formatIssuesToCsv } from '../../utils/csv-formatter.js'
import { pollForCompletion } from '../../utils/poll-progress.js'
import { batchResultLines, numberOrNull, scoreAndCountLines, summarizeScanDetail } from '../../utils/scan-detail.js'

export default class ScanDocx extends Command {
  static args = {
    file: Args.string({
      description: 'Word document (.docx) file or directory to scan',
      required: true,
    }),
  }
static description = 'Scan Word documents (.docx) for accessibility issues'
static examples = [
    '<%= config.bin %> <%= command.id %> document.docx',
    '<%= config.bin %> <%= command.id %> ./documents/',
    '<%= config.bin %> <%= command.id %> document.docx --format json',
    '<%= config.bin %> <%= command.id %> ./docs/ --api-url http://localhost:8000',
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
    const { args, flags } = await this.parse(ScanDocx)
    const startTime = Date.now()

    intro('Aelira CLI - Word Document Accessibility Scanner')

    try {
      const targetPath = path.resolve(args.file)
      const stats = await fs.stat(targetPath)

      await (stats.isDirectory() ? this.scanDirectory(targetPath, flags, startTime) : this.scanFile(targetPath, flags, startTime));
    } catch (error: any) {
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private displayBatchResults(results: any[], totalTime: number): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Batch Word Document Scan Results`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    const successful = results.filter((r) => !r.error).length
    const failed = results.filter((r) => r.error).length

    this.log(`  Total Files: ${results.length}`)
    this.log(`  Successful: ${successful}`)
    this.log(`  Failed: ${failed}`)
    this.log(`  Processing Time: ${(totalTime / 1000).toFixed(1)}s\n`)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    for (const line of batchResultLines(results)) this.log(line)

    if (failed > 0) {
      this.log('Failed Scans:\n')
      for (const result of results
        .filter((r) => r.error)) {
          this.log(`  ✗ ${result.file}: ${result.error}`)
        }

      this.log('')
    }
  }

  private displaySingleResult(result: any, scanTime: number): void {
    const summary = summarizeScanDetail(result)
    const { structure } = summary

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Word Document Accessibility Report`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  File: ${summary.fileName ?? 'Unknown'}`)
    this.log(`  Paragraphs: ${numberOrNull(structure.total_paragraphs) ?? 'N/A'}`)
    this.log(`  Tables: ${numberOrNull(structure.total_tables) ?? 'N/A'}`)
    this.log(`  Images: ${numberOrNull(structure.total_images) ?? 'N/A'}\n`)

    for (const line of scoreAndCountLines(summary)) this.log(line)

    this.log(`\n  Processing Time: ${(scanTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    if (summary.scanId) {
      this.log(`  Scan ID: ${summary.scanId}`)
    }

    this.log('💡 Tip: Use --format json for detailed issue breakdown')
  }

  private async findDocxFiles(dirPath: string): Promise<string[]> {
    const files: string[] = []
    const entries = await fs.readdir(dirPath, { withFileTypes: true })

    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name)

      if (entry.isDirectory()) {
        files.push(...(await this.findDocxFiles(fullPath)))
      } else if (entry.isFile() && entry.name.toLowerCase().endsWith('.docx')) {
        files.push(fullPath)
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
    s.start('Finding Word documents...')

    const files = await this.findDocxFiles(dirPath)

    if (files.length === 0) {
      s.stop('No Word documents found')
      outro('⚠️  No .docx files to scan')
      return
    }

    s.stop(`Found ${files.length} Word document${files.length > 1 ? 's' : ''}`)

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
      outro(`✨ Batch scan complete! Scanned ${files.length} documents`)
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
    s.start('Uploading Word document to Aelira API...')

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
          performance: { scan_time: scanDuration },
        }

        if (flags.output) {
          await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
          outro(`✅ Word document scan complete. Report saved to ${flags.output}`)
        } else {
          this.log(JSON.stringify(output, null, 2))
        }
      } else {
        this.displaySingleResult(result, scanDuration)
        outro('✨ Word document scan complete!')
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
    const response = await api.postForm('/education/word/scan', formData as any, {
      timeout: 60_000,
    })
    const uploadResult = await response.json()

    if (!uploadResult.scan_id) {
      throw new Error('Unexpected response: no scan_id returned')
    }

    // Poll for completion with progress updates
    return pollForCompletion(api, uploadResult.scan_id, s, { timeout: 120_000 })
  }
}
