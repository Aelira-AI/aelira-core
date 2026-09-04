import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import FormData from 'form-data'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

import { ApiClient } from '../../utils/api-client.js'
import { formatIssuesToCsv } from '../../utils/csv-formatter.js'
import { pollForCompletion } from '../../utils/poll-progress.js'

export default class ScanPpt extends Command {
  static args = {
    file: Args.string({
      description: 'PowerPoint file or directory to scan',
      required: true,
    }),
  }
static description =
    'Scan PowerPoint presentations for accessibility issues (alt text, contrast, structure)'
static examples = [
    '<%= config.bin %> <%= command.id %> lecture.pptx',
    '<%= config.bin %> <%= command.id %> ./presentations/',
    '<%= config.bin %> <%= command.id %> lecture.pptx --format json',
    '<%= config.bin %> <%= command.id %> ./slides/ --api-url http://localhost:8000',
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
    const { args, flags } = await this.parse(ScanPpt)
    const startTime = Date.now()

    intro('Aelira CLI - PowerPoint Accessibility Scanner')

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
    this.log(`  Batch PowerPoint Scan Results`)
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
        this.log(`   Slides: ${result.total_slides || 'N/A'}`)
        if (result.issues) {
          const totalIssues =
            (result.issues.missing_alt_text || 0) +
            (result.issues.contrast_violations || 0) +
            (result.issues.structure || 0) +
            (result.issues.other || 0)
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
    this.log(`  PowerPoint Accessibility Report`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  File: ${result.filename || 'Unknown'}`)
    this.log(`  Slides: ${result.total_slides || 'N/A'}`)
    this.log(`  Size: ${result.file_size_mb ? result.file_size_mb.toFixed(2) + ' MB' : 'N/A'}\n`)

    if (result.compliance_score !== undefined) {
      this.log(`  Compliance Score: ${result.compliance_score}/100`)
    }

    if (result.issues) {
      this.log(`\n  Issues Found:`)
      this.log(`  - Missing Alt Text: ${result.issues.missing_alt_text || 0}`)
      this.log(`  - Contrast Violations: ${result.issues.contrast_violations || 0}`)
      this.log(`  - Structure Issues: ${result.issues.structure || 0}`)
      this.log(`  - Other: ${result.issues.other || 0}`)

      const totalIssues =
        (result.issues.missing_alt_text || 0) +
        (result.issues.contrast_violations || 0) +
        (result.issues.structure || 0) +
        (result.issues.other || 0)
      this.log(`\n  Total Issues: ${totalIssues}`)
    }

    this.log(`\n  Processing Time: ${(scanTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    if (result.scan_id) {
      this.log(`  Scan ID: ${result.scan_id}`)
      this.log(`  View full report: ${result.report_url || 'N/A'}\n`)
    }

    this.log('💡 Tip: Use --format json for detailed issue breakdown')
    this.log('💡 Tip: Use directory path to batch scan multiple presentations')
  }

  private async findPptFiles(dirPath: string): Promise<string[]> {
    const files: string[] = []

    const entries = await fs.readdir(dirPath, { withFileTypes: true })

    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name)

      if (entry.isDirectory()) {
        // Recursively scan subdirectories
        files.push(...(await this.findPptFiles(fullPath)))
      } else if (
        entry.isFile() &&
        (entry.name.toLowerCase().endsWith('.pptx') || entry.name.toLowerCase().endsWith('.ppt'))
      ) {
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
    s.start('Finding PowerPoint files...')

    const files = await this.findPptFiles(dirPath)

    if (files.length === 0) {
      s.stop('No PowerPoint files found')
      outro('⚠️  No PowerPoint files to scan')
      return
    }

    s.stop(`Found ${files.length} PowerPoint file${files.length > 1 ? 's' : ''}`)

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
      outro(`✨ Batch scan complete! Scanned ${files.length} presentations`)
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
    s.start('Uploading PowerPoint to Aelira API...')

    try {
      const result = await this.uploadAndScan(filePath, flags['api-url'], s)
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
          outro(`✅ PowerPoint scan complete. Report saved to ${flags.output}`)
        } else {
          this.log(JSON.stringify(output, null, 2))
        }
      } else {
        this.displaySingleResult(result, scanDuration)
        outro('✨ PowerPoint scan complete!')
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
    const response = await api.postForm('/education/powerpoint/scan', formData as any, {
      timeout: 60_000,
    })
    const uploadResult = await response.json()

    if (!uploadResult.scan_id) {
      throw new Error('Unexpected response: no scan_id returned')
    }

    // Poll for completion with progress updates
    return pollForCompletion(api, uploadResult.scan_id, s, { timeout: 90_000 })
  }
}
