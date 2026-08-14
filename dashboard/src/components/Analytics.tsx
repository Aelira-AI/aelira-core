import { useEffect, useState } from 'react';
import { UMAMI_WEBSITE_ID, UMAMI_URL, hasAnalyticsConsent } from '../utils/analytics';

// ============================================================================
// Component
// ============================================================================

/**
 * Analytics component that loads Umami tracking script
 * Only loads when user has consented to analytics cookies
 */
export function Analytics(): null {
  const [canLoad, setCanLoad] = useState<boolean>(false);
  const [scriptLoaded, setScriptLoaded] = useState<boolean>(false);

  useEffect(() => {
    // Check initial consent
    const checkConsent = (): void => {
      const allowed = hasAnalyticsConsent();
      setCanLoad(allowed);
    };

    checkConsent();

    // Listen for consent changes
    const handleConsentChange = (): void => {
      checkConsent();
    };

    window.addEventListener('cookieConsentChanged', handleConsentChange);

    return () => {
      window.removeEventListener('cookieConsentChanged', handleConsentChange);
    };
  }, []);

  // Load script when consent is given
  useEffect(() => {
    if (!canLoad || !UMAMI_WEBSITE_ID || scriptLoaded) return;

    // Check if script already exists - use timeout to avoid synchronous setState
    const existingScript = document.querySelector(`script[data-website-id="${UMAMI_WEBSITE_ID}"]`);
    if (existingScript) {
      // Use microtask to avoid synchronous setState in effect
      queueMicrotask(() => setScriptLoaded(true));
      return;
    }

    const script = document.createElement('script');
    script.src = `${UMAMI_URL}/script.js`;
    script.async = true;
    script.defer = true;
    script.setAttribute('data-website-id', UMAMI_WEBSITE_ID);
    script.setAttribute('data-domains', 'dashboard.example.com');

    script.onload = (): void => {
      setScriptLoaded(true);
    };

    document.head.appendChild(script);

    return () => {
      // Don't remove script on unmount - it needs to persist
    };
  }, [canLoad, scriptLoaded]);

  // This component doesn't render anything visible
  return null;
}
