import React, { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { Layers, GripVertical } from 'lucide-react';
import type {
  ReadingBlock,
  ReadingOrderData,
  ScaledPosition,
} from './readingOrderUtils';
import {
  reorderBlocks,
  moveBlock,
  scaleBlock,
  getArrowPoints,
  isArtifact,
  getArtifactLabel,
} from './readingOrderUtils';

// ============================================================================
// Types
// ============================================================================

interface ReadingOrderOverlayProps {
  data: ReadingOrderData;
  onChange?: (newOrder: number[]) => void;
  readOnly?: boolean;
}

type ViewMode = 'original' | 'remediated';

// ============================================================================
// Block Badge Component
// ============================================================================

interface BlockBadgeProps {
  sequenceNumber: number;
  artifactLabel: string | null;
  isDimmed: boolean;
}

function BlockBadge({
  sequenceNumber,
  artifactLabel,
  isDimmed,
}: BlockBadgeProps): React.ReactElement {
  if (artifactLabel) {
    return (
      <span
        className="absolute -top-2 -left-2 z-10 flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-bold border border-[var(--border-secondary)] bg-[var(--surface-tertiary)] text-[var(--content-tertiary)]"
        aria-label={`Artifact: ${artifactLabel === 'H' ? 'header' : artifactLabel === 'F' ? 'footer' : 'page number'}`}
      >
        {artifactLabel}
      </span>
    );
  }

  return (
    <span
      className={`absolute -top-2 -left-2 z-10 flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-bold border ${
        isDimmed
          ? 'border-[var(--border-secondary)] bg-[var(--surface-tertiary)] text-[var(--content-tertiary)]'
          : 'border-[var(--accent)] bg-[var(--accent-solid)] text-white'
      }`}
    >
      {sequenceNumber}
    </span>
  );
}

// ============================================================================
// Positioned Block Component
// ============================================================================

interface PositionedBlockProps {
  block: ReadingBlock;
  position: ScaledPosition;
  sequenceNumber: number;
  isDraggable: boolean;
  isDragOver: boolean;
  onDragStart: (e: React.DragEvent, orderIndex: number) => void;
  onDragOver: (e: React.DragEvent, orderIndex: number) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent, orderIndex: number) => void;
  orderIndex: number;
}

