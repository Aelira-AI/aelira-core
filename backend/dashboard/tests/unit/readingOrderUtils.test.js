/**
 * Unit tests for reading order utility functions.
 * Uses Node.js native test runner (same pattern as tableStructureUtils.test.js).
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  reorderBlocks,
  moveBlock,
  scaleBlock,
  getArrowPoints,
  isArtifact,
  getArtifactLabel,
} from '../../src/components/review/readingOrderUtils.ts';

// ============================================================================
// Test fixtures
// ============================================================================

/** Simple 4-block page layout */
function makeSimpleBlocks() {
  return [
    { index: 0, bbox: [72, 700, 540, 750], text: 'Title heading', pageNum: 1 },
    { index: 1, bbox: [72, 500, 540, 690], text: 'First paragraph of body text', pageNum: 1 },
    { index: 2, bbox: [72, 300, 300, 490], text: 'Left column content', pageNum: 1 },
    { index: 3, bbox: [310, 300, 540, 490], text: 'Right column content', pageNum: 1 },
  ];
}

/** Blocks with artifacts (header, footer, page number) */
function makeBlocksWithArtifacts() {
  return [
    { index: 0, bbox: [72, 750, 540, 780], text: 'University Header', pageNum: 1, isHeader: true },
    { index: 1, bbox: [72, 500, 540, 700], text: 'Main content', pageNum: 1 },
    { index: 2, bbox: [72, 20, 540, 50], text: 'Page footer text', pageNum: 1, isFooter: true },
    { index: 3, bbox: [500, 20, 540, 40], text: '1', pageNum: 1, isPageNumber: true },
  ];
}

// ============================================================================
// reorderBlocks
// ============================================================================

describe('reorderBlocks', () => {
  it('should return blocks in the specified order', () => {
    const blocks = makeSimpleBlocks();
    const ordered = reorderBlocks(blocks, [3, 2, 1, 0]);

    assert.equal(ordered.length, 4);
    assert.equal(ordered[0].index, 3);
    assert.equal(ordered[1].index, 2);
    assert.equal(ordered[2].index, 1);
    assert.equal(ordered[3].index, 0);
  });

  it('should return same order when order matches original', () => {
    const blocks = makeSimpleBlocks();
    const ordered = reorderBlocks(blocks, [0, 1, 2, 3]);

    assert.equal(ordered.length, 4);
    assert.equal(ordered[0].index, 0);
    assert.equal(ordered[3].index, 3);
  });

  it('should handle partial order (subset of blocks)', () => {
    const blocks = makeSimpleBlocks();
    const ordered = reorderBlocks(blocks, [0, 2]);

    assert.equal(ordered.length, 2);
    assert.equal(ordered[0].index, 0);
    assert.equal(ordered[1].index, 2);
  });

  it('should skip indices not found in blocks', () => {
    const blocks = makeSimpleBlocks();
    const ordered = reorderBlocks(blocks, [0, 99, 1]);

    assert.equal(ordered.length, 2);
    assert.equal(ordered[0].index, 0);
    assert.equal(ordered[1].index, 1);
  });

  it('should return empty array for empty order', () => {
    const blocks = makeSimpleBlocks();
    const ordered = reorderBlocks(blocks, []);
    assert.equal(ordered.length, 0);
  });

  it('should return empty array for empty blocks', () => {
    const ordered = reorderBlocks([], [0, 1, 2]);
    assert.equal(ordered.length, 0);
  });

  it('should preserve block data', () => {
    const blocks = makeSimpleBlocks();
    const ordered = reorderBlocks(blocks, [2]);

    assert.equal(ordered[0].text, 'Left column content');
    assert.deepEqual(ordered[0].bbox, [72, 300, 300, 490]);
    assert.equal(ordered[0].pageNum, 1);
  });
});

// ============================================================================
// moveBlock
// ============================================================================

