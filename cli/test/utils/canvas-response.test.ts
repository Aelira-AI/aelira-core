import { expect } from 'chai'

import { buildScanStatusQuery, extractFileIds } from '../../src/utils/canvas.js'

/**
 * Contract tests for the canvas response helpers.
 *
 * Every payload below is copied from the backend model rather than invented,
 * which is the whole point: C1 and C2 were wrong-key bugs that three code-reading
 * reviews missed, because reading TypeScript cannot reveal a contract defined in
 * Python in another directory. Pinning the shape here makes the next drift a
 * failing test instead of a silent no-op.
 */
describe('canvas response helpers', () => {
  describe('extractFileIds', () => {
    it('reads ids from the jobs list of a real bulk-scan response', () => {
      // CanvasBulkScanResponse — backend/src/api/canvas_scan_routes.py:88-93
      const response = {
        jobs: [
          { file_id: '555', file_name: 'syllabus.pdf', job_id: 'job-1' },
          { file_id: '556', file_name: 'week1.pptx', job_id: 'job-2' },
        ],
        skipped: 1,
        total: 2,
      }

      expect(extractFileIds(response)).to.deep.equal(['555', '556'])
    })

    it('ignores a flat file_ids key, which the API does not return', () => {
      expect(extractFileIds({ file_ids: ['555', '556'] })).to.deep.equal([])
    })

    it('returns an empty list for a course with no scannable files', () => {
      expect(extractFileIds({ jobs: [], skipped: 0, total: 0 })).to.deep.equal([])
    })

    it('survives a missing or malformed payload without throwing', () => {
      expect(extractFileIds({})).to.deep.equal([])
      expect(extractFileIds(null)).to.deep.equal([])
      expect(extractFileIds({ jobs: 'not-a-list' })).to.deep.equal([])
    })

    it('drops jobs with no usable file id rather than emitting a blank', () => {
      const response = { jobs: [{ file_id: '555' }, { job_id: 'job-2' }, { file_id: '' }] }
      expect(extractFileIds(response)).to.deep.equal(['555'])
    })
  })

  describe('buildScanStatusQuery', () => {
    it('joins ids into one comma-separated file_ids value', () => {
      // The endpoint declares `file_ids: str = Query(...)` and splits on comma
      // (canvas_scan_routes.py:478, 495) — one string, not a repeated param.
      expect(buildScanStatusQuery(['555', '556', '557'])).to.deep.equal({
        file_ids: '555,556,557',
      })
    })

    it('produces a single query parameter, not one per file', () => {
      const query = buildScanStatusQuery(['555', '556'])
      expect(Object.keys(query)).to.deep.equal(['file_ids'])

      // Serialized the way ApiClient will send it.
      const serialized = new URLSearchParams(query).toString()
      expect(serialized).to.equal('file_ids=555%2C556')
      expect(serialized).to.not.contain('file_ids=555&file_ids=556')
    })

    it('handles a single file id', () => {
      expect(buildScanStatusQuery(['555'])).to.deep.equal({ file_ids: '555' })
    })
  })
})
