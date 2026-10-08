import React from 'react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ColumnAlign = 'left' | 'center' | 'right';

export interface ColumnDef<T> {
  /** Unique key used to identify the column */
  key: string;
  /** Header label text (rendered mono-uppercase) */
  header: string;
  /**
   * Optional custom cell renderer. Receives the row object.
   * When omitted the column is purely decorative / for headers only.
   */
  render?: (row: T) => React.ReactNode;
  /** Extra className applied to both <th> and <td> */
  className?: string;
  /** Text alignment for the column (default: left) */
  align?: ColumnAlign;
}

export interface DataTableProps<T> {
  columns: ColumnDef<T>[];
  rows: T[];
  /** Return a stable React key for each row */
  getRowKey: (row: T) => string | number;
  /**
   * Optional caption for screen readers.
   * Rendered as a visually-hidden <caption> element.
   */
  caption?: string;
  /** Extra className applied to the outer <div> wrapper */
  className?: string;
}

// ---------------------------------------------------------------------------
// Alignment helpers
// ---------------------------------------------------------------------------

const ALIGN_TH: Record<ColumnAlign, string> = {
  left: 'text-left',
  center: 'text-center',
  right: 'text-right',
};

const ALIGN_TD: Record<ColumnAlign, string> = {
  left: 'text-left',
  center: 'text-center',
  right: 'text-right',
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * DataTable — Clarity Design System (dashboard)
 *
 * Generic, presentational table component.
 *
 * Features:
 *   - Mono uppercase headers (`font-mono text-xs uppercase tracking-wider`)
 *   - `var(--content-tertiary)` header text
 *   - Hairline row separators (`border-t border-[var(--border-primary)]`)
 *   - Row hover `hover:bg-[var(--surface-secondary)]`
 *   - Generic over row type T; columns carry a `render` prop for custom cells
 *
 * Usage:
 * ```tsx
 * <DataTable
 *   columns={[
 *     { key: 'name', header: 'Name', render: (row) => row.name },
 *     { key: 'score', header: 'Score', render: (row) => <ScoreChip score={row.score} />, align: 'right' },
 *   ]}
 *   rows={data}
 *   getRowKey={(row) => row.id}
 *   caption="Scan history"
 * />
 * ```
 */
export function DataTable<T>({
  columns,
  rows,
  getRowKey,
  caption,
  className = '',
}: DataTableProps<T>): React.ReactElement {
  return (
    <div className={['overflow-x-auto', className].filter(Boolean).join(' ')}>
      <table className="w-full">
        {caption && (
          <caption className="sr-only">{caption}</caption>
        )}

        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                className={[
                  'px-6 py-3',
                  'font-mono text-xs uppercase tracking-wider',
                  'text-[var(--content-tertiary)]',
                  ALIGN_TH[col.align ?? 'left'],
                  col.className ?? '',
                ]
                  .filter(Boolean)
                  .join(' ')}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {rows.map((row) => (
            <tr
              key={getRowKey(row)}
              className="border-t border-[var(--border-primary)] hover:bg-[var(--surface-secondary)] transition-colors duration-150"
            >
              {columns.map((col) => (
                <td
                  key={col.key}
                  className={[
                    'px-6 py-4 whitespace-nowrap',
                    ALIGN_TD[col.align ?? 'left'],
                    col.className ?? '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                >
                  {col.render ? col.render(row) : null}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
