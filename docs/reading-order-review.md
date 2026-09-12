# Reading-order review

On a document's Review page, choose **Show comparison** to inspect the original PDF or the current saved PDF. The panel is read-only and can be collapsed without hiding the review filters.

Each version is read independently from checksum-verified bytes. The page image is rendered from those same bytes. The ordered list follows the PDF structure tree, including supported MCID/ParentTree links and text alternatives; it does not use visual extraction order or a remediation proposal as evidence of saved order. Alternatives are identified because assistive technologies may announce them differently. This view is not an accessibility conformance verdict.

Numbered highlights are provided only for uniquely matched complete painted lines. Repeated text, multiline content, and replacement or alternative text may have no unambiguous visual location. Their sourced text remains in the ordered list without a guessed highlight.

Choose a page and switch between **Original PDF** and **Saved PDF**. Use **Refresh comparison** to re-read the current artifact. Missing originals and missing, expired, superseded or invalid saved artifacts are reported independently. Untagged or unresolved structures do not receive a fabricated fallback order. The viewer does not edit PDF tags or provide a save action.

## Supported evidence and limits

The initial implementation supports page-stream marked content, explicit or ParentTree-resolved page ownership, text alternatives, and rotated/cropped page geometry. Annotation references and form/other content-stream scopes that cannot be resolved safely remain unavailable. Non-PDF files are not visualized.

Inspection is bounded to 50 MiB per file, 500 pages, 2,000 returned text segments, 200,000 Unicode code points, bounded stream expansion, and a page image of at most 1,200 pixels on its longest edge. Streams without filters, single-Flate streams and checked JPEG images are supported; other filter chains remain unavailable. Before rendering, page content is limited to 1 MiB per page, 4 MiB of cumulative stream invocations, 20,000 operations and 20 million cumulative image pixels across invocations. Repeated references count repeatedly. Form XObjects, Type 3 fonts, patterns, shadings, soft masks and inline images are unsupported in this initial preview subset.

Parsing uses the application's PDF libraries, without separate process-level isolation or a hard execution deadline. These bounds are defensive checks, not CPU or memory guarantees or claims of complete PDF support.

`GET /api/reviews/{scan_id}/reading-order?page=1` uses the authenticated scan scope, including course restrictions for LTI staff, and returns independent `source` and `saved` snapshots with safe status/reason fields. Responses are not cacheable. Saved evidence is bound to the scan's current artifact and remains previewable for review before approval; viewing does not approve, publish or modify anything.

Course-scoped Review access currently supports Canvas file bindings only. Other LMS course launches cannot use matching Canvas course identifiers. Department-wide administrators retain their department scope. The same restrictions apply to Review actions, evidence exports, queue rows and aggregate counts.

Regression coverage is in `tests/test_reading_order_snapshot.py`, `tests/test_review_reading_order.py`, `tests/test_review_authorization.py`, and `dashboard/tests/unit/readingOrderComparison.test.js`.
