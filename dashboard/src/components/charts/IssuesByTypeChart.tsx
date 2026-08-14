import React, { useState } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts';
import type { TooltipContentProps } from 'recharts/types/component/Tooltip';
import type { ValueType, NameType } from 'recharts/types/component/DefaultTooltipContent';
import type { PieLabelRenderProps } from 'recharts/types/polar/Pie';

// ============================================================================
// Types
// ============================================================================

interface Issue {
  severity?: string;
  impact?: string;
}

interface IssuesByTypeChartProps {
  issues: Issue[];
}

interface ChartDataItem {
  name: string;
  value: number;
  color: string;
}

type ChartType = 'bar' | 'pie';

// ============================================================================
// Constants
// ============================================================================

const COLORS: Record<string, string> = {
  critical: '#A21CAF', // fuchsia-700 (danger)
  high: '#0284C7', // sky-600 (warning)
  serious: '#0284C7', // sky-600 (alias for high)
  medium: '#4F46E5', // indigo-600 (info)
  moderate: '#4F46E5', // indigo-600 (alias for medium)
  low: '#64748B', // slate-500 (tertiary)
  minor: '#64748B', // slate-500 (alias for low)
};

// ============================================================================
// Component
// ============================================================================

export function IssuesByTypeChart({ issues }: IssuesByTypeChartProps): React.ReactElement {
  const [chartType, setChartType] = useState<ChartType>('bar');

  // Count issues by severity/impact
  const severityCounts = {
    critical: 0,
    high: 0,
    medium: 0,
    low: 0,
  };

  issues.forEach((issue) => {
    const severity = (issue.severity || issue.impact || '').toLowerCase();

    if (severity === 'critical') {
      severityCounts.critical++;
    } else if (severity === 'high' || severity === 'serious') {
      severityCounts.high++;
    } else if (severity === 'medium' || severity === 'moderate') {
      severityCounts.medium++;
    } else if (severity === 'low' || severity === 'minor') {
      severityCounts.low++;
    }
  });

  // Prepare data for charts
  const barData: ChartDataItem[] = [
    { name: 'Critical', value: severityCounts.critical, color: COLORS.critical },
    { name: 'High', value: severityCounts.high, color: COLORS.high },
    { name: 'Medium', value: severityCounts.medium, color: COLORS.medium },
    { name: 'Low', value: severityCounts.low, color: COLORS.low },
  ];

  // Filter out zero values for pie chart
  const pieData = barData.filter((item) => item.value > 0);

  const totalIssues = issues.length;

  if (totalIssues === 0) {
    return (
      <div className="card">
        <h3 className="text-lg font-semibold text-primary mb-4">Issues by Severity</h3>
        <div className="text-center py-8 text-secondary">
          No issues found - your content is fully accessible!
        </div>
      </div>
    );
  }

  const renderBarTooltip = ({ active, payload }: TooltipContentProps<ValueType, NameType>): React.ReactElement | null => {
    if (active && payload && payload.length) {
      const data = payload[0].payload as ChartDataItem;
      return (
        <div className="bg-[var(--surface-primary)] border border-[var(--border-primary)] rounded-lg p-3 shadow-lg">
          <p className="font-semibold text-primary mb-1">{data.name}</p>
          <p className="text-sm text-secondary">
            {data.value} {data.value === 1 ? 'issue' : 'issues'}
          </p>
        </div>
      );
    }
    return null;
  };

  const renderPieTooltip = ({ active, payload }: TooltipContentProps<ValueType, NameType>): React.ReactElement | null => {
    if (active && payload && payload.length) {
      const data = payload[0].payload as ChartDataItem;
      const percentage = ((data.value / totalIssues) * 100).toFixed(1);
      return (
        <div className="bg-[var(--surface-primary)] border border-[var(--border-primary)] rounded-lg p-3 shadow-lg">
          <p className="font-semibold text-primary mb-1">{data.name}</p>
          <p className="text-sm text-secondary">
            {data.value} {data.value === 1 ? 'issue' : 'issues'} ({percentage}%)
          </p>
        </div>
      );
    }
    return null;
  };

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-primary">Issues by Severity</h3>
        <div className="flex space-x-2" role="group" aria-label="Chart type">
          <button
            onClick={() => setChartType('bar')}
            className={`px-3 py-1 text-sm rounded ${
              chartType === 'bar'
                ? 'bg-[var(--surface-accent-strong)] text-white'
                : 'bg-[var(--surface-tertiary)] text-secondary hover:bg-[var(--surface-accent-subtle)]'
            }`}
            aria-label="Show bar chart"
            aria-pressed={chartType === 'bar'}
          >
            Bar
          </button>
          <button
            onClick={() => setChartType('pie')}
            className={`px-3 py-1 text-sm rounded ${
              chartType === 'pie'
                ? 'bg-[var(--surface-accent-strong)] text-white'
                : 'bg-[var(--surface-tertiary)] text-secondary hover:bg-[var(--surface-accent-subtle)]'
            }`}
            aria-label="Show pie chart"
            aria-pressed={chartType === 'pie'}
          >
            Pie
          </button>
        </div>
      </div>

      <div
        className="h-64"
        style={{ minHeight: '256px' }}
        role="img"
        aria-label={`Issues by severity: ${barData.map(d => `${d.name}: ${d.value}`).join(', ')}. Total: ${totalIssues} issues.`}
      >
        {chartType === 'bar' ? (
          <ResponsiveContainer width="100%" height="100%" minHeight={256}>
            <BarChart data={barData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
              <XAxis dataKey="name" tick={{ fill: '#6B7280', fontSize: 12 }} />
              <YAxis tick={{ fill: '#6B7280', fontSize: 12 }} allowDecimals={false} />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'var(--card-bg, #FFF)',
                  border: '1px solid var(--card-border, #E5E7EB)',
                  borderRadius: '6px',
                  color: 'var(--text-primary, #000)',
                }}
                cursor={{ fill: 'rgba(99, 102, 241, 0.1)' }}
                content={renderBarTooltip}
              />
              <Bar dataKey="value" radius={[8, 8, 0, 0]}>
                {barData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.color} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <ResponsiveContainer width="100%" height="100%" minHeight={256}>
            <PieChart>
              <Pie
                data={pieData as unknown as Record<string, unknown>[]}
                cx="50%"
                cy="50%"
                labelLine={false}
                label={(props: PieLabelRenderProps) =>
                  `${props.name}: ${props.value} (${((props.percent ?? 0) * 100).toFixed(0)}%)`
                }
                outerRadius={80}
                fill="#8884d8"
                dataKey="value"
              >
                {pieData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip content={renderPieTooltip} />
            </PieChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Screen reader data summary */}
      <div className="sr-only">
        <h4>Issues by Severity Summary</h4>
        <ul>
          {barData.map((item) => (
            <li key={item.name}>
              {item.name}: {item.value} {item.value === 1 ? 'issue' : 'issues'}
            </li>
          ))}
        </ul>
      </div>

      {/* Summary Stats */}
      <div className="mt-4 pt-4 border-t border-[var(--border-primary)]">
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-secondary">Total Issues:</span>
            <span className="ml-2 font-semibold text-primary">{totalIssues}</span>
          </div>
          <div>
            <span className="text-secondary">Most Common:</span>
            <span className="ml-2 font-semibold text-primary">
              {barData.reduce((max, item) => (item.value > max.value ? item : max), barData[0]).name}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
