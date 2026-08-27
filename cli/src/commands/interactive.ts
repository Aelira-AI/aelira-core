import { cancel, intro, isCancel, outro, select, spinner } from '@clack/prompts'
import { Command } from '@oclif/core'
import pc from 'picocolors'

export default class AeliraInteractive extends Command {
  static description = 'Launch Aelira interactive CLI'
static examples = ['<%= config.bin %>']

  async run(): Promise<void> {
    console.clear()

    // Get version from package.json via OCLIF config
    const {version} = this.config

    // ASCII Art Logo
    const logo = `
${pc.cyan('   █████╗ ███████╗██╗     ██╗██████╗  █████╗ ')}
${pc.cyan('  ██╔══██╗██╔════╝██║     ██║██╔══██╗██╔══██╗')}
${pc.cyan('  ███████║█████╗  ██║     ██║██████╔╝███████║')}
${pc.cyan('  ██╔══██║██╔══╝  ██║     ██║██╔══██╗██╔══██║')}
${pc.cyan('  ██║  ██║███████╗███████╗██║██║  ██║██║  ██║')}
${pc.cyan('  ╚═╝  ╚═╝╚══════╝╚══════╝╚═╝╚═╝  ╚═╝╚═╝  ╚═╝')}

${pc.dim('  AI-Powered Accessibility Testing for WCAG 2.1')}
${pc.dim(`  Version ${version} - Higher Education MVP`)}
`

    console.log(logo)

    intro(pc.cyan('Welcome to Aelira CLI'))

    await this.showMainMenu()
  }

  private async handleCodeScan(): Promise<void> {
    const path = await this.promptForInput('Enter file or directory path:')
    if (!path) return

    const s = spinner()
    s.start('Scanning source code...')

    try {
      await this.config.runCommand('scan', ['code', path])
      s.stop('Scan complete!')
    } catch (error: any) {
      s.stop('Scan failed')
      this.error(error)
    }
  }

  private async handleEvidenceReport(): Promise<void> {
    const deptId = await this.promptForInput('Enter department ID (press Enter for default):')

    const s = spinner()
    s.start('Generating accessibility evidence report...')

    try {
      await (deptId ? this.config.runCommand('report', ['evidence', deptId]) : this.config.runCommand('report', ['evidence']));

      s.stop('Evidence report generated!')
    } catch (error: any) {
      s.stop('Report generation failed')
      this.error(error)
    }
  }

  private async handleDocumentScan(): Promise<void> {
    const docType = await select({
      message: 'Choose document type:',
      options: [
        { label: '📕 PDF Documents', value: 'pdf' },
        { label: '📊 PowerPoint Presentations', value: 'ppt' },
        { label: '🔬 LaTeX Equations', value: 'latex' },
        { label: '← Back to Main Menu', value: 'back' },
      ],
    })

    if (isCancel(docType) || docType === 'back') {
      return
    }

    const path = await this.promptForInput('Enter file or directory path:')
    if (!path) return

    const s = spinner()
    s.start(`Scanning ${docType.toUpperCase()} files...`)

    try {
      await this.config.runCommand('scan', [docType as string, path])
      s.stop('Scan complete!')
    } catch (error: any) {
      s.stop('Scan failed')
      this.error(error)
    }
  }

  private async handleMediaScan(): Promise<void> {
    const mediaType = await select({
      message: 'Choose media type:',
      options: [
        { label: '🖼️  Images (AI Alt Text)', value: 'image' },
        { label: '🎬 Videos/Audio (Transcription)', value: 'video' },
        { label: '← Back to Main Menu', value: 'back' },
      ],
    })

    if (isCancel(mediaType) || mediaType === 'back') {
      return
    }

    const path = await this.promptForInput('Enter file or directory path:')
    if (!path) return

    const s = spinner()
    s.start(`Processing ${mediaType === 'image' ? 'images' : 'videos'}...`)

    try {
      await this.config.runCommand('scan', [mediaType as string, path])
      s.stop('Processing complete!')
    } catch (error: any) {
      s.stop('Processing failed')
      this.error(error)
    }
  }

