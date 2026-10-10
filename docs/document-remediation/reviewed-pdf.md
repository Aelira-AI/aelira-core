# PDF outcomes and reviewed working files

Scanning measures source-derived accessibility rules. Optional AI descriptions
and judgments about existing alternative text are separate review findings;
they do not change the numeric score. A before/after comparison requires fresh
rule-based measurements bound to the exact source and saved-output checksums.
The score is not a WCAG or PDF/UA conformance declaration.

## What a remediation result means

The results page separates changes requiring human review, manual work remaining,
other applied changes, and withheld, failed or unreported outcomes. A recorded
application means a mutation was saved. A saved-file finding check establishes
only its stated structural presence; it does not establish the correctness of
an image description, reading order or learning content.

Manual outcomes provide a reason, attempted operation and next step when the job
recorded them. Older records without this evidence remain explicitly unreported.
The platform does not infer verification or approval from an old success label.
Terminal jobs do not display an active progress bar.

## Review and rebuild

1. Open **Review changes** from the remediation result and compare the source
   with each proposed change. Approve, edit or reject the individual changes.
2. When decisions are complete, use **Rebuild from reviewed changes** where it
   is offered. The PDF replay applies supported, accepted, source-bound changes
   without generating new descriptions. Rejected changes are left out.
3. Download the **improved working file** and inspect the saved PDF and remaining
   findings. A useful verified draft can remain downloadable while manual work
   is still required. Preserve the original and continue corrections in the
   source document or a suitable PDF editing tool.
4. Review the publication blockers. Approve the exact current file only after
   reviewing its changes and remaining findings. A different artifact, edited
   decision, rejected included change or stale checksum invalidates approval.
5. Where the provider supports it, explicitly write back the approved file.
   Approval and writeback are separate actions. Batch operations apply the same
   file-specific authority and approval requirements to every selected item.

Rebuilding from the original would discard manual edits to the current PDF, so
that operation is unavailable for a working file containing manual PDF edits.
The existing structure editor can continue supported edits to that file.

## Repair boundaries

Automatic reading-order changes are limited to source-bound structures that
preserve content and pass the saved-file checks. Ambiguous columns, unsupported
structures or incomplete comparisons remain manual. Link descriptions use
verified source content and supported annotation groups; arbitrary descriptions
are not invented.

Image repair requires the exact source image and its unambiguous structure
ownership. Empty alternative text cannot stand in for an informative image.
Declaring an image decorative requires semantic review and is distinct from
adding a nonempty Figure description. PDF/UA metadata alone cannot make a
document conformant.

Unreadable font mappings are detected before expensive processing. The recovery
library accepts bounded source-bound reviewed font/semantic manifests and
verifies saved text, paint and geometry. It does not automatically approve a
guessed character mapping or expose a general font-repair editor in the dashboard.

## API surfaces

Managed artifact metadata, download and approval use
`/education/scans/{scan_id}/artifacts/{artifact_id}`. Review decisions use
`/api/reviews/{scan_id}`. A reviewed PDF rebuild at
`POST /education/pdf/remediate/{scan_id}` returns a durable job with HTTP 202.
Supported explicit artifact publication uses the artifact's `/writeback`
endpoint or `/education/artifacts/batch-writeback`.

Every operation retains authenticated tenant, course and file scope. Downloads
check the stored bytes. Queued publication checks current approval again before
calling the provider. Unsupported provider operations remain unavailable; use
the working-file download and the institution's own publication process.

Cloud publication also checks that the scan still belongs to the current source
file and that its version, name and destination have not changed since the upload
was queued. A file marked for rescan cannot publish an older approved result.
Repeating the same request reuses its job; conflicting upload options cannot
start another provider effect for the same artifact.

## Updating an installation

Deploy the API, worker and dashboard from the same version. No new database
migration is required for these changes. Run remediation again from the retained
original to create the new per-finding and output-membership evidence. Earlier
records do not acquire verification or publication approval automatically.