describe('moveBlock', () => {
  it('should move a block forward in the order', () => {
    const order = [0, 1, 2, 3];
    const result = moveBlock(order, 0, 2);

    assert.deepEqual(result, [1, 2, 0, 3]);
  });

  it('should move a block backward in the order', () => {
    const order = [0, 1, 2, 3];
    const result = moveBlock(order, 3, 1);

    assert.deepEqual(result, [0, 3, 1, 2]);
  });

  it('should return a copy when from equals to', () => {
    const order = [0, 1, 2, 3];
    const result = moveBlock(order, 1, 1);

    assert.deepEqual(result, [0, 1, 2, 3]);
    assert.notEqual(result, order); // should be a new array
  });

  it('should handle moving to the beginning', () => {
    const order = [0, 1, 2, 3];
    const result = moveBlock(order, 3, 0);

    assert.deepEqual(result, [3, 0, 1, 2]);
  });

  it('should handle moving to the end', () => {
    const order = [0, 1, 2, 3];
    const result = moveBlock(order, 0, 3);

    assert.deepEqual(result, [1, 2, 3, 0]);
  });

  it('should return a copy for out-of-bounds fromIdx', () => {
    const order = [0, 1, 2];
    const result = moveBlock(order, -1, 1);

    assert.deepEqual(result, [0, 1, 2]);
    assert.notEqual(result, order);
  });

  it('should return a copy for out-of-bounds toIdx', () => {
    const order = [0, 1, 2];
    const result = moveBlock(order, 1, 5);

    assert.deepEqual(result, [0, 1, 2]);
    assert.notEqual(result, order);
  });

  it('should not mutate the original array', () => {
    const order = [0, 1, 2, 3];
    moveBlock(order, 0, 3);

    assert.deepEqual(order, [0, 1, 2, 3]);
  });

  it('should handle two-element array', () => {
    const order = [0, 1];
    const result = moveBlock(order, 0, 1);

    assert.deepEqual(result, [1, 0]);
  });

  it('should handle adjacent swap forward', () => {
    const order = [0, 1, 2, 3];
    const result = moveBlock(order, 1, 2);

    assert.deepEqual(result, [0, 2, 1, 3]);
  });

  it('should handle adjacent swap backward', () => {
    const order = [0, 1, 2, 3];
    const result = moveBlock(order, 2, 1);

    assert.deepEqual(result, [0, 2, 1, 3]);
  });
});

// ============================================================================
// scaleBlock
// ============================================================================

describe('scaleBlock', () => {
  it('should scale PDF coordinates to container dimensions', () => {
    const block = { index: 0, bbox: [72, 700, 540, 750], text: 'Test', pageNum: 1 };
    // PDF Letter size: 612 x 792, container: 612 x 792 (1:1 scale)
    const pos = scaleBlock(block, 612, 792, 612, 792);

    assert.equal(pos.left, 72);
    assert.equal(pos.top, 792 - 750); // top = pageHeight - y1 = 42
    assert.equal(pos.width, 540 - 72); // 468
    assert.equal(pos.height, 750 - 700); // 50
  });

  it('should handle 2x scaling', () => {
    const block = { index: 0, bbox: [0, 0, 306, 396], text: 'Test', pageNum: 1 };
    // PDF: 612x792, container: 1224x1584 (2x)
    const pos = scaleBlock(block, 1224, 1584, 612, 792);

    assert.equal(pos.left, 0);
    assert.equal(pos.top, (792 - 396) * 2); // 792
    assert.equal(pos.width, 306 * 2); // 612
    assert.equal(pos.height, 396 * 2); // 792
  });

  it('should handle half scaling', () => {
    const block = { index: 0, bbox: [100, 200, 300, 400], text: 'Test', pageNum: 1 };
    // PDF: 612x792, container: 306x396 (0.5x)
    const pos = scaleBlock(block, 306, 396, 612, 792);

    assert.equal(pos.left, 50); // 100 * 0.5
    assert.equal(pos.top, (792 - 400) * 0.5); // 196
    assert.equal(pos.width, 100); // 200 * 0.5
    assert.equal(pos.height, 100); // 200 * 0.5
  });

  it('should handle block at page origin', () => {
    const block = { index: 0, bbox: [0, 0, 100, 100], text: 'Test', pageNum: 1 };
    const pos = scaleBlock(block, 612, 792, 612, 792);

    assert.equal(pos.left, 0);
    assert.equal(pos.top, 792 - 100); // 692 (bottom of page in CSS)
    assert.equal(pos.width, 100);
    assert.equal(pos.height, 100);
  });

  it('should handle block at top of page', () => {
    const block = { index: 0, bbox: [0, 692, 100, 792], text: 'Test', pageNum: 1 };
    const pos = scaleBlock(block, 612, 792, 612, 792);

    assert.equal(pos.left, 0);
    assert.equal(pos.top, 0); // top of page in CSS
    assert.equal(pos.width, 100);
    assert.equal(pos.height, 100);
  });
});

// ============================================================================
// getArrowPoints
// ============================================================================

