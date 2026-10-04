# Reading-order review

On a document's Review page, choose **Show comparison** to inspect the original PDF or the current saved PDF. The comparison can be collapsed without hiding the review filters. Choose **Edit PDF structure** to prepare one bounded candidate edit from verified PDF structure targets.

Each version is read independently from checksum-verified bytes. The page image is rendered from those same bytes. The ordered list follows the PDF structure tree, including supported MCID/ParentTree links and text alternatives; it does not use visual extraction order or a remediation proposal as evidence of saved order. Alternatives are identified because assistive technologies may announce them differently. This view is not an accessibility conformance verdict.

Numbered highlights are provided only for uniquely matched complete painted lines. Repeated text, multiline content, and replacement or alternative text may have no unambiguous visual location. Their sourced text remains in the ordered list without a guessed highlight.

Choose a page and switch between **Original PDF** and **Saved PDF**. Use **Refresh comparison** to re-read the current artifact. Missing originals and missing, expired, superseded or invalid saved artifacts are reported independently. Untagged or unresolved structures do not receive a fabricated fallback order. After a confirmed edit save, the comparison reloads current evidence without replacing the editor.

The editor's source selection is explicit. Editing **Original PDF** starts from original bytes and omits changes already present in the current saved artifact. Editing **Current saved PDF** starts from the current artifact. Select a target by its role, page and verified tagged-text excerpt. Targets without usable context are excluded. For a supported leaf P or H1–H6, choose a new H1–H6 level; conversion back to P is not supported. For a supported container, use the Up and Down buttons to arrange every immediate child. For a supported simple Table, explicitly designate its first row as column headers. Cancel discards the draft. Each successful save stores a separate immutable candidate artifact in pending review; it does not approve that candidate or write it back to the cloud source. A stale-state conflict requires **Reload targets** before another save. A failed save retains the previous candidate.

The Review page's reading-order comparison currently follows the scan's local current-artifact pointer. When an explicit `cloud_file_id` selects a cloud file's separate current artifact, the page suppresses that comparison rather than presenting the local artifact as cloud evidence. Cloud-backed Review links need that explicit file context for editing.

## Supported evidence and limits

The initial implementation supports page-stream marked content, explicit or ParentTree-resolved page ownership, text alternatives, and rotated/cropped page geometry. Annotation references and form/other content-stream scopes that cannot be resolved safely remain unavailable. Non-PDF files are not visualized.

Inspection is bounded to 50 MiB per file, 500 pages, 2,000 returned text segments, 200,000 Unicode code points, bounded stream expansion, and a page image of at most 1,200 pixels on its longest edge. Streams without filters, single-Flate streams and checked JPEG images are supported; other filter chains remain unavailable. Before rendering, page content is limited to 1 MiB per page, 4 MiB of cumulative stream invocations, 20,000 operations and 20 million cumulative image pixels across invocations. Repeated references count repeatedly. Form XObjects, Type 3 fonts, patterns, shadings, soft masks and inline images are unsupported in this initial preview subset.

Parsing uses the application's PDF libraries, without separate process-level isolation or a hard execution deadline. These bounds are defensive checks, not CPU or memory guarantees or claims of complete PDF support.

`GET /api/reviews/{scan_id}/reading-order?page=1` uses the authenticated scan scope, including course restrictions for LTI staff, and returns independent `source` and `saved` snapshots with safe status/reason fields. Responses are not cacheable. Saved evidence is bound to the scan's current artifact and remains previewable for review before approval; viewing does not approve, publish or modify anything.

Course-scoped Review access currently supports Canvas file bindings only. Other LMS course launches cannot use matching Canvas course identifiers. Department-wide administrators retain their department scope. The same restrictions apply to Review actions, evidence exports, queue rows and aggregate counts.

Regression coverage is in `tests/test_reading_order_snapshot.py`, `tests/test_review_reading_order.py`, `tests/test_review_authorization.py`, and `dashboard/tests/unit/readingOrderComparison.test.js`.

## Candidate editing primitive

`src/education/pdf_review_candidate.py` provides a direct-library building block
for issue #372. The authenticated editing API below uses it; the dashboard
comparison remains read-only. Callers
can inspect checksum-bound structure targets and request one explicit heading
level change, complete sibling-order permutation, or first-row column-header
designation on a simple tagged table. The table request requires a Table target
and explicitly asserts that its first row contains column headers; the library
does not infer that intent from cell text or appearance. It changes those TD
roles to TH and sets each Table-owned Scope to Column. The input is immutable bytes;
the result contains separate candidate bytes and their checksum, with human
review still required. A target identifier is not an authorization credential.

The primitive accepts existing, unambiguous MCID/ParentTree-bound structure only.
It refuses encrypted/signed/form PDFs, custom role maps/namespaces, missing or
shared structure nodes, mixed content/structure children, and parent alternatives
that could mask a reordered sequence. The existing inspection resource bounds
apply, with additional limits of 2,000 structure nodes, depth 40 and 100,000 graph
visits. Unsupported input raises a safe reason without parser details.

