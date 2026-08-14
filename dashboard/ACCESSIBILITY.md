# Dashboard Accessibility Audit

## Overview

This document summarizes the accessibility audit conducted for the Aelira dashboard in preparation for the February 2026 pilot launch.

**Audit Date:** January 2026
**WCAG Target:** WCAG 2.1 Level AA

---

## Audit Summary

### Already Compliant

The dashboard was built with accessibility in mind. The following features were already implemented:

#### Skip Navigation (WCAG 2.4.1)
- **App.tsx**: Skip link to main content implemented
- Location: Lines 48-54, appears on focus for keyboard users

#### Heading Hierarchy
- All pages follow proper h1 > h2 > h3 structure
- Dashboard, History, Upload, Settings pages verified

#### ARIA Landmarks and Roles
- **Navbar**: `role="banner"` on header element
- **Sidebar**: `aria-label="Main navigation"` on nav, `role="dialog"` on mobile menu
- **ToastContext**: `role="alert"` and `aria-live="polite"` on notifications
- **History**: `role="dialog"` and `aria-modal="true"` on delete confirmation modal

#### Keyboard Navigation
- **Sidebar**: Focus trap on mobile menu, Escape key closes menu
- **All interactive elements**: Focus visible states implemented
- **Filter buttons**: `aria-pressed` state for toggle buttons

#### Form Accessibility
- Labels associated with form controls
- Checkboxes have associated labels
- `aria-expanded` and `aria-controls` on expandable elements

---

## Fixes Applied (January 2026)

### Loading Spinner Pattern
**Files:** Dashboard.tsx, Issues.tsx, History.tsx, ScanDetail.tsx, Remediate.tsx, AdminDashboard.tsx, FocusOrderDetail.tsx, IntegrationsSettings.tsx

Added accessible loading states with proper ARIA:
- `role="status"` on wrapper div
- `aria-label="Loading [context]"` for screen readers
- `aria-hidden="true"` on spinner icon
- `sr-only` text for additional context

**Pattern:**
```tsx
<div role="status" aria-label="Loading dashboard">
  <Loader aria-hidden="true" />
  <span className="sr-only">Loading dashboard data...</span>
</div>
```

### Error Message Pattern
**Files:** Dashboard.tsx, ScanDetail.tsx, Login.tsx, FocusOrderDetail.tsx

Added `role="alert"` to error message containers.

### Success/Error Messages in Login.tsx
- Added `role="status"` and `aria-live="polite"` to success messages
- Added `role="alert"` to error messages
- Added `aria-hidden="true"` to all decorative icons

### Expandable API Key Section (Login.tsx)
- Added `aria-expanded={showApiKeyLogin}` to toggle button
- Added `aria-controls="api-key-form"` linking to form
- Added `id="api-key-form"` to collapsible form

### Icon Accessibility
**Files:** Dashboard.tsx, Login.tsx, ThemeToggle.tsx, ToastContext.tsx, Remediate.tsx, IntegrationsSettings.tsx

Added `aria-hidden="true"` to all decorative icons:
- FileText, TrendingUp, AlertTriangle (Dashboard)
- Mail, Key, Loader2, ChevronUp/Down, Google, Microsoft (Login)
- Sun, Moon (ThemeToggle)
- Toast type icons, X close icon (ToastContext)
- XCircle, ArrowLeft, FileText (Remediate)
- ArrowLeft, Bell, X (IntegrationsSettings)

---

## Fixes Applied (Earlier)

### 1. FileUploader.tsx

**Issue:** Icon-only buttons missing accessible labels

**Fixed:**
- Line 1047: Added `aria-label` to remove file button
- Line 1082: Added `aria-label` to cancel scan button
- Added `aria-hidden="true"` to decorative X icons

