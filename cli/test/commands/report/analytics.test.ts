import { expect } from 'chai'

import ReportAnalytics from '../../../src/commands/report/analytics.js'

function renderTrend(points: Array<{ avg_compliance_score: number | null }>): string {
  const lines: string[] = []
  const command = Object.create(ReportAnalytics.prototype) as ReportAnalytics
  command.log = (line = '') => { lines.push(line) }
  ;(command as any).renderHistoricalTrend(points)
  return lines.join('\n')
}

describe('report analytics nullable historical trend', () => {
  it('keeps an all-unknown series unassessed without a synthetic zero', () => {
    const output = renderTrend([
      { avg_compliance_score: null },
      { avg_compliance_score: null },
    ])
    expect(output).to.include('Score: Unassessed')
    expect(output).not.to.include('0.0/100')
    expect(output).not.to.include('Change:')
  })

  it('compares only assessed points in a mixed series', () => {
    const output = renderTrend([
      { avg_compliance_score: null },
      { avg_compliance_score: 82 },
      { avg_compliance_score: null },
      { avg_compliance_score: 88 },
      { avg_compliance_score: null },
    ])
    expect(output).to.include('First Assessed Score: 82.0/100')
    expect(output).to.include('Latest Assessed Score: 88.0/100')
    expect(output).to.include('Change: ↑ 6.0 points')
  })

  it('does not invent a change from one assessed point', () => {
    const output = renderTrend([
      { avg_compliance_score: null },
      { avg_compliance_score: 0 },
      { avg_compliance_score: null },
    ])
    expect(output).to.include('Latest Assessed Score: 0.0/100')
    expect(output).to.include('Change: Unavailable')
  })
})
