import { intro, isCancel, outro, password, select, spinner, text } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as os from 'node:os'
import pc from 'picocolors'

import { ApiClient, ApiConnectionError, ApiError } from '../utils/api-client.js'
import { getApiKey, getApiUrl, initializeConfig, setConfigValue } from '../utils/config.js'

export default class Auth extends Command {
  static args = {
    action: Args.string({
      default: 'login',
      description: 'Action to perform (login or logout)',
      options: ['login', 'logout'],
    }),
  }
  static description = 'Authenticate the Aelira CLI with your account'
  static examples = [
    '<%= config.bin %> <%= command.id %> login',
    '<%= config.bin %> <%= command.id %> logout',
  ]
  static flags = {
    'api-url': Flags.string({
      description: 'Aelira API URL',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(Auth)

    // Ensure config exists
    await initializeConfig()

    switch (args.action) {
      case 'login': {
        await this.handleLogin({ 'api-url': await getApiUrl(flags['api-url']) })
        break
      }

      case 'logout': {
        await this.handleLogout()
        break
      }

      default: {
        this.error(`Unknown action: ${args.action}`)
      }
    }
  }

  private async handleLogin(flags: { 'api-url': string }): Promise<void> {
    intro('Aelira CLI - Authentication')

    const method = await select({
      message: 'How would you like to authenticate?',
      options: [
        { label: 'Email magic link (institutional email)', value: 'email' },
        { label: 'Browser (open dashboard to create API key)', value: 'browser' },
        { label: 'Paste an existing API key', value: 'key' },
      ],
    })

    if (isCancel(method)) {
      outro('Authentication cancelled.')
      return
    }

    switch (method) {
      case 'browser': {
        await this.loginWithBrowser(flags)
        break
      }

      case 'email': {
        await this.loginWithEmail(flags)
        break
      }

      case 'key': {
        await this.loginWithKey(flags)
        break
      }
    }
  }

  private async loginWithEmail(flags: { 'api-url': string }): Promise<void> {
    const email = await text({
      message: 'Enter your institutional email address',
      placeholder: 'you@university.edu',
    })

    if (isCancel(email)) {
      outro('Authentication cancelled.')
      return
    }

    const api = new ApiClient({ apiUrl: flags['api-url'] })

    // Request magic link
    const s = spinner()
    s.start('Sending login code...')

    try {
      await api.post('/auth/magic-link/request', { email })
      s.stop('Login code sent!')
    } catch (error: unknown) {
      s.stop('Failed to send login code.')
      if (error instanceof ApiError) {
        if (error.status === 422) {
          outro(pc.red('Only institutional email addresses are accepted (.edu, .ac.uk, etc.)'))
          return
        }

        if (error.status === 429) {
          outro(pc.red('Too many login attempts. Try again later.'))
          return
        }
      }

      if (error instanceof ApiConnectionError) {
        outro(pc.red('Cannot reach the API. Check your --api-url.'))
        return
      }

      throw error
    }

    this.log(pc.dim('\nCheck your email for a login code.\n'))

    // Prompt for token
    const token = await text({
      message: 'Paste the login code from your email',
    })

    if (isCancel(token)) {
      outro('Authentication cancelled.')
      return
    }

    // Verify token
    s.start('Verifying login code...')

    let cookieValue: string
    try {
      const response = await fetch(`${flags['api-url']}/auth/magic-link/verify`, {
        body: JSON.stringify({ email, token }),
        headers: { 'Content-Type': 'application/json' },
        method: 'POST',
      })

      if (!response.ok) {
        s.stop('Verification failed.')
        if (response.status === 400) {
          outro(pc.red('Invalid or expired token. Please request a new one.'))
          return
        }

        outro(pc.red(`Verification failed with status ${response.status}.`))
        return
      }

      // Extract session cookie
      const cookies = response.headers.getSetCookie()
      const accessCookie = cookies.find(c => c.startsWith('aelira_access='))
      if (!accessCookie) {
        s.stop('Verification failed.')
        outro(pc.red('No session cookie received. Please try again.'))
        return
      }

      cookieValue = accessCookie.split('=')[1].split(';')[0]
      s.stop('Login code verified!')
    } catch (error: unknown) {
      s.stop('Verification failed.')
      if (error instanceof TypeError || (error instanceof Error && error.message.includes('fetch'))) {
        outro(pc.red('Cannot reach the API. Check your --api-url.'))
        return
      }

      throw error
    }

    // Create API key using session cookie
    s.start('Creating API key...')

    try {
      const keyResponse = await fetch(`${flags['api-url']}/auth/keys`, {
        body: JSON.stringify({ name: `CLI — ${os.hostname()}` }),
        headers: {
          'Content-Type': 'application/json',
          'Cookie': `aelira_access=${cookieValue}`,
        },
        method: 'POST',
      })

      if (!keyResponse.ok) {
        s.stop('Failed to create API key.')
        outro(pc.red(`Failed to create API key (status ${keyResponse.status}).`))
        return
      }

      const keyData = await keyResponse.json() as { full_key: string }
      const fullKey = keyData.full_key

      // Store the key
      await setConfigValue('apiKey', fullKey)
      s.stop('API key created and stored!')

      // Validate
      const validateApi = new ApiClient({ apiKey: fullKey, apiUrl: flags['api-url'] })
      await validateApi.get('/auth/validate')

      outro(pc.green('Authenticated successfully! You can now use Aelira CLI.'))
    } catch (error: unknown) {
      s.stop('Failed to create API key.')
      if (error instanceof ApiError && error.status === 401) {
        outro(pc.red('Authentication failed. Please try again.'))
        return
      }

      if (error instanceof ApiConnectionError) {
        outro(pc.red('Cannot reach the API. Check your --api-url.'))
        return
      }

      throw error
    }
  }

  private async loginWithBrowser(flags: { 'api-url': string }): Promise<void> {
    const apiUrl = flags['api-url']
    let dashboardUrl: string
    if (apiUrl.includes('localhost') || apiUrl.includes('127.0.0.1')) {
      dashboardUrl = 'http://localhost:3000/settings/api-keys'
    } else {
      dashboardUrl = apiUrl.replace('api.', 'dashboard.') + '/settings/api-keys'
    }

    const s = spinner()
    s.start('Opening browser...')

    try {
      const openModule = await import('open')
      await openModule.default(dashboardUrl)
      s.stop('Browser opened!')
    } catch {
      s.stop(`Could not open browser. Visit: ${dashboardUrl}`)
    }

    this.log(pc.dim('\nCreate an API key in the dashboard, then paste it here.\n'))

    const key = await password({
      message: 'Paste your API key',
    })

    if (isCancel(key)) {
      outro('Authentication cancelled.')
      return
    }

    await this.validateAndStoreKey(key, flags)
  }

  private async loginWithKey(flags: { 'api-url': string }): Promise<void> {
    const key = await password({
      message: 'Paste your API key',
    })

    if (isCancel(key)) {
      outro('Authentication cancelled.')
      return
    }

    await this.validateAndStoreKey(key, flags)
  }

  private async validateAndStoreKey(key: string, flags: { 'api-url': string }): Promise<void> {
    const s = spinner()
    s.start('Validating API key...')

    try {
      const api = new ApiClient({ apiKey: key, apiUrl: flags['api-url'] })
      await api.get('/auth/validate')
      s.stop('API key validated!')

      await setConfigValue('apiKey', key)
      outro(pc.green('API key validated and stored. You can now use Aelira CLI.'))
    } catch (error: unknown) {
      s.stop('Validation failed.')
      if (error instanceof ApiError && error.status === 401) {
        outro(pc.red('Invalid API key.'))
        return
      }

      if (error instanceof ApiConnectionError) {
        outro(pc.red('Cannot reach the API. Check your --api-url.'))
        return
      }

      throw error
    }
  }

  private async handleLogout(): Promise<void> {
    const key = await getApiKey()
    if (!key) {
      outro('Not currently logged in.')
      return
    }

    await setConfigValue('apiKey', '')
    outro('Logged out. API key removed from config.')
  }
}
