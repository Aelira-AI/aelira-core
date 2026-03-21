// ============================================================================
// Table Structure Utility Functions
// Pure functions for manipulating table structure data, used by
// TableStructureEditor and testable independently.
// ============================================================================

// ============================================================================
// Types
// ============================================================================

export type CellScope = 'Column' | 'Row' | 'None';

export interface TableCell {
  row: number;
  col: number;
  text: string;
  is_header: boolean;
  scope?: CellScope;
  colspan?: number;
  rowspan?: number;
}

export interface TableStructure {
  rows: number;
  cols: number;
  cells: TableCell[];
  header_rows: number;
  header_cols: number;
}

// ============================================================================
// Cell Lookup
// ============================================================================

/**
 * Find a cell by its row and column indices.
 * Returns undefined if no cell exists at that position.
 */
export function findCell(
  structure: TableStructure,
  row: number,
  col: number,
): TableCell | undefined {
  return structure.cells.find((c) => c.row === row && c.col === col);
}

// ============================================================================
// Toggle TH/TD
// ============================================================================

/**
 * Toggle a cell between header (TH) and data (TD).
 * When toggling to TH, sets scope to 'Column' by default.
 * When toggling to TD, removes scope.
 * Returns a new TableStructure with the updated cell (immutable).
 */
export function toggleCellType(
  structure: TableStructure,
  row: number,
  col: number,
): TableStructure {
  const cellIndex = structure.cells.findIndex(
    (c) => c.row === row && c.col === col,
  );
  if (cellIndex === -1) return structure;

  const cell = structure.cells[cellIndex];
  const updatedCell: TableCell = {
    ...cell,
    is_header: !cell.is_header,
    scope: !cell.is_header ? 'Column' : 'None',
  };

  const updatedCells = [...structure.cells];
  updatedCells[cellIndex] = updatedCell;

  // Recalculate header_rows and header_cols
  const { headerRows, headerCols } = computeHeaderCounts(
    updatedCells,
    structure.rows,
    structure.cols,
  );

  return {
    ...structure,
    cells: updatedCells,
    header_rows: headerRows,
    header_cols: headerCols,
  };
}

// ============================================================================
// Set Cell Scope
// ============================================================================

/**
 * Set the scope attribute on a header cell.
 * Only applies to cells where is_header === true.
 * Returns the original structure unchanged if the cell is not a header.
 */
export function setCellScope(
  structure: TableStructure,
  row: number,
  col: number,
  scope: CellScope,
): TableStructure {
  const cellIndex = structure.cells.findIndex(
    (c) => c.row === row && c.col === col,
  );
  if (cellIndex === -1) return structure;

  const cell = structure.cells[cellIndex];
  if (!cell.is_header) return structure;

  const updatedCell: TableCell = { ...cell, scope };
  const updatedCells = [...structure.cells];
  updatedCells[cellIndex] = updatedCell;

  return {
    ...structure,
    cells: updatedCells,
  };
}

// ============================================================================
// Parse Table Structure from Fix Content
// ============================================================================

/**
 * Attempt to parse a TableStructure from a fix's fixed_content JSON string.
 * Returns null if the string is not valid table structure JSON.
 */
export function parseTableStructure(
  fixContent: string | undefined,
): TableStructure | null {
  if (!fixContent) return null;

  try {
    const parsed: unknown = JSON.parse(fixContent);
    if (!isTableStructure(parsed)) return null;
    return parsed;
  } catch {
    return null;
  }
}

/**
 * Serialize a TableStructure back to a JSON string.
 */
export function serializeTableStructure(structure: TableStructure): string {
  return JSON.stringify(structure, null, 2);
}

// ============================================================================
// Validation / Type Guard
// ============================================================================

/**
 * Type guard to check if an unknown value is a valid TableStructure.
 */
