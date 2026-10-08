import React from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface BreadcrumbItem {
  label: string;
  /** When present, the item is rendered as a router Link */
  href?: string;
}

export interface BreadcrumbProps {
  items: BreadcrumbItem[];
  className?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Breadcrumb — Clarity Design System (dashboard)
 *
 * Singular name (`Breadcrumb`) to match the website's component naming.
 * Accepts `items: BreadcrumbItem[]`; items with `href` become router Links,
 * the last item is always treated as the current page (no link).
 *
 * Visual:
 *   - Non-current items: `text-[var(--content-tertiary)]`
 *   - Current item: `text-[var(--content-primary)]` + `aria-current="page"`
 *   - Chevron separators via lucide `ChevronRight` (aria-hidden)
 *
 * Back-compat: `layout/Breadcrumbs.tsx` re-exports this component under the
 * old `Breadcrumbs` name so existing consumers (FocusOrderDetail, Remediate,
 * ScanDetail) continue to work without edits.
 */
export function Breadcrumb({
  items,
  className = '',
}: BreadcrumbProps): React.ReactElement {
  return (
    <nav
      aria-label="Breadcrumb"
      className={['flex items-center', className].filter(Boolean).join(' ')}
    >
      <ol className="flex items-center flex-wrap gap-1 text-sm">
        {items.map((item, index) => {
          const isLast = index === items.length - 1;

          return (
            <li key={index} className="flex items-center">
              {/* Separator before every item except the first */}
              {index > 0 && (
                <ChevronRight
                  className="w-4 h-4 text-[var(--content-tertiary)] mx-1 shrink-0"
                  aria-hidden="true"
                />
              )}

              {isLast || !item.href ? (
                <span
                  className={[
                    'truncate max-w-[200px]',
                    isLast
                      ? 'text-[var(--content-primary)] font-medium'
                      : 'text-[var(--content-tertiary)]',
                  ].join(' ')}
                  aria-current={isLast ? 'page' : undefined}
                >
                  {item.label}
                </span>
              ) : (
                <Link
                  to={item.href}
                  className="text-[var(--content-tertiary)] hover:text-[var(--content-accent)] transition-colors truncate max-w-[200px]"
                >
                  {item.label}
                </Link>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
