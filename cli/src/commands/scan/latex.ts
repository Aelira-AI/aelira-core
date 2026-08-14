import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'

import { ApiClient } from '../../utils/api-client.js'
import { formatIssuesToCsv } from '../../utils/csv-formatter.js'
import { pollForCompletion } from '../../utils/poll-progress.js'

export default class ScanLatex extends Command {
  static args = {
    file: Args.string({
      description: 'LaTeX file or directory to convert to accessible MathML',
      required: true,
    }),
  }
static description = 'Convert LaTeX equations to accessible MathML with ARIA labels (supports ChemFig, mhchem, physics notation, TikZ)'
static examples = [
    '<%= config.bin %> <%= command.id %> equations.tex',
    '<%= config.bin %> <%= command.id %> ./latex-files/',
    '<%= config.bin %> <%= command.id %> document.tex --format json',
    '<%= config.bin %> <%= command.id %> chemistry.tex --type chemistry',
    '<%= config.bin %> <%= command.id %> quantum.tex --type physics',
    '<%= config.bin %> <%= command.id %> ./math/ --api-url http://localhost:8000',
  ]
static flags = {
    'api-url': Flags.string({
      default: 'http://localhost:8000',
      description: 'Aelira API URL',
    }),
    'expand-macros': Flags.boolean({
      default: true,
      description: 'Expand custom macros before conversion',
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
    type: Flags.string({
      char: 't',
      description: 'LaTeX content type hint (math, chemistry, physics, diagram)',
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
    apiUrl: string,
    flags: any,
    s: ReturnType<typeof spinner>,
  ): Promise<any> {
    const api = new ApiClient({ apiUrl })
    const latexContent = await fs.readFile(filePath, 'utf8')

    // Upload with shorter timeout (just request transfer)
    const response = await api.post('/education/latex/convert', {
      content_type_hint: flags.type || null,
      expand_macros: flags['expand-macros'] !== false,
      filename: path.basename(filePath),
      latex_content: latexContent,
    }, {
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

    const totalEquations = results
      .filter((r) => !r.error)
      .reduce((sum, r) => sum + (r.equations_converted || 0), 0)

    this.log(`  Total Files: ${results.length}`)
    this.log(`  Successful: ${successful}`)
    this.log(`  Failed: ${failed}`)
    this.log(`  Total Equations Converted: ${totalEquations}`)
    this.log(`  Processing Time: ${(totalTime / 1000).toFixed(1)}s\n`)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    // Show top conversions
    const successfulConversions = results.filter((r) => !r.error && r.equations_converted)

    if (successfulConversions.length > 0) {
      this.log('Top Results:\n')
      for (const [index, result] of successfulConversions.slice(0, 5).entries()) {
        this.log(`${index + 1}. ${result.file}`)
        this.log(`   Equations: ${result.equations_converted || 0}`)
        this.log(`   Success Rate: ${result.success_rate ? result.success_rate.toFixed(1) + '%' : 'N/A'}`)
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
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  LaTeX to MathML Conversion Report`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  File: ${result.filename || 'Unknown'}`)
    this.log(`  Equations Found: ${result.equations_converted || 0}`)
    this.log(`  Success Rate: ${result.success_rate ? result.success_rate.toFixed(1) + '%' : 'N/A'}\n`)

    // Show content types detected
    if (result.content_types_detected && result.content_types_detected.length > 0) {
      this.log('  📊 Content Types Detected:')
      result.content_types_detected.forEach((type: string) => {
        const emoji = this.getContentTypeEmoji(type)
        this.log(`    ${emoji} ${type}`)
      })
      this.log('')
    }

    // Show specialized content counts
    if (result.chemistry_formulas > 0 || result.physics_notation > 0 || result.tikz_diagrams > 0) {
      this.log('  🔬 Specialized Content:')
      if (result.chemistry_formulas > 0) this.log(`    ⚗️  Chemistry formulas: ${result.chemistry_formulas}`)
      if (result.physics_notation > 0) this.log(`    ⚛️  Physics notation: ${result.physics_notation}`)
      if (result.tikz_diagrams > 0) this.log(`    📐 TikZ diagrams: ${result.tikz_diagrams}`)
      this.log('')
    }

    if (result.mathml_content) {
      this.log(`  ✓ MathML generated successfully`)
    }

    if (result.aria_labels_generated > 0) {
      this.log(`  ✓ ${result.aria_labels_generated} ARIA labels generated`)
    }

    if (result.errors && result.errors.length > 0) {
      this.log(`\n  ⚠️  Errors: ${result.errors.length}`)
      result.errors.slice(0, 3).forEach((error: any, idx: number) => {
        this.log(`    ${idx + 1}. ${error}`)
      })
      if (result.errors.length > 3) {
        this.log(`    ... and ${result.errors.length - 3} more`)
      }
    }

    this.log(`\n  Processing Time: ${(processTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    if (result.scan_id) {
      this.log(`  Scan ID: ${result.scan_id}`)
    }

    this.log('💡 Tip: Use --type chemistry for ChemFig/mhchem content')
    this.log('💡 Tip: Use --type physics for bra-ket and vector notation')
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

  private getContentTypeEmoji(type: string): string {
    switch (type?.toLowerCase()) {
      case 'chemistry': { return '⚗️'
      }

      case 'diagram': { return '📐'
      }

      case 'math': { return '📐'
      }

      case 'physics': { return '⚛️'
      }

      case 'table': { return '📊'
      }

      default: { return '📄'
      }
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
        const result = await this.convertLatex(file, flags['api-url'], flags, s)
        results.push({ file: path.basename(file), ...result })
        s.stop(`✓ ${path.basename(file)}`)
      } catch (error: any) {
        s.stop(`✗ ${path.basename(file)}: ${error.message}`)
        results.push({ error: error.message, file: path.basename(file) })
      }
    }

    // Output results
    if (flags.format === 'csv') {
      const allIssues = results.flatMap((r) => r.issues_list || r.issues || r.errors || [])
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
      const result = await this.convertLatex(filePath, flags['api-url'], flags, s)
      const processDuration = Date.now() - startTime

      s.stop('Conversion complete')

      if (flags.format === 'csv') {
        const csv = formatIssuesToCsv(result.issues_list || result.issues || result.errors || [], path.basename(filePath))
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
