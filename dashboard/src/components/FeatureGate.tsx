import React, { ReactNode, ComponentType } from 'react';
import { Link } from 'react-router-dom';
import { Lock, ArrowRight, Sparkles } from 'lucide-react';
import { useFeatureAccess } from '../hooks/useFeatureAccess';
import type { FeatureKey } from '../utils/featureAccess';
import { trackEvent } from './Analytics';

// ============================================================================
// Types
// ============================================================================

interface FeatureGateProps {
  feature: FeatureKey;
  featureName: string;
  description: string;
  children: ReactNode;
}

interface RequireFeatureOptions {
  feature: FeatureKey;
  featureName: string;
  description: string;
}

// ============================================================================
// FeatureGate Component
// ============================================================================

/**
 * FeatureGate - Wraps content that requires a specific feature
 *
 * If the user doesn't have access to the feature, shows an upgrade prompt instead.
 */
export function FeatureGate({
  feature,
  featureName,
  description,
  children,
}: FeatureGateProps): React.ReactElement {
  const { hasFeature, tierDisplayName, isFreeTier } = useFeatureAccess();

  // If user has access to this feature, just render children
  if (hasFeature(feature)) {
    return <>{children}</>;
  }

  // Otherwise, show upgrade prompt
  return (
    <div className="p-8">
      <div className="max-w-2xl mx-auto">
        <div className="card">
          <div className="p-8 text-center">
            {/* Lock Icon */}
            <div
              className="w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-6"
              style={{ backgroundColor: 'var(--surface-accent-subtle)' }}
            >
              <Lock className="w-8 h-8" style={{ color: 'var(--content-accent)' }} />
            </div>

            {/* Title */}
            <h1 className="text-2xl font-bold text-primary mb-2">{featureName} Not Available</h1>

            {/* Description */}
            <p className="text-secondary mb-6 max-w-md mx-auto">{description}</p>

            {/* Current Plan Badge */}
            <div
              className="inline-flex items-center gap-2 px-4 py-2 rounded-full mb-6"
              style={{ backgroundColor: 'var(--surface-tertiary)' }}
            >
              <span className="text-sm text-secondary">Current plan:</span>
              <span className="text-sm font-medium text-primary">{tierDisplayName}</span>
            </div>

            {/* Upgrade CTA */}
            <div className="space-y-4">
              <a
                href="/pricing"
                className="btn-primary inline-flex items-center gap-2 px-6 py-3"
                onClick={() => trackEvent('dash-upgrade-click', { source: 'feature_gate', target_tier: 'unknown' })}
              >
                <Sparkles className="w-4 h-4" />
                Upgrade to Unlock
                <ArrowRight className="w-4 h-4" />
              </a>

              <p className="text-xs text-tertiary">
                {isFreeTier
                  ? 'Upgrade to a Department plan for unlimited access to all features.'
                  : 'Contact support to upgrade your plan.'}
              </p>
            </div>

            {/* Back Link */}
            <div className="mt-8 pt-6 border-t" style={{ borderColor: 'var(--border-subtle)' }}>
              <Link
                to="/dashboard"
                className="text-sm font-medium hover:opacity-80 transition-opacity"
                style={{ color: 'var(--content-accent)' }}
              >
                ← Back to Dashboard
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// RequireFeature HOC
// ============================================================================

/**
 * RequireFeature - Higher-order component version for wrapping entire pages
 *
 * Usage:
 * export default RequireFeature(BulkUploadPage, {
 *   feature: 'showBulkUpload',
 *   featureName: 'Bulk Upload',
 *   description: 'Upload and process multiple documents at once.'
 * });
 */
export function RequireFeature<P extends object>(
  Component: ComponentType<P>,
  { feature, featureName, description }: RequireFeatureOptions
): React.FC<P> {
  return function FeatureGuardedComponent(props: P): React.ReactElement {
    return (
      <FeatureGate feature={feature} featureName={featureName} description={description}>
        <Component {...props} />
      </FeatureGate>
    );
  };
}
