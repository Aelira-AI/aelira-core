import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'

import { ApiClient } from '../../utils/api-client.js'

export default class ReportAnalytics extends Command {
  static args = {
    department_id: Args.string({
      description: 'Department ID for analytics',
      required: false,
    }),
  }
static description = 'View historical compliance trends and deadline projections'
static examples = [
    '<%= config.bin %> <%= command.id %>',
    '<%= config.bin %> <%= command.id %> dept-123',
    '<%= config.bin %> <%= command.id %> --days 30',
    '<%= config.bin %> <%= command.id %> --projection',
  ]
static flags = {
    'api-url': Flags.string({
      default: 'http://localhost:8000',
      description: 'Aelira API URL',
    }),
    days: Flags.integer({
      char: 'd',
      default: 30,
      description: 'Number of days to look back (7-365)',
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
    projection: Flags.boolean({
      char: 'p',
      default: false,
      description: 'Show April 2027 ADA Title II deadline projection',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance timing information',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(ReportAnalytics)
    const startTime = Date.now()
    const departmentId = args.department_id || 'default'
    const api = new ApiClient({ apiUrl: flags['api-url'] })

    intro('Aelira CLI - Compliance Analytics & Trends')

    try {
      const s = spinner()

      // Fetch trend data
      s.start(`Fetching ${flags.days}-day historical trend...`)
      const trendData = await api.getJson<any>(
        `/analytics/trend/${departmentId}`,
        { query: { days: String(flags.days) }, timeout: 30_000 },
      )
      s.stop('Trend data fetched')

      // Fetch trend analysis
      s.start('Analyzing week-over-week changes...')
      const analysisData = await api.getJson<any>(
        `/analytics/trend/${departmentId}/analysis`,
        { timeout: 30_000 },
      )
      s.stop('Analysis complete')

      // Fetch projection if requested
      let projectionData = null
      if (flags.projection) {
        s.start('Calculating April 2027 ADA Title II deadline projection...')
        projectionData = await api.getJson<any>(
          `/analytics/projection/${departmentId}`,
          { timeout: 30_000 },
        )
        s.stop('Projection calculated')
      }

      const scanDuration = Date.now() - startTime

      const result = {
        analysis: analysisData,
        department_id: departmentId,
        generated_at: new Date().toISOString(),
        projection: projectionData,
        trend: trendData,
      }

      if (flags.format === 'json') {
        const output = { ...result, performance: { execution_time: scanDuration } }
        if (flags.output) {
          await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
          outro(`✅ Analytics saved to ${flags.output}`)
        } else {
          this.log(JSON.stringify(output, null, 2))
        }
      } else {
        this.displayResults(result, scanDuration)
        outro('✨ Analytics report complete!')
      }

      if (flags.timer) {
        this.log(`\n⏱️  Total execution time: ${scanDuration}ms`)
      }
    } catch (error: any) {
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private displayResults(result: any, processTime: number): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Compliance Analytics Dashboard`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  Department ID: ${result.department_id}`)
    this.log(`  Period: ${result.trend?.period_days || 30} days\n`)

    this.renderHistoricalTrend(result.trend?.trend || [])
    this.renderWeekOverWeek(result.analysis?.analysis)
    this.renderProjection(result.projection?.projection)

    this.log(`  Processing Time: ${(processTime / 1000).toFixed(1)}s`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log('💡 Tip: Use --projection for April 2027 ADA Title II deadline analysis')
    this.log('💡 Tip: Use --days 90 for longer historical trend')
  }

  private renderHistoricalTrend(trend: any[]): void {
    if (trend.length === 0) return

    this.log('  📈 Historical Trend:')
    this.log(`  - Data Points: ${trend.length}`)

    const firstScore = trend[0]?.avg_compliance_score || 0
    const lastScore = trend.at(-1)?.avg_compliance_score || 0
    const change = lastScore - firstScore
    const arrow = change >= 0 ? '↑' : '↓'

    this.log(`  - First Score: ${firstScore.toFixed(1)}/100`)
    this.log(`  - Current Score: ${lastScore.toFixed(1)}/100`)
    this.log(`  - Change: ${arrow} ${Math.abs(change).toFixed(1)} points\n`)
  }

  private renderProjection(proj: any): void {
    if (!proj) return

    this.log('  🎯 ADA Title II Deadline Projection (April 26, 2027):')
    this.log(`  - Days Until Deadline: ${proj.days_until_deadline || 'N/A'}`)
    this.log(`  - Current Score: ${proj.current_score?.toFixed(1) || 'N/A'}/100`)
    this.log(`  - Target Score: ${proj.target_score || 90}/100`)
    this.log(`  - Projected Score: ${proj.projected_score?.toFixed(1) || 'N/A'}/100`)
    this.log(`  - On Track: ${proj.on_track ? '✅ Yes' : '❌ No'}`)

    if (!proj.on_track && proj.improvement_needed) {
      this.log(`  - Points Needed: ${proj.improvement_needed.toFixed(1)}`)
      this.log(`  - Weekly Improvement Required: ${proj.weekly_improvement_needed?.toFixed(2) || 'N/A'} pts/week`)
    }

    this.log('')
  }

  private renderWeekOverWeek(analysis: any): void {
    if (!analysis) return

    this.log('  📊 Week-over-Week Analysis:')
    this.log(`  - Current Avg Score: ${analysis.current_avg_score?.toFixed(1) || 'N/A'}/100`)
    this.log(`  - Previous Avg Score: ${analysis.previous_avg_score?.toFixed(1) || 'N/A'}/100`)

    const scoreChange = analysis.score_change || 0
    const scoreArrow = scoreChange >= 0 ? '↑' : '↓'
    this.log(`  - Score Change: ${scoreArrow} ${Math.abs(scoreChange).toFixed(1)} (${analysis.score_change_pct?.toFixed(1) || 0}%)`)

    const issueChange = analysis.issues_change || 0
    const issueArrow = issueChange <= 0 ? '↓' : '↑'
    this.log(`  - Issues Change: ${issueArrow} ${Math.abs(issueChange)}`)

    this.log(`  - Trend Direction: ${this.getTrendEmoji(analysis.trend_direction)} ${analysis.trend_direction || 'stable'}`)
    this.log('')
  }

  private getTrendEmoji(direction: string): string {
    switch (direction?.toLowerCase()) {
      case 'declining': {
        return '📉'
      }

      case 'improving': {
        return '📈'
      }

      case 'stable': {
        return '➡️'
      }

      default: {
        return '📊'
      }
    }
  }
}
