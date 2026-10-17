import React, { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { APP_NAME, titleForPath } from '../../utils/routeTitles';

// ============================================================================
// RouteAnnouncer
// ============================================================================

/**
 * Keeps document.title in step with the current route (WCAG 2.4.2) and tells
 * assistive technology that a client-side navigation happened. A single-page
 * app does not reload, so without this every route keeps the first page's
 * title and a screen reader hears nothing when the view changes.
 */
export function RouteAnnouncer(): React.ReactElement {
  const { pathname } = useLocation();
  const [announcement, setAnnouncement] = useState('');
  const firstRender = useRef(true);

  useEffect(() => {
    const title = titleForPath(pathname);
    document.title = title;
    if (firstRender.current) {
      // The page load itself is announced by the browser.
      firstRender.current = false;
      return;
    }
    setAnnouncement(title.replace(` | ${APP_NAME}`, ''));
  }, [pathname]);

  return (
    <div className="sr-only" role="status" aria-live="polite" aria-atomic="true" data-route-announcer>
      {announcement}
    </div>
  );
}
