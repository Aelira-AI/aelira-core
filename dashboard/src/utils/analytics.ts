/**
 * Umami analytics helpers: consent check and event/page tracking.
 *
 * Kept separate from components/Analytics.tsx (the script-loader component)
 * so that file only exports a component — a requirement for Vite fast
 * refresh. The component loads the Umami script; these helpers talk to the
 * global it exposes.
 */

interface ConsentData {
  analytics?: boolean;
}

interface UmamiInstance {
  track: (
    eventOrOptions: string | { website: string; url: string },
    data?: Record<string, unknown>
  ) => void;
}

declare global {
  interface Window {
    umami?: UmamiInstance;
  }
}

export const UMAMI_WEBSITE_ID = import.meta.env.VITE_UMAMI_WEBSITE_ID || '';
export const UMAMI_URL = import.meta.env.VITE_UMAMI_URL || '';

export const CONSENT_COOKIE_NAME = 'aelira-cookie-consent';

/**
 * Check if analytics consent is given
 */
export function hasAnalyticsConsent(): boolean {
  try {
    const stored = localStorage.getItem(CONSENT_COOKIE_NAME);
    if (!stored) return false;
    const consent: ConsentData = JSON.parse(stored);
    return consent.analytics === true;
  } catch {
    return false;
  }
}

/**
 * Track a custom event with Umami
 * Only sends if analytics consent is given
 *
 * @param eventName - Name of the event to track
 * @param eventData - Optional data to attach to the event
 *
 * @example
 * trackEvent('scan_started', { type: 'pdf' });
 * trackEvent('feature_used', { feature: 'bulk_upload' });
 */
export function trackEvent(eventName: string, eventData: Record<string, unknown> = {}): void {
  if (typeof window === 'undefined') return;

  // Check consent before tracking
  if (!hasAnalyticsConsent()) return;

  // Umami exposes a global umami object
  if (window.umami?.track) {
    window.umami.track(eventName, eventData);
  }
}

/**
 * Track a page view manually
 * Useful for SPA navigation that Umami might miss
 *
 * @param url - Optional URL to track (defaults to current path)
 */
export function trackPageView(url?: string): void {
  if (typeof window === 'undefined') return;

  // Check consent before tracking
  if (!hasAnalyticsConsent()) return;

  if (window.umami?.track && UMAMI_WEBSITE_ID) {
    window.umami.track({
      website: UMAMI_WEBSITE_ID,
      url: url || window.location.pathname,
    });
  }
}
