# Remediation confidence

`confidence` is an optional reported score in the range 0–1. A score is not a
calibrated probability of correctness. Successful application of a change does
not establish a confidence score or prove accessibility conformance.

Omitted scores are `null` (unknown) in remediation results, database rows, review
responses, and JSON evidence exports. A genuine score of `0` stays zero. Unknown
fixes require review and never pass a numeric batch-approval threshold, even a
threshold of zero. A reviewer can explicitly approve an unknown fix after
examining it; that decision binds the exact current evidence digest.

The queue reports an unknown minimum if any fix in the document is unscored and
sorts those documents first. Department averages include reported scores only;
they are `null` when there are no reported scores. CSV exports leave unknown
numeric cells empty. Existing explicit numeric scores keep their meaning.

Visual semantic fixes retain all source, provider, deterministic verification,
contract, and human approval requirements. Their reported scores remain capped
at 0.55. Unknown scores stay unknown and cannot replace any of those checks.

## Historical correction

Migration `20260917_unknown_confidence` follows `20260905_visual_analysis`. Before
upgrading, take a coordinated database backup and pause review/remediation
writers. Inventory the affected legacy records with this read-only query:

```sql
SELECT scan_id, review_status, COUNT(*) AS affected_fixes
FROM scan_fixes
WHERE fix_method = 'ai_generated'
  AND source_kind IS NULL
  AND confidence = 1.0
GROUP BY scan_id, review_status
ORDER BY scan_id, review_status;
```

The generic AI path historically supplied 1.0 by default. Those rows contain no
reliable marker distinguishing an explicitly supplied 1.0 from the default, so
the migration conservatively corrects all matching rows to unknown. Other
methods, visual source kinds, and explicitly stored scores below 1.0 are outside
this correction. Newly supplied explicit scores remain supported.

Each correction appends `confidence_corrected` to the audit log, retaining the
previous score, review state, reviewer, notes, time, and digest bindings. The
current fix digest is recomputed; its approval digest is cleared. Accepted and
other non-rejected fixes become pending, while rejections remain rejected.
Existing audit records are never rewritten.

Current approved artifacts for affected scans that have not been written back
lose their approval and return to pending review, with an additional audit event.
Associated cloud writeback state returns to pending review. Already written-back
artifacts, historical artifact bytes, and previously downloaded evidence exports
remain unchanged; the migration does not undo external publication. Current
exports show the corrected evidence and its correction history. Repeated upgrades
do not duplicate corrections.

Downgrade is refused while any unknown scores remain: replacing unknown with a
number would manufacture evidence. Roll back application changes only to a
version that can handle nullable confidence, or restore a coordinated backup
under the normal recovery procedure. A backup restore also rolls back subsequent
review/audit activity and must account for any external writebacks. Do not delete
audit events or reattach prior approvals to the corrected digest.
