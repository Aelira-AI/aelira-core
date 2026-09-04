import { confirm, intro, note, outro, select, spinner, text } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import pc from 'picocolors'

import { ApiClient } from '../../utils/api-client.js'
import { getApiUrl } from '../../utils/config.js'

interface LmsProvider {
  bodyField: string
  label: string
  placeholder: string
}

/** Providers that need an instance URL before they can be connected. */
const LMS_PROVIDERS: Record<string, LmsProvider | undefined> = {
  blackboard: {
    bodyField: 'blackboard_instance_url',
    label: 'Blackboard',
    placeholder: 'https://blackboard.university.edu',
  },
  brightspace: {
    bodyField: 'brightspace_instance_url',
    label: 'Brightspace',
    placeholder: 'https://university.brightspace.com',
  },
  canvas: {
    bodyField: 'canvas_instance_url',
    label: 'Canvas',
    placeholder: 'https://canvas.university.edu',
  },
  moodle: {
    bodyField: 'moodle_instance_url',
    label: 'Moodle',
    placeholder: 'https://moodle.university.edu',
  },
}

const PROVIDER_OPTIONS = [
  {
    label: '📁 Google Workspace (Drive, Docs, Slides, Sheets)',
    value: 'google',
  },
  {
    label: '📁 Microsoft 365 (OneDrive, SharePoint)',
    value: 'microsoft',
  },
  {
    label: '📚 Canvas LMS',
    value: 'canvas',
  },
  {
    label: '📚 Blackboard Learn',
    value: 'blackboard',
  },
  {
    label: '🌏 Moodle LMS (World\'s most-used LMS)',
    value: 'moodle',
  },
  {
    label: '🎓 D2L Brightspace (Community colleges)',
    value: 'brightspace',
  },
]

export default class IntegrationsConnect extends Command {
  static args = {
    provider: Args.string({
      description: 'Cloud provider (google, microsoft, canvas, blackboard, moodle, brightspace)',
      options: ['google', 'microsoft', 'canvas', 'blackboard', 'moodle', 'brightspace'],
    }),
  }
static description = 'Connect cloud storage or LMS provider'
static examples = [
    '<%= config.bin %> <%= command.id %> google',
    '<%= config.bin %> <%= command.id %> microsoft',
    '<%= config.bin %> <%= command.id %> canvas --instance-url https://canvas.university.edu',
    '<%= config.bin %> <%= command.id %> moodle --instance-url https://moodle.university.edu',
    '<%= config.bin %> <%= command.id %> brightspace --instance-url https://university.brightspace.com',
  ]
static flags = {
    'api-key': Flags.string({
      description: 'API key for authentication (optional in development)',
    }),
    'api-url': Flags.string({
      description: 'Aelira API URL',
    }),
    'instance-url': Flags.string({
      description: 'LMS instance URL (required for Canvas/Blackboard/Moodle)',
    }),
  }

  public async run(): Promise<void> {
    const { args, flags } = await this.parse(IntegrationsConnect)

    intro('Aelira CLI - Connect Cloud Provider')

    let {provider} = args

    // Interactive provider selection if not provided
    if (!provider) {
      provider = await this.promptForProvider()
      if (!provider) {
        outro('Connection cancelled')
        return
      }
    }

    // For Canvas/Blackboard/Moodle/Brightspace, get instance URL
    const lms = LMS_PROVIDERS[provider]
    let instanceUrl: string | undefined = flags['instance-url']
    if (lms && !instanceUrl) {
      instanceUrl = await this.promptForInstanceUrl(lms)
      if (!instanceUrl) {
        outro('Connection cancelled')
        return
      }
    }

    const s = spinner()
    s.start(`Connecting to ${provider}...`)

    try {
      const apiUrl = await getApiUrl(flags['api-url'])
      const api = new ApiClient({ apiKey: flags['api-key'], apiUrl })

      const body: any = {
        redirect_uri: `${apiUrl}/${provider}/callback`,
      }

      if (lms && instanceUrl) {
        body[lms.bodyField] = instanceUrl
      }

      const response = await api.post(`/${provider}/connect`, body)
      const data = await response.json()
      s.stop('OAuth URL generated')

      const authUrl = data.auth_url || data.authorization_url

      if (!authUrl) {
        throw new Error('No authorization URL returned from API')
      }

      note(
        `${pc.bold('Authorization URL:')}\n\n${pc.cyan(authUrl)}\n\n` +
          `${pc.dim('1. Open this URL in your browser')}\n` +
          `${pc.dim('2. Sign in to your ' + provider + ' account')}\n` +
          `${pc.dim('3. Authorize Aelira to access your files')}\n` +
          `${pc.dim('4. You will be redirected back to the dashboard')}`,
        'Next Steps'
      )

      const shouldOpen = await confirm({
        message: 'Would you like to open the authorization URL in your browser now?',
      })

      if (shouldOpen === true) {
        const { default: open } = await import('open')
        await open(authUrl)
        outro(`✨ Browser opened! Complete the authorization flow to continue.`)
      } else {
        outro(`✨ Copy the URL above and open it in your browser to continue.`)
      }
    } catch (error: any) {
      s.stop('Connection failed')
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  /** Returns undefined if the prompt was cancelled. */
  private async promptForInstanceUrl(lms: LmsProvider): Promise<string | undefined> {
    const urlInput = await text({
      message: `Enter your ${lms.label} instance URL:`,
      placeholder: lms.placeholder,
      validate(value: string | undefined) {
        if (!value?.startsWith('http')) {
          return 'URL must start with http:// or https://'
        }
      },
    })

    return typeof urlInput === 'symbol' ? undefined : (urlInput as string)
  }

  /** Returns undefined if the prompt was cancelled. */
  private async promptForProvider(): Promise<string | undefined> {
    const selection = await select({
      message: 'Which provider would you like to connect?',
      options: PROVIDER_OPTIONS,
    })

    return typeof selection === 'symbol' ? undefined : (selection as string)
  }
}
