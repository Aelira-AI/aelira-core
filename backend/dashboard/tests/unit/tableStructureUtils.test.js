/**
 * Unit tests for table structure utility functions.
 * Uses Node.js native test runner (same pattern as featureAccess.test.js).
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  findCell,
  toggleCellType,
  setCellScope,
  parseTableStructure,
  serializeTableStructure,
  buildGrid,
  isCoveredBySpan,
  createEmptyTable,
} from '../../src/components/review/tableStructureUtils.ts';

// ============================================================================
// Test fixtures
// ============================================================================

/** Simple 3x3 table: first row is headers, rest are data */
function makeSimpleTable() {
  return {
    rows: 3,
    cols: 3,
    cells: [
      { row: 0, col: 0, text: 'Name', is_header: true, scope: 'Column' },
      { row: 0, col: 1, text: 'Age', is_header: true, scope: 'Column' },
      { row: 0, col: 2, text: 'Grade', is_header: true, scope: 'Column' },
      { row: 1, col: 0, text: 'Alice', is_header: false, scope: 'None' },
      { row: 1, col: 1, text: '20', is_header: false, scope: 'None' },
      { row: 1, col: 2, text: 'A', is_header: false, scope: 'None' },
      { row: 2, col: 0, text: 'Bob', is_header: false, scope: 'None' },
      { row: 2, col: 1, text: '22', is_header: false, scope: 'None' },
      { row: 2, col: 2, text: 'B', is_header: false, scope: 'None' },
    ],
    header_rows: 1,
    header_cols: 0,
  };
}

/** Table with merged cells: header spanning 2 columns */
function makeMergedTable() {
  return {
    rows: 2,
    cols: 3,
    cells: [
      { row: 0, col: 0, text: 'Student Info', is_header: true, scope: 'Column', colspan: 2 },
      { row: 0, col: 2, text: 'Grade', is_header: true, scope: 'Column' },
      { row: 1, col: 0, text: 'Alice', is_header: false, scope: 'None' },
      { row: 1, col: 1, text: '20', is_header: false, scope: 'None' },
      { row: 1, col: 2, text: 'A', is_header: false, scope: 'None' },
    ],
    header_rows: 1,
    header_cols: 0,
  };
}

// ============================================================================
// findCell
// ============================================================================

describe('findCell', () => {
  it('should find a cell by row and col', () => {
    const table = makeSimpleTable();
    const cell = findCell(table, 1, 0);
    assert.ok(cell);
    assert.equal(cell.text, 'Alice');
    assert.equal(cell.is_header, false);
  });

  it('should return undefined for non-existent position', () => {
    const table = makeSimpleTable();
    const cell = findCell(table, 5, 5);
    assert.equal(cell, undefined);
  });

  it('should find header cells', () => {
    const table = makeSimpleTable();
    const cell = findCell(table, 0, 1);
    assert.ok(cell);
    assert.equal(cell.text, 'Age');
    assert.equal(cell.is_header, true);
    assert.equal(cell.scope, 'Column');
  });
});

// ============================================================================
// toggleCellType
// ============================================================================