  private async handleProfileSettings(): Promise<void> {
    const profileAction = await select({
      message: 'Profile Management:',
      options: [
        { label: '📋 List All Profiles', value: 'list' },
        { label: '➕ Create New Profile', value: 'create' },
        { label: '🔄 Switch Profile', value: 'use' },
        { label: '🗑️  Delete Profile', value: 'delete' },
        { label: '← Back', value: 'back' },
      ],
    })

    if (isCancel(profileAction) || profileAction === 'back') {
      return
    }

    try {
      switch (profileAction) {
        case 'create': {
          const name = await this.promptForInput('Enter profile name:')
          if (name) {
            const apiUrl = await this.promptForInput('Enter API URL for this profile:')
            await this.config.runCommand('config', ['profile', 'create', name, '--api-url', apiUrl || 'http://localhost:8000'])
          }

          break
        }

        case 'delete': {
          const name = await this.promptForInput('Enter profile name to delete:')
          if (name) {
            await this.config.runCommand('config', ['profile', 'delete', name])
          }

          break
        }

        case 'list': {
          await this.config.runCommand('config', ['profile', 'list'])
          await this.promptForInput('Press Enter to continue...')
          break
        }

        case 'use': {
          const name = await this.promptForInput('Enter profile name to switch to:')
          if (name) {
            await this.config.runCommand('config', ['profile', 'use', name])
          }

          break
        }
      }
    } catch (error: any) {
      this.log(`Error: ${error.message}`)
    }
  }

  private async handleSettings(): Promise<void> {
    const settingsAction = await select({
      message: 'Settings & Configuration:',
      options: [
        { label: '📋 Show Current Configuration', value: 'show' },
        { label: '🔧 Run Setup Wizard', value: 'init' },
        { label: '🔗 Set API URL', value: 'set-api-url' },
        { label: '🔑 Set API Key', value: 'set-api-key' },
        { label: '🏢 Set Department', value: 'set-department' },
        { label: '✅ Test API Connection', value: 'validate' },
        { label: '👥 Manage Profiles', value: 'profiles' },
        { label: '← Back to Main Menu', value: 'back' },
      ],
    })

    if (isCancel(settingsAction) || settingsAction === 'back') {
      return
    }

    const s = spinner()

    try {
      switch (settingsAction) {
        case 'init': {
          await this.config.runCommand('config', ['init'])
          break
        }

        case 'profiles': {
          await this.handleProfileSettings()
          break
        }

        case 'set-api-key': {
          const key = await this.promptForInput('Enter your API key:')
          if (key) {
            s.start('Saving API key...')
            await this.config.runCommand('config', ['set', 'api-key', key])
            s.stop('API key saved!')
          }

          break
        }

        case 'set-api-url': {
          const url = await this.promptForInput('Enter API URL (e.g., http://localhost:8000):')
          if (url) {
            s.start('Saving API URL...')
            await this.config.runCommand('config', ['set', 'api-url', url])
            s.stop('API URL saved!')
          }

          break
        }

        case 'set-department': {
          const dept = await this.promptForInput('Enter department ID:')
          if (dept) {
            s.start('Saving department...')
            await this.config.runCommand('config', ['set', 'department', dept])
            s.stop('Department saved!')
          }

          break
        }

        case 'show': {
          await this.config.runCommand('config', ['show'])
          await this.promptForInput('Press Enter to continue...')
          break
        }

        case 'validate': {
          await this.config.runCommand('config', ['validate'])
          await this.promptForInput('Press Enter to continue...')
          break
        }
      }
    } catch (error: any) {
      s.stop('Operation failed')
      this.log(`Error: ${error.message}`)
    }
  }

