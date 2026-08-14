import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'

import { ApiClient } from '../utils/api-client.js'

export default class Issues extends Command {
  static args = {
    action: Args.string({
      default: 'list',
      description: 'Action to perform (list, stats, update, assign, note)',
      options: ['list', 'stats', 'update', 'assign', 'note'],
      required: false,
    }),
    issue_id: Args.string({
      description: 'Issue ID (required for update, assign, note actions)',
      required: false,
    }),
  }
static description = 'Manage and track accessibility issues for team collaboration'
static examples = [
    '<%= config.bin %> <%= command.id %> list',
    '<%= config.bin %> <%= command.id %> list --status open',
    '<%= config.bin %> <%= command.id %> stats',
    '<%= config.bin %> <%= command.id %> update abc123 --status resolved',
    '<%= config.bin %> <%= command.id %> assign abc123 --to user@example.com',
    '<%= config.bin %> <%= command.id %> note abc123 --message "Fixed manually"',
  ]
static flags = {
    'api-url': Flags.string({
      default: 'http://localhost:8000',
      description: 'Aelira API URL',
    }),
    department: Flags.string({
      char: 'd',
      default: 'default',
      description: 'Department ID',
    }),
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console or json)',
      options: ['console', 'json'],
    }),
    limit: Flags.integer({
      default: 50,
      description: 'Maximum number of issues to return',
    }),
    message: Flags.string({
      char: 'm',
      description: 'Note message to add',
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path (for JSON format)',
    }),
    severity: Flags.string({
      description: 'Filter by severity (critical, high, medium, low)',
    }),
    status: Flags.string({
      char: 's',
      description: 'Filter by status or new status (open, in_progress, resolved, wont_fix, false_positive)',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance timing information',
    }),
    to: Flags.string({
      description: 'User ID to assign issue to',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(Issues)
    const startTime = Date.now()

    intro('Aelira CLI - Issue Tracker')

    try {
      switch (args.action) {
        case 'assign': {
          if (!args.issue_id) throw new Error('Issue ID required for assign action')
          await this.assignIssue(args.issue_id, flags, startTime)
          break
        }

        case 'list': {
          await this.listIssues(flags, startTime)
          break
        }

        case 'note': {
          if (!args.issue_id) throw new Error('Issue ID required for note action')
          await this.addNote(args.issue_id, flags, startTime)
          break
        }

        case 'stats': {
          await this.showStats(flags, startTime)
          break
        }

        case 'update': {
          if (!args.issue_id) throw new Error('Issue ID required for update action')
          await this.updateIssue(args.issue_id, flags, startTime)
          break
        }

        default: {
          throw new Error(`Unknown action: ${args.action}`)
        }
      }

      if (flags.timer) {
        this.log(`\n⏱️  Total execution time: ${Date.now() - startTime}ms`)
      }
    } catch (error: any) {
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private async addNote(issueId: string, flags: any, _startTime: number): Promise<void> {
    if (!flags.message) throw new Error('--message flag required for note action')

    const s = spinner()
    s.start(`Adding note to issue ${issueId}...`)

    const api = new ApiClient({ apiUrl: flags['api-url'] })
    await api.post(`/analytics/issues/${issueId}/note`, { note: flags.message })

    s.stop('Note added')
    this.log(`\n✅ Note added to issue ${issueId}`)
    outro('✨ Note added!')
  }

  private async assignIssue(issueId: string, flags: any, _startTime: number): Promise<void> {
    if (!flags.to) throw new Error('--to flag required for assign action')

    const s = spinner()
    s.start(`Assigning issue ${issueId}...`)

    const api = new ApiClient({ apiUrl: flags['api-url'] })
    const response = await api.post(`/analytics/issues/${issueId}/assign`, { assigned_to: flags.to })
    const result = await response.json()
    s.stop('Issue assigned')

    this.log(`\n✅ Issue ${issueId} assigned to: ${result.assigned_to}`)
    outro('✨ Assignment complete!')
  }

  private displayIssueList(result: any): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Issue Tracker - ${result.department_id || 'default'}`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  Total Issues: ${result.count || 0}\n`)

    const issues = result.issues || []
    if (issues.length === 0) {
      this.log('  No issues found matching your criteria.\n')
      return
    }

    issues.slice(0, 20).forEach((issue: any, idx: number) => {
      const severityEmoji = this.getSeverityEmoji(issue.severity)
      const statusEmoji = this.getStatusEmoji(issue.status)

      this.log(`  ${idx + 1}. ${severityEmoji} [${issue.severity?.toUpperCase() || 'UNKNOWN'}] ${issue.issue_type}`)
      this.log(`     ${statusEmoji} Status: ${issue.status || 'open'}`)
      this.log(`     📄 File: ${issue.file_name || 'Unknown'}`)
      if (issue.assigned_to_name) {
        this.log(`     👤 Assigned: ${issue.assigned_to_name}`)
      }

      this.log('')
    })

    if (issues.length > 20) {
      this.log(`  ... and ${issues.length - 20} more issues\n`)
    }

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')
    this.log('💡 Tip: Use --status open to filter by status')
    this.log('💡 Tip: Use --format json for full details')
  }

  private displayStats(result: any): void {
    const stats = result.stats || {}

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Issue Statistics - ${result.department_id || 'default'}`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  📊 Overview:`)
    this.log(`  - Total Issues: ${stats.total_issues || 0}`)
    this.log(`  - Resolution Rate: ${stats.resolution_rate ? stats.resolution_rate.toFixed(1) + '%' : 'N/A'}\n`)

    this.log(`  📋 By Status:`)
    this.log(`  - Open: ${stats.open_issues || 0}`)
    this.log(`  - In Progress: ${stats.in_progress_issues || 0}`)
    this.log(`  - Resolved: ${stats.resolved_issues || 0}`)
    this.log(`  - Won't Fix: ${stats.wont_fix_issues || 0}`)
    this.log(`  - False Positive: ${stats.false_positive_issues || 0}\n`)

    this.log(`  🤖 Auto-Remediation:`)
    this.log(`  - Auto-Fixable: ${stats.auto_fixable_issues || 0}`)
    this.log(`  - Auto-Fixed: ${stats.auto_fixed_issues || 0}\n`)

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')
  }

  private getSeverityEmoji(severity: string): string {
    switch (severity?.toLowerCase()) {
      case 'critical': { return '🔴'
      }

      case 'high': { return '🟠'
      }

      case 'low': { return '🟢'
      }

      case 'medium': { return '🟡'
      }

      default: { return '⚪'
      }
    }
  }

  private getStatusEmoji(status: string): string {
    switch (status?.toLowerCase()) {
      case 'false_positive': { return '❌'
      }

      case 'in_progress': { return '🔧'
      }

      case 'open': { return '📭'
      }

      case 'resolved': { return '✅'
      }

      case 'wont_fix': { return '🚫'
      }

      default: { return '❓'
      }
    }
  }

  private async listIssues(flags: any, startTime: number): Promise<void> {
    const s = spinner()
    s.start('Fetching issues...')

    const api = new ApiClient({ apiUrl: flags['api-url'] })
    const query: Record<string, string> = { limit: String(flags.limit) }
    if (flags.status) query.status = flags.status
    if (flags.severity) query.severity = flags.severity

    const response = await api.get(`/analytics/issues/${flags.department}`, { query })
    const result = await response.json()
    s.stop(`Found ${result.count || 0} issues`)

    if (flags.format === 'json') {
      const output = { ...result, performance: { execution_time: Date.now() - startTime } }
      if (flags.output) {
        await fs.writeFile(flags.output, JSON.stringify(output, null, 2))
        outro(`✅ Issues saved to ${flags.output}`)
      } else {
        this.log(JSON.stringify(output, null, 2))
      }
    } else {
      this.displayIssueList(result)
      outro('✨ Issue list complete!')
    }
  }

  private async showStats(flags: any, _startTime: number): Promise<void> {
    const s = spinner()
    s.start('Fetching issue statistics...')

    const api = new ApiClient({ apiUrl: flags['api-url'] })
    const response = await api.get(`/analytics/issues/${flags.department}/stats`)
    const result = await response.json()
    s.stop('Stats fetched')

    if (flags.format === 'json') {
      this.log(JSON.stringify(result, null, 2))
    } else {
      this.displayStats(result)
      outro('✨ Stats complete!')
    }
  }

  private async updateIssue(issueId: string, flags: any, _startTime: number): Promise<void> {
    if (!flags.status) throw new Error('--status flag required for update action')

    const s = spinner()
    s.start(`Updating issue ${issueId}...`)

    const api = new ApiClient({ apiUrl: flags['api-url'] })
    const response = await api.patch(`/analytics/issues/${issueId}/status`, { status: flags.status })
    const result = await response.json()
    s.stop('Issue updated')

    this.log(`\n✅ Issue ${issueId} updated to status: ${result.new_status}`)
    outro('✨ Update complete!')
  }
}
