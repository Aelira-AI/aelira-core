import { intro, outro, spinner } from '@clack/prompts'
import { Command, Flags } from '@oclif/core'

import { ApiClient } from '../../utils/api-client.js'
import { resolveDepartment } from '../../utils/canvas.js'

export default class CanvasCourses extends Command {
  static description = 'List Canvas courses available to the connected account'
  static examples = [
    '<%= config.bin %> <%= command.id %>',
    '<%= config.bin %> <%= command.id %> --department 42 --format json',
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
  }

  public async run(): Promise<void> {
    const { flags } = await this.parse(CanvasCourses)

    intro('Aelira CLI - Canvas Courses')
    const s = spinner()
    s.start('Fetching Canvas courses...')

    try {
      const api = new ApiClient({ apiKey: flags['api-key'], apiUrl: flags['api-url'] })
      const department = await resolveDepartment(flags.department)
      // ApiClient supports a query option (src/utils/api-client.ts:37-42);
      // prefer it over hand-built query strings.
      const query: Record<string, string> = {}
      if (department) query.department_id = department

      const response = await api.get('/canvas/courses', { query })
      const data = await response.json()
      s.stop('Courses retrieved')

      if (flags.format === 'json') {
        this.log(JSON.stringify(data, null, 2))
      } else {
        const courses = Array.isArray(data) ? data : (data.courses ?? [])
        if (courses.length === 0) {
          this.log('  No courses found.')
        } else {
          for (const course of courses) {
            this.log(`  ${course.id}  ${course.name ?? '(unnamed)'}`)
          }

          this.log(`\n  ${courses.length} course(s)`)
        }
      }

      outro('Done')
    } catch (error: any) {
      s.stop('Failed to fetch Canvas courses')
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }
}
