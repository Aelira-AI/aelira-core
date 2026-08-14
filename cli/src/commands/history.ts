import { intro, isCancel, outro, select, spinner } from '@clack/prompts'
import { Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'
import pc from 'picocolors'

interface ScanHistoryEntry {
  criticalCount: number
  duration: number
  file: string
  id: string
  score: number
  seriousCount: number
  target: string
  timestamp: string
  totalIssues: number
  type: string
}

interface HistoryIndex {
  entries: ScanHistoryEntry[]
  version: number
}

const HISTORY_DIR = path.join(os.homedir(), '.aelira', 'history')
const HISTORY_INDEX = path.join(HISTORY_DIR, 'index.json')

export default class History extends Command {
  static description = 'View and manage scan history'
static examples = [
    '<%= config.bin %> <%= command.id %>',
    '<%= config.bin %> <%= command.id %> --target example.com',
    '<%= config.bin %> <%= command.id %> --type website --limit 20',
    '<%= config.bin %> <%= command.id %> --export history.json',
    '<%= config.bin %> <%= command.id %> --clear',
  ]
static flags = {
    clear: Flags.boolean({
      description: 'Clear all scan history',
    }),
    export: Flags.string({
      char: 'e',
      description: 'Export history to file',
    }),
    format: Flags.string({
      char: 'f',
      default: 'table',
      description: 'Output format',
      options: ['table', 'json', 'csv'],
    }),
    limit: Flags.integer({
      char: 'l',
      default: 10,
      description: 'Number of entries to show',
    }),
    target: Flags.string({
      char: 't',
      description: 'Filter by target URL or path',
    }),
    type: Flags.string({
      description: 'Filter by scan type (website, pdf, ppt, etc.)',
      options: ['website', 'pdf', 'ppt', 'docx', 'xlsx', 'code', 'html'],
    }),
  }

  static async addEntry(entry: Omit<ScanHistoryEntry, 'file' | 'id'>): Promise<void> {
    const historyDir = path.join(os.homedir(), '.aelira', 'history')
    const indexPath = path.join(historyDir, 'index.json')

    // Ensure directory exists
    await fs.mkdir(historyDir, { recursive: true })

    // Load existing history
    let history: HistoryIndex
    try {
      const content = await fs.readFile(indexPath, 'utf8')
      history = JSON.parse(content)
    } catch {
      history = { entries: [], version: 1 }
    }

    // Generate ID
    const id = `scan-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const file = `${id}.json`

    // Save full scan result
    const fullResultPath = path.join(historyDir, file)
    await fs.writeFile(fullResultPath, JSON.stringify(entry, null, 2))

    // Add to index
    const newEntry: ScanHistoryEntry = {
      ...entry,
      file,
      id,
    }

    history.entries.push(newEntry)

    // Keep only last 1000 entries
    if (history.entries.length > 1000) {
      const removed = history.entries.splice(0, history.entries.length - 1000)
      // Clean up old files
      for (const oldEntry of removed) {
        try {
          await fs.unlink(path.join(historyDir, oldEntry.file))
        } catch {
          // Ignore errors
        }
      }
    }

    // Save index
    await fs.writeFile(indexPath, JSON.stringify(history, null, 2))
  }

  async run(): Promise<void> {
    const { flags } = await this.parse(History)

    intro('Aelira CLI - Scan History')

    // Ensure history directory exists
    await this.ensureHistoryDir()

    // Handle clear action
    if (flags.clear) {
      await this.clearHistory()
      return
    }

    // Handle export action
    if (flags.export) {
      await this.exportHistory(flags.export, flags.format)
      return
    }

    // Load and display history
    const s = spinner()
    s.start('Loading scan history...')

    const history = await this.loadHistory()
    s.stop(`Found ${history.entries.length} scan entries`)

    if (history.entries.length === 0) {
      this.log('\n📭 No scan history found.')
      this.log('   Run `aelira scan <url>` to start building your history.\n')
      outro('Ready to scan')
      return
    }

    // Filter entries
    let {entries} = history

    if (flags.target) {
      entries = entries.filter((e) =>
        e.target.toLowerCase().includes(flags.target!.toLowerCase()),
      )
    }

    if (flags.type) {
      entries = entries.filter((e) => e.type === flags.type)
    }

    // Sort by timestamp (newest first)
    entries = entries.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())

    // Limit entries
    entries = entries.slice(0, flags.limit)

    if (entries.length === 0) {
      this.log('\n📭 No matching entries found.')
      outro('Try different filters')
      return
    }

    // Display based on format
    switch (flags.format) {
      case 'csv': {
        this.displayCSV(entries)
        break
      }

      case 'json': {
        this.displayJSON(entries)
        break
      }

      default: {
        this.displayTable(entries)
      }
    }

    // Offer to view details
    await this.offerDetailView(entries)
  }

  private async clearHistory(): Promise<void> {
    const confirm = await select({
      message: 'Are you sure you want to clear all scan history?',
      options: [
        { label: 'Yes, clear all history', value: 'yes' },
        { label: 'No, cancel', value: 'no' },
      ],
    })

    if (isCancel(confirm) || confirm === 'no') {
      outro('History preserved')
      return
    }

    const s = spinner()
    s.start('Clearing history...')

    try {
      // Remove all files in history directory
      const files = await fs.readdir(HISTORY_DIR)
      for (const file of files) {
        await fs.unlink(path.join(HISTORY_DIR, file))
      }

      s.stop('History cleared')
      outro('✅ All scan history has been cleared')
    } catch (error: any) {
      s.stop('Error clearing history')
      this.error(error.message)
    }
  }

  private displayCSV(entries: ScanHistoryEntry[]): void {
    const headers = ['ID', 'Timestamp', 'Target', 'Type', 'Score', 'Critical', 'Serious', 'Total Issues']
    this.log(headers.join(','))

    for (const entry of entries) {
      const row = [
        entry.id,
        entry.timestamp,
        `"${entry.target}"`,
        entry.type,
        entry.score,
        entry.criticalCount,
        entry.seriousCount,
        entry.totalIssues,
      ]
      this.log(row.join(','))
    }
  }

  private displayJSON(entries: ScanHistoryEntry[]): void {
    this.log(JSON.stringify(entries, null, 2))
  }

  private displayTable(entries: ScanHistoryEntry[]): void {
    this.log('\n' + pc.bold('Recent Scans'))
    this.log('━'.repeat(100))

    // Header
    this.log(
      pc.dim(
        '  ' +
          'Date'.padEnd(12) +
          'Time'.padEnd(10) +
          'Type'.padEnd(10) +
          'Score'.padEnd(8) +
          'Critical'.padEnd(10) +
          'Target',
      ),
    )
    this.log('─'.repeat(100))

    for (const entry of entries) {
      const date = new Date(entry.timestamp)
      const dateStr = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
      const timeStr = date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })

      const scoreColor = entry.score >= 80 ? pc.green : entry.score >= 60 ? pc.yellow : pc.red
      const criticalColor = entry.criticalCount > 0 ? pc.red : pc.dim

      // Truncate target for display
      const targetDisplay = entry.target.length > 40 ? entry.target.slice(0, 37) + '...' : entry.target

      this.log(
        '  ' +
          dateStr.padEnd(12) +
          timeStr.padEnd(10) +
          entry.type.padEnd(10) +
          scoreColor(String(entry.score).padEnd(8)) +
          criticalColor(String(entry.criticalCount).padEnd(10)) +
          pc.dim(targetDisplay),
      )
    }

    this.log('━'.repeat(100))
    this.log(`  Showing ${entries.length} of ${entries.length} entries\n`)
  }

  private async ensureHistoryDir(): Promise<void> {
    await fs.mkdir(HISTORY_DIR, { recursive: true })
  }

  private async exportHistory(outputPath: string, format: string): Promise<void> {
    const s = spinner()
    s.start('Exporting history...')

    const history = await this.loadHistory()

    let content: string

    switch (format) {
      case 'csv': {
        const headers = ['ID', 'Timestamp', 'Target', 'Type', 'Score', 'Critical', 'Serious', 'Total Issues', 'Duration']
        const rows = history.entries.map((e) => [
          e.id,
          e.timestamp,
          `"${e.target}"`,
          e.type,
          e.score,
          e.criticalCount,
          e.seriousCount,
          e.totalIssues,
          e.duration,
        ])
        content = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n')
        break
      }

      default: {
        content = JSON.stringify(history, null, 2)
      }
    }

    await fs.writeFile(outputPath, content)
    s.stop('Export complete')

    outro(`✅ History exported to ${outputPath}`)
  }

  private async loadHistory(): Promise<HistoryIndex> {
    try {
      const content = await fs.readFile(HISTORY_INDEX, 'utf8')
      return JSON.parse(content)
    } catch {
      return { entries: [], version: 1 }
    }
  }

  private async offerDetailView(entries: ScanHistoryEntry[]): Promise<void> {
    const viewMore = await select({
      message: 'View scan details?',
      options: [
        { label: 'No, exit', value: 'exit' },
        ...entries.slice(0, 5).map((e) => ({
          label: `${new Date(e.timestamp).toLocaleString()} - ${e.target.slice(0, 30)}...`,
          value: e.id,
        })),
      ],
    })

    if (isCancel(viewMore) || viewMore === 'exit') {
      outro('Done')
      return
    }

    // Load full scan result
    const entry = entries.find((e) => e.id === viewMore)
    if (!entry) {
      outro('Entry not found')
      return
    }

    try {
      const fullPath = path.join(HISTORY_DIR, entry.file)
      const content = await fs.readFile(fullPath, 'utf8')
      const fullResult = JSON.parse(content)

      this.log('\n' + pc.bold('Scan Details'))
      this.log('━'.repeat(60))
      this.log(`  Target:    ${entry.target}`)
      this.log(`  Type:      ${entry.type}`)
      this.log(`  Timestamp: ${new Date(entry.timestamp).toLocaleString()}`)
      this.log(`  Score:     ${entry.score}%`)
      this.log(`  Duration:  ${entry.duration}ms`)
      this.log('─'.repeat(60))
      this.log(`  Critical:  ${entry.criticalCount}`)
      this.log(`  Serious:   ${entry.seriousCount}`)
      this.log(`  Total:     ${entry.totalIssues}`)
      this.log('━'.repeat(60))

      if (fullResult.violations && fullResult.violations.length > 0) {
        this.log('\n' + pc.bold('Violations:'))
        for (const v of fullResult.violations.slice(0, 10)) {
          const icon =
            v.impact === 'critical' ? '🔴' : v.impact === 'serious' ? '🟠' : '🟡'
          this.log(`  ${icon} [${v.impact}] ${v.id}`)
          this.log(`     ${pc.dim(v.description || '')}`)
        }

        if (fullResult.violations.length > 10) {
          this.log(`\n  ${pc.dim(`... and ${fullResult.violations.length - 10} more`)}`)
        }
      }

      outro('Done')
    } catch {
      this.log('\n' + pc.yellow('⚠️  Full scan details not available'))
      outro('Done')
    }
  }
}
