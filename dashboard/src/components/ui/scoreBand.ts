export type ScoreBand = 'success' | 'warning' | 'danger' | 'neutral';

export function bandForScore(score: number | null): ScoreBand {
  if (score === null) return 'neutral';
  if (score >= 90) return 'success';
  if (score >= 70) return 'warning';
  return 'danger';
}
