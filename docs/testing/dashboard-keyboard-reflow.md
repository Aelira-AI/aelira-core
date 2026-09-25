# Dashboard keyboard and reflow checks

This checklist covers the Review Queue and Settings corrections in #487, within
the wider accessibility work in #376. It is not an accessibility conformance
statement or a substitute for testing with assistive technology.

## Setup

Use an isolated installation and a synthetic account with reviewable documents
and more than one active session. Open the dashboard in a real browser. Repeat
the layout checks at 320 and 1280 CSS pixels. Do not use real API keys or revoke
another person's sessions during verification.

## Review Queue

1. Open Review Queue. Confirm the Status and Type selects display their selected
   values and remain within the viewport.
2. Tab to a document checkbox. Press Space twice. It must select and then
   deselect the document while the URL remains `/review`.
3. Tab from the checkbox to the document link. Confirm a visible focus indicator.
   Press Enter. It must open that document's review page.
4. Return to the queue. At the narrow width, focus the region named "Documents
   awaiting review" and use the arrow keys to scroll across the table. All
   columns must remain reachable without horizontally scrolling the whole page.
5. Check that document names, selection controls and column headings remain
   readable. The table retains two-dimensional scrolling at narrow widths.

## Settings

1. Open Settings and scroll to API Key Management. Confirm the Refresh button
   remains fully visible and reachable with Tab.
2. Scroll to Active Sessions. Confirm device descriptions, the Current badge,
   addresses, dates and sign-out controls stay inside their cards.
3. At desktop width, confirm the headings and actions still share their rows
   where space permits.

## Recorded scope

The correction was exercised in Chrome 153.0.8010.54 against a native ARM64
container on a DGX Spark. The checkbox, document-link and keyboard-scroll checks
passed; screenshots were inspected at 320 and 1280 CSS pixels. Upload, Issues,
Review Queue, History, Settings and Admin had no whole-page horizontal overflow
at either width in the tested states. This does not cover every subflow, browser,
zoom setting, or assistive-technology combination. Those remain tracked in #376.

The dashboard production build, TypeScript check, ESLint and all 393 existing
dashboard unit tests passed on the Spark. Tests that read backend templates and
branding require the complete repository alongside the dashboard directory.