describe('toggleCellType', () => {
  it('should toggle a data cell to header', () => {
    const table = makeSimpleTable();
    const updated = toggleCellType(table, 1, 0);

    const cell = findCell(updated, 1, 0);
    assert.ok(cell);
    assert.equal(cell.is_header, true);
    assert.equal(cell.scope, 'Column');
    assert.equal(cell.text, 'Alice'); // text unchanged
  });

  it('should toggle a header cell to data', () => {
    const table = makeSimpleTable();
    const updated = toggleCellType(table, 0, 0);

    const cell = findCell(updated, 0, 0);
    assert.ok(cell);
    assert.equal(cell.is_header, false);
    assert.equal(cell.scope, 'None');
  });

  it('should not mutate the original structure', () => {
    const table = makeSimpleTable();
    const original = findCell(table, 1, 0);
    assert.ok(original);
    assert.equal(original.is_header, false);

    toggleCellType(table, 1, 0);

    // Original should be unchanged
    const stillOriginal = findCell(table, 1, 0);
    assert.ok(stillOriginal);
    assert.equal(stillOriginal.is_header, false);
  });

  it('should return same structure for non-existent cell', () => {
    const table = makeSimpleTable();
    const updated = toggleCellType(table, 10, 10);
    assert.equal(updated, table);
  });

  it('should update header_rows when toggling breaks contiguous headers', () => {
    const table = makeSimpleTable();
    // Toggle first header cell to data — row 0 is no longer all headers
    const updated = toggleCellType(table, 0, 0);
    assert.equal(updated.header_rows, 0);
  });

  it('should update header_rows when toggling creates contiguous header row', () => {
    const table = makeSimpleTable();
    // Make all cells in row 1 headers
    let updated = toggleCellType(table, 1, 0);
    updated = toggleCellType(updated, 1, 1);
    updated = toggleCellType(updated, 1, 2);

    // Now rows 0 and 1 are all headers
    assert.equal(updated.header_rows, 2);
  });

  it('should update header_cols when first column becomes all headers', () => {
    const table = makeSimpleTable();
    // First column already has row 0 as header. Make rows 1 and 2 headers too.
    let updated = toggleCellType(table, 1, 0);
    updated = toggleCellType(updated, 2, 0);

    assert.equal(updated.header_cols, 1);
  });
});

// ============================================================================
// setCellScope
// ============================================================================

describe('setCellScope', () => {
  it('should set scope on a header cell', () => {
    const table = makeSimpleTable();
    const updated = setCellScope(table, 0, 0, 'Row');

    const cell = findCell(updated, 0, 0);
    assert.ok(cell);
    assert.equal(cell.scope, 'Row');
    assert.equal(cell.is_header, true);
  });

  it('should not modify a data cell', () => {
    const table = makeSimpleTable();
    const updated = setCellScope(table, 1, 0, 'Row');

    // Should return original since cell is not a header
    assert.equal(updated, table);
  });

  it('should not mutate original structure', () => {
    const table = makeSimpleTable();
    setCellScope(table, 0, 0, 'Row');

    const original = findCell(table, 0, 0);
    assert.ok(original);
    assert.equal(original.scope, 'Column'); // unchanged
  });

  it('should return same structure for non-existent cell', () => {
    const table = makeSimpleTable();
    const updated = setCellScope(table, 10, 10, 'Column');
    assert.equal(updated, table);
  });

  it('should set scope to None', () => {
    const table = makeSimpleTable();
    const updated = setCellScope(table, 0, 1, 'None');

    const cell = findCell(updated, 0, 1);
    assert.ok(cell);
    assert.equal(cell.scope, 'None');
  });
});

// ============================================================================
// parseTableStructure
// ============================================================================

describe('parseTableStructure', () => {
  it('should parse valid JSON table structure', () => {
    const table = makeSimpleTable();
    const json = JSON.stringify(table);
    const parsed = parseTableStructure(json);

    assert.ok(parsed);
    assert.equal(parsed.rows, 3);
    assert.equal(parsed.cols, 3);
    assert.equal(parsed.cells.length, 9);
    assert.equal(parsed.header_rows, 1);
  });

  it('should return null for invalid JSON', () => {
    const result = parseTableStructure('not valid json {{{');
    assert.equal(result, null);
  });

  it('should return null for undefined', () => {
    const result = parseTableStructure(undefined);
    assert.equal(result, null);
  });

  it('should return null for empty string', () => {
    const result = parseTableStructure('');
    assert.equal(result, null);
  });

  it('should return null for valid JSON but not a table structure', () => {
    const result = parseTableStructure('{"name": "test"}');
    assert.equal(result, null);
  });

  it('should return null for JSON missing required fields', () => {
    const result = parseTableStructure('{"rows": 2, "cols": 3}');
    assert.equal(result, null);
  });

  it('should return null for JSON with invalid cells', () => {
    const result = parseTableStructure(JSON.stringify({
      rows: 1,
      cols: 1,
      cells: [{ row: 0 }], // Missing col, text, is_header
      header_rows: 0,
      header_cols: 0,
    }));
    assert.equal(result, null);
  });

  it('should return null for JSON with non-array cells', () => {
    const result = parseTableStructure(JSON.stringify({
      rows: 1,
      cols: 1,
      cells: 'not-array',
      header_rows: 0,
      header_cols: 0,
    }));
    assert.equal(result, null);
  });

  it('should accept table with zero rows and cols', () => {
    const result = parseTableStructure(JSON.stringify({
      rows: 0,
      cols: 0,
      cells: [],
      header_rows: 0,
      header_cols: 0,
    }));
    assert.ok(result);
    assert.equal(result.rows, 0);
    assert.equal(result.cells.length, 0);
  });
});

