import React from 'react';

interface ConfidenceBadgeProps {
  confidence: number;
  size?: 'sm' | 'md';
}

export function ConfidenceBadge({ confidence, size = 'md' }: ConfidenceBadgeProps): React.ReactElement {
  const pct = Math.round(confidence * 100);
  const color =
    confidence >= 0.85 ? 'text-green-400' :
    confidence >= 0.7 ? 'text-yellow-400' :
    confidence >= 0.5 ? 'text-orange-400' :
    'text-red-400';

  const dotSize = size === 'sm' ? 'w-2 h-2' : 'w-2.5 h-2.5';
  const textSize = size === 'sm' ? 'text-xs' : 'text-sm';

  return (
    <span className={`inline-flex items-center gap-1.5 ${textSize}`}>
      <span className={`${dotSize} rounded-full ${color} bg-current`} aria-hidden="true" />
      <span className={color}>{pct}%</span>
    </span>
  );
}
