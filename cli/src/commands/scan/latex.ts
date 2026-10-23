import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import FormData from 'form-data'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

import { ApiClient } from '../../utils/api-client.js'
import { formatIssuesToCsv } from '../../utils/csv-formatter.js'
import { pollForCompletion } from '../../utils/poll-progress.js'
import { numberOrNull, summarizeScanDetail } from '../../utils/scan-detail.js'

export default class ScanLatex extends Command {
  static args = {
    file: Args.string({
      description: 'LaTeX file or directory to convert to accessible MathML',
      required: true,
    }),
  }
static description = 'Convert LaTeX equations to MathML (supports ChemFig, mhchem, physics notation, TikZ). Model-drafted equation descriptions are kept as review drafts and never written as ARIA labels.'
static examples = [
    '<%= config.bin %> <%= command.id %> equations.tex',
    '<%= config.bin %> <%= command.id %> ./latex-files/',
    '<%= config.bin %> <%= command.id %> document.tex --format json',
    '<%= config.bin %> <%= command.id %> ./math/ --api-url http://localhost:8000',
  ]
static flags = {
    'api-url': Flags.string({
      description: 'Aelira API URL',
    }),
    'expand-macros': Flags.boolean({
      default: true,
      deprecated: {
        message: 'The LaTeX convert endpoint takes no macro option, so --expand-macros is not sent and has no effect.',
      },
      description: 'Not sent to the API; the LaTeX convert endpoint takes no macro option',
    }),
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console, csv or json)',
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
    type: Flags.string({
      char: 't',
      deprecated: {
        message: 'The LaTeX convert endpoint takes no content type hint, so --type is not sent and has no effect.',
      },
      description: 'Not sent to the API; the LaTeX convert endpoint takes no content type hint',
      options: ['math', 'chemistry', 'physics', 'diagram'],
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(ScanLatex)
    const startTime = Date.now()

    intro('Aelira CLI - LaTeX to MathML Converter')

    try {
      const targetPath = path.resolve(args.file)
      const stats = await fs.stat(targetPath)

      if (stats.isDirectory()) {
        // Batch process directory
        await this.processDirectory(targetPath, flags, startTime)
      } else {
        // Single file processing
        await this.processFile(targetPath, flags, startTime)
      }
    } catch (error: any) {
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private async convertLatex(
    filePath: string,
    apiUrl: string | undefined,
    s: ReturnType<typeof spinner>,
  ): Promise<any> {
    const api = new ApiClient({ apiUrl })
    // The endpoint takes the source as an uploaded `file` field, not JSON.
    const formData = new FormData()
    formData.append('file', await fs.readFile(filePath), path.basename(filePath))

    // Upload with shorter timeout (just file transfer)
    const response = await api.postForm('/education/latex/convert', formData as any, {
      timeout: 30_000,
    })
    const uploadResult = await response.json()

    if (!uploadResult.scan_id) {
      throw new Error('Unexpected response: no scan_id returned')
    }

    // Poll for completion with progress updates
    return pollForCompletion(api, uploadResult.scan_id, s, { timeout: 60_000 })
  }

  private displayBatchResults(results: any[], totalTime: number): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Batch LaTeX Conversion Results`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    const successful = results.filter((r) => !r.error).length
    const failed = results.filter((r) => r.error).length

    const conversions = results
      .filter((r) => !r.error)
      .map((r) => ({ file: r.file, stats: this.conversionStats(r) }))
    const totalEquations = conversions.reduce((sum, { stats }) => sum + (stats.converted ?? 0), 0)

    this.log(`  Total Files: ${results.length}`)
    this.log(`  Successful: ${successful}`)
    this.log(`  Failed: ${failed}`)
    this.log(`  Total Equations Converted: ${totalEquations}`)
    this.log(`  Processing Time: ${(totalTime / 1000).toFixed(1)}s\n`)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    // Show top conversions
    const successfulConversions = conversions.filter(({ stats }) => stats.converted)

    if (successfulConversions.length > 0) {
      this.log('Top Results:\n')
      for (const [index, { file, stats }] of successfulConversions.slice(0, 5).entries()) {
        this.log(`${index + 1}. ${file}`)
        this.log(`   Equations: ${stats.converted}`)
        this.log(`   Success Rate: ${stats.rate}`)
        this.log('')
      }
    }

    if (failed > 0) {
      this.log('Failed Conversions:\n')
      for (const result of results
        .filter((r) => r.error)) {
          this.log(`  ✗ ${result.file}: ${result.error}`)
        }

      this.log('')
    }

    this.log('💡 Tip: Use --format json --output report.json for full results')
  }

  private displaySingleResult(result: any, processTime: number): void {
    const summary = summarizeScanDetail(result)
    const stats = this.conversionStats(result)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  LaTeX to MathML Conversion Report`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  File: ${summary.fileName ?? 'Unknown'}`)
    this.log(`  Equations Found: ${stats.found ?? 'N/A'}`)
    this.log(`  Converted: ${stats.converted ?? 'N/A'}`)
    this.log(`  Failed: ${stats.failed ?? 'N/A'}`)
    this.log(`  Success Rate: ${stats.rate}`)

    this.log(`\n  Processing Time: ${(processTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    if (summary.scanId) {
      this.log(`  Scan ID: ${summary.scanId}`)
    }

    this.log('💡 Tip: Use --format json for complete MathML output')
  }

  private async findLatexFiles(dirPath: string): Promise<string[]> {
    const files: string[] = []

    const entries = await fs.readdir(dirPath, { withFileTypes: true })

    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name)

      if (entry.isDirectory()) {
        // Recursively scan subdirectories
        files.push(...(await this.findLatexFiles(fullPath)))
      } else if (
        entry.isFile() &&
        (entry.name.toLowerCase().endsWith('.tex') || entry.name.toLowerCase().endsWith('.latex'))
      ) {
        files.push(fullPath)
      }
    }

    return files
  }

  /** Conversion counts from scan_structure() in src/education/latex_evidence.py. */
  private conversionStats(result: any): { converted: null | number; failed: null | number; found: null | number; rate: string } {
    const { structure } = summarizeScanDetail(result)
    const rate = numberOrNull(structure.conversion_success_rate)
    return {
      converted: numberOrNull(structure.successful_conversions),
      failed: numberOrNull(structure.failed_conversions),
      found: numberOrNull(structure.total_equations),
      rate: rate === null ? 'N/A' : `${rate.toFixed(1)}%`,
    }
  }


  private async processDirectory(
    dirPath: string,
    flags: any,
    startTime: number
  ): Promise<void> {
    const s = spinner()
    s.start('Finding LaTeX files...')

    const files = await this.findLatexFiles(dirPath)

    if (files.length === 0) {
      s.stop('No LaTeX files found')
      outro('⚠️  No LaTeX files to process')
      return
    }

    s.stop(`Found ${files.length} LaTeX file${files.length > 1 ? 's' : ''}`)

    const results: any[] = []

    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      s.start(`Processing ${path.basename(file)} (${i + 1}/${files.length})...`)

      try {
        const result = await this.convertLatex(file, flags['api-url'], s)
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
        outro(`✅ Batch conversion complete. CSV saved to ${flags.output}`)
      } else {
        this.log(csv)
      }
    } else if (flags.format === 'json') {
      const output = {
        performance: {
          files_processed: files.length,
          total_time: Date.now() - startTime,
        },
        results,
      }

      if (flags.output) {
        await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
        outro(`✅ Batch conversion complete. Report saved to ${flags.output}`)
      } else {
        this.log(JSON.stringify(output, null, 2))
      }
    } else {
      this.displayBatchResults(results, Date.now() - startTime)
      outro(`✨ Batch conversion complete! Processed ${files.length} files`)
    }

    if (flags.timer) {
      this.log(`\n⏱️  Total execution time: ${Date.now() - startTime}ms`)
    }
  }

  private async processFile(
    filePath: string,
    flags: any,
    startTime: number
  ): Promise<void> {
    const s = spinner()
    s.start('Converting LaTeX to MathML...')

    try {
      const result = await this.convertLatex(filePath, flags['api-url'], s)
      const processDuration = Date.now() - startTime

      s.stop('Conversion complete')

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
            processing_time: processDuration,
          },
        }

        if (flags.output) {
          await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
          outro(`✅ LaTeX conversion complete. Report saved to ${flags.output}`)
        } else {
          this.log(JSON.stringify(output, null, 2))
        }
      } else {
        this.displaySingleResult(result, processDuration)
        outro('✨ LaTeX conversion complete!')
      }

      if (flags.timer) {
        this.log(`\n⏱️  Total execution time: ${processDuration}ms`)
      }
    } catch (error: any) {
      s.stop('Conversion failed')
      throw error
    }
  }
}