// ============================================================================
// serializeTableStructure
// ============================================================================

describe('serializeTableStructure', () => {
  it('should serialize to valid JSON', () => {
    const table = makeSimpleTable();
    const json = serializeTableStructure(table);
    const parsed = JSON.parse(json);
    assert.equal(parsed.rows, 3);
    assert.equal(parsed.cols, 3);
  });

  it('should be reversible with parseTableStructure', () => {
    const table = makeSimpleTable();
    const json = serializeTableStructure(table);
    const parsed = parseTableStructure(json);
    assert.ok(parsed);
    assert.equal(parsed.rows, table.rows);
    assert.equal(parsed.cols, table.cols);
    assert.equal(parsed.cells.length, table.cells.length);
  });
});

// ============================================================================
// buildGrid
// ============================================================================

describe('buildGrid', () => {
  it('should build a grid with correct dimensions', () => {
    const table = makeSimpleTable();
    const grid = buildGrid(table);

    assert.equal(grid.length, 3); // rows
    assert.equal(grid[0].length, 3); // cols
    assert.equal(grid[1].length, 3);
    assert.equal(grid[2].length, 3);
  });

  it('should place cells at correct positions', () => {
    const table = makeSimpleTable();
    const grid = buildGrid(table);

    assert.ok(grid[0][0]);
    assert.equal(grid[0][0].text, 'Name');
    assert.ok(grid[1][1]);
    assert.equal(grid[1][1].text, '20');
    assert.ok(grid[2][2]);
    assert.equal(grid[2][2].text, 'B');
  });

  it('should handle empty table', () => {
    const table = { rows: 0, cols: 0, cells: [], header_rows: 0, header_cols: 0 };
    const grid = buildGrid(table);
    assert.equal(grid.length, 0);
  });

  it('should place origin cell for merged cells', () => {
    const table = makeMergedTable();
    const grid = buildGrid(table);

    // Origin cell should be at (0,0)
    assert.ok(grid[0][0]);
    assert.equal(grid[0][0].text, 'Student Info');
    assert.equal(grid[0][0].colspan, 2);

    // Position (0,1) should be null (covered by span)
    assert.equal(grid[0][1], null);
  });
});

// ============================================================================
// isCoveredBySpan
// ============================================================================

