import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'

import { ApiClient } from '../../utils/api-client.js'

export default class ReportCompliance extends Command {
  static args = {
    department_id: Args.string({
      description: 'Department ID for scan-evidence statistics',
      required: false,
    }),
  }
static description = 'Deprecated scan-evidence statistics view; use `report evidence` for PDFs'
static examples = [
    '<%= config.bin %> <%= command.id %> dept-123 --format json',
    '<%= config.bin %> <%= command.id %> dept-123 --pdf evidence-report.pdf',
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
      options: ['console', 'json'],
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path (for JSON format)',
    }),
    pdf: Flags.string({
      description: 'Download the accessibility evidence report PDF to this path',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance timing information',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(ReportCompliance)
    const startTime = Date.now()
    const api = new ApiClient({ apiUrl: flags['api-url'] })

    this.warn(
      '`aelira report compliance` is deprecated; use `aelira report evidence` for the bounded PDF report.',
    )
    intro('Aelira CLI - Scan Evidence Statistics')

    try {
      const s = spinner()
      s.start('Fetching scan evidence statistics...')

      const departmentId = args.department_id || 'default'

      // Fetch department statistics
      const stats = await api.getJson<any>(
        `/education/compliance/${departmentId}/stats`,
        { timeout: 30_000 },
      )

      // Fetch priority issues
      const issues = await api.getJson<any>(
        `/education/compliance/${departmentId}/issues`,
        { timeout: 30_000 },
      )

      s.stop('Data fetched successfully')

      const reportData = {
        department_id: departmentId,
        generated_at: new Date().toISOString(),
        report_kind: 'scan_evidence_statistics',
        scope_notice:
          "These statistics summarize Aelira's automated checks of scanned content. This scan evidence summary does not determine conformance with an accessibility standard or legal requirement.",
        findings: this.toFindings(issues),
        scan_statistics: this.toEvidenceStatistics(stats),
      }

      // Generate PDF if requested
      if (flags.pdf) {
        const pdfSpinner = spinner()
        pdfSpinner.start('Generating accessibility evidence report...')

        const response = await api.get(
          `/analytics/evidence-report/${departmentId}`,
          { headers: { Accept: 'application/pdf' }, timeout: 120_000 },
        )

        const pdfBuffer = Buffer.from(await response.arrayBuffer())
        await fs.writeFile(flags.pdf, pdfBuffer)
        pdfSpinner.stop(`Accessibility evidence report saved to ${flags.pdf}`)
      }

      // Output results
      if (flags.format === 'json') {
        const output = {
          ...reportData,
          performance: {
            execution_time: Date.now() - startTime,
          },
        }

        if (flags.output) {
          await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
          outro(`Scan evidence statistics saved to ${flags.output}`)
        } else {
          this.log(JSON.stringify(output, null, 2))
        }
      } else {
        this.displayReport(reportData)
        outro('Scan evidence statistics generated')
      }

      if (flags.timer) {
        this.log(`\n⏱️  Total execution time: ${Date.now() - startTime}ms`)
      }
    } catch (error: any) {
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private displayReport(report: any): void {
    const { department_id, findings, scan_statistics } = report

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Department Scan Evidence Statistics`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  Department ID: ${department_id}`)
    this.log(`  Generated: ${new Date(report.generated_at).toLocaleString()}\n`)

    this.log(`  ${report.scope_notice}\n`)

    this.renderStats(scan_statistics)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.renderPriorityIssues(findings)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log('  Use --pdf evidence-report.pdf to download the bounded evidence PDF')
    this.log('  Use --format json for scan evidence statistics and findings')
  }

  private renderFileTypes(fileTypes: any): void {
    if (!fileTypes) return

    this.log('  📁 File Type Breakdown:')
    for (const [type, count] of Object.entries(fileTypes)) {
      this.log(`  - ${type}: ${count}`)
    }

    this.log('')
  }

  private renderPriorityIssues(issues: any): void {
    if (!issues || issues.length === 0) {
      this.log('  No scan findings returned.\n')
      return
    }

    this.log('  Priority Scan Findings:\n')

    issues.slice(0, 10).forEach((issue: any, index: number) => {
      this.log(
        `  ${index + 1}. [${issue.severity?.toUpperCase() || 'UNKNOWN'}] ${issue.issue_type || issue.title || issue.description || 'Recorded finding'}`,
      )
      this.log(`     File: ${issue.file_name || issue.filename || 'Unknown'}`)
      if (issue.description) {
        this.log(`     ${issue.description}`)
      }

      if (issue.affected_count) {
        this.log(`     Affects ${issue.affected_count} file(s)`)
      }

      this.log('')
    })

    if (issues.length > 10) {
      this.log(`  ... and ${issues.length - 10} more issues\n`)
    }
  }

  private renderSeverityCounts(severity: any): void {
    if (!severity) return

    this.log('  Findings by Severity:')
    this.log(`  - Critical: ${severity.critical || 0}`)
    this.log(`  - High: ${severity.high || 0}`)
    this.log(`  - Medium: ${severity.medium || 0}`)
    this.log(`  - Low: ${severity.low || 0}`)

    const totalIssues =
      (severity.critical || 0) + (severity.high || 0) + (severity.medium || 0) + (severity.low || 0)
    this.log(`  - Total: ${totalIssues}`)
    this.log('')
  }

  private renderStats(stats: any): void {
    if (!stats) return

    this.log('  Scan Statistics:')
    this.log(`  - Total Files Scanned: ${stats.total_files_scanned || 0}`)
    this.log(
      `  - Average Scan Score: ${stats.average_scan_score === null ? 'N/A' : `${stats.average_scan_score.toFixed(1)}/100`}`,
    )
    this.log('')

    this.renderFileTypes(stats.file_types)
    this.renderSeverityCounts(stats.findings_by_severity)
    this.renderTrend(stats.trend)
  }

  private renderTrend(trend: any): void {
    if (!trend) return

    this.log('  📈 Trend Analysis:')
    this.log(
      `  - Last Week Scan Score: ${trend.last_week_scan_score === null ? 'N/A' : `${trend.last_week_scan_score.toFixed(1)}/100`}`,
    )
    this.log(
      `  - Current Scan Score: ${trend.current_scan_score === null ? 'N/A' : `${trend.current_scan_score.toFixed(1)}/100`}`,
    )

    if (trend.change_points !== null) {
      const arrow = trend.change_points >= 0 ? '↑' : '↓'
      this.log(`  - Change: ${arrow} ${Math.abs(trend.change_points).toFixed(1)} points`)
    }

    this.log('')
  }

  private toEvidenceStatistics(stats: any): any {
    const finiteOrNull = (value: unknown): number | null =>
      typeof value === 'number' && Number.isFinite(value) ? value : null

    return {
      average_scan_score: finiteOrNull(
        stats?.compliance_scores?.average ?? stats?.average_score,
      ),
      file_types: stats?.scan_types ?? stats?.file_types ?? {},
      findings_by_severity: stats?.issues ?? stats?.issues_by_severity ?? {},
      total_files_scanned:
        stats?.overview?.total_files_scanned ?? stats?.total_files ?? 0,
      trend: stats?.trend
        ? {
            change_points: finiteOrNull(stats.trend.improvement),
            current_scan_score: finiteOrNull(stats.trend.current_score),
            last_week_scan_score: finiteOrNull(stats.trend.last_week_score),
          }
        : null,
    }
  }

  private toFindings(payload: any): any[] {
    if (Array.isArray(payload)) return payload
    return Array.isArray(payload?.issues) ? payload.issues : []
  }
}