function PositionedBlock({
  block,
  position,
  sequenceNumber,
  isDraggable,
  isDragOver,
  onDragStart,
  onDragOver,
  onDragLeave,
  onDrop,
  orderIndex,
}: PositionedBlockProps): React.ReactElement {
  const artifact = isArtifact(block);
  const artifactLabel = getArtifactLabel(block);

  // Truncate text for display
  const displayText =
    block.text.length > 60 ? block.text.slice(0, 57) + '...' : block.text;

  return (
    <div
      className={`absolute rounded border transition-all duration-100 ${
        artifact
          ? 'border-[var(--border-secondary)] bg-[var(--surface-tertiary)]/40'
          : 'border-[var(--accent)]/40 bg-[var(--accent)]/5'
      } ${isDragOver ? 'ring-2 ring-[var(--accent)] ring-offset-1' : ''} ${
        isDraggable ? 'cursor-grab active:cursor-grabbing' : ''
      }`}
      style={{
        left: `${position.left}px`,
        top: `${position.top}px`,
        width: `${position.width}px`,
        height: `${position.height}px`,
      }}
      draggable={isDraggable}
      onDragStart={(e) => onDragStart(e, orderIndex)}
      onDragOver={(e) => onDragOver(e, orderIndex)}
      onDragLeave={onDragLeave}
      onDrop={(e) => onDrop(e, orderIndex)}
      aria-label={`Block ${sequenceNumber}: ${block.text.slice(0, 50)}${artifact ? ' (artifact)' : ''}`}
    >
      <BlockBadge
        sequenceNumber={sequenceNumber}
        artifactLabel={artifactLabel}
        isDimmed={artifact}
      />

      {/* Drag handle */}
      {isDraggable && (
        <span className="absolute -top-2 -right-2 z-10 flex items-center justify-center w-5 h-5 rounded-full bg-[var(--surface-secondary)] border border-[var(--border-secondary)] text-[var(--content-tertiary)]">
          <GripVertical className="w-3 h-3" aria-hidden="true" />
        </span>
      )}

      {/* Text preview - only show if block is tall enough */}
      {position.height > 20 && (
        <div
          className={`px-1.5 py-1 overflow-hidden text-[9px] leading-tight ${
            artifact
              ? 'text-[var(--content-tertiary)]'
              : 'text-[var(--content-secondary)]'
          }`}
          style={{ maxHeight: `${position.height - 4}px` }}
        >
          {displayText}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Arrow SVG Overlay
// ============================================================================

interface ArrowOverlayProps {
  orderedBlocks: ReadingBlock[];
  positions: Map<number, ScaledPosition>;
  containerWidth: number;
  containerHeight: number;
}

function ArrowOverlay({
  orderedBlocks,
  positions,
  containerWidth,
  containerHeight,
}: ArrowOverlayProps): React.ReactElement {
  const arrows = [];

  for (let i = 0; i < orderedBlocks.length - 1; i++) {
    const pos1 = positions.get(orderedBlocks[i].index);
    const pos2 = positions.get(orderedBlocks[i + 1].index);
    if (!pos1 || !pos2) continue;

    const arrow = getArrowPoints(pos1, pos2);
    arrows.push(
      <g key={`arrow-${i}`}>
        <line
          x1={arrow.x1}
          y1={arrow.y1}
          x2={arrow.x2}
          y2={arrow.y2}
          stroke="var(--accent)"
          strokeWidth="1.5"
          strokeOpacity="0.5"
          strokeDasharray="4 2"
        />
        <path
          d={arrow.path}
          fill="var(--accent)"
          fillOpacity="0.5"
        />
      </g>,
    );
  }

  return (
    <svg
      className="absolute inset-0 pointer-events-none"
      width={containerWidth}
      height={containerHeight}
      aria-hidden="true"
    >
      {arrows}
    </svg>
  );
}

// ============================================================================
// Sidebar Order List
// ============================================================================

interface OrderListProps {
  orderedBlocks: ReadingBlock[];
  isDraggable: boolean;
  dragOverIndex: number | null;
  onDragStart: (e: React.DragEvent, orderIndex: number) => void;
  onDragOver: (e: React.DragEvent, orderIndex: number) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent, orderIndex: number) => void;
}

function OrderList({
  orderedBlocks,
  isDraggable,
  dragOverIndex,
  onDragStart,
  onDragOver,
  onDragLeave,
  onDrop,
}: OrderListProps): React.ReactElement {
  return (
    <div className="space-y-0.5 max-h-[400px] overflow-y-auto">
      {orderedBlocks.map((block, i) => {
        const artifact = isArtifact(block);
        const artifactLabel = getArtifactLabel(block);
        const displayText =
          block.text.length > 40 ? block.text.slice(0, 37) + '...' : block.text;

        return (
          <div
            key={block.index}
            className={`flex items-center gap-2 px-2 py-1 rounded text-xs transition-colors ${
              dragOverIndex === i
                ? 'bg-[var(--accent)]/10 border border-[var(--accent)]/30'
                : 'border border-transparent hover:bg-[var(--surface-secondary)]'
            } ${isDraggable ? 'cursor-grab active:cursor-grabbing' : ''}`}
            draggable={isDraggable}
            onDragStart={(e) => onDragStart(e, i)}
            onDragOver={(e) => onDragOver(e, i)}
            onDragLeave={onDragLeave}
            onDrop={(e) => onDrop(e, i)}
          >
            {isDraggable && (
              <GripVertical
                className="w-3 h-3 text-[var(--content-tertiary)] flex-shrink-0"
                aria-hidden="true"
              />
            )}
            <span
              className={`flex-shrink-0 w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold ${
                artifact
                  ? 'bg-[var(--surface-tertiary)] text-[var(--content-tertiary)] border border-[var(--border-secondary)]'
                  : 'bg-[var(--accent-solid)] text-white'
              }`}
            >
              {artifactLabel ?? i + 1}
            </span>
            <span
              className={`truncate ${
                artifact
                  ? 'text-[var(--content-tertiary)] italic'
                  : 'text-[var(--content-secondary)]'
              }`}
            >
              {displayText}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export function ReadingOrderOverlay({
  data,
  onChange,
  readOnly = false,
}: ReadingOrderOverlayProps): React.ReactElement {
  const [viewMode, setViewMode] = useState<ViewMode>('remediated');
  const [dragFromIndex, setDragFromIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);
  const [currentOrder, setCurrentOrder] = useState<number[]>(data.newOrder);
  const containerRef = useRef<HTMLDivElement>(null);

  // Sync currentOrder when data prop changes (e.g., navigating pages)
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setCurrentOrder(data.newOrder);
    setDragFromIndex(null);
    setDragOverIndex(null);
  }, [data]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Use a fixed container width; height is derived from aspect ratio
  const containerWidth = 500;
  const aspectRatio = data.pageHeight / data.pageWidth;
  const containerHeight = containerWidth * aspectRatio;

  // Determine which order to display
  const activeOrder = viewMode === 'original' ? data.originalOrder : currentOrder;

  // Get ordered blocks
  const orderedBlocks = useMemo(
    () => reorderBlocks(data.blocks, activeOrder),
    [data.blocks, activeOrder],
  );

  // Compute scaled positions for all blocks
  const positions = useMemo(() => {
    const map = new Map<number, ScaledPosition>();
    for (const block of data.blocks) {
      map.set(
        block.index,
        scaleBlock(block, containerWidth, containerHeight, data.pageWidth, data.pageHeight),
      );
    }
    return map;
  }, [data.blocks, containerWidth, containerHeight, data.pageWidth, data.pageHeight]);

  // ---- Drag handlers ----

  const handleDragStart = useCallback(
    (e: React.DragEvent, orderIndex: number): void => {
      if (readOnly || viewMode === 'original') return;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', String(orderIndex));
      setDragFromIndex(orderIndex);
    },
    [readOnly, viewMode],
  );

  const handleDragOver = useCallback(
    (e: React.DragEvent, orderIndex: number): void => {
      if (readOnly || viewMode === 'original') return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      setDragOverIndex(orderIndex);
    },
    [readOnly, viewMode],
  );

  const handleDragLeave = useCallback((_e: React.DragEvent): void => {
    setDragOverIndex(null);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent, toIndex: number): void => {
      e.preventDefault();
      setDragOverIndex(null);

      if (readOnly || viewMode === 'original' || dragFromIndex === null) return;

      const newOrder = moveBlock(currentOrder, dragFromIndex, toIndex);
      setCurrentOrder(newOrder);
      setDragFromIndex(null);
      onChange?.(newOrder);
    },
    [readOnly, viewMode, dragFromIndex, currentOrder, onChange],
  );

  const handleDragEnd = useCallback((): void => {
    setDragFromIndex(null);
    setDragOverIndex(null);
  }, []);

  // ---- Computed state ----

  const isDraggable = !readOnly && viewMode === 'remediated';
  const hasOrderChanges = viewMode === 'remediated' &&
    JSON.stringify(currentOrder) !== JSON.stringify(data.newOrder);

  // ---- Render ----

  if (data.blocks.length === 0) {
    return (
      <div className="border border-[var(--border-primary)] rounded-lg p-6 text-center bg-[var(--surface-secondary)]">
        <Layers
          className="w-8 h-8 mx-auto mb-2 text-[var(--content-tertiary)]"
          aria-hidden="true"
        />
        <p className="text-sm text-[var(--content-tertiary)]">
          No reading order data available
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3" onDragEnd={handleDragEnd}>
      {/* Header bar */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Layers
            className="w-4 h-4 text-[var(--accent)]"
            aria-hidden="true"
          />
          <span className="text-sm font-medium text-[var(--content-primary)]">
            Reading Order
          </span>
          <span className="text-xs text-[var(--content-tertiary)]">
            {data.blocks.length} blocks
          </span>
          {hasOrderChanges && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--feature-warning-surface)] text-[var(--feature-warning-content)]">
              Modified
            </span>
          )}
        </div>

        {/* Before/After toggle */}
        <div
          className="flex rounded-lg border border-[var(--border-primary)] overflow-hidden"
          role="tablist"
          aria-label="Reading order view"
        >
          <button
            role="tab"
            aria-selected={viewMode === 'original'}
            onClick={() => setViewMode('original')}
            className={`px-3 py-1 text-xs font-medium transition-colors ${
              viewMode === 'original'
                ? 'bg-[var(--accent-solid)] text-white'
                : 'bg-[var(--surface-primary)] text-[var(--content-secondary)] hover:bg-[var(--surface-secondary)]'
            }`}
          >
            Original Order
          </button>
          <button
            role="tab"
            aria-selected={viewMode === 'remediated'}
            onClick={() => setViewMode('remediated')}
            className={`px-3 py-1 text-xs font-medium transition-colors ${
              viewMode === 'remediated'
                ? 'bg-[var(--accent-solid)] text-white'
                : 'bg-[var(--surface-primary)] text-[var(--content-secondary)] hover:bg-[var(--surface-secondary)]'
            }`}
          >
            Remediated Order
          </button>
        </div>
      </div>

      {/* Instructions */}
      {isDraggable && (
        <p className="text-xs text-[var(--content-tertiary)]">
          Drag blocks or list items to reorder. Switch to &quot;Original
          Order&quot; to compare.
        </p>
      )}

      {/* Main content: page overlay + sidebar list */}
      <div className="flex gap-4">
        {/* Page overlay container */}
        <div
          ref={containerRef}
          className="relative border border-[var(--border-primary)] rounded-lg bg-white overflow-hidden flex-shrink-0"
          style={{
            width: `${containerWidth}px`,
            height: `${containerHeight}px`,
          }}
          aria-label="PDF page reading order visualization"
        >
          {/* Arrow connections */}
          <ArrowOverlay
            orderedBlocks={orderedBlocks}
            positions={positions}
            containerWidth={containerWidth}
            containerHeight={containerHeight}
          />

          {/* Positioned blocks */}
          {orderedBlocks.map((block, i) => {
            const pos = positions.get(block.index);
            if (!pos) return null;

            return (
              <PositionedBlock
                key={block.index}
                block={block}
                position={pos}
                sequenceNumber={i + 1}
                isDraggable={isDraggable}
                isDragOver={dragOverIndex === i}
                onDragStart={handleDragStart}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                orderIndex={i}
              />
            );
          })}
        </div>

        {/* Sidebar order list */}
        <div className="flex-1 min-w-[180px]">
          <p className="text-xs font-medium text-[var(--content-secondary)] mb-2">
            {viewMode === 'original' ? 'Original' : 'Remediated'} Sequence
          </p>
          <OrderList
            orderedBlocks={orderedBlocks}
            isDraggable={isDraggable}
            dragOverIndex={dragOverIndex}
            onDragStart={handleDragStart}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          />
        </div>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 text-xs text-[var(--content-tertiary)]">
        <div className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-full bg-[var(--accent-solid)]" />
          <span>Content block</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-full bg-[var(--surface-tertiary)] border border-[var(--border-secondary)]" />
          <span>Artifact (H/F/#)</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="inline-block w-6 border-t border-dashed border-[var(--accent)]" />
          <span>Reading flow</span>
        </div>
      </div>
    </div>
  );
}
