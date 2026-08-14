import { intro, outro, spinner } from '@clack/prompts'
import { Command, Flags } from '@oclif/core'

import { ApiClient } from '../../utils/api-client.js'
import { resolveDepartment } from '../../utils/canvas.js'

export default class CanvasStatus extends Command {
  static description = 'Show Canvas connection status for a department'
  static examples = [
    '<%= config.bin %> <%= command.id %>',
    '<%= config.bin %> <%= command.id %> --department 42 --format json',
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
  }

  public async run(): Promise<void> {
    const { flags } = await this.parse(CanvasStatus)

    intro('Aelira CLI - Canvas Status')
    const s = spinner()
    s.start('Checking Canvas connection...')

    try {
      const api = new ApiClient({ apiKey: flags['api-key'], apiUrl: flags['api-url'] })
      const department = await resolveDepartment(flags.department)
      // ApiClient supports a query option (src/utils/api-client.ts:37-42);
      // prefer it over hand-built query strings.
      const query: Record<string, string> = {}
      if (department) query.department_id = department

      const response = await api.get('/canvas/status', { query })
      const data = await response.json()
      s.stop('Status retrieved')

      if (flags.format === 'json') {
        this.log(JSON.stringify(data, null, 2))
      } else {
        // CanvasConnectionStatus is {connected, canvas_instance_url, user_email,
        // connected_at, credential_id} — backend/src/api/canvas_routes.py:60-67.
        this.log(`  Connected: ${data.connected ? 'yes' : 'no'}`)
        if (data.canvas_instance_url) this.log(`  Canvas URL: ${data.canvas_instance_url}`)
        if (data.user_email) this.log(`  User: ${data.user_email}`)
        if (data.connected_at) this.log(`  Connected at: ${data.connected_at}`)
      }

      outro('Done')
    } catch (error: any) {
      s.stop('Failed to check Canvas status')
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }
}
