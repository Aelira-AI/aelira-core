// ============================================================================
// Reading Order Utility Functions
// Pure functions for manipulating reading order data, used by
// ReadingOrderOverlay and testable independently.
// ============================================================================

// ============================================================================
// Types
// ============================================================================

export interface ReadingBlock {
  index: number;
  bbox: [number, number, number, number]; // [x0, y0, x1, y1] in PDF coordinates
  text: string;
  pageNum: number;
  isHeader?: boolean;
  isFooter?: boolean;
  isPageNumber?: boolean;
}

export interface ReadingOrderData {
  pageWidth: number;
  pageHeight: number;
  blocks: ReadingBlock[];
  originalOrder: number[]; // block indices in original order
  newOrder: number[]; // block indices in remediated order
}

export interface ScaledPosition {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface ArrowPath {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  path: string; // SVG path data for the arrowhead
}

// ============================================================================
// Reorder Blocks
// ============================================================================

/**
 * Return blocks arranged in the specified order.
 * The order array contains block indices that map to blocks by their `index`
 * property.
 */
export function reorderBlocks(
  blocks: ReadingBlock[],
  order: number[],
): ReadingBlock[] {
  const blockMap = new Map<number, ReadingBlock>();
  for (const block of blocks) {
    blockMap.set(block.index, block);
  }

  const result: ReadingBlock[] = [];
  for (const idx of order) {
    const block = blockMap.get(idx);
    if (block) {
      result.push(block);
    }
  }
  return result;
}

// ============================================================================
// Move Block
// ============================================================================

/**
 * Move a block from one position to another in the order array.
 * Returns a new array with the block relocated. Both fromIdx and toIdx
 * are positional indices within the order array (not block indices).
 */
export function moveBlock(
  order: number[],
  fromIdx: number,
  toIdx: number,
): number[] {
  if (fromIdx < 0 || fromIdx >= order.length) return [...order];
  if (toIdx < 0 || toIdx >= order.length) return [...order];
  if (fromIdx === toIdx) return [...order];

  const result = [...order];
  const [moved] = result.splice(fromIdx, 1);
  result.splice(toIdx, 0, moved);
  return result;
}

// ============================================================================
// Scale Block
// ============================================================================

/**
 * Convert a block's PDF coordinates to CSS positions scaled to the container.
 * PDF coordinates have origin at bottom-left; CSS has origin at top-left.
 * The bbox is [x0, y0, x1, y1] where y0 < y1 in PDF space (y0 is bottom).
 */
export function scaleBlock(
  block: ReadingBlock,
  containerWidth: number,
  containerHeight: number,
  pageWidth: number,
  pageHeight: number,
): ScaledPosition {
  const scaleX = containerWidth / pageWidth;
  const scaleY = containerHeight / pageHeight;

  const [x0, y0, x1, y1] = block.bbox;

  // PDF y-axis is bottom-up, CSS is top-down
  const left = x0 * scaleX;
  const top = (pageHeight - y1) * scaleY;
  const width = (x1 - x0) * scaleX;
  const height = (y1 - y0) * scaleY;

  return { left, top, width, height };
}

// ============================================================================
// Arrow Points
// ============================================================================

/**
 * Calculate the SVG line and arrowhead path between two consecutive blocks.
 * Connects from the center-bottom of block1 to the center-top of block2.
 * Returns coordinates and an SVG path string for the arrowhead triangle.
 */
export function getArrowPoints(
  block1Position: ScaledPosition,
  block2Position: ScaledPosition,
): ArrowPath {
  // Start from center-bottom of first block
  const x1 = block1Position.left + block1Position.width / 2;
  const y1 = block1Position.top + block1Position.height;

  // End at center-top of second block
  const x2 = block2Position.left + block2Position.width / 2;
  const y2 = block2Position.top;

  // Calculate arrowhead at the end point
  const angle = Math.atan2(y2 - y1, x2 - x1);
  const arrowSize = 6;

  // Shorten the line slightly so the arrowhead sits at the end
  const endX = x2 - Math.cos(angle) * arrowSize;
  const endY = y2 - Math.sin(angle) * arrowSize;

  // Arrowhead triangle points
  const tipX = x2;
  const tipY = y2;
  const leftX = tipX - arrowSize * Math.cos(angle - Math.PI / 6);
  const leftY = tipY - arrowSize * Math.sin(angle - Math.PI / 6);
  const rightX = tipX - arrowSize * Math.cos(angle + Math.PI / 6);
  const rightY = tipY - arrowSize * Math.sin(angle + Math.PI / 6);

  const path = `M ${tipX} ${tipY} L ${leftX} ${leftY} L ${rightX} ${rightY} Z`;

  return { x1, y1: y1, x2: endX, y2: endY, path };
}

// ============================================================================
// Artifact Detection
// ============================================================================

/**
 * Check if a block is an artifact (header, footer, or page number).
 * Artifacts are typically excluded from logical reading order.
 */
export function isArtifact(block: ReadingBlock): boolean {
  return Boolean(block.isHeader || block.isFooter || block.isPageNumber);
}

/**
 * Get the artifact badge label for a block.
 * Returns "H" for header, "F" for footer, "#" for page number, or null.
 */
export function getArtifactLabel(block: ReadingBlock): string | null {
  if (block.isHeader) return 'H';
  if (block.isFooter) return 'F';
  if (block.isPageNumber) return '#';
  return null;
}