Line numbers drift as the file grows (it's 1400+ lines and actively maintained) — search for `aria-label` in `src/components/upload/FileUploader.tsx` rather than trusting numbers here.

### 2. FolderSelectionModal.tsx

**Issue:** Modal missing dialog role attributes

**Fixed:**
- Lines 50-52: Added `role="dialog"`, `aria-modal="true"`, and `aria-label`
- Line 71: Added `aria-hidden="true"` to close button icon

### 3. FolderTree.tsx

**Issue:** Tree navigation missing accessibility attributes

**Fixed:**
- Lines 112-113: Added `aria-expanded` and `aria-label` to expand/collapse buttons
- Lines 116-120: Added `aria-hidden="true"` to chevron/loader icons
- Line 125: Added `aria-hidden="true"` to folder icon container
- Line 153: Added `aria-hidden="true"` to check icon
- Line 320: Added `role="alert"` to error message
- Lines 339, 400: Added `aria-label`/`aria-hidden` to loader icons

---

## Components Verified

| Component | Status | Notes |
|-----------|--------|-------|
| App.tsx | Pass | Skip link implemented |
| Navbar.tsx | Pass | role="banner", aria-labels on buttons |
| Sidebar.tsx | Pass | aria-label on nav, focus trap, escape key |
| ThemeToggle.tsx | Pass | Dynamic aria-label for theme state |
| ToastContext.tsx | Pass | role="alert", aria-live="polite" |
| Dashboard.tsx | Fixed | Loading spinner, error state, icons |
| History.tsx | Fixed | Loading spinner |
| ScanDetail.tsx | Fixed | Loading spinner, error state |
| Issues.tsx | Fixed | Loading spinner |
| Remediate.tsx | Fixed | Loading spinner, icons |
| AdminDashboard.tsx | Fixed | Loading spinner |
| FocusOrderDetail.tsx | Fixed | Loading spinner, error state |
| IntegrationsSettings.tsx | Fixed | Loading spinner, icons |
| Upload.tsx | Pass | Proper heading hierarchy |
| Settings.tsx | Pass | Proper heading hierarchy, sections labeled |
| FileUploader.tsx | Fixed | Added aria-labels to icon buttons |
| FolderTree.tsx | Fixed | Added aria-expanded, aria-labels |
| FolderSelectionModal.tsx | Fixed | Added dialog role attributes |
| ComplianceActions.tsx | Pass | Proper heading hierarchy |
| IssueList.tsx | Pass | Semantic structure |
| DeleteConfirmModal | Pass | role="dialog", aria-modal, aria-labelledby |
| CookieBanner.tsx | Pass | role="dialog"/aria-modal/aria-labelledby on the banner and preferences modal; decorative dismiss backdrop is role="presentation" aria-hidden |
| Sidebar.tsx (mobile overlay) | Pass | Decorative dismiss backdrop is role="presentation" aria-hidden; explicit close button for keyboard users |
| charts/IssuesByTypeChart.tsx | Pass | Chart type switcher is a labelled role="group" with aria-label="Chart type"; buttons carry aria-pressed |
| charts/WCAGCriteriaChart.tsx | Pass | role="img" with a dynamic aria-label describing the chart |
| hooks/useFocusTrap.ts | Pass | Tab/Shift+Tab focus trap with wraparound and focus restore; used by review/TableStructureEditor.tsx's scope selector |

---

## Keyboard Navigation Flows

The following critical flows can be completed using keyboard only:

1. **Login Flow**: Tab through form fields, Enter to submit
2. **Upload Flow**: Tab to file dropzone, interact with keyboard, Tab to options
3. **Scan Results**: Tab through issues, links, and action buttons
4. **Settings**: Tab through all sections and form controls
5. **Session Management**: Tab to revoke buttons with clear labels

---

## Color Contrast

- Light mode: All text meets 4.5:1 contrast ratio
- Dark mode: All text meets 4.5:1 contrast ratio
- Interactive elements: Visible focus indicators in both modes

---

## Screen Reader Support — How to Verify

This document records what ARIA/semantic structure is implemented, not screen-reader test session results. To verify screen reader behavior yourself, follow [`SCREEN_READER_TESTING.md`](SCREEN_READER_TESTING.md), which covers:
- Navigation landmarks
- Button purposes / accessible names
- Form label announcements
- Dynamic content via `aria-live` regions
- Modal focus trapping and announcement

---

## Recommendations for Future Development

1. **Always add `aria-label` to icon-only buttons**
2. **Always add `aria-hidden="true"` to decorative icons**
3. **Use `role="alert"` for error messages that appear dynamically**
4. **Test keyboard navigation for new interactive components**
5. **Maintain heading hierarchy (h1 for page title, h2 for sections)**

---

## Testing Tools Used

- Manual keyboard navigation testing
- VoiceOver (macOS) screen reader testing
- Code review for ARIA attributes

---

---

## Automated Scan Results (January 19, 2026)

Scope: this dashboard only. (An earlier version of this doc also reported results for a separate marketing-website codebase that isn't part of this repository — removed; not reproducible here.)

### axe-core Scan Results

axe-core version at scan time: v4.11.0. The dependency has since moved to v4.11.1 (patch release, pulled in transitively via `axe-playwright` — see `package-lock.json`); re-run the scan with the current version rather than trusting this number going forward.

| Site | URL Tested | Violations | Status |
|------|------------|------------|--------|
| Dashboard | http://localhost:5173/login | 0 | ✅ Pass |

**All critical and serious accessibility violations found in that scan were resolved.**

### Issues Fixed After Scanning

1. **aria-prohibited-attr (dashboard)**: Added `role="region"` to Toast container in ToastContext.tsx
2. **Missing main landmark (dashboard)**: Changed Login wrapper from `<div>` to `<main>`

To reproduce or extend this scan, run the Playwright + axe-playwright suite (`npm run test`) against the routes you care about — see [`SCREEN_READER_TESTING.md`](SCREEN_READER_TESTING.md) for manual coverage of routes an automated scan won't reach (modals, dynamic content, keyboard-only flows).

---

*Last Updated: January 19, 2026 — scoped to the dashboard's login page only; components added since (CookieBanner, charts, useFocusTrap) have not been re-scanned. Re-run before relying on "0 violations" as current.*
