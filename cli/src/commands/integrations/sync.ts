import { intro, outro, spinner } from '@clack/prompts'
import { Command, Flags } from '@oclif/core'
import pc from 'picocolors'

import { ApiClient, ApiError } from '../../utils/api-client.js'

export default class IntegrationsSync extends Command {
  static description = 'Trigger file sync from connected cloud providers'

  static examples = [
    '<%= config.bin %> <%= command.id %>',
    '<%= config.bin %> <%= command.id %> --provider google',
    '<%= config.bin %> <%= command.id %> --provider microsoft --folder-ids folder-1,folder-2',
    '<%= config.bin %> <%= command.id %> --format json',
  ]

  static flags = {
    'api-key': Flags.string({
      description: 'API key for authentication (optional in development)',
    }),
    'api-url': Flags.string({
      description: 'Aelira API URL',
    }),
    'folder-ids': Flags.string({
      description: 'Comma-separated folder IDs to sync (defaults to active sync folders for the provider)',
    }),
    format: Flags.string({
      char: 'f',
      default: 'console',
      description: 'Output format (console or json)',
      options: ['console', 'json'],
    }),
    provider: Flags.string({
      char: 'p',
      description: 'Cloud provider to sync (google or microsoft)',
      options: ['google', 'microsoft'],
    }),
  }

  public async run(): Promise<void> {
    const { flags } = await this.parse(IntegrationsSync)

    intro('Aelira CLI - Cloud File Sync')

    const s = spinner()
    s.start('Triggering sync...')

    try {
      const api = new ApiClient({ apiKey: flags['api-key'], apiUrl: flags['api-url'] })
      const jobs: any[] = []

      if (flags['folder-ids']) {
        if (!flags.provider) {
          throw new Error('Provider must be specified when passing --folder-ids. Use --provider google or --provider microsoft.')
        }

        const folderIds = flags['folder-ids']
          .split(',')
          .map((id) => id.trim())
          .filter(Boolean)

        if (folderIds.length === 0) {
          throw new Error('At least one folder ID must be provided in --folder-ids.')
        }

        const response = await this.triggerProviderSync(api, flags.provider, folderIds)
        jobs.push(response)
      } else if (flags.provider) {
        const foldersRes = await api.get(`/integrations/sync-folders?provider=${flags.provider}`)
        const foldersData = await foldersRes.json()
        const activeFolderIds = (foldersData.folders || [])
          .filter((folder: any) => folder.is_active)
          .map((folder: any) => folder.id)

        if (activeFolderIds.length === 0) {
          throw new Error(
            `No active sync folders found for ${flags.provider}.\n\n` +
              pc.dim(`Use \`aelira integrations folders --provider ${flags.provider}\` to select folders first.`)
          )
        }

        const response = await this.triggerProviderSync(api, flags.provider, activeFolderIds)
        jobs.push(response)
      } else {
        const foldersRes = await api.get('/integrations/sync-folders')
        const foldersData = await foldersRes.json()
        const activeFolders = (foldersData.folders || []).filter((folder: any) => folder.is_active)

        if (activeFolders.length === 0) {
          throw new Error(
            'No active sync folders found.\n\n' +
              pc.dim('Use `aelira integrations folders` to select folders to sync first.')
          )
        }

        const providersWithFolders = [...new Set(activeFolders.map((f: any) => f.provider))] as string[]
        for (const prov of providersWithFolders) {
          const providerFolderIds = activeFolders
            .filter((f: any) => f.provider === prov)
            .map((f: any) => f.id)

          if (providerFolderIds.length > 0) {
            const response = await this.triggerProviderSync(api, prov, providerFolderIds)
            jobs.push(response)
          }
        }
      }

      s.stop('Sync jobs queued')

      if (flags.format === 'json') {
        this.log(JSON.stringify(jobs.length === 1 ? jobs[0] : { jobs }, null, 2))
        return
      }

      this.displaySyncResult(jobs)
      outro('File sync initiated.')
    } catch (error: any) {
      s.stop('Sync failed')
      outro(`Error: ${error.message}`)
      this.error(error)
    }
  }

  private async triggerProviderSync(api: ApiClient, provider: string, folderIds: string[]): Promise<any> {
    try {
      const response = await api.post('/integrations/sync', {
        folder_ids: folderIds,
        provider,
      })
      return await response.json()
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.status === 404 && error.body.includes('Integration not connected')) {
          throw new Error(
            `Integration not connected for ${provider}.\n\n` +
              pc.dim(`Run: aelira integrations connect ${provider}`)
          )
        }

        if (error.status === 404 && error.body.includes('Sync folder not found')) {
          throw new Error(`Sync folder not found for ${provider}. Ensure selected folders are active.`)
        }
      }

      throw error
    }
  }

  private displaySyncResult(jobs: any[]): void {
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log('  Cloud File Sync Status')
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  Queued Jobs: ${jobs.length}\n`)

    for (const job of jobs) {
      this.log(`    ${pc.bold(String(job.provider || '').toUpperCase())}`)
      this.log(`       Job ID: ${job.job_id}`)
      this.log(`       Status: ${job.status}`)
      this.log(`       Folders: ${job.folder_ids?.length || 0}\n`)
    }

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(pc.dim('Sync jobs will process in the background.'))
    this.log(pc.dim('Files will be discovered and queued for scanning.'))
    this.log(pc.dim('Check the dashboard to monitor progress.'))
  }
}
