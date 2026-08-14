import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'

import { ApiClient } from '../utils/api-client.js'

export default class Remediate extends Command {
  static args = {
    scan_id: Args.string({
      description: 'Scan ID to remediate (from previous scan)',
      required: true,
    }),
  }
static description = 'Auto-remediate accessibility issues from a previous scan'
static examples = [
    '<%= config.bin %> <%= command.id %> abc123',
    '<%= config.bin %> <%= command.id %> abc123 --download',
    '<%= config.bin %> <%= command.id %> abc123 --format json',
  ]
static flags = {
    'api-url': Flags.string({
      default: 'http://localhost:8000',
      description: 'Aelira API URL',
    }),
    download: Flags.boolean({
      char: 'd',
      default: false,
      description: 'Download the remediated file',
    }),
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console or json)',
      options: ['console', 'json'],
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path for remediated file',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance timing information',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(Remediate)
    const startTime = Date.now()

    intro('Aelira CLI - Auto-Remediation Engine')

    try {
      const api = new ApiClient({ apiUrl: flags['api-url'] })
      const s = spinner()
      s.start(`Remediating scan ${args.scan_id}...`)

      // Trigger remediation
      const response = await api.post(
        `/education/remediate/${args.scan_id}`,
        {},
        { timeout: 180_000 }, // 3 minute timeout for remediation
      )

      const result = await response.json()
      s.stop('Remediation complete')

      // Download remediated file if requested
      if (flags.download || flags.output) {
        const downloadSpinner = spinner()
        downloadSpinner.start('Downloading remediated file...')

        try {
          const downloadResponse = await api.get(
            `/education/scans/${args.scan_id}/remediated`,
            { timeout: 60_000 },
          )

          const fileBuffer = Buffer.from(await downloadResponse.arrayBuffer())
          const filename = flags.output || `remediated_${args.scan_id}.${result.file_type || 'pdf'}`
          await fs.writeFile(filename, fileBuffer)
          downloadSpinner.stop(`Downloaded: ${filename}`)
        } catch {
          downloadSpinner.stop('Download failed - file may not be available yet')
        }
      }

      const scanDuration = Date.now() - startTime

      if (flags.format === 'json') {
        const output = {
          ...result,
          performance: { remediation_time: scanDuration },
        }
        this.log(JSON.stringify(output, null, 2))
      } else {
        this.displayResult(result, scanDuration)
        outro('✨ Remediation complete!')
      }

      if (flags.timer) {
        this.log(`\n⏱️  Total execution time: ${scanDuration}ms`)
      }
    } catch (error: any) {
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private displayResult(result: any, processTime: number): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Auto-Remediation Report`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  Scan ID: ${result.scan_id || 'Unknown'}`)
    this.log(`  File: ${result.filename || 'Unknown'}`)
    this.log(`  File Type: ${result.file_type || 'Unknown'}\n`)

    this.log('  📊 Remediation Results:')
    this.log(`  - Issues Fixed: ${result.issues_fixed || 0}`)
    this.log(`  - Issues Remaining: ${result.issues_remaining || 0}`)
    this.log(`  - Auto-Fix Rate: ${result.fix_rate ? result.fix_rate.toFixed(1) + '%' : 'N/A'}\n`)

    if (result.fixes_applied && result.fixes_applied.length > 0) {
      this.log('  ✅ Fixes Applied:')
      result.fixes_applied.slice(0, 5).forEach((fix: any, idx: number) => {
        this.log(`    ${idx + 1}. ${fix.issue_type}: ${fix.description || 'Fixed'}`)
      })
      if (result.fixes_applied.length > 5) {
        this.log(`    ... and ${result.fixes_applied.length - 5} more`)
      }

      this.log('')
    }

    if (result.original_score !== undefined && result.new_score !== undefined) {
      const improvement = result.new_score - result.original_score
      this.log('  📈 Score Improvement:')
      this.log(`  - Before: ${result.original_score}/100`)
      this.log(`  - After: ${result.new_score}/100`)
      this.log(`  - Improvement: +${improvement.toFixed(1)} points\n`)
    }

    this.log(`  Processing Time: ${(processTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log('💡 Tip: Use --download to get the remediated file')
    this.log('💡 Tip: Use --format json for complete remediation details')
  }

}
