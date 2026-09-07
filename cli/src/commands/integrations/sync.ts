import { intro, outro, spinner } from '@clack/prompts'
import { Command, Flags } from '@oclif/core'
import pc from 'picocolors'

import { ApiClient, ApiError } from '../../utils/api-client.js'

export default class IntegrationsSync extends Command {
  static description = 'Trigger file sync from connected cloud providers'
static examples = [
    '<%= config.bin %> <%= command.id %>',
    '<%= config.bin %> <%= command.id %> --format json',
  ]
static flags = {
    'api-key': Flags.string({
      description: 'API key for authentication (optional in development)',
    }),
    'api-url': Flags.string({
      description: 'Aelira API URL',
    }),
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console or json)',
      options: ['console', 'json'],
    }),
  }

  public async run(): Promise<void> {
    const { flags } = await this.parse(IntegrationsSync)

    intro('Aelira CLI - Cloud File Sync')

    const s = spinner()
    s.start('Triggering sync for all connected providers...')

    try {
      const api = new ApiClient({ apiKey: flags['api-key'], apiUrl: flags['api-url'] })

      let response: Response
      try {
        response = await api.post('/integrations/sync', {})
      } catch (error) {
        if (error instanceof ApiError && error.status === 400 && error.body.includes('No cloud providers connected')) {
          throw new Error(
            'No cloud providers connected.\n\n' +
              pc.dim('Run: aelira integrations connect google') +
              '\n' +
              pc.dim('Or: aelira integrations connect microsoft')
          )
        }

        throw error
      }

      const data = await response.json()
      s.stop('Sync jobs queued')

      if (flags.format === 'json') {
        this.log(JSON.stringify(data, null, 2))
      } else {
        this.displaySyncResult(data)
      }

      outro('✨ File sync initiated!')
    } catch (error: any) {
      s.stop('Sync failed')
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private displaySyncResult(data: any): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Cloud File Sync Status`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  Message: ${data.message}`)
    this.log(`  Queued Jobs: ${data.jobs?.length || 0}\n`)

    if (data.jobs && data.jobs.length > 0) {
      this.log('  Sync Jobs:')
      for (const job of data.jobs) {
        const icon = job.provider === 'google' ? '📁' : '📂'
        this.log(`    ${icon} ${pc.bold(job.provider.toUpperCase())}`)
        this.log(`       Job ID: ${job.job_id}`)
        this.log(`       Status: ${job.status}\n`)
      }
    }

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(pc.dim('Sync jobs will process in the background.'))
    this.log(pc.dim('Files will be discovered and queued for scanning.'))
    this.log(pc.dim('Check the dashboard to monitor progress.'))
  }
}
