import { intro, outro, spinner } from '@clack/prompts'
import { Args, Command, Flags } from '@oclif/core'
import * as fs from 'node:fs/promises'

import { ApiClient, ApiError } from '../../utils/api-client.js'

export default class ReportCertificate extends Command {
  static args = {
    department_id: Args.string({
      description: 'Department ID for certificate generation',
      required: false,
    }),
  }
static description = 'Generate a professional compliance certificate (Bronze/Silver/Gold/Platinum)'
static examples = [
    '<%= config.bin %> <%= command.id %>',
    '<%= config.bin %> <%= command.id %> dept-123',
    '<%= config.bin %> <%= command.id %> dept-123 --output certificate.pdf',
    '<%= config.bin %> <%= command.id %> --check-eligibility',
  ]
static flags = {
    'api-url': Flags.string({
      default: 'http://localhost:8000',
      description: 'Aelira API URL',
    }),
    'check-eligibility': Flags.boolean({
      char: 'c',
      default: false,
      description: 'Check certificate eligibility without generating',
    }),
    output: Flags.string({
      char: 'o',
      description: 'Output file path for certificate PDF',
    }),
    timer: Flags.boolean({
      default: false,
      description: 'Show performance timing information',
    }),
  }

  async run(): Promise<void> {
    const { args, flags } = await this.parse(ReportCertificate)
    const startTime = Date.now()
    const departmentId = args.department_id || 'default'
    const api = new ApiClient({ apiUrl: flags['api-url'] })

    intro('Aelira CLI - Compliance Certificate Generator')

    try {
      await (flags['check-eligibility'] ? this.checkEligibility(departmentId, api) : this.generateCertificate(departmentId, api, flags.output));

      if (flags.timer) {
        this.log(`\n⏱️  Total execution time: ${Date.now() - startTime}ms`)
      }
    } catch (error: any) {
      outro(`❌ Error: ${error.message}`)
      this.error(error)
    }
  }

  private async checkEligibility(departmentId: string, api: ApiClient): Promise<void> {
    const s = spinner()
    s.start('Checking certificate eligibility...')

    const data = await api.getJson<any>(
      `/analytics/certificate/${departmentId}/eligibility`,
      { timeout: 30_000 },
    )

    s.stop('Eligibility check complete')

    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log('  Certificate Eligibility Status')
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

    this.log(`  Department ID: ${data.department_id}`)
    this.log(`  Compliance Score: ${data.compliance_score?.toFixed(1) || 'N/A'}/100\n`)

    if (data.eligible) {
      const levelEmoji = this.getLevelEmoji(data.certificate_level)
      this.log(`  ${levelEmoji} Eligible for: ${data.certificate_level} Certificate`)
      this.log(`  ${data.description}\n`)

      if (data.points_to_next_level > 0) {
        this.log(`  📈 ${data.points_to_next_level.toFixed(1)} points to next level\n`)
      }

      this.log('  Run without --check-eligibility to generate your certificate!')
    } else {
      this.log('  ❌ Not Eligible for Certificate')
      this.log(`  ${data.description}\n`)
      this.log(`  📈 Need ${data.points_to_next_level?.toFixed(1) || '?'} more points to qualify`)
    }

    this.log('')
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')

    outro(data.eligible ? '✨ Certificate available!' : '💪 Keep improving your compliance score!')
  }

  private async generateCertificate(
    departmentId: string,
    api: ApiClient,
    outputPath?: string
  ): Promise<void> {
    const s = spinner()
    s.start('Generating compliance certificate...')

    let response: Response
    try {
      response = await api.get(
        `/analytics/certificate/${departmentId}`,
        { headers: { Accept: 'application/pdf' }, timeout: 60_000 },
      )
    } catch (error) {
      if (error instanceof ApiError && error.status === 404 && error.body.includes('below')) {
        s.stop('Certificate generation failed')
        this.log('\n  ❌ Your compliance score is below the minimum threshold (70%)')
        this.log('  Run with --check-eligibility to see your current status\n')
        throw new Error('Not eligible for certificate')
      }

      throw error
    }

    const pdfBuffer = Buffer.from(await response.arrayBuffer())

    // Determine output filename
    const filename = outputPath || `compliance_certificate_${departmentId}_${new Date().toISOString().slice(0, 10)}.pdf`

    await fs.writeFile(filename, pdfBuffer)
    s.stop('Certificate generated successfully!')

    this.log('')
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log('  🎉 Compliance Certificate Generated!')
    this.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    this.log(`\n  📄 Saved to: ${filename}`)
    this.log('  🏆 Share this certificate to demonstrate your accessibility commitment!')
    this.log('')

    outro('✨ Certificate ready for download!')
  }

  private getLevelEmoji(level: string): string {
    switch (level?.toLowerCase()) {
      case 'bronze': {
        return '🥉'
      }

      case 'gold': {
        return '🥇'
      }

      case 'platinum': {
        return '💎'
      }

      case 'silver': {
        return '🥈'
      }

      default: {
        return '🏆'
      }
    }
  }
}
