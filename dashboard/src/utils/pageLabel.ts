// Route → page label mapping for the header breadcrumb.


const ROUTE_LABELS: Record<string, string> = {
  '/dashboard': 'Overview',
  '/upload': 'New Scan',
  '/bulk-upload': 'Bulk Upload',
  '/history': 'Scan History',
  '/issues': 'Issues',
  '/compliance': 'Compliance',
  '/integrations': 'Integrations',
  '/integrations/settings': 'Integration Settings',
  '/integrations/files': 'Cloud Files',
  '/integrations/canvas': 'Canvas',
  '/integrations/brightspace': 'Brightspace',
  '/settings': 'Settings',
  '/admin': 'Admin',
  '/review-queue': 'Review Queue',
};

const OPAQUE_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function derivePageLabel(pathname: string): string {
  if (ROUTE_LABELS[pathname]) return ROUTE_LABELS[pathname];
  if (pathname.startsWith('/scan/')) return 'Scan Details';
  // The id after /review/ is a scan UUID, never a label. The page heading names the file.
  if (pathname.startsWith('/review/')) return 'Document Review';
  if (pathname.startsWith('/focus-order/')) return 'Focus Order';
  if (pathname.startsWith('/remediate/')) return 'Remediate';
  if (pathname.startsWith('/canvas/courses/')) {
    return pathname.endsWith('/review') ? 'Content Review' : 'Course Content';
  }
  const segments = pathname.split('/').filter(Boolean);
  // An opaque id is not a page name: label the page by the segment before it.
  while (segments.length > 1 && OPAQUE_ID.test(segments[segments.length - 1])) segments.pop();
  const segment = segments.pop() ?? '';
  return segment
    ? segment.charAt(0).toUpperCase() + segment.slice(1).replace(/-/g, ' ')
    : 'Dashboard';
}
