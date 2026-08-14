import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import FormData from 'form-data'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

import { ApiClient } from '../../utils/api-client.js'
import { formatIssuesToCsv } from '../../utils/csv-formatter.js'
import { pollForCompletion } from '../../utils/poll-progress.js'

export default class ScanPdf extends Command {
  static args = {
    file: Args.string({
      description: 'PDF file or directory to scan',
      required: true,
    }),
  }
static description = 'Scan PDF files for accessibility issues with OCR and remediation'
static examples = [
    '<%= config.bin %> <%= command.id %> document.pdf',
    '<%= config.bin %> <%= command.id %> ./course-materials/',
    '<%= config.bin %> <%= command.id %> document.pdf --format json',
    '<%= config.bin %> <%= command.id %> ./pdfs/ --api-url http://localhost:8000',
  ]
static flags = {
    'api-url': Flags.string({
      default: 'http://localhost:8000',
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
    'skip-ocr': Flags.boolean({
      default: false,
      description: 'Skip OCR processing for scanned PDFs',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance timing information',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(ScanPdf)
    const startTime = Date.now()

    intro('Aelira CLI - PDF Accessibility Scanner')

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
    this.log(`  Batch PDF Scan Results`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    const successful = results.filter((r) => !r.error).length
    const failed = results.filter((r) => r.error).length

    this.log(`  Total Files: ${results.length}`)
    this.log(`  Successful: ${successful}`)
    this.log(`  Failed: ${failed}`)
    this.log(`  Processing Time: ${(totalTime / 1000).toFixed(1)}s\n`)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    // Show top issues from successful scans
    const successfulScans = results.filter((r) => !r.error && r.compliance_score !== undefined)

    if (successfulScans.length > 0) {
      this.log('Top Results:\n')
      for (const [index, result] of successfulScans.slice(0, 5).entries()) {
        this.log(`${index + 1}. ${result.file}`)
        this.log(`   Score: ${result.compliance_score}/100`)
        if (result.issues) {
          const totalIssues =
            (result.issues.critical || 0) +
            (result.issues.high || 0) +
            (result.issues.medium || 0) +
            (result.issues.low || 0)
          this.log(`   Issues: ${totalIssues}`)
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
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  PDF Accessibility Report`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  File: ${result.filename || 'Unknown'}`)
    this.log(`  Pages: ${result.total_pages || 'N/A'}`)
    this.log(`  Size: ${result.file_size_mb ? result.file_size_mb.toFixed(2) + ' MB' : 'N/A'}`)
    this.log(`  OCR: ${result.ocr_performed ? 'Yes' : 'No'}\n`)

    if (result.compliance_score !== undefined) {
      this.log(`  Compliance Score: ${result.compliance_score}/100`)
    }

    if (result.issues) {
      this.log(`\n  Issues Found:`)
      this.log(`  - Critical: ${result.issues.critical || 0}`)
      this.log(`  - High: ${result.issues.high || 0}`)
      this.log(`  - Medium: ${result.issues.medium || 0}`)
      this.log(`  - Low: ${result.issues.low || 0}`)
    }

    this.log(`\n  Processing Time: ${(scanTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    if (result.scan_id) {
      this.log(`  Scan ID: ${result.scan_id}`)
      this.log(`  View full report: ${result.report_url || 'N/A'}\n`)
    }

    this.log('💡 Tip: Use --format json for detailed issue breakdown')
    this.log('💡 Tip: Use directory path to batch scan multiple PDFs')
  }

  private async findPdfFiles(dirPath: string): Promise<string[]> {
    const files: string[] = []

    const entries = await fs.readdir(dirPath, { withFileTypes: true })

    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name)

      if (entry.isDirectory()) {
        // Recursively scan subdirectories
        files.push(...(await this.findPdfFiles(fullPath)))
      } else if (entry.isFile() && entry.name.toLowerCase().endsWith('.pdf')) {
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
    s.start('Finding PDF files...')

    const files = await this.findPdfFiles(dirPath)

    if (files.length === 0) {
      s.stop('No PDF files found')
      outro('⚠️  No PDF files to scan')
      return
    }

    s.stop(`Found ${files.length} PDF file${files.length > 1 ? 's' : ''}`)

    const results: any[] = []

    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      s.start(`Scanning ${path.basename(file)} (${i + 1}/${files.length})...`)

      try {
        const result = await this.uploadAndScan(file, flags['api-url'], flags['skip-ocr'], s)
        results.push({ file: path.basename(file), ...result })
        s.stop(`✓ ${path.basename(file)}`)
      } catch (error: any) {
        s.stop(`✗ ${path.basename(file)}: ${error.message}`)
        results.push({ error: error.message, file: path.basename(file) })
      }
    }

    // Output results
    if (flags.format === 'csv') {
      const allIssues = results.flatMap((r) => r.issues_list || r.issues || [])
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
      outro(`✨ Batch scan complete! Scanned ${files.length} PDFs`)
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
    s.start('Uploading PDF to Aelira API...')

    try {
      const result = await this.uploadAndScan(filePath, flags['api-url'], flags['skip-ocr'], s)
      const scanDuration = Date.now() - startTime

      s.stop('Scan complete')

      if (flags.format === 'csv') {
        const csv = formatIssuesToCsv(result.issues_list || result.issues || [], path.basename(filePath))
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
          outro(`✅ PDF scan complete. Report saved to ${flags.output}`)
        } else {
          this.log(JSON.stringify(output, null, 2))
        }
      } else {
        this.displaySingleResult(result, scanDuration)
        outro('✨ PDF scan complete!')
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
    apiUrl: string,
    skipOcr: boolean,
    s: ReturnType<typeof spinner>,
  ): Promise<any> {
    const api = new ApiClient({ apiUrl })
    const formData = new FormData()
    formData.append('file', await fs.readFile(filePath), path.basename(filePath))
    formData.append('skip_ocr', skipOcr.toString())

    // Upload with shorter timeout (just file transfer)
    const response = await api.postForm('/education/pdf/scan', formData as any, {
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
