import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'

import { ApiClient } from '../../utils/api-client.js'
import { buildScanStatusQuery, extractFileIds, resolveDepartment } from '../../utils/canvas.js'

export default class CanvasScan extends Command {
  static args = {
    course_id: Args.string({
      description: 'Canvas course id',
      required: true,
    }),
  }
  static description = 'Bulk-scan a Canvas course, optionally waiting for every file to finish'
  static examples = [
    '<%= config.bin %> <%= command.id %> 101',
    '<%= config.bin %> <%= command.id %> 101 --wait --format json',
  ]
  static flags = {
    'api-key': Flags.string({
      description: 'API key for authentication (optional in development)',
    }),
    'api-url': Flags.string({
      default: 'http://localhost:8000',
      description: 'Aelira API URL',
    }),
    department: Flags.string({
      description: 'Department id (defaults to the configured department)',
    }),
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console or json)',
      options: ['console', 'json'],
    }),
    wait: Flags.boolean({
      default: false,
      description: 'Poll scan status until every file finishes',
    }),
  }

  public async run(): Promise<void> {
    const { args, flags } = await this.parse(CanvasScan)

    intro('Aelira CLI - Canvas Scan')
    const s = spinner()
    s.start('Submitting scan...')

    try {
      const api = new ApiClient({ apiKey: flags['api-key'], apiUrl: flags['api-url'] })
      const department = await resolveDepartment(flags.department)

      // This queues background scan jobs for every file in the course. A
      // retried gateway timeout would re-queue the whole course, so this
      // non-idempotent write relies on ApiClient.post()'s no-retry default —
      // same reasoning as canvas remediate, no explicit opt-out needed.
      const response = await api.post('/canvas/scan/bulk', {
        course_id: args.course_id,
        department_id: department,
      }, { timeout: 300_000 })
      const data = await response.json()
      s.stop('Scan submitted')

      if (flags.format === 'json') {
        this.log(JSON.stringify(data, null, 2))
      } else {
        this.log(`  Scan submitted for course ${args.course_id}`)
      }

      const fileIds = extractFileIds(data)
      if (flags.wait) {
        if (fileIds.length > 0) {
          await this.pollStatus(api, args.course_id, fileIds)
        } else if (flags.format !== 'json') {
          this.log('  Skipping status polling: the scan returned no files.')
        }
      }

      outro('Done')
    } catch (error: any) {
      s.stop('Failed to submit scan')
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private async pollStatus(api: ApiClient, courseId: string, fileIds: string[]): Promise<void> {
    const s = spinner()
    s.start('Waiting for scans to finish...')
    const deadline = Date.now() + 600_000

    while (Date.now() < deadline) {
      const response = await api.get(
        `/canvas/courses/${encodeURIComponent(courseId)}/scan-status`,
        { query: buildScanStatusQuery(fileIds) },
      )
      const status = await response.json()
      const files: any[] = status.files ?? []

      // An empty or missing `files` array means the API hasn't reported
      // status yet, not that everything is done — treat it as "still
      // waiting" so a bare/early response can't read as a false success.
      if (files.length > 0) {
        const pending = files.filter(
          (f: any) => f.status !== 'completed' && f.status !== 'failed',
        )
        if (pending.length === 0) {
          const failed = files.filter((f: any) => f.status === 'failed').length
          const succeeded = files.length - failed
          s.stop(
            failed > 0
              ? `All scans finished (${succeeded} succeeded, ${failed} failed)`
              : 'All scans finished',
          )
          return
        }

        s.message(`${fileIds.length - pending.length}/${fileIds.length} finished...`)
      } else {
        s.message('Waiting for scan status to be reported...')
      }

      await new Promise((resolve) => {
        setTimeout(resolve, 5000)
      })
    }

    s.stop('Timed out waiting for scans')
  }
}