describe('getArrowPoints', () => {
  it('should generate arrow between vertically aligned blocks', () => {
    const pos1 = { left: 100, top: 0, width: 200, height: 50 };
    const pos2 = { left: 100, top: 100, width: 200, height: 50 };

    const arrow = getArrowPoints(pos1, pos2);

    // Start from center-bottom of block1
    assert.equal(arrow.x1, 200); // 100 + 200/2
    assert.equal(arrow.y1, 50); // 0 + 50

    // End near center-top of block2 (shortened by arrowhead)
    assert.ok(Math.abs(arrow.x2 - 200) < 0.01); // approximately centered
    assert.ok(arrow.y2 < 100); // slightly before block2 top

    // Path should be a valid SVG path string
    assert.ok(arrow.path.startsWith('M '));
    assert.ok(arrow.path.includes('L '));
    assert.ok(arrow.path.endsWith(' Z'));
  });

  it('should generate arrow between horizontally offset blocks', () => {
    const pos1 = { left: 0, top: 0, width: 100, height: 50 };
    const pos2 = { left: 300, top: 100, width: 100, height: 50 };

    const arrow = getArrowPoints(pos1, pos2);

    // Start: center-bottom of block1
    assert.equal(arrow.x1, 50); // 0 + 100/2
    assert.equal(arrow.y1, 50); // 0 + 50

    // End: near center-top of block2
    assert.ok(arrow.x2 > 50); // moved toward block2
    assert.ok(arrow.y2 < 100); // slightly above block2 top
  });

  it('should return valid SVG path string', () => {
    const pos1 = { left: 0, top: 0, width: 100, height: 50 };
    const pos2 = { left: 0, top: 200, width: 100, height: 50 };

    const arrow = getArrowPoints(pos1, pos2);

    // The path should contain 3 points (M, L, L, Z) for the arrowhead triangle
    const parts = arrow.path.split(' ');
    assert.ok(parts.includes('M'));
    assert.ok(parts.includes('Z'));
    // Should have two L commands
    const lCount = parts.filter(p => p === 'L').length;
    assert.equal(lCount, 2);
  });
});

// ============================================================================
// isArtifact
// ============================================================================

describe('isArtifact', () => {
  it('should return true for header blocks', () => {
    const block = { index: 0, bbox: [0, 0, 100, 100], text: 'Header', pageNum: 1, isHeader: true };
    assert.equal(isArtifact(block), true);
  });

  it('should return true for footer blocks', () => {
    const block = { index: 0, bbox: [0, 0, 100, 100], text: 'Footer', pageNum: 1, isFooter: true };
    assert.equal(isArtifact(block), true);
  });

  it('should return true for page number blocks', () => {
    const block = { index: 0, bbox: [0, 0, 100, 100], text: '1', pageNum: 1, isPageNumber: true };
    assert.equal(isArtifact(block), true);
  });

  it('should return false for regular content blocks', () => {
    const block = { index: 0, bbox: [0, 0, 100, 100], text: 'Content', pageNum: 1 };
    assert.equal(isArtifact(block), false);
  });

  it('should return false when all artifact flags are false', () => {
    const block = { index: 0, bbox: [0, 0, 100, 100], text: 'Content', pageNum: 1, isHeader: false, isFooter: false, isPageNumber: false };
    assert.equal(isArtifact(block), false);
  });

  it('should return false when artifact flags are undefined', () => {
    const block = { index: 0, bbox: [0, 0, 100, 100], text: 'Content', pageNum: 1 };
    assert.equal(isArtifact(block), false);
  });
});

// ============================================================================
// getArtifactLabel
// ============================================================================

describe('getArtifactLabel', () => {
  it('should return "H" for header blocks', () => {
    const block = { index: 0, bbox: [0, 0, 100, 100], text: 'Header', pageNum: 1, isHeader: true };
    assert.equal(getArtifactLabel(block), 'H');
  });

  it('should return "F" for footer blocks', () => {
    const block = { index: 0, bbox: [0, 0, 100, 100], text: 'Footer', pageNum: 1, isFooter: true };
    assert.equal(getArtifactLabel(block), 'F');
  });

  it('should return "#" for page number blocks', () => {
    const block = { index: 0, bbox: [0, 0, 100, 100], text: '1', pageNum: 1, isPageNumber: true };
    assert.equal(getArtifactLabel(block), '#');
  });

  it('should return null for regular content blocks', () => {
    const block = { index: 0, bbox: [0, 0, 100, 100], text: 'Content', pageNum: 1 };
    assert.equal(getArtifactLabel(block), null);
  });

  it('should prioritize header over footer', () => {
    const block = { index: 0, bbox: [0, 0, 100, 100], text: 'Ambiguous', pageNum: 1, isHeader: true, isFooter: true };
    assert.equal(getArtifactLabel(block), 'H');
  });
});
