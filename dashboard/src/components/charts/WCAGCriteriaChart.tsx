import React from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import type { TooltipContentProps } from 'recharts/types/component/Tooltip';
import type { ValueType, NameType } from 'recharts/types/component/DefaultTooltipContent';

// ============================================================================
// Types
// ============================================================================

interface Issue {
  criterion?: string;
  wcag_criterion?: string;
  severity?: string;
  impact?: string;
}

interface WCAGCriteriaChartProps {
  issues: Issue[];
}

interface CriterionData {
  criterion: string;
  critical: number;
  high: number;
  medium: number;
  low: number;
  total: number;
}

// ============================================================================
// Component
// ============================================================================

export function WCAGCriteriaChart({ issues }: WCAGCriteriaChartProps): React.ReactElement {
  // Extract WCAG criterion from issues
  const criterionMap = new Map<string, CriterionData>();

  issues.forEach((issue) => {
    const criterion = issue.criterion || issue.wcag_criterion || 'Unknown';

    // Extract just the number part (e.g., "1.1.1" from "WCAG 2.1 Level AA: 1.1.1 Non-text Content")
    const match = criterion.match(/(\d+\.\d+(?:\.\d+)?)/);
    const criterionNum = match ? match[1] : criterion;

    if (!criterionMap.has(criterionNum)) {
      criterionMap.set(criterionNum, {
        criterion: criterionNum,
        critical: 0,
        high: 0,
        medium: 0,
        low: 0,
        total: 0,
      });
    }

    const data = criterionMap.get(criterionNum)!;
    const severity = (issue.severity || issue.impact || '').toLowerCase();

    if (severity === 'critical') {
      data.critical++;
    } else if (severity === 'high' || severity === 'serious') {
      data.high++;
    } else if (severity === 'medium' || severity === 'moderate') {
      data.medium++;
    } else if (severity === 'low' || severity === 'minor') {
      data.low++;
    }

    data.total++;
  });

  // Convert to array and sort by total (descending)
  const chartData = Array.from(criterionMap.values())
    .sort((a, b) => b.total - a.total)
    .slice(0, 10); // Top 10 criteria

  if (chartData.length === 0) {
    return (
      <div className="card">
        <h3 className="text-lg font-semibold text-primary mb-4">Top WCAG Criteria Issues</h3>
        <div className="text-center py-8 text-secondary">No WCAG criteria violations found</div>
      </div>
    );
  }

  const renderTooltip = ({ active, payload }: TooltipContentProps<ValueType, NameType>): React.ReactElement | null => {
    if (active && payload && payload.length) {
      const criterion = (payload[0].payload as CriterionData).criterion;
      return (
        <div className="bg-[var(--surface-primary)] border border-[var(--border-primary)] rounded-lg p-3 shadow-lg">
          <p className="font-semibold text-[var(--content-primary)] mb-2 font-mono">
            {criterion}
          </p>
          {[...payload].reverse().map(
            (entry, index) =>
              (entry.value as number) > 0 && (
                <div
                  key={index}
                  className="flex items-center justify-between gap-3 text-sm"
                >
                  <span className="text-[var(--content-secondary)] capitalize">{entry.name}:</span>
                  <span className="font-semibold text-[var(--content-primary)]">
                    {entry.value}
                  </span>
                </div>
              )
          )}
        </div>
      );
    }
    return null;
  };

  const legendFormatter = (value: string): string => {
    const labels: Record<string, string> = {
      critical: 'Critical',
      high: 'High',
      medium: 'Medium',
      low: 'Low',
    };
    return labels[value] || value;
  };

  return (
    <div className="card">
      <h3 className="text-lg font-semibold text-primary mb-4">Top WCAG Criteria Issues</h3>
      <p className="text-sm text-secondary mb-4">
        Most common WCAG 2.1 AA criteria violations (showing top 10)
      </p>

      <div
        className="h-80 min-h-[320px]"
        role="img"
        aria-label={`Top WCAG criteria issues chart showing ${chartData.length} criteria. Most common: ${chartData[0]?.criterion || 'none'} with ${chartData[0]?.total || 0} issues.`}
      >
        <ResponsiveContainer width="100%" height="100%" minHeight={320}>
          <BarChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 60 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
            <XAxis
              dataKey="criterion"
              tick={{ fill: '#6B7280', fontSize: 11 }}
              angle={-45}
              textAnchor="end"
              height={80}
            />
            <YAxis tick={{ fill: '#6B7280', fontSize: 12 }} allowDecimals={false} />
            <Tooltip content={renderTooltip} />
            <Legend
              wrapperStyle={{ paddingTop: '20px' }}
              formatter={legendFormatter}
              iconType="square"
            />
            <Bar dataKey="critical" stackId="a" fill="#A21CAF" radius={[0, 0, 0, 0]} />
            <Bar dataKey="high" stackId="a" fill="#0284C7" radius={[0, 0, 0, 0]} />
            <Bar dataKey="medium" stackId="a" fill="#4F46E5" radius={[0, 0, 0, 0]} />
            <Bar dataKey="low" stackId="a" fill="#6B7280" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Screen reader data summary */}
      <div className="sr-only">
        <h4>WCAG Criteria Violations Summary</h4>
        <ul>
          {chartData.map((item) => (
            <li key={item.criterion}>
              Criterion {item.criterion}: {item.total} total issues
              ({item.critical} critical, {item.high} high, {item.medium} medium, {item.low} low)
            </li>
          ))}
        </ul>
      </div>

      {/* Top Violations Summary */}
      <div className="mt-4 pt-4 border-t border-[var(--border-primary)]">
        <h4 className="text-sm font-medium text-primary mb-2">Most Common Violations:</h4>
        <div className="space-y-2">
          {chartData.slice(0, 3).map((item) => (
            <div key={item.criterion} className="flex items-center justify-between text-sm">
              <span className="text-secondary">
                <span className="font-mono font-medium text-primary">{item.criterion}</span> (
                {item.total} issues)
              </span>
              <div className="flex space-x-2 text-xs">
                {item.critical > 0 && (
                  <span className="px-2 py-1 bg-[var(--feature-danger-surface)] text-[var(--feature-danger-content)] rounded">
                    {item.critical} critical
                  </span>
                )}
                {item.high > 0 && (
                  <span className="px-2 py-1 bg-[var(--feature-warning-surface)] text-[var(--feature-warning-content)] rounded">
                    {item.high} high
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
