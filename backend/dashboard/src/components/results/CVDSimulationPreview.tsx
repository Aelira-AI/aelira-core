import React, { useState } from 'react';
import { Eye } from 'lucide-react';

// ============================================================================
// Types
// ============================================================================

interface CVDType {
  id: string;
  name: string;
  description: string;
}

interface SimulatedColors {
  [key: string]: string;
}

interface CVDSimulationPreviewProps {
  foregroundColor?: string;
  backgroundColor?: string;
  originalContrast?: number;
  simulatedColors?: SimulatedColors | null;
}

// ============================================================================
// Constants
// ============================================================================

const CVD_TYPES: CVDType[] = [
  { id: 'normal', name: 'Normal Vision', description: 'No color vision deficiency' },
  { id: 'protanopia', name: 'Protanopia', description: 'Red-blind (1% of males)' },
  { id: 'deuteranopia', name: 'Deuteranopia', description: 'Green-blind (1% of males)' },
  { id: 'tritanopia', name: 'Tritanopia', description: 'Blue-blind (very rare)' },
  { id: 'protanomaly', name: 'Protanomaly', description: 'Red-weak (1% of males)' },
  {
    id: 'deuteranomaly',
    name: 'Deuteranomaly',
    description: 'Green-weak (5% of males, most common)',
  },
  { id: 'tritanomaly', name: 'Tritanomaly', description: 'Blue-weak (very rare)' },
  { id: 'achromatopsia', name: 'Achromatopsia', description: 'Total color blindness (very rare)' },
];

// ============================================================================
// Component
// ============================================================================

/**
 * CVD Simulation Preview Component
 *
 * Displays a side-by-side comparison of colors for different types of color vision deficiency
 */
export function CVDSimulationPreview({
  foregroundColor = '#000000',
  backgroundColor = '#ffffff',
  originalContrast = 21,
  simulatedColors = null,
}: CVDSimulationPreviewProps): React.ReactElement {
  const [selectedCVD, setSelectedCVD] = useState<string>('normal');

  // For demonstration purposes, we'll show the original colors
  // In production, simulatedColors would come from the backend RGBlind simulation
  const getSimulatedColor = (type: string, color: string): string => {
    if (simulatedColors && simulatedColors[type]) {
      return simulatedColors[type];
    }
    // Fallback: show original color (backend will provide actual simulations)
    return color;
  };

  const renderColorBox = (cvdType: CVDType): React.ReactElement => {
    const simFg = getSimulatedColor(cvdType.id, foregroundColor);
    const simBg = getSimulatedColor(cvdType.id, backgroundColor);

    return (
      <div
        key={cvdType.id}
        onClick={() => setSelectedCVD(cvdType.id)}
        className={`cursor-pointer p-4 rounded-lg border-2 transition-all ${
          selectedCVD === cvdType.id
            ? 'border-[var(--content-info)] shadow-lg'
            : 'border-[var(--border-primary)] hover:border-[var(--border-accent)]'
        }`}
      >
        <div className="flex items-center space-x-2 mb-2">
          <Eye className="w-4 h-4 text-secondary" />
          <span className="text-xs font-semibold text-primary">{cvdType.name}</span>
        </div>
        <div
          className="w-full h-20 rounded flex items-center justify-center mb-2"
          style={{ backgroundColor: simBg, color: simFg }}
        >
          <span className="font-semibold text-lg">Aa</span>
        </div>
        <p className="text-xs text-secondary">{cvdType.description}</p>
      </div>
    );
  };

  const selectedCVDInfo = CVD_TYPES.find((t) => t.id === selectedCVD);

  return (
    <div className="card">
      <div className="mb-4">
        <h3 className="text-lg font-semibold text-primary mb-2">Color Blindness Simulation</h3>
        <p className="text-sm text-secondary">
          See how this color combination appears to users with different types of color vision
          deficiency. Click on a simulation to enlarge.
        </p>
      </div>

      {/* Original Colors Info */}
      <div className="mb-4 p-3 bg-surface-tertiary rounded border border-[var(--border-primary)]">
        <div className="grid grid-cols-3 gap-4 text-xs">
          <div>
            <p className="text-secondary mb-1">Foreground:</p>
            <div className="flex items-center space-x-2">
              <div
                className="w-6 h-6 rounded border border-[var(--border-primary)]"
                style={{ backgroundColor: foregroundColor }}
              />
              <code className="text-primary">{foregroundColor}</code>
            </div>
          </div>
          <div>
            <p className="text-secondary mb-1">Background:</p>
            <div className="flex items-center space-x-2">
              <div
                className="w-6 h-6 rounded border border-[var(--border-primary)]"
                style={{ backgroundColor: backgroundColor }}
              />
              <code className="text-primary">{backgroundColor}</code>
            </div>
          </div>
          <div>
            <p className="text-secondary mb-1">WCAG Contrast:</p>
            <p className="font-semibold text-primary">
              {originalContrast.toFixed(2)}:1
              {originalContrast >= 7.0 ? ' AAA' : originalContrast >= 4.5 ? ' AA' : ' Fail'}
            </p>
          </div>
        </div>
      </div>

      {/* CVD Simulations Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {CVD_TYPES.map((cvdType) => renderColorBox(cvdType))}
      </div>

      {/* Selected CVD Detail */}
      {selectedCVD !== 'normal' && selectedCVDInfo && (
        <div className="mt-4 p-4 bg-[var(--feature-primary-surface)] rounded border border-[var(--border-accent)]">
          <h4 className="text-sm font-semibold text-[var(--feature-primary-content)] mb-2">
            {selectedCVDInfo.name} Details
          </h4>
          <p className="text-xs text-[var(--feature-primary-content)] mb-2">
            {selectedCVDInfo.description}
          </p>
          <div className="text-xs text-[var(--feature-primary-content)]">
            <p className="mb-1">
              <strong>Affected Population:</strong>{' '}
              {selectedCVD === 'deuteranomaly'
                ? '5% of males (most common)'
                : selectedCVD.includes('anomaly') || selectedCVD.includes('opia')
                  ? '~1% of males'
                  : '<0.01% of population'}
            </p>
            <p>
              <strong>Recommendation:</strong>{' '}
              {selectedCVD.includes('protan') || selectedCVD.includes('deuter')
                ? 'Avoid red/green combinations. Use blue/yellow or patterns/textures.'
                : selectedCVD.includes('tritan')
                  ? 'Avoid blue/yellow combinations. Use red/green instead.'
                  : 'Use high contrast and patterns/textures, not just color.'}
            </p>
          </div>
        </div>
      )}

      {/* Population Statistics */}
      <div className="mt-4 p-3 bg-[var(--feature-info-surface)] rounded border border-[var(--border-primary)]">
        <p className="text-xs text-[var(--feature-info-content)]">
          <strong>Note:</strong> Color vision deficiency affects approximately{' '}
          <strong>8% of males</strong> and <strong>0.5% of females</strong> worldwide. Ensuring
          color accessibility is critical for inclusive design.
        </p>
      </div>
    </div>
  );
}
