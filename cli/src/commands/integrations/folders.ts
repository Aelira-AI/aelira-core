import { intro, isCancel, multiselect, outro, select, spinner } from '@clack/prompts'
import { Command, Flags } from '@oclif/core'
import pc from 'picocolors'

import { ApiClient, ApiError } from '../../utils/api-client.js'

export default class IntegrationsFolders extends Command {
  static description = 'Manage folder selection for cloud sync (privacy-critical)'
static examples = [
    '<%= config.bin %> <%= command.id %>',
    '<%= config.bin %> <%= command.id %> --provider google',
    '<%= config.bin %> <%= command.id %> --provider microsoft --format json',
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
    provider: Flags.string({
      char: 'p',
      description: 'Cloud provider (google or microsoft)',
      options: ['google', 'microsoft'],
    }),
  }

  public async run(): Promise<void> {
    const { flags } = await this.parse(IntegrationsFolders)

    intro('Aelira CLI - Folder Selection (Privacy-First)')

    let {provider} = flags

    // Interactive provider selection if not provided
    if (!provider) {
      const action = await select({
        message: 'What would you like to do?',
        options: [
          { label: 'View selected folders', value: 'list' },
          { label: 'Select folders to sync (Google Workspace)', value: 'google' },
          { label: 'Select folders to sync (Microsoft 365)', value: 'microsoft' },
          { label: 'Remove folders from sync', value: 'remove' },
        ],
      })

      if (isCancel(action)) {
        outro('Cancelled')
        return
      }

      if (action === 'list') {
        await this.listFolders(flags)
        return
      }

      if (action === 'remove') {
        await this.removeFolders(flags)
        return
      }

      provider = action as string
    }

    // Show current folders and allow selection
    await this.selectFolders(provider, flags)
  }

  private async listFolders(flags: any): Promise<void> {
    const s = spinner()
    s.start('Fetching selected folders...')

    try {
      const api = new ApiClient({ apiKey: flags['api-key'], apiUrl: flags['api-url'] })

      const response = await api.get('/integrations/sync-folders')
      const data = await response.json()
      s.stop('Folders retrieved')

      if (flags.format === 'json') {
        this.log(JSON.stringify(data, null, 2))
        return
      }

      if (data.folders.length === 0) {
        outro('⚠️  No folders selected for sync.\n\n' + pc.dim('Use `aelira integrations folders` to select folders.'))
        return
      }

      this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
      this.log(`  Selected Sync Folders (${data.folders.length})`)
      this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

      for (const folder of data.folders) {
        const icon = folder.provider === 'google' ? '📁' : '📂'
        this.log(`  ${icon} ${pc.bold(folder.folder_name)}`)
        this.log(`     Provider: ${folder.provider}`)
        this.log(`     Subfolders: ${folder.sync_subfolders ? 'Yes' : 'No'}`)
        this.log(`     Added: ${new Date(folder.created_at).toLocaleDateString()}\n`)
      }

      this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')

      outro('✨ Folder list complete!')
    } catch (error: any) {
      s.stop('Failed to fetch folders')
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private async removeFolders(flags: any): Promise<void> {
    const s = spinner()
    s.start('Fetching selected folders...')

    try {
      const api = new ApiClient({ apiKey: flags['api-key'], apiUrl: flags['api-url'] })

      const response = await api.get('/integrations/sync-folders')
      const data = await response.json()
      s.stop('Folders retrieved')

      if (data.folders.length === 0) {
        outro('⚠️  No folders selected for sync.')
        return
      }

      const foldersToRemove = await multiselect({
        message: 'Select folders to remove from sync:',
        options: data.folders.map((folder: any) => ({
          label: `${folder.folder_name} (${folder.provider})`,
          value: folder.id,
        })),
        required: false,
      })

      if (isCancel(foldersToRemove) || foldersToRemove.length === 0) {
        outro('Cancelled')
        return
      }

      s.start(`Removing ${foldersToRemove.length} folder(s)...`)

      for (const folderId of foldersToRemove) {
        await api.delete(`/integrations/sync-folders/${folderId}`)
      }

      s.stop(`Removed ${foldersToRemove.length} folder(s)`)
      outro(`✨ Successfully removed ${foldersToRemove.length} folder(s) from sync!`)
    } catch (error: any) {
      s.stop('Failed to remove folders')
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private async selectFolders(provider: string, flags: any): Promise<void> {
    const s = spinner()
    s.start(`Fetching ${provider} folders...`)

    try {
      const api = new ApiClient({ apiKey: flags['api-key'], apiUrl: flags['api-url'] })

      // Fetch root folders
      const endpoint =
        provider === 'google' ? '/google/drive/folders' : '/microsoft/onedrive/folders'

      let response: Response
      try {
        response = await api.get(endpoint)
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          throw new Error(`${provider} is not connected. Run: aelira integrations connect ${provider}`)
        }

        throw error
      }

      const data = await response.json()
      s.stop('Folders retrieved')

      if (data.folders.length === 0) {
        outro(`⚠️  No folders found in your ${provider} account.`)
        return
      }

      const selectedFolders = await multiselect({
        message: `Select folders to sync from ${provider}:`,
        options: data.folders.map((folder: any) => ({
          label: folder.name,
          value: folder.id,
        })),
        required: false,
      })

      if (isCancel(selectedFolders) || selectedFolders.length === 0) {
        outro('Cancelled')
        return
      }

      s.start(`Adding ${selectedFolders.length} folder(s) to sync list...`)

      for (const folderId of selectedFolders) {
        const folder = data.folders.find((f: any) => f.id === folderId)
        if (!folder) continue

        await api.post('/integrations/sync-folders', {
          folder_id: folder.id,
          folder_name: folder.name,
          provider,
          sync_subfolders: true,
        })
      }

      s.stop(`Added ${selectedFolders.length} folder(s)`)
      outro(
        `✨ Successfully added ${selectedFolders.length} folder(s) to sync!\n\n` +
          pc.dim('Run `aelira integrations sync` to start syncing files.')
      )
    } catch (error: any) {
      s.stop('Failed to select folders')
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

}
