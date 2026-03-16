import React from 'react';
import {
  FileText,
  Presentation,
  Calculator,
  Image,
  Video,
  Globe,
  Code,
  BarChart3,
  FileSpreadsheet,
  FileType,
  Lock,
  LucideIcon,
} from 'lucide-react';
import { useFeatureAccess } from '../../hooks/useFeatureAccess';
import { hasScanType } from '../../utils/featureAccess';

// ============================================================================
// Types
// ============================================================================

interface ScanType {
  id: string;
  name: string;
  description: string;
  icon: LucideIcon;
  iconColor: string;
  bgColor: string;
  badge?: string;
}

interface ScanTypeSelectorProps {
  selected: string | null;
  onSelect: (scanType: string) => void;
}

// ============================================================================
// Constants
// ============================================================================

const SCAN_TYPES: ScanType[] = [
  {
    id: 'website',
    name: 'Website',
    description: 'WCAG 2.1 AA scan with AI code fixes',
    icon: Globe,
    iconColor: '#4F46E5',
    bgColor: 'rgba(79, 70, 229, 0.1)',
  },
  {
    id: 'code',
    name: 'Website Code',
    description: 'Upload HTML/CSS/JS files for analysis',
    icon: Code,
    iconColor: '#0891B2',
    bgColor: 'rgba(8, 145, 178, 0.1)',
  },
  {
    id: 'pdf',
    name: 'PDF Documents',
    description: 'Scan PDFs for accessibility issues and OCR',
    icon: FileText,
    iconColor: '#DC2626',
    bgColor: 'rgba(220, 38, 38, 0.1)',
  },
  {
    id: 'word',
    name: 'Word Documents',
    description: 'Check headings, alt text, lists, tables, and links',
    icon: FileType,
    iconColor: '#2563EB',
    bgColor: 'rgba(37, 99, 235, 0.1)',
    badge: 'New',
  },
  {
    id: 'excel',
    name: 'Excel Spreadsheets',
    description: 'Check sheet names, headers, charts, and navigation',
    icon: FileSpreadsheet,
    iconColor: '#16A34A',
    bgColor: 'rgba(22, 163, 74, 0.1)',
    badge: 'New',
  },
  {
    id: 'powerpoint',
    name: 'PowerPoint',
    description: 'Check contrast, alt text, and reading order',
    icon: Presentation,
    iconColor: '#EA580C',
    bgColor: 'rgba(234, 88, 12, 0.1)',
  },
  {
    id: 'latex',
    name: 'LaTeX/Math',
    description: 'Convert equations to accessible MathML',
    icon: Calculator,
    iconColor: '#7C3AED',
    bgColor: 'rgba(124, 58, 237, 0.1)',
  },
  {
    id: 'image',
    name: 'Images',
    description: 'AI alt text + decorative detection + validation',
    icon: Image,
    iconColor: '#F59E0B',
    bgColor: 'rgba(245, 158, 11, 0.1)',
    badge: 'Enhanced',
  },
  {
    id: 'chart',
    name: 'Charts & Graphs',
    description: 'Detailed descriptions for data visualizations',
    icon: BarChart3,
    iconColor: '#0D9488',
    bgColor: 'rgba(13, 148, 136, 0.1)',
  },
  {
    id: 'video',
    name: 'Videos',
    description: 'Captions + audio descriptions + seizure safety',
    icon: Video,
    iconColor: '#9333EA',
    bgColor: 'rgba(147, 51, 234, 0.1)',
    badge: 'Enhanced',
  },
];

// ============================================================================
// Component
// ============================================================================

export function ScanTypeSelector({ selected, onSelect }: ScanTypeSelectorProps): React.ReactElement {
  const { tier, tierDisplayName } = useFeatureAccess();

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {SCAN_TYPES.map((type) => {
        const Icon = type.icon;
        const isSelected = selected === type.id;
        const isAvailable = hasScanType(tier, type.id);

        return (
          <button
            key={type.id}
            onClick={() => isAvailable && onSelect(type.id)}
            disabled={!isAvailable}
            className={`card text-left transition-all ${
              !isAvailable ? 'opacity-60 cursor-not-allowed' : 'hover:shadow-lg'
            }`}
            style={
              isSelected && isAvailable
                ? {
                    boxShadow: '0 0 0 2px var(--border-accent)',
                    backgroundColor: 'var(--surface-accent-subtle)',
                  }
                : undefined
            }
          >
            <div className="flex items-start space-x-4">
              <div className="p-3 rounded-lg relative" style={{ backgroundColor: type.bgColor }}>
                <Icon
                  className="w-6 h-6"
                  style={{ color: isAvailable ? type.iconColor : '#9CA3AF' }}
                />
                {!isAvailable && (
                  <div className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-amber-100 flex items-center justify-center">
                    <Lock className="w-2.5 h-2.5 text-amber-600" />
                  </div>
                )}
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className={`font-semibold ${isAvailable ? 'text-primary' : 'text-tertiary'}`}>
                    {type.name}
                  </h3>
                  {!isAvailable ? (
                    <span
                      className="text-xs px-2 py-0.5 rounded-full font-medium"
                      style={{
                        backgroundColor: 'rgba(245, 158, 11, 0.15)',
                        color: '#D97706',
                      }}
                    >
                      Upgrade
                    </span>
                  ) : (
                    type.badge && (
                      <span
                        className="text-xs px-2 py-0.5 rounded-full font-medium"
                        style={{
                          backgroundColor:
                            type.badge === 'New'
                              ? 'rgba(16, 185, 129, 0.15)'
                              : 'rgba(99, 102, 241, 0.15)',
                          color: type.badge === 'New' ? '#059669' : '#4F46E5',
                        }}
                      >
                        {type.badge}
                      </span>
                    )
                  )}
                </div>
                <p className={`text-sm ${isAvailable ? 'text-secondary' : 'text-tertiary'}`}>
                  {isAvailable ? type.description : `Upgrade from ${tierDisplayName} to access`}
                </p>
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
}