function isTableStructure(value: unknown): value is TableStructure {
  if (typeof value !== 'object' || value === null) return false;

  const obj = value as Record<string, unknown>;

  if (typeof obj.rows !== 'number' || obj.rows < 0) return false;
  if (typeof obj.cols !== 'number' || obj.cols < 0) return false;
  if (!Array.isArray(obj.cells)) return false;
  if (typeof obj.header_rows !== 'number') return false;
  if (typeof obj.header_cols !== 'number') return false;

  // Validate at least a sampling of cells
  for (const cell of obj.cells) {
    if (typeof cell !== 'object' || cell === null) return false;
    const c = cell as Record<string, unknown>;
    if (typeof c.row !== 'number') return false;
    if (typeof c.col !== 'number') return false;
    if (typeof c.text !== 'string') return false;
    if (typeof c.is_header !== 'boolean') return false;
  }

  return true;
}

// ============================================================================
// Build Grid (for rendering)
// ============================================================================

/**
 * Build a 2D grid representation for rendering.
 * Handles colspan/rowspan by marking occupied positions.
 * Returns a 2D array where each element is either a TableCell (the origin cell)
 * or null (occupied by a spanning cell).
 */
export function buildGrid(
  structure: TableStructure,
): (TableCell | null)[][] {
  const grid: (TableCell | null)[][] = Array.from(
    { length: structure.rows },
    () => Array.from<TableCell | null>({ length: structure.cols }).fill(null),
  );

  // Place each cell at its origin position. Cells with colspan/rowspan
  // occupy multiple positions, but only the origin is stored in the grid;
  // spanned positions remain null. The renderer uses isCoveredBySpan()
  // to decide which positions to skip.
  for (const cell of structure.cells) {
    if (cell.row < structure.rows && cell.col < structure.cols) {
      grid[cell.row][cell.col] = cell;
    }
  }

  return grid;
}

/**
 * Check whether a grid position is covered by a spanning cell
 * (i.e., it's part of a colspan/rowspan but not the origin).
 */
export function isCoveredBySpan(
  structure: TableStructure,
  row: number,
  col: number,
): boolean {
  // If a cell exists at exactly this position, it's the origin — not covered
  const directCell = structure.cells.find(
    (c) => c.row === row && c.col === col,
  );
  if (directCell) return false;

  // Check if any cell spans over this position
  for (const cell of structure.cells) {
    const rs = cell.rowspan ?? 1;
    const cs = cell.colspan ?? 1;
    if (rs <= 1 && cs <= 1) continue;

    if (
      row >= cell.row &&
      row < cell.row + rs &&
      col >= cell.col &&
      col < cell.col + cs
    ) {
      return true;
    }
  }

  return false;
}

// ============================================================================
// Header Count Helpers
// ============================================================================

/**
 * Compute the number of contiguous header rows (from the top) and
 * contiguous header columns (from the left).
 */
function computeHeaderCounts(
  cells: TableCell[],
  totalRows: number,
  totalCols: number,
): { headerRows: number; headerCols: number } {
  // Header rows: count consecutive rows where ALL cells are headers
  // Skip rows with no cells (covered by rowspan) — they don't break the run
  let headerRows = 0;
  for (let r = 0; r < totalRows; r++) {
    const rowCells = cells.filter((c) => c.row === r);
    if (rowCells.length === 0) continue; // spanned row, skip
    if (rowCells.every((c) => c.is_header)) {
      headerRows++;
    } else {
      break;
    }
  }

  // Header cols: count consecutive columns where ALL cells are headers
  // Skip columns with no cells (covered by colspan) — they don't break the run
  let headerCols = 0;
  for (let c = 0; c < totalCols; c++) {
    const colCells = cells.filter((cell) => cell.col === c);
    if (colCells.length === 0) continue; // spanned col, skip
    if (colCells.every((cell) => cell.is_header)) {
      headerCols++;
    } else {
      break;
    }
  }

  return { headerRows, headerCols };
}

// ============================================================================
// Create Empty Table
// ============================================================================

/**
 * Create a default table structure with the given dimensions.
 * First row is headers by default.
 */
export function createEmptyTable(rows: number, cols: number): TableStructure {
  const cells: TableCell[] = [];

  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      cells.push({
        row: r,
        col: c,
        text: '',
        is_header: r === 0,
        scope: r === 0 ? 'Column' : 'None',
      });
    }
  }

  return {
    rows,
    cols,
    cells,
    header_rows: 1,
    header_cols: 0,
  };
}
