import React, { useState, useCallback, useEffect, useRef } from 'react';
import { Save, RotateCcw, Table2 } from 'lucide-react';
import type { TableStructure, TableCell, CellScope } from './tableStructureUtils';
import {
  toggleCellType,
  setCellScope,
  isCoveredBySpan,
  findCell,
} from './tableStructureUtils';

// ============================================================================
// Types
// ============================================================================

interface TableStructureEditorProps {
  structure: TableStructure;
  onChange: (updated: TableStructure) => void;
  readOnly?: boolean;
}

interface CellPosition {
  row: number;
  col: number;
}

// ============================================================================
// Scope Selector Popover
// ============================================================================

interface ScopeSelectorProps {
  currentScope: CellScope;
  onSelect: (scope: CellScope) => void;
  onClose: () => void;
  position: { top: number; left: number };
}

function ScopeSelector({
  currentScope,
  onSelect,
  onClose,
  position,
}: ScopeSelectorProps): React.ReactElement {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent): void {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    }
    function handleEscape(e: KeyboardEvent): void {
      if (e.key === 'Escape') {
        onClose();
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [onClose]);

  // Focus the first button on mount
  useEffect(() => {
    const firstButton = ref.current?.querySelector('button');
    firstButton?.focus();
  }, []);

  const scopes: { value: CellScope; label: string }[] = [
    { value: 'Column', label: 'Column Header' },
    { value: 'Row', label: 'Row Header' },
    { value: 'None', label: 'No Scope' },
  ];

  return (
    <div
      ref={ref}
      className="absolute z-50 rounded-lg shadow-lg border border-[var(--border-primary)] bg-[var(--surface-primary)] py-1 min-w-[140px]"
      style={{ top: position.top, left: position.left }}
      role="menu"
      aria-label="Set header scope"
    >
      <p className="px-3 py-1 text-xs font-medium text-[var(--content-tertiary)] uppercase">
        Scope
      </p>
      {scopes.map(({ value, label }) => (
        <button
          key={value}
          onClick={() => {
            onSelect(value);
            onClose();
          }}
          className={`w-full text-left px-3 py-1.5 text-sm transition-colors ${
            currentScope === value
              ? 'bg-[var(--accent-primary)] text-white'
              : 'text-[var(--content-primary)] hover:bg-[var(--surface-secondary)]'
          }`}
          role="menuitem"
          aria-current={currentScope === value ? 'true' : undefined}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

// ============================================================================
// Scope Badge
// ============================================================================

function ScopeBadge({ scope }: { scope: CellScope }): React.ReactElement | null {
  if (scope === 'None') return null;

  const label = scope === 'Column' ? 'Col' : 'Row';
  return (
    <span className="block text-[10px] font-medium text-[var(--accent-primary)] opacity-80 mt-0.5">
      {label}
    </span>
  );
}

// ============================================================================
// Table Cell Component
// ============================================================================

interface TableCellEditorProps {
  cell: TableCell;
  isSelected: boolean;
  readOnly: boolean;
  onSelect: (row: number, col: number) => void;
  onToggle: (row: number, col: number) => void;
  onScopeClick: (row: number, col: number, e: React.MouseEvent) => void;
}

function TableCellEditor({
  cell,
  isSelected,
  readOnly,
  onSelect,
  onToggle,
  onScopeClick,
}: TableCellEditorProps): React.ReactElement {
  const handleKeyDown = (e: React.KeyboardEvent): void => {
    if (readOnly) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onToggle(cell.row, cell.col);
    }
  };

  const handleClick = (e: React.MouseEvent): void => {
    if (readOnly) return;
    onSelect(cell.row, cell.col);

    // Right-click or Ctrl+click opens scope selector for header cells
    if (cell.is_header && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      onScopeClick(cell.row, cell.col, e);
      return;
    }
  };

  const handleDoubleClick = (): void => {
    if (readOnly) return;
    onToggle(cell.row, cell.col);
  };

  const handleContextMenu = (e: React.MouseEvent): void => {
    if (readOnly) return;
    if (cell.is_header) {
      e.preventDefault();
      onScopeClick(cell.row, cell.col, e);
    }
  };

  const isHeader = cell.is_header;
  const scope = cell.scope ?? 'None';

  // Build cell class names
  const baseClasses = 'px-3 py-2 text-sm border border-[var(--border-secondary)] transition-all duration-100 select-none';
  const headerClasses = isHeader
    ? 'bg-[var(--accent-primary)]/10 font-semibold text-[var(--content-primary)]'
    : 'bg-[var(--surface-primary)] text-[var(--content-secondary)]';
  const selectedClasses = isSelected
    ? 'ring-2 ring-[var(--accent-primary)] ring-inset'
    : '';
  const interactiveClasses = readOnly
    ? ''
    : 'cursor-pointer hover:bg-[var(--surface-secondary)]';

  const CellTag = isHeader ? 'th' : 'td';

  return (
    <CellTag
      className={`${baseClasses} ${headerClasses} ${selectedClasses} ${interactiveClasses}`}
      colSpan={cell.colspan ?? 1}
      rowSpan={cell.rowspan ?? 1}
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
      onContextMenu={handleContextMenu}
      onKeyDown={handleKeyDown}
      tabIndex={readOnly ? -1 : 0}
      role={readOnly ? undefined : 'button'}
      aria-label={`${isHeader ? 'Header' : 'Data'} cell: ${cell.text || '(empty)'}${isHeader && scope !== 'None' ? `, scope: ${scope}` : ''}. Double-click to toggle${isHeader ? '. Right-click to set scope.' : '.'}`}
      scope={isHeader && scope !== 'None' ? (scope === 'Column' ? 'col' : 'row') : undefined}
    >
      <div className="min-w-[40px]">
        <span className="block truncate max-w-[200px]">
          {cell.text || <span className="text-[var(--content-tertiary)] italic">(empty)</span>}
        </span>
        {isHeader && <ScopeBadge scope={scope} />}
      </div>
    </CellTag>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export function TableStructureEditor({
  structure,
  onChange,
  readOnly = false,
}: TableStructureEditorProps): React.ReactElement {
  const [editedStructure, setEditedStructure] = useState<TableStructure>(structure);
  const [selectedCell, setSelectedCell] = useState<CellPosition | null>(null);
  const [scopePopover, setScopePopover] = useState<{
    row: number;
    col: number;
    position: { top: number; left: number };
  } | null>(null);
  const [hasChanges, setHasChanges] = useState(false);
  const tableRef = useRef<HTMLTableElement>(null);

  // Reset local state when parent provides a new structure prop
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setEditedStructure(structure);
    setHasChanges(false);
    setSelectedCell(null);
    setScopePopover(null);
  }, [structure]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // ---- Handlers ----

  const handleSelect = useCallback((row: number, col: number): void => {
    setSelectedCell({ row, col });
  }, []);

  const handleToggle = useCallback(
    (row: number, col: number): void => {
      if (readOnly) return;
      const updated = toggleCellType(editedStructure, row, col);
      if (updated !== editedStructure) {
        setEditedStructure(updated);
        setHasChanges(true);
        // Close scope popover if toggling changed header status
        if (scopePopover?.row === row && scopePopover?.col === col) {
          setScopePopover(null);
        }
      }
    },
    [editedStructure, readOnly, scopePopover],
  );

  const handleScopeClick = useCallback(
    (row: number, col: number, e: React.MouseEvent): void => {
      if (readOnly) return;
      const cell = findCell(editedStructure, row, col);
      if (!cell || !cell.is_header) return;

      // Position the popover near the click
      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
      const tableRect = tableRef.current?.getBoundingClientRect();
      if (tableRect) {
        setScopePopover({
          row,
          col,
          position: {
            top: rect.bottom - tableRect.top + 4,
            left: rect.left - tableRect.left,
          },
        });
      }
    },
    [editedStructure, readOnly],
  );

  const handleScopeSelect = useCallback(
    (scope: CellScope): void => {
      if (!scopePopover) return;
      const updated = setCellScope(
        editedStructure,
        scopePopover.row,
        scopePopover.col,
        scope,
      );
      if (updated !== editedStructure) {
        setEditedStructure(updated);
        setHasChanges(true);
      }
    },
    [editedStructure, scopePopover],
  );

  const handleSave = (): void => {
    onChange(editedStructure);
    setHasChanges(false);
  };

  const handleReset = (): void => {
    setEditedStructure(structure);
    setHasChanges(false);
    setSelectedCell(null);
    setScopePopover(null);
  };

  // ---- Keyboard navigation ----

  const handleTableKeyDown = useCallback(
    (e: React.KeyboardEvent): void => {
      if (!selectedCell || readOnly) return;

      let newRow = selectedCell.row;
      let newCol = selectedCell.col;

      switch (e.key) {
        case 'ArrowUp':
          e.preventDefault();
          newRow = Math.max(0, selectedCell.row - 1);
          break;
        case 'ArrowDown':
          e.preventDefault();
          newRow = Math.min(editedStructure.rows - 1, selectedCell.row + 1);
          break;
        case 'ArrowLeft':
          e.preventDefault();
          newCol = Math.max(0, selectedCell.col - 1);
          break;
        case 'ArrowRight':
          e.preventDefault();
          newCol = Math.min(editedStructure.cols - 1, selectedCell.col + 1);
          break;
        default:
          return;
      }

      // Skip covered cells by moving further in the same direction
      while (isCoveredBySpan(editedStructure, newRow, newCol)) {
        if (e.key === 'ArrowDown' && newRow < editedStructure.rows - 1) newRow++;
        else if (e.key === 'ArrowUp' && newRow > 0) newRow--;
        else if (e.key === 'ArrowRight' && newCol < editedStructure.cols - 1) newCol++;
        else if (e.key === 'ArrowLeft' && newCol > 0) newCol--;
        else break;
      }

      setSelectedCell({ row: newRow, col: newCol });
    },
    [selectedCell, editedStructure, readOnly],
  );

  // ---- Render ----

  if (editedStructure.rows === 0 || editedStructure.cols === 0) {
    return (
      <div className="border border-[var(--border-primary)] rounded-lg p-6 text-center bg-[var(--surface-secondary)]">
        <Table2
          className="w-8 h-8 mx-auto mb-2 text-[var(--content-tertiary)]"
          aria-hidden="true"
        />
        <p className="text-sm text-[var(--content-tertiary)]">
          No table structure data available
        </p>
      </div>
    );
  }

  // Build rows for rendering
  const rows: React.ReactElement[] = [];
  for (let r = 0; r < editedStructure.rows; r++) {
    const cells: React.ReactElement[] = [];
    for (let c = 0; c < editedStructure.cols; c++) {
      // Skip covered positions
      if (isCoveredBySpan(editedStructure, r, c)) continue;

      const cell = findCell(editedStructure, r, c);
      if (!cell) continue;

      const isSelected =
        selectedCell !== null &&
        selectedCell.row === r &&
        selectedCell.col === c;

      cells.push(
        <TableCellEditor
          key={`${r}-${c}`}
          cell={cell}
          isSelected={isSelected}
          readOnly={readOnly}
          onSelect={handleSelect}
          onToggle={handleToggle}
          onScopeClick={handleScopeClick}
        />,
      );
    }

    rows.push(<tr key={r}>{cells}</tr>);
  }

  const scopeCell = scopePopover
    ? findCell(editedStructure, scopePopover.row, scopePopover.col)
    : null;

  return (
    <div className="space-y-3">
      {/* Header bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Table2
            className="w-4 h-4 text-[var(--accent-primary)]"
            aria-hidden="true"
          />
          <span className="text-sm font-medium text-[var(--content-primary)]">
            Table Structure
          </span>
          <span className="text-xs text-[var(--content-tertiary)]">
            {editedStructure.rows} rows, {editedStructure.cols} cols
          </span>
          {editedStructure.header_rows > 0 && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]">
              {editedStructure.header_rows} header row{editedStructure.header_rows > 1 ? 's' : ''}
            </span>
          )}
          {editedStructure.header_cols > 0 && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]">
              {editedStructure.header_cols} header col{editedStructure.header_cols > 1 ? 's' : ''}
            </span>
          )}
        </div>

        {!readOnly && (
          <div className="flex items-center gap-2">
            {hasChanges && (
              <button
                onClick={handleReset}
                className="text-xs py-1 px-2 flex items-center gap-1 rounded border border-[var(--border-primary)] text-[var(--content-secondary)] hover:bg-[var(--surface-secondary)] transition-colors"
                aria-label="Reset table changes"
              >
                <RotateCcw className="w-3 h-3" aria-hidden="true" />
                Reset
              </button>
            )}
            <button
              onClick={handleSave}
              disabled={!hasChanges}
              className="text-xs py-1 px-2 flex items-center gap-1 rounded bg-[var(--accent-primary)] text-white hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
              aria-label="Save table structure changes"
            >
              <Save className="w-3 h-3" aria-hidden="true" />
              Save
            </button>
          </div>
        )}
      </div>

      {/* Instructions */}
      {!readOnly && (
        <p className="text-xs text-[var(--content-tertiary)]">
          Double-click a cell to toggle between header (TH) and data (TD).
          Right-click a header cell to set its scope.
          Use arrow keys to navigate.
        </p>
      )}

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-[var(--border-primary)]">
        <div className="relative">
          <table
            ref={tableRef}
            className="w-full border-collapse text-left"
            onKeyDown={handleTableKeyDown}
            role="grid"
            aria-label="Editable table structure"
          >
            <tbody>
              {rows}
            </tbody>
          </table>

          {/* Scope popover */}
          {scopePopover && scopeCell && (
            <ScopeSelector
              currentScope={scopeCell.scope ?? 'None'}
              onSelect={handleScopeSelect}
              onClose={() => setScopePopover(null)}
              position={scopePopover.position}
            />
          )}
        </div>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 text-xs text-[var(--content-tertiary)]">
        <div className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm bg-[var(--accent-primary)]/10 border border-[var(--accent-primary)]/30" />
          <span>Header (TH)</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm bg-[var(--surface-primary)] border border-[var(--border-secondary)]" />
          <span>Data (TD)</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[var(--accent-primary)] font-medium">Col</span>
          <span>= Column scope</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[var(--accent-primary)] font-medium">Row</span>
          <span>= Row scope</span>
        </div>
      </div>
    </div>
  );
}