Table-header edits require at least two direct TR rows with the same number of
direct TH/TD cells, at least two columns, one resolved page, and existing
MCID/ParentTree bindings. Each cell contains only direct integer or MCR marked
content; page inheritance stops at the structure root and rejects parent cycles.
Header associations, spans, cell IDs, class-based
attributes, alternate attribute owners, nested and ragged grids, and existing
row headers are outside this operation. Existing unrelated cell content and
attributes are retained. A table whose first row already consists entirely of
TH cells with Scope Column is a no-op refusal.

Before returning, it saves and reopens the candidate, rechecks source bindings and
compares the complete reachable object graph with the explicitly edited graph.
Object numbering, stream compression and trailer transport identifiers can
change during serialization; decoded stream content and graph relationships
must remain equal. JPEG image streams retain their exact encoded bytes and
decoding parameters instead of requiring lossy decoding/re-encoding.
This comparison is not a PDF/UA verdict or a check that the
reviewer's chosen heading/order conveys the intended meaning. Tests also compare
text and rendered page pixels for the supported synthetic fixtures.

The design follows [W3C's tag-order guidance](https://www.w3.org/WAI/WCAG21/Techniques/pdf/PDF3),
[Adobe's PDF table attribute definitions](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/pdfreference1.6.pdf),
[PDF Association's table structure guidance](https://pdfa.org/wp-content/until2016_uploads/2015/12/StructureElementsBestPracticeGuide_2016-01-19.pdf),
and [pikepdf's save and stream contracts](https://pikepdf.readthedocs.io/en/stable/api/main.html).
The wider browser/PDF-assistive-technology journey remains in #372. No existing
review status, artifact or approval is changed by this library alone.

Saved-candidate regression coverage: `tests/test_pdf_review_candidate.py`.

## Authenticated candidate publication

`GET /api/reviews/{scan_id}/pdf-edit-targets?source_kind=original` returns
editable structure targets and a `precondition` object for the verified original.
Each target also has `context`: `status` (`available`, `empty`, or `unavailable`),
one-based `page_numbers`, ordered `segments` with `page_number`, `text`, and
`source` (`MCID`, `ActualText`, or `Alt`), and `truncated`. These excerpts come
from the target's verified marked-content ownership, including descendants for
containers; repeated visible text never establishes target identity. An empty
context means verified structure with no non-whitespace excerpt. Unavailable
means no safe excerpt can be supplied within the response budget. No visual
position or reading-order claim is inferred from the excerpts. A target retains
at most 16 page numbers, four segments, and 240 text characters; additional
content sets `truncated`. The sum of serialized target context objects is capped
at 256 KiB, with omitted contexts explicitly unavailable and truncated.
Use `source_kind=saved` to inspect the exact current artifact instead. For a
cloud-backed scan, department-scoped callers must supply its `cloud_file_id`;
course-scoped Canvas callers are bound to their authorized course file. Cloud
selection uses the CloudFile's current-artifact pointer, which is distinct from
the local scan pointer.

`POST /api/reviews/{scan_id}/pdf-edit-candidates` accepts the following fields:

| Field | Value |
|---|---|
| `source_kind` | `original` or `saved`, matching the inspection |
| `cloud_file_id` | The inspected cloud file ID, or `null` for a local scan |
| `expected_artifact_id` | Inspection's `precondition.expected_artifact_id`, including explicit `null` |
| `expected_source_sha256` | Inspection's `precondition.source_sha256` |
| `expected_state_digest` | Inspection's `precondition.state_digest` |
| `operation` | One of the explicit operations below |

Supported operations use the inspection's checksum-bound target IDs:

- Heading: `{"kind":"heading","target_id":"<target>","level":2}`; level is an integer from 1 through 6.
- Sibling order: `{"kind":"order","target_id":"<parent>","children":["<child-b>","<child-a>"]}`; supply every immediate child exactly once.
- Column headers: `{"kind":"table_column_headers","target_id":"<table>"}`; explicitly designate the first row of a supported simple table as column headers.

The API rejects extra request fields, client PDF bytes and filesystem paths.
Target IDs are limited to 256 characters, order lists to 2,000 entries, and the
encoded operation to 8 KiB; the service also bounds the complete persisted edit
metadata to 8 KiB. The candidate primitive's document and structure limits still
apply. These identifiers and digests are concurrency checks, not authorization.

Candidate generation happens outside database locks. The service persists the
expected source, predecessor and review-state digest in the staging row, then
checks them again under its authority locks before making the artifact current.
A concurrent save, changed review decision, changed course binding or unavailable
predecessor prevents replacement. Stale state returns `409`; refresh inspection
before preparing another edit. Unsupported edits return `422`. A retryable
publication failure returns `503` and retains the prior current artifact.

Successful saves return `201` with the new `artifact_id`, checksum,
`review_status: "pending"` and `needs_review: true`. The artifact records the
operation and source checksum. It does not inherit approval or write back to an
LMS; a cloud replacement resets its writeback status to `pending_review`.
Existing approval gates still apply separately. Both inspection and successful
save responses carry `Cache-Control: no-store`.

The database migration adds paired `edit_precondition` and `edit_provenance`
fields; existing ordinary artifacts keep both fields empty. Route coverage lives
in `tests/test_review_pdf_edit_routes.py`. Real publication, migration, failure
and concurrency checks live in `tests/test_pdf_edit_publication_postgres.py` and
are required in CI's disposable PostgreSQL race job.
