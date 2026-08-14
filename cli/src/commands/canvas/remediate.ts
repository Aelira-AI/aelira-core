import { confirm, intro, isCancel, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'

import { ApiClient } from '../../utils/api-client.js'
import { resolveDepartment } from '../../utils/canvas.js'

export default class CanvasRemediate extends Command {
  static args = {
    course_id: Args.string({
      description: 'Canvas course id',
      required: true,
    }),
    file_id: Args.string({
      description: 'Canvas file id',
      required: true,
    }),
  }
  static description = 'Remediate a Canvas file, optionally replacing the original in Canvas'
  static examples = [
    '<%= config.bin %> <%= command.id %> 101 555',
    '<%= config.bin %> <%= command.id %> 101 555 --upload-back --yes',
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
    'no-ai': Flags.boolean({
      default: false,
      description: 'Skip AI-generated fixes and apply structural fixes only',
    }),
    'upload-back': Flags.boolean({
      default: false,
      description: 'Replace the file in Canvas with the remediated version',
    }),
    yes: Flags.boolean({
      char: 'y',
      default: false,
      description: 'Skip the confirmation prompt for --upload-back',
    }),
  }

  public async run(): Promise<void> {
    const { args, flags } = await this.parse(CanvasRemediate)

    intro('Aelira CLI - Canvas Remediate')

    if (flags['upload-back'] && !flags.yes) {
      // Without a TTY the prompt renders but can never resolve, so the process
      // hangs and then dies on an unsettled top-level await. Fail fast instead.
      if (!process.stdin.isTTY) {
        outro('❌ Error: stdin is not a TTY, so --upload-back cannot be confirmed')
        this.error('stdin is not a TTY; pass --yes to confirm --upload-back.', { exit: 1 })
      }

      const confirmed = await confirm({
        message: `Replace file ${args.file_id} in Canvas course ${args.course_id}? This overwrites the original.`,
      })
      if (isCancel(confirmed) || !confirmed) {
        outro('Cancelled; nothing was changed')
        return
      }
    }

    const s = spinner()
    s.start('Submitting remediation...')

    // Set when the API accepts the request but declines to queue the job. The
    // non-zero exit happens after the try block so it is not swallowed by the
    // catch below.
    let failureMessage: string | undefined

    try {
      const api = new ApiClient({ apiKey: flags['api-key'], apiUrl: flags['api-url'] })
      const department = await resolveDepartment(flags.department)

      // This call can write into a live Canvas course. If the request
      // actually succeeded server-side but the response was lost to a
      // timeout, a retry would resubmit the same write and risk a duplicate
      // or double-overwrite the user never sees. ApiClient.post() now
      // defaults to no retry for exactly this reason, so no explicit
      // opt-out is needed here anymore.
      const response = await api.post('/canvas/remediate', {
        course_id: args.course_id,
        department_id: department,
        file_id: args.file_id,
        upload_back: flags['upload-back'],
        use_ai: !flags['no-ai'],
      }, { timeout: 300_000 })
      const data = await response.json()

      // CanvasRemediateResponse is {success, scan_id, job_id, message}
      // (backend/src/api/canvas_routes.py:80-86). A 2xx is not proof the job
      // was queued: when Canvas is not connected the route returns HTTP 200
      // with success=false and an explanatory message (canvas_routes.py:499-503).
      // Treat anything short of an explicit true as a failure.
      const queued = data.success === true
      s.stop(queued ? 'Remediation queued' : 'Remediation was not queued')

      if (flags.format === 'json') {
        this.log(JSON.stringify(data, null, 2))
      } else if (queued) {
        this.log(`  Remediation queued for file ${args.file_id} in course ${args.course_id}`)
        if (data.job_id) this.log(`  Job id: ${data.job_id}`)
        if (flags['upload-back']) {
          // The route only queues the job — nothing has been downloaded,
          // remediated or uploaded yet. Do not claim a completed write-back.
          this.log(
            data.job_id
              ? `  Canvas will be updated when job ${data.job_id} completes.`
              : '  Canvas will be updated when the job completes.',
          )
        }
      } else {
        this.log(`  ${data.message ?? 'The API did not queue the remediation job.'}`)
      }

      if (queued) {
        outro('Done')
      } else {
        failureMessage = data.message ?? 'The API did not queue the remediation job.'
        outro('❌ Error: remediation was not queued')
      }
    } catch (error: any) {
      s.stop('Failed to submit remediation')
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }

    if (failureMessage) {
      this.error(failureMessage, { exit: 1 })
    }
  }
}
