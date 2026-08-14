import { confirm, intro, isCancel, outro, select, spinner, text } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import pc from 'picocolors'

import {
  configExists,
  createProfile,
  deleteProfile,
  getActiveProfile,
  getApiKey,
  getApiUrl,
  getConfigPath,
  getDepartment,
  initializeConfig,
  listProfiles,
  readConfig,
  setActiveProfile,
  setConfigValue,
  validateConnection,
} from '../utils/config.js'

export default class Config extends Command {
  static args = {
    action: Args.string({
      default: 'show',
      description: 'Action to perform (init, set, show, validate, profile)',
      options: ['init', 'set', 'show', 'validate', 'profile'],
      required: false,
    }),
    key: Args.string({
      description: 'Config key to set (api-url, api-key, department)',
      required: false,
    }),
    value: Args.string({
      description: 'Value to set',
      required: false,
    }),
  }
static description = 'Manage Aelira CLI configuration (API URL, API key, profiles)'
static examples = [
    '<%= config.bin %> <%= command.id %> init              # Interactive setup wizard',
    '<%= config.bin %> <%= command.id %> show              # Display current configuration',
    '<%= config.bin %> <%= command.id %> set api-url http://localhost:8000',
    '<%= config.bin %> <%= command.id %> set api-key sk_xxx',
    '<%= config.bin %> <%= command.id %> set department engineering',
    '<%= config.bin %> <%= command.id %> validate          # Test API connection',
    '<%= config.bin %> <%= command.id %> profile list      # List all profiles',
    '<%= config.bin %> <%= command.id %> profile create staging',
    '<%= config.bin %> <%= command.id %> profile use staging',
    '<%= config.bin %> <%= command.id %> profile delete staging',
  ]
static flags = {
    'api-key': Flags.string({
      description: 'API key for the profile',
    }),
    'api-url': Flags.string({
      description: 'API URL for the profile',
    }),
    department: Flags.string({
      description: 'Department ID for the profile',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance timing information',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(Config)
    const startTime = Date.now()

    try {
      switch (args.action) {
        case 'init': {
          await this.initConfig()
          break
        }

        case 'profile': {
          await this.handleProfile(args.key, args.value, flags)
          break
        }

        case 'set': {
          await this.setConfig(args.key, args.value)
          break
        }

        case 'show': {
          await this.showConfig()
          break
        }

        case 'validate': {
          await this.validateConfig()
          break
        }

        default: {
          throw new Error(`Unknown action: ${args.action}`)
        }
      }

      if (flags.timer) {
        this.log(`\n⏱️  Total execution time: ${Date.now() - startTime}ms`)
      }
    } catch (error: any) {
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private async handleProfile(subAction?: string, profileName?: string, flags?: any): Promise<void> {
    intro('Aelira CLI - Profile Management')

    switch (subAction) {
      case 'create': {
        if (!profileName) {
          throw new Error('Profile name required: aelira config profile create <name>')
        }

        const s = spinner()
        s.start(`Creating profile "${profileName}"...`)
        await createProfile(profileName, {
          apiKey: flags?.['api-key'],
          apiUrl: flags?.['api-url'] || 'http://localhost:8000',
          department: flags?.department,
          name: profileName,
        })
        s.stop(`Profile "${profileName}" created`)
        outro(`✨ Profile created! Use "aelira config profile use ${profileName}" to switch to it`)
        break
      }

      case 'delete': {
        if (!profileName) {
          throw new Error('Profile name required: aelira config profile delete <name>')
        }

        const s = spinner()
        s.start(`Deleting profile "${profileName}"...`)
        await deleteProfile(profileName)
        s.stop(`Profile "${profileName}" deleted`)
        outro('✨ Profile deleted!')
        break
      }

      case 'list': {
        const profiles = await listProfiles()
        this.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        this.log('  Aelira CLI Profiles')
        this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

        for (const profile of profiles) {
          const marker = profile.active ? pc.green('→ ') : '  '
          const label = profile.active ? pc.green(profile.name) : profile.name
          this.log(`${marker}${label}`)
          this.log(`    API URL: ${profile.config.apiUrl}`)
          this.log(`    API Key: ${profile.config.apiKey ? '••••••••' + profile.config.apiKey.slice(-4) : pc.dim('(not set)')}`)
          this.log(`    Department: ${profile.config.department || pc.dim('(not set)')}\n`)
        }

        this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')
        outro('💡 Tip: Use "aelira config profile use <name>" to switch profiles')
        break
      }

      case 'use': {
        if (!profileName) {
          throw new Error('Profile name required: aelira config profile use <name>')
        }

        const s = spinner()
        s.start(`Switching to profile "${profileName}"...`)
        await setActiveProfile(profileName)
        s.stop(`Switched to profile "${profileName}"`)
        outro('✨ Profile switched!')
        break
      }

      default: {
        this.log('\nAvailable profile commands:')
        this.log('  aelira config profile list              # List all profiles')
        this.log('  aelira config profile create <name>     # Create a new profile')
        this.log('  aelira config profile use <name>        # Switch to a profile')
        this.log('  aelira config profile delete <name>     # Delete a profile')
        this.log('\nOptions for create:')
        this.log('  --api-url <url>        API URL for the profile')
        this.log('  --api-key <key>        API key for the profile')
        this.log('  --department <id>      Department ID for the profile')
        outro('')
      }
    }
  }

  private async initConfig(): Promise<void> {
    intro('Aelira CLI - Configuration Setup')

    const exists = await configExists()
    if (exists) {
      const shouldOverwrite = await confirm({
        message: 'Configuration already exists. Do you want to reconfigure?',
      })
      if (isCancel(shouldOverwrite) || !shouldOverwrite) {
        outro('Configuration unchanged.')
        return
      }
    }

    // API URL
    const apiUrl = await text({
      defaultValue: 'http://localhost:8000',
      message: 'Enter the Aelira API URL:',
      placeholder: 'http://localhost:8000',
      validate(value) {
        if (!value) return 'API URL is required'
        try {
          const url = new URL(value)
          // URL must have protocol
          if (!url.protocol) return 'Invalid URL format'
        } catch {
          return 'Invalid URL format'
        }
      },
    })

    if (isCancel(apiUrl)) {
      outro('Configuration cancelled.')
      return
    }

    // API Key (optional)
    const apiKey = await text({
      message: 'Enter your API key (optional, press Enter to skip):',
      placeholder: 'sk_...',
    })

    if (isCancel(apiKey)) {
      outro('Configuration cancelled.')
      return
    }

    // Department (optional)
    const department = await text({
      defaultValue: 'default',
      message: 'Enter your department ID (optional):',
      placeholder: 'engineering',
    })

    if (isCancel(department)) {
      outro('Configuration cancelled.')
      return
    }

    // Save configuration
    const s = spinner()
    s.start('Saving configuration...')

    await initializeConfig()
    await setConfigValue('apiUrl', apiUrl as string)
    if (apiKey) {
      await setConfigValue('apiKey', apiKey as string)
    }

    if (department) {
      await setConfigValue('department', department as string)
    }

    s.stop('Configuration saved')

    // Test connection
    const shouldTest = await confirm({
      initialValue: true,
      message: 'Would you like to test the API connection?',
    })

    if (!isCancel(shouldTest) && shouldTest) {
      s.start('Testing connection...')
      const result = await validateConnection(apiUrl as string)
      if (result.success) {
        s.stop(pc.green('✓ ') + result.message)
      } else {
        s.stop(pc.yellow('⚠ ') + result.message)
        this.log('\n💡 Tip: Make sure the Aelira backend is running')
        this.log('   Run: cd backend && ./run_api.sh')
      }
    }

    this.log(`\n📁 Config saved to: ${getConfigPath()}`)
    outro('✨ Configuration complete!')
  }

  private async setConfig(key?: string, value?: string): Promise<void> {
    intro('Aelira CLI - Set Configuration')

    if (!key) {
      // Interactive mode - let user select what to set
      const choice = await select({
        message: 'What would you like to configure?',
        options: [
          { label: 'API URL', value: 'api-url' },
          { label: 'API Key', value: 'api-key' },
          { label: 'Department', value: 'department' },
        ],
      })

      if (isCancel(choice)) {
        outro('Configuration cancelled.')
        return
      }

      key = choice as string
    }

    // Map CLI keys to config keys
    const keyMap: Record<string, 'apiKey' | 'apiUrl' | 'department'> = {
      'api-key': 'apiKey',
      'api-url': 'apiUrl',
      department: 'department',
    }

    const configKey = keyMap[key]
    if (!configKey) {
      throw new Error(`Unknown config key: ${key}. Valid keys: api-url, api-key, department`)
    }

    if (!value) {
      // Prompt for value
      const currentValue = key === 'api-url'
        ? await getApiUrl()
        : key === 'api-key'
          ? await getApiKey()
          : await getDepartment()

      const newValue = await text({
        defaultValue: key === 'api-key' ? undefined : currentValue,
        message: `Enter new value for ${key}:`,
        placeholder: currentValue || `Enter ${key}`,
      })

      if (isCancel(newValue)) {
        outro('Configuration cancelled.')
        return
      }

      value = newValue as string
    }

    const s = spinner()
    s.start(`Setting ${key}...`)
    await setConfigValue(configKey, value)
    s.stop(`${key} updated`)

    this.log(`\n✅ ${key} = ${key === 'api-key' ? '••••••••' + value.slice(-4) : value}`)
    outro('✨ Configuration updated!')
  }

  private async showConfig(): Promise<void> {
    const config = await readConfig()
    const profile = await getActiveProfile()
    const envApiUrl = process.env.AELIRA_API_URL
    const envApiKey = process.env.AELIRA_API_KEY
    const envDepartment = process.env.AELIRA_DEPARTMENT

    this.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log('  Aelira CLI Configuration')
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  📁 Config file: ${getConfigPath()}`)
    this.log(`  📋 Active profile: ${pc.green(config.activeProfile)}\n`)

    this.log('  Current Settings:')
    this.log('  ─────────────────')

    // API URL
    const effectiveApiUrl = envApiUrl || profile.apiUrl
    this.log(`  API URL: ${effectiveApiUrl}`)
    if (envApiUrl) {
      this.log(`           ${pc.dim('(from AELIRA_API_URL environment variable)')}`)
    }

    // API Key
    const effectiveApiKey = envApiKey || profile.apiKey
    if (effectiveApiKey) {
      this.log(`  API Key: ••••••••${effectiveApiKey.slice(-4)}`)
      if (envApiKey) {
        this.log(`           ${pc.dim('(from AELIRA_API_KEY environment variable)')}`)
      }
    } else {
      this.log(`  API Key: ${pc.dim('(not set)')}`)
    }

    // Department
    const effectiveDepartment = envDepartment || profile.department || 'default'
    this.log(`  Department: ${effectiveDepartment}`)
    if (envDepartment) {
      this.log(`              ${pc.dim('(from AELIRA_DEPARTMENT environment variable)')}`)
    }

    this.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log('💡 Commands:')
    this.log('   aelira config init              # Run setup wizard')
    this.log('   aelira config set <key> <val>   # Set a config value')
    this.log('   aelira config validate          # Test API connection')
    this.log('   aelira config profile list      # List all profiles\n')

    this.log('📌 Environment variables override config file:')
    this.log('   AELIRA_API_URL, AELIRA_API_KEY, AELIRA_DEPARTMENT\n')
  }

  private async validateConfig(): Promise<void> {
    intro('Aelira CLI - Connection Validation')

    const s = spinner()
    s.start('Testing API connection...')

    const result = await validateConnection()

    if (result.success) {
      s.stop(pc.green('✓ ') + result.message)

      if (result.details) {
        this.log('\n  API Health Check:')
        this.log('  ─────────────────')
        this.log(`  Status: ${pc.green(result.details.status || 'healthy')}`)
        if (result.details.version) {
          this.log(`  Version: ${result.details.version}`)
        }

        if (result.details.models) {
          this.log(`  AI Models: ${result.details.models.join(', ')}`)
        }

        if (result.details.database) {
          this.log(`  Database: ${result.details.database}`)
        }
      }

      outro('✨ Connection successful!')
    } else {
      s.stop(pc.red('✗ ') + result.message)

      this.log('\n💡 Troubleshooting tips:')
      this.log('   1. Make sure the Aelira backend is running:')
      this.log('      cd backend && ./run_api.sh')
      this.log('   2. Check the API URL is correct:')
      this.log('      aelira config show')
      this.log('   3. Verify network connectivity')

      outro('❌ Connection failed')
    }
  }
}
