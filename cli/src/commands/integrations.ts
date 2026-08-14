import { intro, outro, spinner } from '@clack/prompts'
import { Command, Flags } from '@oclif/core'

import { ApiClient } from '../utils/api-client.js'

interface ProviderRow {
  heading: string
  key: string
  /** Field holding the account name, when the provider reports one. */
  nameField?: string
}

const PROVIDER_ROWS: ProviderRow[] = [
  { heading: '  📁 Google Workspace', key: 'google', nameField: 'name' },
  { heading: '  📁 Microsoft 365', key: 'microsoft', nameField: 'name' },
  { heading: '  📚 Canvas LMS', key: 'canvas' },
  { heading: '  📚 Blackboard Learn', key: 'blackboard' },
  { heading: '  🌏 Moodle LMS', key: 'moodle', nameField: 'fullname' },
  { heading: '  🎓 D2L Brightspace', key: 'brightspace', nameField: 'fullname' },
]

export default class Integrations extends Command {
  static description = 'Show cloud integration status for Google Workspace and Microsoft 365'
static examples = [
    '<%= config.bin %> <%= command.id %>',
    '<%= config.bin %> <%= command.id %> --api-url http://localhost:8000',
    '<%= config.bin %> <%= command.id %> --format json',
  ]
static flags = {
    'api-key': Flags.string({
      description: 'API key for authentication (optional in development)',
    }),
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
  }

  public async run(): Promise<void> {
    const { flags } = await this.parse(Integrations)

    intro('Aelira CLI - Cloud Integrations Status')

    const s = spinner()
    s.start('Fetching integration status...')

    try {
      const api = new ApiClient({ apiKey: flags['api-key'], apiUrl: flags['api-url'] })

      const response = await api.get('/integrations/status')
      const data = await response.json()
      s.stop('Status retrieved')

      if (flags.format === 'json') {
        this.log(JSON.stringify(data, null, 2))
      } else {
        this.displayStatus(data)
      }

      outro('✨ Integration status retrieved!')
    } catch (error: any) {
      s.stop('Failed to retrieve status')
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private displayStatus(data: any): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`  Cloud Integration Status`)
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    for (const [index, row] of PROVIDER_ROWS.entries()) {
      if (index > 0) this.log('')
      this.renderProviderStatus(row, data[row.key])
    }

    this.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log('💡 Tip: Use `aelira integrations connect` to connect cloud providers')
    this.log('💡 Tip: Use `aelira integrations folders` to select folders to sync')
  }

  private renderProviderStatus(row: ProviderRow, provider: any): void {
    this.log(row.heading)

    if (!provider?.connected) {
      this.log(`    Status: ✗ Not Connected`)
      return
    }

    this.log(`    Status: ✓ Connected`)
    this.log(`    Email: ${provider.email || 'N/A'}`)

    if (row.nameField) {
      this.log(`    Name: ${provider[row.nameField] || 'N/A'}`)
    }

    if (provider.last_sync_at) {
      this.log(`    Last Sync: ${new Date(provider.last_sync_at).toLocaleString()}`)
    }
  }
}
