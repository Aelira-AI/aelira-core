import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'

import { ApiClient } from '../../utils/api-client.js'

export default class ReportCompliance extends Command {
  static args = {
    department_id: Args.string({
      description: 'Department ID for compliance reporting',
      required: false,
    }),
  }
static description = 'Generate department-wide compliance reports with priority issue ranking'
static examples = [
    '<%= config.bin %> <%= command.id %>',
    '<%= config.bin %> <%= command.id %> dept-123',
    '<%= config.bin %> <%= command.id %> dept-123 --format json',
    '<%= config.bin %> <%= command.id %> dept-123 --pdf report.pdf',
    '<%= config.bin %> <%= command.id %> --api-url http://localhost:8000',
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
      description: 'Generate PDF report and save to specified path',
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

    intro('Aelira CLI - Compliance Report Generator')

    try {
      const s = spinner()
      s.start('Fetching compliance data...')

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
        issues,
        stats,
      }

      // Generate PDF if requested
      if (flags.pdf) {
        const pdfSpinner = spinner()
        pdfSpinner.start('Generating PDF report...')

        const response = await api.get(
          `/education/compliance/${departmentId}/report/pdf`,
          { headers: { Accept: 'application/pdf' }, timeout: 60_000 },
        )

        const pdfBuffer = Buffer.from(await response.arrayBuffer())
        await fs.writeFile(flags.pdf, pdfBuffer)
        pdfSpinner.stop(`PDF report saved to ${flags.pdf}`)
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
          outro(`✅ Compliance report saved to ${flags.output}`)
        } else {
          this.log(JSON.stringify(output, null, 2))
        }
      } else {
        this.displayReport(reportData)
        outro('✨ Compliance report generated!')
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
    const { department_id, issues, stats } = report

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Department Compliance Report`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  Department ID: ${department_id}`)
    this.log(`  Generated: ${new Date(report.generated_at).toLocaleString()}\n`)

    this.renderStats(stats)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.renderPriorityIssues(issues)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.renderRecommendations(stats)

    this.log('')
    this.log('  📄 Use --pdf report.pdf to generate a downloadable PDF report')
    this.log('  📊 Use --format json for detailed data export')
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
      this.log('  ✅ No critical issues found!\n')
      return
    }

    this.log('  🔥 Top Priority Issues:\n')

    issues.slice(0, 10).forEach((issue: any, index: number) => {
      this.log(`  ${index + 1}. [${issue.severity?.toUpperCase() || 'UNKNOWN'}] ${issue.title}`)
      this.log(`     File: ${issue.filename || 'Unknown'}`)
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

  private renderRecommendations(stats: any): void {
    this.log('  💡 Recommendations:\n')

    if (stats && stats.average_score < 70) {
      this.log('  • Focus on Critical and High severity issues first')
      this.log('  • Consider bulk remediation for PDFs and PowerPoints')
      this.log('  • Review LaTeX equations for MathML conversion')
    } else if (stats && stats.average_score < 85) {
      this.log('  • Address remaining High severity issues')
      this.log('  • Review Medium severity issues for quick wins')
      this.log('  • Ensure all images have alt text')
    } else {
      this.log('  • Maintain current compliance standards')
      this.log('  • Address Low severity issues when time permits')
      this.log('  • Consider automated monitoring for new content')
    }
  }

  private renderSeverityCounts(severity: any): void {
    if (!severity) return

    this.log('  ⚠️  Issues by Severity:')
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

    this.log('  📊 Overall Statistics:')
    this.log(`  - Total Files Scanned: ${stats.total_files || 0}`)
    this.log(`  - Average Compliance Score: ${stats.average_score ? stats.average_score.toFixed(1) : 'N/A'}/100`)
    this.log(
      `  - Files Meeting Threshold: ${stats.compliant_files || 0}/${stats.total_files || 0} (${stats.compliance_percentage ? stats.compliance_percentage.toFixed(1) : 0}%)`
    )
    this.log('')

    this.renderFileTypes(stats.file_types)
    this.renderSeverityCounts(stats.issues_by_severity)
    this.renderTrend(stats.trend)
  }

  private renderTrend(trend: any): void {
    if (!trend) return

    this.log('  📈 Trend Analysis:')
    this.log(`  - Last Week Score: ${trend.last_week_score ? trend.last_week_score.toFixed(1) : 'N/A'}/100`)
    this.log(`  - Current Score: ${trend.current_score ? trend.current_score.toFixed(1) : 'N/A'}/100`)

    if (trend.improvement !== undefined) {
      const arrow = trend.improvement >= 0 ? '↑' : '↓'
      this.log(`  - Change: ${arrow} ${Math.abs(trend.improvement).toFixed(1)} points`)
    }

    this.log('')
  }
}
