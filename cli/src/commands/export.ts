import { intro, outro, spinner } from '@clack/prompts'
import { Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'

import { ApiClient } from '../utils/api-client.js'
import { formatScanHistoryToCsv } from '../utils/csv-formatter.js'

export default class Export extends Command {
  static description = 'Export scan history to CSV or JSON'
  static examples = [
    '<%= config.bin %> <%= command.id %> --output scans.csv',
    '<%= config.bin %> <%= command.id %> --format json',
    '<%= config.bin %> <%= command.id %> --limit 100 --output full-report.csv',
  ]
  static flags = {
    'api-url': Flags.string({
      description: 'Aelira API URL',
    }),
    format: Flags.string({
      char: 'f',
      default: 'csv',
      description: 'Output format',
      options: ['csv', 'json'],
    }),
    limit: Flags.integer({
      default: 50,
      description: 'Maximum number of scans to export',
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path (required for CSV)',
    }),
  }

  async run(): Promise<void> {
    const { flags } = await this.parse(Export)

    // CSV requires --output
    if (flags.format === 'csv' && !flags.output) {
      this.error('CSV export requires --output flag. Use --format json for stdout.')
    }

    intro('Aelira CLI - Export Scan History')

    const api = new ApiClient({ apiUrl: flags['api-url'] })
    const s = spinner()

    try {
      // Fetch scan list
      s.start('Fetching scan history...')
      const listResponse = await api.get('/education/scans', {
        query: { limit: String(flags.limit) },
        timeout: 30_000,
      })
      const scans = await listResponse.json()

      if (!scans || (Array.isArray(scans) && scans.length === 0)) {
        s.stop('No scans found')
        outro('No scans found. Ensure your API key is configured with `aelira config show`.')
        return
      }

      const scanList = Array.isArray(scans) ? scans : scans.scans || []
      const scansWithIssues = scanList.filter((scan: any) => (scan.total_issues || 0) > 0)

      // Fetch details for each scan with issues
      s.message(`Fetching details for ${scansWithIssues.length} scans...`)
      const detailedScans: any[] = []

      for (const [i, scan] of scansWithIssues.entries()) {
        try {
          const scanId = scan.scan_id || scan.id
          const detail = await api.get(`/education/scans/${scanId}`, { timeout: 30_000 })
          const detailData = await detail.json()
          detailedScans.push({
            ...scan,
            issues: detailData.issues || detailData.result?.issues || [],
          })
        } catch {
          // Skip scans that fail to fetch — continue with remaining
        }

        s.message(`Fetching details... ${i + 1}/${scansWithIssues.length}`)
      }

      s.stop(`Fetched ${detailedScans.length} scans`)

      // Format output
      if (flags.format === 'csv') {
        const csv = formatScanHistoryToCsv(detailedScans)
        await fs.writeFile(flags.output!, csv)
        outro(`CSV exported to ${flags.output} (${detailedScans.length} scans)`)
      } else {
        const json = JSON.stringify(detailedScans, null, 2)
        if (flags.output) {
          await fs.writeFile(flags.output, json)
          outro(`JSON exported to ${flags.output} (${detailedScans.length} scans)`)
        } else {
          this.log(json)
        }
      }
    } catch (error: any) {
      s.stop('Export failed')
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }
}
