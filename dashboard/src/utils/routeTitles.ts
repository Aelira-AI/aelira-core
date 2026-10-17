import { matchPath } from 'react-router-dom';

// ============================================================================
// Route titles
// ============================================================================

export const APP_NAME = 'Aelira Dashboard';

/** First match wins, so more specific patterns come first. */
const ROUTE_TITLES: ReadonlyArray<readonly [pattern: string, title: string]> = [
  ['/login', 'Sign in'],
  ['/auth/verify', 'Verifying sign-in link'],
  ['/accept-invitation', 'Accept invitation'],
  ['/dashboard', 'Dashboard'],
  ['/upload', 'New scan'],
  ['/bulk-upload', 'Bulk upload'],
  ['/history', 'Scan history'],
  ['/scan/:id', 'Scan details'],
  ['/issues', 'Issues'],
  ['/remediate/:scanId', 'Remediation'],
  ['/review', 'Review queue'],
  ['/review/:scanId', 'Document review'],
  ['/settings', 'Settings'],
  ['/admin', 'Admin'],
  ['/integrations', 'Integrations'],
  ['/integrations/settings', 'Alert settings'],
  ['/alerts', 'Alert settings'],
  ['/integrations/files', 'Cloud files'],
  ['/cloud-files', 'Cloud files'],
  ['/integrations/canvas', 'Canvas courses'],
  ['/integrations/brightspace', 'Brightspace courses'],
  ['/canvas/courses/:courseId/content', 'Canvas course content'],
  ['/canvas/courses/:courseId/content/:cloudFileId/review', 'Review Canvas content'],
  ['/brightspace/courses/:orgUnitId/content', 'Brightspace course content'],
  ['/brightspace/courses/:orgUnitId/content/:cloudFileId/review', 'Review Brightspace content'],
  ['/lti/overview', 'Course overview'],
  ['/lti/go', 'Opening course'],
  ['/lti/course/:courseId', 'Course accessibility'],
  ['/lti/course/:courseId/content/:cloudFileId/review', 'Review Brightspace content'],
  ['/lti/report/:scanId', 'Accessibility report'],
  ['/lti/pick/:courseId', 'Choose files'],
];

export function titleForPath(pathname: string): string {
  for (const [pattern, title] of ROUTE_TITLES) {
    if (matchPath({ path: pattern, end: true }, pathname)) return `${title} | ${APP_NAME}`;
  }
  return APP_NAME;
}
