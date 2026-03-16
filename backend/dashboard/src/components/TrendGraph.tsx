import React from 'react';
import {
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Area,
  AreaChart,
} from 'recharts';
import type { TooltipContentProps } from 'recharts/types/component/Tooltip';
import type { ValueType, NameType } from 'recharts/types/component/DefaultTooltipContent';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';

// ============================================================================
// Types
// ============================================================================

interface TrendDataPoint {
  date: string;
  score: number;
  scans: number;
}

interface TrendGraphProps {
  data: TrendDataPoint[] | null | undefined;
  loading?: boolean;
}

type TrendDirection = 'improving' | 'declining' | 'stable';

// ============================================================================
// Helper Functions
// ============================================================================

// Format date for display
const formatDate = (dateStr: string): string => {
  const date = new Date(dateStr);
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};

// ============================================================================
// Custom Tooltip Component
// ============================================================================

const CustomTooltip = ({ active, payload }: TooltipContentProps<ValueType, NameType>): React.ReactElement | null => {
  if (active && payload && payload.length) {
    const data = payload[0].payload as TrendDataPoint;
    return (
      <div className="p-3 rounded-lg shadow-lg bg-surface-primary border border-primary">
        <p className="text-sm font-medium text-primary">{formatDate(data.date)}</p>
        <p
          className={`text-lg font-bold ${
            data.score >= 90
              ? 'text-[var(--feature-success-content)]'
              : data.score >= 70
                ? 'text-[var(--feature-warning-content)]'
                : 'text-[var(--feature-danger-content)]'
          }`}
        >
          {Math.round(data.score)}/100
        </p>
        <p className="text-xs text-tertiary">
          {data.scans} scan{data.scans !== 1 ? 's' : ''}
        </p>
      </div>
    );
  }
  return null;
};

// ============================================================================
// Component
// ============================================================================

export function TrendGraph({ data, loading }: TrendGraphProps): React.ReactElement {
  if (loading) {
    return (
      <div className="card-glass">
        <h2 className="text-xl font-semibold text-primary mb-4">Compliance Trend</h2>
        <div className="flex items-center justify-center h-64">
          <div className="text-tertiary">Loading trend data...</div>
        </div>
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div className="card-glass">
        <h2 className="text-xl font-semibold text-primary mb-4">Compliance Trend</h2>
        <div className="flex items-center justify-center h-64">
          <div className="text-tertiary">
            Not enough data to show trend. Upload more scans to see progress over time.
          </div>
        </div>
      </div>
    );
  }

  // Calculate trend direction
  const firstScore = data[0]?.score || 0;
  const lastScore = data[data.length - 1]?.score || 0;
  const scoreDiff = lastScore - firstScore;
  const trendDirection: TrendDirection =
    scoreDiff > 5 ? 'improving' : scoreDiff < -5 ? 'declining' : 'stable';

  return (
    <div className="card-glass">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-primary">Compliance Trend (30 Days)</h2>

        {/* Trend Indicator */}
        <div className="flex items-center space-x-2">
          {trendDirection === 'improving' && (
            <>
              <TrendingUp className="w-5 h-5 text-[var(--feature-success-content)]" aria-hidden="true" />
              <span className="text-sm font-medium text-[var(--feature-success-content)]">
                Improving (+{Math.round(scoreDiff)})
              </span>
            </>
          )}
          {trendDirection === 'declining' && (
            <>
              <TrendingDown className="w-5 h-5 text-[var(--feature-danger-content)]" aria-hidden="true" />
              <span className="text-sm font-medium text-[var(--feature-danger-content)]">
                Declining ({Math.round(scoreDiff)})
              </span>
            </>
          )}
          {trendDirection === 'stable' && (
            <>
              <Minus className="w-5 h-5 text-tertiary" aria-hidden="true" />
              <span className="text-sm font-medium text-secondary">
                Stable ({Math.round(scoreDiff) >= 0 ? '+' : ''}
                {Math.round(scoreDiff)})
              </span>
            </>
          )}
        </div>
      </div>

      {/* Chart with gradient fill */}
      <div
        role="img"
        aria-label={`Compliance trend over 30 days. Score ${
          trendDirection === 'improving' ? 'improved' : trendDirection === 'declining' ? 'declined' : 'remained stable'
        } by ${Math.abs(scoreDiff)} points. Current score: ${Math.round(lastScore)} out of 100.`}
      >
      <ResponsiveContainer width="100%" height={300}>
        <AreaChart data={data} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
          {/* Gradient definition */}
          <defs>
            <linearGradient id="scoreGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--interactive-primary-bg)" stopOpacity={0.3} />
              <stop offset="100%" stopColor="var(--interactive-primary-bg)" stopOpacity={0.02} />
            </linearGradient>
          </defs>

          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-primary)" opacity={0.3} />
          <XAxis
            dataKey="date"
            tickFormatter={formatDate}
            stroke="var(--content-tertiary)"
            style={{ fontSize: '12px' }}
          />
          <YAxis domain={[0, 100]} stroke="var(--content-tertiary)" style={{ fontSize: '12px' }} />
          <Tooltip content={CustomTooltip} />

          {/* Reference lines for compliance thresholds */}
          <ReferenceLine y={90} stroke="#059669" strokeDasharray="3 3" opacity={0.5} />
          <ReferenceLine y={70} stroke="#0284C7" strokeDasharray="3 3" opacity={0.5} />

          {/* Area with gradient fill */}
          <Area
            type="monotone"
            dataKey="score"
            stroke="var(--interactive-primary-bg)"
            strokeWidth={3}
            fill="url(#scoreGradient)"
            dot={{
              fill: 'var(--interactive-primary-bg)',
              r: 4,
              strokeWidth: 2,
              stroke: 'var(--surface-primary)',
            }}
            activeDot={{ r: 6, strokeWidth: 2, stroke: 'var(--surface-primary)' }}
          />
        </AreaChart>
      </ResponsiveContainer>
      </div>

      {/* Screen reader data summary */}
      <div className="sr-only">
        <h3>Compliance Trend Data</h3>
        <ul>
          {data.map((point) => (
            <li key={point.date}>
              {formatDate(point.date)}: Score {Math.round(point.score)}, {point.scans} scan{point.scans !== 1 ? 's' : ''}
            </li>
          ))}
        </ul>
      </div>

      {/* Legend */}
      <div className="flex items-center justify-center space-x-6 mt-4 text-xs">
        <div className="flex items-center space-x-2">
          <div className="w-3 h-3 rounded-full bg-[var(--feature-success-content)]"></div>
          <span className="text-tertiary">Excellent (90+)</span>
        </div>
        <div className="flex items-center space-x-2">
          <div className="w-3 h-3 rounded-full bg-[var(--feature-warning-content)]"></div>
          <span className="text-tertiary">Good (70-89)</span>
        </div>
        <div className="flex items-center space-x-2">
          <div className="w-3 h-3 rounded-full bg-[var(--feature-danger-content)]"></div>
          <span className="text-tertiary">Needs Work (&lt;70)</span>
        </div>
      </div>
    </div>
  );
}
