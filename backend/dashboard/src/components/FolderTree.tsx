import React, { useState, useEffect, useCallback } from 'react';
import { ChevronRight, ChevronDown, Folder as FolderIcon, FolderOpen, Check, Loader2 } from 'lucide-react';
import { apiClient } from '../api/client';

// ============================================================================
// Types
// ============================================================================

type Provider = 'google' | 'microsoft';

interface Folder {
  id: string;
  name: string;
  parent_id?: string;
}

interface SelectedFolder {
  provider: Provider;
  folder_id: string;
  folder_name: string;
  folder_path: string;
}

interface SyncedFolder {
  folder_id: string;
  folder_name?: string;
  folder_path?: string;
  provider?: Provider;
}

interface FolderTreeNodeProps {
  folder: Folder;
  provider: Provider;
  selectedFolders: SelectedFolder[];
  syncedFolders: SyncedFolder[];
  onToggleSelect: (folder: SelectedFolder) => void;
  level?: number;
}

interface FolderTreeProps {
  provider: Provider;
  onClose?: () => void;
  onSave?: () => void;
}

// ============================================================================
// FolderTreeNode Component
// ============================================================================

function FolderTreeNode({
  folder,
  provider,
  selectedFolders,
  syncedFolders,
  onToggleSelect,
  level = 0,
}: FolderTreeNodeProps): React.ReactElement {
  const [isExpanded, setIsExpanded] = useState<boolean>(false);
  const [children, setChildren] = useState<Folder[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [loaded, setLoaded] = useState<boolean>(false);

  const isSelected = selectedFolders.some((f) => f.folder_id === folder.id);
  const isSynced = syncedFolders.some((f) => f.folder_id === folder.id);

  const handleExpand = async (): Promise<void> => {
    if (!isExpanded && !loaded) {
      // Fetch children when expanding for the first time
      setLoading(true);
      try {
        const endpoint =
          provider === 'google' ? '/google/drive/folders' : '/microsoft/onedrive/folders';

        const response = await apiClient.get<{ folders: Folder[] }>(endpoint, {
          params: { parent_id: folder.id },
        });

        setChildren(response.data.folders || []);
        setLoaded(true);
      } catch (err) {
        console.error('Failed to load subfolders:', err);
      } finally {
        setLoading(false);
      }
    }
    setIsExpanded(!isExpanded);
  };

  const handleSelect = (): void => {
    onToggleSelect({
      provider,
      folder_id: folder.id,
      folder_name: folder.name,
      folder_path: folder.parent_id ? `${folder.name}` : folder.name,
    });
  };

  return (
    <div className="folder-tree-node">
      <div
        className="flex items-center gap-2 py-1 px-2 rounded hover:bg-opacity-50 transition-colors"
        style={{
          paddingLeft: `${level * 20 + 8}px`,
          backgroundColor: isSelected ? 'var(--surface-tertiary)' : 'transparent',
        }}
      >
        {/* Expand/collapse button */}
        <button
          onClick={handleExpand}
          className="flex items-center justify-center w-5 h-5"
          style={{ color: 'var(--content-secondary)' }}
          aria-expanded={isExpanded}
          aria-label={`${isExpanded ? 'Collapse' : 'Expand'} ${folder.name}`}
        >
          {loading ? (
            <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
          ) : isExpanded ? (
            <ChevronDown className="w-4 h-4" aria-hidden="true" />
          ) : (
            <ChevronRight className="w-4 h-4" aria-hidden="true" />
          )}
        </button>

        {/* Folder icon */}
        <div style={{ color: 'var(--accent-primary)' }} aria-hidden="true">
          {isExpanded ? <FolderOpen className="w-4 h-4" /> : <FolderIcon className="w-4 h-4" />}
        </div>

        {/* Checkbox */}
        <label className="flex items-center gap-2 flex-1 cursor-pointer">
          <input
            type="checkbox"
            checked={isSelected || isSynced}
            disabled={isSynced}
            onChange={handleSelect}
            className="w-4 h-4 rounded transition-colors"
            style={{
              accentColor: 'var(--accent-primary)',
              cursor: isSynced ? 'not-allowed' : 'pointer',
            }}
          />
          <span className="text-sm font-medium" style={{ color: 'var(--content-primary)' }}>
            {folder.name}
          </span>
          {isSynced && (
            <span
              className="flex items-center gap-1 text-xs px-2 py-0.5 rounded-full"
              style={{
                backgroundColor: 'var(--status-success-bg)',
                color: 'var(--status-success-text)',
              }}
            >
              <Check className="w-3 h-3" aria-hidden="true" />
              Synced
            </span>
          )}
        </label>
      </div>

      {/* Child folders */}
      {isExpanded && children.length > 0 && (
        <div className="folder-tree-children">
          {children.map((child) => (
            <FolderTreeNode
              key={child.id}
              folder={child}
              provider={provider}
              selectedFolders={selectedFolders}
              syncedFolders={syncedFolders}
              onToggleSelect={onToggleSelect}
              level={level + 1}
            />
          ))}
        </div>
      )}

      {/* No subfolders message */}
      {isExpanded && !loading && children.length === 0 && (
        <div
          className="text-xs py-1"
          style={{
            paddingLeft: `${(level + 1) * 20 + 36}px`,
            color: 'var(--content-tertiary)',
          }}
        >
          No subfolders
        </div>
      )}
    </div>
  );
}

// ============================================================================
// FolderTree Component
// ============================================================================

/**
 * FolderTree Component
 *
 * Displays a hierarchical folder tree with checkboxes for selecting folders to sync.
 * Supports lazy loading of subfolders and tracks selected folders.
 *
 * Privacy-critical: Only explicitly selected folders will be synced, not entire drives.
 */
export function FolderTree({ provider, onClose, onSave }: FolderTreeProps): React.ReactElement {
  const [rootFolders, setRootFolders] = useState<Folder[]>([]);
  const [syncedFolders, setSyncedFolders] = useState<SyncedFolder[]>([]);
  const [selectedFolders, setSelectedFolders] = useState<SelectedFolder[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      // Fetch root folders
      const endpoint =
        provider === 'google' ? '/google/drive/folders' : '/microsoft/onedrive/folders';

      const foldersResponse = await apiClient.get<{ folders: Folder[] }>(endpoint);
      setRootFolders(foldersResponse.data.folders || []);

      // Fetch currently synced folders
      const syncedResponse = await apiClient.get<{ folders: SyncedFolder[] }>(
        '/integrations/sync-folders',
        {
          params: { provider },
        }
      );
      setSyncedFolders(syncedResponse.data.folders || []);
    } catch (err) {
      console.error('Failed to load folders:', err);
      setError('Failed to load folders. Please try again.');
    } finally {
      setLoading(false);
    }
  }, [provider]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleToggleSelect = (folder: SelectedFolder): void => {
    setSelectedFolders((prev) => {
      const exists = prev.some((f) => f.folder_id === folder.folder_id);
      if (exists) {
        return prev.filter((f) => f.folder_id !== folder.folder_id);
      } else {
        return [...prev, folder];
      }
    });
  };

  const handleSave = async (): Promise<void> => {
    setSaving(true);
    setError(null);
    try {
      // Add newly selected folders
      for (const folder of selectedFolders) {
        await apiClient.post('/integrations/sync-folders', folder);
      }

      // Refresh synced folders list
      await fetchData();
      setSelectedFolders([]);

      // Notify parent and close
      if (onSave) {
        onSave();
      }
      if (onClose) {
        onClose();
      }
    } catch (err) {
      console.error('Failed to save folder selection:', err);
      const typedError = err as { response?: { data?: { detail?: string } } };
      setError(typedError.response?.data?.detail || 'Failed to save folder selection. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const providerName = provider === 'google' ? 'Google Drive' : 'Microsoft OneDrive';

  return (
    <div className="folder-tree-container flex flex-col h-full">
      {/* Header */}
      <div className="mb-4">
        <h2 className="text-xl font-bold mb-2" style={{ color: 'var(--content-primary)' }}>
          Select Folders to Sync
        </h2>
        <p className="text-sm" style={{ color: 'var(--content-secondary)' }}>
          Choose which folders from {providerName} you want to scan for accessibility issues. Only
          selected folders will be synced (privacy-conscious).
        </p>
      </div>

      {/* Error message */}
      {error && (
        <div
          className="mb-4 p-3 rounded-lg text-sm"
          style={{
            backgroundColor: 'var(--status-error-bg)',
            color: 'var(--status-error-text)',
          }}
          role="alert"
        >
          {error}
        </div>
      )}

      {/* Folder tree */}
      <div
        className="flex-1 overflow-y-auto rounded-lg border p-4 mb-4"
        style={{
          backgroundColor: 'var(--surface-secondary)',
          borderColor: 'var(--border-primary)',
        }}
      >
        {loading ? (
          <div className="flex items-center justify-center h-32">
            <Loader2
              className="w-6 h-6 animate-spin"
              style={{ color: 'var(--accent-primary)' }}
              aria-label="Loading folders"
            />
          </div>
        ) : rootFolders.length === 0 ? (
          <div className="text-center py-8" style={{ color: 'var(--content-secondary)' }}>
            No folders found in your {providerName}.
          </div>
        ) : (
          <div className="space-y-1">
            {rootFolders.map((folder) => (
              <FolderTreeNode
                key={folder.id}
                folder={folder}
                provider={provider}
                selectedFolders={selectedFolders}
                syncedFolders={syncedFolders}
                onToggleSelect={handleToggleSelect}
                level={0}
              />
            ))}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between gap-4">
        <div className="text-sm" style={{ color: 'var(--content-secondary)' }}>
          {selectedFolders.length > 0 && (
            <span>
              {selectedFolders.length} folder{selectedFolders.length !== 1 ? 's' : ''} selected
            </span>
          )}
          {syncedFolders.length > 0 && (
            <span className="ml-4">
              {syncedFolders.length} folder{syncedFolders.length !== 1 ? 's' : ''} currently synced
            </span>
          )}
        </div>

        <div className="flex gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            style={{
              backgroundColor: 'var(--surface-tertiary)',
              color: 'var(--content-primary)',
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={selectedFolders.length === 0 || saving}
            className="px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
            style={{
              backgroundColor: 'var(--interactive-primary-bg)',
              color: 'var(--interactive-primary-fg)',
            }}
          >
            {saving ? (
              <span className="flex items-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                Saving...
              </span>
            ) : (
              `Add ${selectedFolders.length} Folder${selectedFolders.length !== 1 ? 's' : ''}`
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

export default FolderTree;
