import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'

import { ApiClient } from '../../utils/api-client.js'
import { resolveDepartment } from '../../utils/canvas.js'

export default class CanvasFiles extends Command {
  static args = {
    course_id: Args.string({
      description: 'Canvas course id',
      required: true,
    }),
  }
  static description = "List a Canvas course's files"
  static examples = [
    '<%= config.bin %> <%= command.id %> 101',
    '<%= config.bin %> <%= command.id %> 101 --search syllabus --format json',
  ]
  static flags = {
    'api-key': Flags.string({
      description: 'API key for authentication (optional in development)',
    }),
    'api-url': Flags.string({
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
    search: Flags.string({
      description: 'Filter files by name',
    }),
  }

  public async run(): Promise<void> {
    const { args, flags } = await this.parse(CanvasFiles)

    intro('Aelira CLI - Canvas Files')
    const s = spinner()
    s.start('Fetching Canvas files...')

    try {
      const api = new ApiClient({ apiKey: flags['api-key'], apiUrl: flags['api-url'] })
      const department = await resolveDepartment(flags.department)
      // ApiClient supports a query option (src/utils/api-client.ts:37-42);
      // prefer it over hand-built query strings.
      const query: Record<string, string> = {}
      if (department) query.department_id = department
      if (flags.search) query.search_term = flags.search

      const response = await api.get(
        `/canvas/courses/${encodeURIComponent(args.course_id)}/files`,
        { query },
      )
      const data = await response.json()
      s.stop('Files retrieved')

      if (flags.format === 'json') {
        this.log(JSON.stringify(data, null, 2))
      } else {
        const files = Array.isArray(data) ? data : (data.files ?? [])
        if (files.length === 0) {
          this.log('  No files found.')
        } else {
          for (const file of files) {
            this.log(`  ${file.id}  ${file.display_name ?? file.filename ?? '(unnamed)'}  ${file.content_type ?? ''}`)
          }

          this.log(`\n  ${files.length} file(s)`)
        }
      }

      outro('Done')
    } catch (error: any) {
      s.stop('Failed to fetch Canvas files')
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }
}
