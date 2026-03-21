# Dashboard Umami Event Tracking

**Date:** 2026-02-18
**Status:** Approved

## Problem

The dashboard at `dashboard.aelira.ai` has the Umami tracking script and a `trackEvent()` utility ready, but zero custom event calls. We can see pageviews but nothing about user engagement, feature adoption, conversion, or churn signals.

## Approach

Direct `trackEvent()` calls in each component. The existing utility in `components/Analytics.tsx` handles GDPR consent. No new abstractions.

## Event Catalog (18 high-signal events)

### Authentication & Onboarding

| Event Name | Page | Trigger | Data |
|---|---|---|---|
| `dash-signup-submit` | Signup | Form submitted | `{ has_tier_intent }` |
| `dash-login-method` | Login | Any auth method used | `{ method: "magic_link" \| "google" \| "microsoft" \| "api_key" }` |
| `dash-magic-link-verified` | VerifyMagicLink | Verification succeeds | `{}` |
| `dash-onboarding-step` | Dashboard | Welcome banner step click | `{ step: "upload" \| "integrations" \| "guide" }` |

### Core Workflow

| Event Name | Page | Trigger | Data |
|---|---|---|---|
| `dash-scan-type-selected` | Upload | Scan type card click | `{ scan_type, is_locked }` |
| `dash-upload-started` | FileUploader | "Scan N files" button | `{ file_count, scan_type, auto_remediate }` |
| `dash-website-scan-started` | WebsiteScanner | "Scan Website" click | `{ crawl_depth, max_pages }` |
| `dash-scan-viewed` | ScanDetail | Page loaded with results | `{ scan_type, score, issue_count }` |
| `dash-remediate-started` | Remediate | "Start Remediation" click | `{ scan_type }` |
| `dash-download-fixed` | FileUploader/ScanDetail | Download remediated file | `{ scan_type }` |
| `dash-download-report` | Dashboard/History/ScanDetail | Download report PDF | `{}` |

### Conversion & Revenue

| Event Name | Page | Trigger | Data |
|---|---|---|---|
| `dash-upgrade-click` | Settings/QuotaBar/FeatureGate | Any upgrade CTA | `{ source, target_tier }` |
| `dash-integration-connect` | Integrations | "Connect" button | `{ provider }` |
| `dash-bulk-upload-started` | BulkUpload | "Start Processing" click | `{ file_count, concurrency }` |

### Engagement & Retention

| Event Name | Page | Trigger | Data |
|---|---|---|---|
| `dash-issue-autofix` | Issues | Auto-fix clicked | `{ scope: "bulk" \| "single" }` |
| `dash-certificate-download` | ComplianceActions | Download certificate | `{ level }` |
| `dash-history-filter` | History | Filter by scan type | `{ filter }` |

### Churn Signal

| Event Name | Page | Trigger | Data |
|---|---|---|---|
| `dash-account-delete-initiated` | Settings | "Delete Account" button | `{}` |

## Files to Modify

1. `pages/Signup.tsx` — `dash-signup-submit`
2. `pages/Login.tsx` — `dash-login-method`
3. `pages/VerifyMagicLink.tsx` — `dash-magic-link-verified`
4. `pages/Dashboard.tsx` — `dash-onboarding-step`, `dash-download-report`
5. `pages/Upload.tsx` — `dash-scan-type-selected`
6. `components/upload/FileUploader.tsx` — `dash-upload-started`, `dash-download-fixed`, `dash-download-report`
7. `components/upload/WebsiteScanner.tsx` — `dash-website-scan-started`
8. `pages/ScanDetail.tsx` — `dash-scan-viewed`, `dash-download-fixed`, `dash-download-report`
9. `pages/Remediate.tsx` — `dash-remediate-started`
10. `pages/History.tsx` — `dash-history-filter`, `dash-download-report`
11. `pages/Issues.tsx` — `dash-issue-autofix`
12. `pages/BulkUpload.tsx` — `dash-bulk-upload-started`
13. `pages/Settings.tsx` — `dash-upgrade-click`, `dash-account-delete-initiated`
14. `pages/Integrations.tsx` — `dash-integration-connect`
15. `components/QuotaBar.tsx` — `dash-upgrade-click`
16. `components/FeatureGate.tsx` — `dash-upgrade-click`
17. `components/ComplianceActions.tsx` — `dash-certificate-download`

## What We're NOT Tracking

- Profile edits, theme toggles, API key show/copy, session management
- Alert settings, issue notes, individual issue expand/collapse
- Admin actions, breadcrumb navigation, modal open/close
- FocusOrderDetail (mock data), CloudFiles browsing