  private async handleWebsiteScan(): Promise<void> {
    const scanType = await select({
      message: 'Choose website scan type:',
      options: [
        { label: '⚡ Basic Scan (Fast, axe-core only)', value: 'basic' },
        { label: '🤖 AI-Enhanced Scan (Classification + Fixes)', value: 'ai' },
        { label: '← Back to Main Menu', value: 'back' },
      ],
    })

    if (isCancel(scanType) || scanType === 'back') {
      return
    }

    const url = await this.promptForInput('Enter URL to scan (e.g., https://example.com):')
    if (!url) return

    const s = spinner()
    s.start('Scanning website...')

    try {
      if (scanType === 'basic') {
        // Run basic scan
        await this.config.runCommand('scan', [url])
      } else {
        // Run AI-enhanced scan
        await this.config.runCommand('analyze', [url])
      }

      s.stop('Scan complete!')
    } catch (error: any) {
      s.stop('Scan failed')
      this.error(error)
    }
  }

  private async promptForInput(message: string): Promise<string | undefined> {
    const prompts = await import('@clack/prompts')
    const result = await prompts.text({ message })

    if (isCancel(result)) {
      cancel('Operation cancelled')
      return undefined
    }

    return result as string
  }

  private async showHelp(): Promise<void> {
    console.log(`
${pc.cyan('📚 Aelira CLI Documentation')}

${pc.bold('Website Scanning:')}
  aelira scan <url>              Basic website scan
  aelira analyze <url>           AI-enhanced scan with fixes

${pc.bold('Document Scanning:')}
  aelira scan pdf <file>         PDF accessibility scan
  aelira scan ppt <file>         PowerPoint scan
  aelira scan latex <file>       LaTeX to MathML conversion

${pc.bold('Media Processing:')}
  aelira scan image <file>       AI alt text generation
  aelira scan video <file>       Video transcription

${pc.bold('Code Scanning:')}
  aelira scan code <file>        Source code accessibility

${pc.bold('Evidence reporting:')}
  aelira report evidence         Download accessibility evidence report
  aelira report compliance       Deprecated scan-statistics compatibility view

${pc.bold('Configuration:')}
  aelira config show             Show current configuration
  aelira config init             Run setup wizard
  aelira config set <key> <val>  Set a config value
  aelira config validate         Test API connection
  aelira config profile list     List all profiles

${pc.bold('Options:')}
  --format json                  Output as JSON
  --output <file>                Save to file
  --timer                        Show performance metrics

${pc.dim('For more info: https://github.com/Aelira-AI/aelira-core/tree/main/cli/docs')}
`)

    await this.promptForInput('Press Enter to continue...')
  }

  private async showMainMenu(): Promise<void> {
    while (true) {
      const action = await select({
        message: 'What would you like to do?',
        options: [
          { label: '🌐 Scan Website', value: 'scan_website' },
          { label: '📄 Scan Documents (PDF, PPT, LaTeX)', value: 'scan_documents' },
          { label: '🎥 Scan Media (Images, Videos)', value: 'scan_media' },
          { label: '💻 Scan Source Code', value: 'scan_code' },
          { label: '📊 Download Accessibility Evidence Report', value: 'evidence_report' },
          { label: '⚙️  Settings & Configuration', value: 'settings' },
          { label: '❓ Help & Documentation', value: 'help' },
          { label: '👋 Exit', value: 'exit' },
        ],
      })

      if (isCancel(action)) {
        cancel('Operation cancelled')
        process.exit(0)
      }

      switch (action) {
        case 'evidence_report': {
          await this.handleEvidenceReport()
          break
        }

        case 'help': {
          await this.showHelp()
          break
        }

        case 'scan_code': {
          await this.handleCodeScan()
          break
        }

        case 'scan_documents': {
          await this.handleDocumentScan()
          break
        }

        case 'scan_media': {
          await this.handleMediaScan()
          break
        }

        case 'scan_website': {
          await this.handleWebsiteScan()
          break
        }

        case 'settings': {
          await this.handleSettings()
          break
        }

        case 'exit': {
          outro(pc.cyan('Thank you for using Aelira! 💜'))
          process.exit(0)
        }
      }
    }
  }
}
