import ReportEvidence from './evidence.js'

/** @deprecated Use `aelira report evidence`. */
export default class ReportCertificate extends ReportEvidence {
  static description = 'Deprecated alias for `aelira report evidence`'

  static examples = [
    '<%= config.bin %> <%= command.id %> dept-123 --output evidence-report.pdf',
  ]

  async run(): Promise<void> {
    this.warn(
      '`aelira report certificate` is deprecated; use `aelira report evidence`. The downloaded PDF is an accessibility evidence report, not a certificate.',
    )
    await super.run()
  }
}
