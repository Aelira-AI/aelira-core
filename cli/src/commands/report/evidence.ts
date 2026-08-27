import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'

import { ApiClient } from '../../utils/api-client.js'

export default class ReportEvidence extends Command {
  static args = {
    department_id: Args.string({
      description: 'Department ID for the accessibility evidence report',
      required: false,
    }),
  }

  static description = 'Download an accessibility evidence report for scanned content'

  static examples = [
    '<%= config.bin %> <%= command.id %>',
    '<%= config.bin %> <%= command.id %> dept-123',
    '<%= config.bin %> <%= command.id %> dept-123 --output evidence-report.pdf',
  ]

  static flags = {
    'api-url': Flags.string({
      default: 'http://localhost:8000',
      description: 'Aelira API URL',
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path for the evidence report PDF',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance timing information',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(ReportEvidence)
    const startTime = Date.now()
    const departmentId = args.department_id || 'default'
    const api = new ApiClient({ apiUrl: flags['api-url'] })
    const reportSpinner = spinner()

    intro('Aelira CLI - Accessibility Evidence Report')
    reportSpinner.start('Generating accessibility evidence report...')

    try {
      const response = await api.get(`/analytics/evidence-report/${departmentId}`, {
        headers: { Accept: 'application/pdf' },
        timeout: 120_000,
      })
      const pdfBuffer = Buffer.from(await response.arrayBuffer())
      const filename =
        flags.output ||
        `accessibility_evidence_report_${departmentId}_${new Date().toISOString().slice(0, 10)}.pdf`

      await fs.writeFile(filename, pdfBuffer)
      reportSpinner.stop('Accessibility evidence report generated')

      this.log(`\n  Saved to: ${filename}`)
      this.log(
        "  This report records Aelira's scanned-content evidence and limitations; it does not determine conformance with an accessibility standard or legal requirement.",
      )

      if (flags.timer) {
        this.log(`\n  Total execution time: ${Date.now() - startTime}ms`)
      }

      outro('Evidence report ready for review')
    } catch (error: any) {
      reportSpinner.stop('Evidence report generation failed')
      outro(`Error: ${error.message}`)
      this.error(error)
    }
  }
}
