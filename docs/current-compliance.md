# Current compliance aggregation

Aelira keeps scan attempts as history, but current dashboard metrics count each
enrolled document once. A scan is an event and is never itself treated as a
stable document unless no safer identity exists.

## Document identity

Identity is always scoped to a department:

- Provider and LMS content uses the tracked `CloudFile.id`, persisted on each
  new provider `Scan.document_id`. Provider IDs, course IDs, and content types
  remain provenance on that row.
- Standalone uploads with a valid SHA-256 use the exact content hash. Uploading
  the same bytes again is another scan attempt of the same document state.
- Website scans use a normalized HTTP or HTTPS URL with a lower-case scheme and
  host, default ports removed, and fragments excluded.
- New hashless non-URL standalone scans receive an opaque `Scan.document_id`
  and remain separate documents. Provider scan creators override that default
  with their `CloudFile.id` and persist `document_source = cloud_file`.

Legacy hashless rows with no `document_id` remain available in scan history but
are excluded from current document stock. Before explicit identities existed,
a failed provider attempt could persist such a row and then lose its scan ID
when the job failed. It is indistinguishable from a legacy hashless standalone
failure, so current aggregation does not guess. Valid content hashes and URLs
remain safe standalone identities for legacy rows.

Provider provenance stays on scan history after an integration is disconnected.
Because the live `CloudFile` inventory is removed at disconnect, those orphaned
provider attempts remain historical and cannot reappear as standalone current
documents.

Filenames and storage paths are never identity inputs. A changed standalone
upload therefore becomes a new document unless a provider-backed source gives
it durable lineage; Aelira does not guess that two different byte sequences are
the same document because their names happen to match.

## Current verified state

For standalone identities, the newest completed scan with a stored
`ScanResult` is current. A later pending or failed attempt does not replace that
measurement. Provider-backed content uses only `CloudFile.last_scan_id`; linked
provider scan history is excluded from standalone aggregation.

Remediation candidates do not change current compliance merely because a job
estimated a better score. The current state advances only after the remediated
content is represented by an authoritative verified scan. This keeps score and
issue evidence on the same measurement.

## Coverage and history

Current responses distinguish:

- `enrolled_document_count`: all current document identities;
- `verified_document_count`: enrolled documents with a current verified result;
- `unverified_document_count`: enrolled documents without one; and
- `historical_scan_count`: every scan attempt, including duplicates and failed
  attempts.

Scores, pages, issue totals, severity totals, scan-type counts, and compliance
bands use verified current documents only. Scan history, activity windows,
trend volume, and evidence-report attempt counts remain historical.
