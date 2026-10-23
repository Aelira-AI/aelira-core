import { expect } from 'chai'

import { formatFileSize, summarizeScanDetail } from '../../src/utils/scan-detail.js'

describe('scan detail helpers', () => {
  describe('summarizeScanDetail', () => {
    it('reads nothing from the top level, where the route only puts success', () => {
      const summary = summarizeScanDetail({ compliance_score: 90, filename: 'x.pdf', issues: { critical: 1 }, success: true })
      expect(summary).to.deep.equal({
        complianceScore: null,
        fileName: null,
        fileSizeBytes: null,
        issueCounts: null,
        issues: [],
        ocrUsed: false,
        pages: null,
        scanId: null,
      })
    })

    it('handles a scan whose result is still null', () => {
      const summary = summarizeScanDetail({ scan: { file_name: 'a.pdf', pages: 2, result: null, scan_id: 's1' }, success: true })
      expect(summary.fileName).to.equal('a.pdf')
      expect(summary.pages).to.equal(2)
      expect(summary.scanId).to.equal('s1')
      expect(summary.complianceScore).to.equal(null)
      expect(summary.issueCounts).to.equal(null)
    })

    it('does not report counts when the API cannot attribute a severity', () => {
      const summary = summarizeScanDetail({
        scan: { result: { summary: { critical: null, high: null, low: null, medium: null, total: null } } },
      })
      expect(summary.issueCounts).to.equal(null)
    })
  })

  describe('formatFileSize', () => {
    it('shows small files in KB rather than 0.00 MB', () => {
      expect(formatFileSize(4473)).to.equal('4.4 KB')
    })

    it('shows files of 1 MB or more in MB', () => {
      expect(formatFileSize(2_621_440)).to.equal('2.50 MB')
    })

    it('shows N/A when the size is unknown', () => {
      expect(formatFileSize(null)).to.equal('N/A')
    })
  })
})
