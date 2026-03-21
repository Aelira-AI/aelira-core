import React, { MouseEvent } from 'react';
import { X } from 'lucide-react';
import { FolderTree } from './FolderTree';

// ============================================================================
// Types
// ============================================================================

interface FolderSelectionModalProps {
  provider: 'google' | 'microsoft';
  isOpen: boolean;
  onClose: () => void;
  onSave?: () => void;
}

// ============================================================================
// Component
// ============================================================================

/**
 * FolderSelectionModal Component
 *
 * Modal wrapper for the FolderTree component.
 * Provides a full-screen modal with backdrop for selecting folders to sync.
 */
export function FolderSelectionModal({
  provider,
  isOpen,
  onClose,
  onSave,
}: FolderSelectionModalProps): React.ReactElement | null {
  if (!isOpen) return null;

  const handleBackdropClick = (e: MouseEvent<HTMLDivElement>): void => {
    // Close on backdrop click
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  const handleContentClick = (e: MouseEvent<HTMLDivElement>): void => {
    e.stopPropagation();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: 'rgba(0, 0, 0, 0.5)' }}
      onClick={handleBackdropClick}
      role="dialog"
      aria-modal="true"
      aria-label={`Select folders to sync from ${provider}`}
    >
      <div
        className="relative w-full max-w-4xl max-h-[90vh] rounded-xl shadow-xl overflow-hidden"
        style={{
          backgroundColor: 'var(--surface-primary)',
        }}
        onClick={handleContentClick}
      >
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 z-10 p-2 rounded-lg transition-colors"
          style={{
            backgroundColor: 'var(--surface-tertiary)',
            color: 'var(--content-primary)',
          }}
          aria-label="Close modal"
        >
          <X className="w-5 h-5" aria-hidden="true" />
        </button>

        {/* Content */}
        <div className="p-6 h-full">
          <FolderTree provider={provider} onClose={onClose} onSave={onSave} />
        </div>
      </div>
    </div>
  );
}

export default FolderSelectionModal;