describe('isCoveredBySpan', () => {
  it('should return false for origin cell of a span', () => {
    const table = makeMergedTable();
    assert.equal(isCoveredBySpan(table, 0, 0), false);
  });

  it('should return true for position covered by colspan', () => {
    const table = makeMergedTable();
    assert.equal(isCoveredBySpan(table, 0, 1), true);
  });

  it('should return false for non-spanned cells', () => {
    const table = makeMergedTable();
    assert.equal(isCoveredBySpan(table, 0, 2), false);
    assert.equal(isCoveredBySpan(table, 1, 0), false);
    assert.equal(isCoveredBySpan(table, 1, 1), false);
  });

  it('should return false for non-spanned simple table', () => {
    const table = makeSimpleTable();
    assert.equal(isCoveredBySpan(table, 0, 0), false);
    assert.equal(isCoveredBySpan(table, 1, 1), false);
    assert.equal(isCoveredBySpan(table, 2, 2), false);
  });

  it('should handle rowspan', () => {
    const table = {
      rows: 3,
      cols: 2,
      cells: [
        { row: 0, col: 0, text: 'Merged', is_header: true, scope: 'Row', rowspan: 2 },
        { row: 0, col: 1, text: 'Data 1', is_header: false, scope: 'None' },
        { row: 1, col: 1, text: 'Data 2', is_header: false, scope: 'None' },
        { row: 2, col: 0, text: 'Normal', is_header: false, scope: 'None' },
        { row: 2, col: 1, text: 'Data 3', is_header: false, scope: 'None' },
      ],
      header_rows: 0,
      header_cols: 0,
    };

    // Origin
    assert.equal(isCoveredBySpan(table, 0, 0), false);
    // Covered by rowspan
    assert.equal(isCoveredBySpan(table, 1, 0), true);
    // Not covered
    assert.equal(isCoveredBySpan(table, 2, 0), false);
  });
});

// ============================================================================
// createEmptyTable
// ============================================================================

describe('createEmptyTable', () => {
  it('should create a table with correct dimensions', () => {
    const table = createEmptyTable(4, 5);
    assert.equal(table.rows, 4);
    assert.equal(table.cols, 5);
    assert.equal(table.cells.length, 20); // 4 * 5
  });

  it('should make first row headers', () => {
    const table = createEmptyTable(3, 3);

    // Row 0 should be headers
    const row0 = table.cells.filter((c) => c.row === 0);
    assert.equal(row0.length, 3);
    assert.ok(row0.every((c) => c.is_header));
    assert.ok(row0.every((c) => c.scope === 'Column'));

    // Row 1 should be data
    const row1 = table.cells.filter((c) => c.row === 1);
    assert.equal(row1.length, 3);
    assert.ok(row1.every((c) => !c.is_header));
    assert.ok(row1.every((c) => c.scope === 'None'));
  });

  it('should set header_rows to 1 and header_cols to 0', () => {
    const table = createEmptyTable(3, 3);
    assert.equal(table.header_rows, 1);
    assert.equal(table.header_cols, 0);
  });

  it('should initialize all cells with empty text', () => {
    const table = createEmptyTable(2, 2);
    assert.ok(table.cells.every((c) => c.text === ''));
  });

  it('should handle 1x1 table', () => {
    const table = createEmptyTable(1, 1);
    assert.equal(table.rows, 1);
    assert.equal(table.cols, 1);
    assert.equal(table.cells.length, 1);
    assert.equal(table.cells[0].is_header, true);
    assert.equal(table.header_rows, 1);
  });
});

// ============================================================================
// Integration: toggle + scope workflow
// ============================================================================

describe('Integration: toggle and scope workflow', () => {
  it('should support toggling a cell to header then setting scope', () => {
    let table = makeSimpleTable();

    // Toggle data cell to header
    table = toggleCellType(table, 1, 0);
    let cell = findCell(table, 1, 0);
    assert.ok(cell);
    assert.equal(cell.is_header, true);
    assert.equal(cell.scope, 'Column'); // default

    // Set scope to Row
    table = setCellScope(table, 1, 0, 'Row');
    cell = findCell(table, 1, 0);
    assert.ok(cell);
    assert.equal(cell.scope, 'Row');

    // Toggle back to data
    table = toggleCellType(table, 1, 0);
    cell = findCell(table, 1, 0);
    assert.ok(cell);
    assert.equal(cell.is_header, false);
    assert.equal(cell.scope, 'None');
  });

  it('should support round-trip serialization after edits', () => {
    let table = makeSimpleTable();
    table = toggleCellType(table, 1, 0);
    table = setCellScope(table, 1, 0, 'Row');

    const json = serializeTableStructure(table);
    const parsed = parseTableStructure(json);
    assert.ok(parsed);

    const cell = findCell(parsed, 1, 0);
    assert.ok(cell);
    assert.equal(cell.is_header, true);
    assert.equal(cell.scope, 'Row');
  });
});
