import React, { useEffect, useState } from 'react';
import { CheckCircle, AlertTriangle, XCircle } from 'lucide-react';

// ============================================================================
// Types
// ============================================================================

interface ComplianceScoreProps {
  score: number;
}

interface ScoreColors {
  bg: string;
  text: string;
  border: string;
  stroke: string;
}

// ============================================================================
// Component
// ============================================================================

export function ComplianceScore({ score }: ComplianceScoreProps): React.ReactElement {
  const [animatedScore, setAnimatedScore] = useState<number>(0);
  // Start visible immediately (no need for effect-triggered state change)
  const [isVisible] = useState<boolean>(true);

  // Animate the score on mount
  useEffect(() => {
    const duration = 1000; // 1 second
    const steps = 60;
    const increment = score / steps;
    let current = 0;

    const timer = setInterval(() => {
      current += increment;
      if (current >= score) {
        setAnimatedScore(score);
        clearInterval(timer);
      } else {
        setAnimatedScore(Math.round(current));
      }
    }, duration / steps);

    return () => clearInterval(timer);
  }, [score]);

  const getScoreColor = (scoreValue: number): ScoreColors => {
    if (scoreValue >= 90)
      return {
        bg: 'bg-[var(--feature-success-surface)]',
        text: 'text-[var(--feature-success-content)]',
        border: 'border-[var(--feature-success-border)]',
        stroke: 'var(--feature-success-border)',
      };
    if (scoreValue >= 70)
      return {
        bg: 'bg-[var(--feature-warning-surface)]',
        text: 'text-[var(--feature-warning-content)]',
        border: 'border-[var(--feature-warning-border)]',
        stroke: 'var(--feature-warning-border)',
      };
    return {
      bg: 'bg-[var(--feature-danger-surface)]',
      text: 'text-[var(--feature-danger-content)]',
      border: 'border-[var(--feature-danger-border)]',
      stroke: 'var(--feature-danger-border)',
    };
  };

  const getScoreIcon = (scoreValue: number): React.ReactElement => {
    if (scoreValue >= 90)
      return <CheckCircle className="w-6 h-6 text-[var(--feature-success-content)]" />;
    if (scoreValue >= 70)
      return <AlertTriangle className="w-6 h-6 text-[var(--feature-warning-content)]" />;
    return <XCircle className="w-6 h-6 text-[var(--feature-danger-content)]" />;
  };

  const getScoreLabel = (scoreValue: number): string => {
    if (scoreValue >= 90) return 'Excellent';
    if (scoreValue >= 70) return 'Good';
    return 'Needs Work';
  };

  const colors = getScoreColor(score);

  // SVG circular progress calculations
  const size = 160;
  const strokeWidth = 12;
  const radius = (size - strokeWidth) / 2;
  const circumference = radius * 2 * Math.PI;
  const strokeDashoffset = circumference - (animatedScore / 100) * circumference;

  return (
    <div
      className={`card-glass border-2 ${colors.border} ${colors.bg}`}
    >
      <p className="text-sm font-medium text-secondary mb-4">Compliance Score</p>

      <div className="flex items-center justify-center">
        {/* Circular Progress Ring */}
        <div className="relative">
          <svg
            width={size}
            height={size}
            className={`transform -rotate-90 ${isVisible ? 'opacity-100' : 'opacity-0'} transition-opacity duration-300`}
          >
            {/* Background circle */}
            <circle
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke="var(--border-secondary)"
              strokeWidth={strokeWidth}
            />
            {/* Progress circle */}
            <circle
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke={colors.stroke}
              strokeWidth={strokeWidth}
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={strokeDashoffset}
              className="transition-all duration-1000 ease-out"
            />
          </svg>

          {/* Center content */}
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className={`text-4xl font-bold font-heading ${colors.text}`}>{animatedScore}</span>
            <span className="text-sm text-secondary">/100</span>
          </div>
        </div>
      </div>

      {/* Status label with icon */}
      <div className="flex items-center justify-center space-x-2 mt-4">
        {getScoreIcon(score)}
        <span className={`text-sm font-semibold ${colors.text}`}>{getScoreLabel(score)}</span>
      </div>
    </div>
  );
}
