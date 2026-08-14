import { expect } from 'chai'

import { escapeField, formatIssuesToCsv, formatScanHistoryToCsv } from '../../src/utils/csv-formatter.js'

describe('csv-formatter', () => {
  describe('escapeField', () => {
    it('returns plain string unchanged', () => {
      expect(escapeField('hello')).to.equal('hello')
    })

    it('wraps fields containing commas in quotes', () => {
      expect(escapeField('hello, world')).to.equal('"hello, world"')
    })

    it('escapes double quotes by doubling them', () => {
      expect(escapeField('say "hi"')).to.equal('"say ""hi"""')
    })

    it('wraps fields containing newlines', () => {
      expect(escapeField('line1\nline2')).to.equal('"line1\nline2"')
    })

    it('wraps fields containing carriage returns', () => {
      expect(escapeField('line1\r\nline2')).to.equal('"line1\r\nline2"')
    })

    it('handles null and undefined', () => {
      expect(escapeField(null)).to.equal('')
      expect(escapeField()).to.equal('')
    })
  })

  describe('formatIssuesToCsv', () => {
    it('formats issues with correct headers', () => {
      const issues = [
        { message: 'Missing alt text', severity: 'serious', rule: 'image-alt', element: '<img>' },
      ]
      const csv = formatIssuesToCsv(issues, 'doc.pdf')
      const lines = csv.split('\n')
      expect(lines[0]).to.equal('file,issue,severity,rule,element')
      expect(lines[1]).to.contain('doc.pdf')
      expect(lines[1]).to.contain('Missing alt text')
      expect(lines[1]).to.contain('serious')
    })

    it('returns header only for empty issues', () => {
      const csv = formatIssuesToCsv([], 'doc.pdf')
      const lines = csv.split('\n')
      expect(lines).to.have.length(1)
      expect(lines[0]).to.equal('file,issue,severity,rule,element')
    })

    it('handles missing fields gracefully', () => {
      const issues = [{ description: 'Some issue' }]
      const csv = formatIssuesToCsv(issues, 'test.html')
      expect(csv).to.contain('Some issue')
    })

    it('normalizes web scan fields (description, impact, criterion)', () => {
      const issues = [
        { description: 'Images must have alt text', impact: 'critical', criterion: '1.1.1', html: '<img>' },
      ]
      const csv = formatIssuesToCsv(issues, 'page.html')
      expect(csv).to.contain('Images must have alt text')
      expect(csv).to.contain('critical')
      expect(csv).to.contain('1.1.1')
    })
  })

  describe('formatScanHistoryToCsv', () => {
    it('formats scan history with correct headers', () => {
      const scans = [{
        created_at: '2026-03-17T10:00:00Z',
        filename: 'doc.pdf',
        issues: [{ message: 'Issue 1', severity: 'serious', rule: 'rule-1', element: '<p>' }],
        scan_id: 'abc-123',
      }]
      const csv = formatScanHistoryToCsv(scans)
      const lines = csv.split('\n')
      expect(lines[0]).to.equal('scan_id,date,file,issue,severity,rule,element')
      expect(lines[1]).to.contain('abc-123')
      expect(lines[1]).to.contain('2026-03-17')
      expect(lines[1]).to.contain('doc.pdf')
    })

    it('handles scans with no issues', () => {
      const scans = [{ scan_id: 'abc', created_at: '2026-03-17T10:00:00Z', filename: 'doc.pdf', issues: [] }]
      const csv = formatScanHistoryToCsv(scans)
      const lines = csv.split('\n')
      expect(lines).to.have.length(1) // header only
    })
  })
})
