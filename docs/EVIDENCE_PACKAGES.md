# Evidence packages

Aelira evidence packages are portable ZIP archives for one scan and its current reviewed remediation. They bind recorded scan observations, reviewer decisions, validator results, audit history, source identity, and remediated-output identity into a machine-readable manifest that can be checked without access to Aelira's database or storage layout.

An evidence package is provenance. It is not a digital signature, notarization, accessibility conformance certificate, legal determination, or proof that a reviewer made the correct decision.

## Download

Use the authenticated review route:

```http
GET /reviews/{scan_id}/audit/package
```

The caller must have access to the scan's tenant. A cross-tenant or unknown scan returns `404`. If the scan points to an output artifact, generation validates that the artifact is the exact current output, has not expired, remains available, and still matches its recorded size, SHA-256, media type, and storage authority. A stale, expired, missing, or altered artifact fails closed.

Document bytes are excluded by default. Either byte stream can be requested explicitly:

```http
GET /reviews/{scan_id}/audit/package?include_source=true
GET /reviews/{scan_id}/audit/package?include_output=true
GET /reviews/{scan_id}/audit/package?include_source=true&include_output=true
```

An included source must exist as a regular stored file and match the scan's recorded byte size and SHA-256. An included output is read through the managed-artifact verifier. The response is an `application/zip` attachment with `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.

## Archive layout

Version 1 contains no more than three entries, in this order:

```text
manifest.json
files/source-<bounded-safe-filename>       # only when explicitly requested
files/output-<bounded-safe-filename>       # only when explicitly requested
```

Member names are relative, sanitized, and at most 128 characters. They never contain a database storage key or server path. ZIP timestamps and permissions are fixed so the same manifest timestamp and input bytes produce byte-identical packages.

The verifier bounds the archive to three members, a 2 MiB manifest, and 512 MiB of total uncompressed content. Generation fails at the same total-content boundary.

## Manifest version 1.0.0

`manifest.json` is UTF-8 JSON with deterministic key ordering. Its top-level fields are:

| Field | Meaning |
|---|---|
| `schema_version` | Semantic version of this manifest contract. |
| `package` | Generation timestamp and Aelira Core tool name/version. |
| `scan` | Existing bounded scan identity and timestamps. |
| `source` | Source identity, origin, safe filename, media type, byte size, SHA-256, timestamps, availability, inclusion flag, and optional archive path. |
| `output` | Current artifact identity, safe filename, media type, byte size, SHA-256, timestamps, expiry, review state, approval-review digest, availability, inclusion flag, and optional archive path. |
| `evidence` | Recorded summary, machine observations, reviewer decisions, validator results, audit trail, and the evidence limitation statement. |

Unavailable or unrecorded values are `null` with an explicit `availability` state. A missing artifact is represented as unavailable; it is not replaced with a plausible ID, digest, result, or review decision. `included: false` and `path: null` mean the identity is recorded but the document bytes are not in the package.

## Compatibility

The verifier supports major version `1`. A later `1.x.y` manifest is compatible: readers must preserve the v1 meaning of known fields and may ignore unknown fields. A different major version is incompatible and fails closed. Producers increment the major version for a breaking field or meaning change, the minor version for backward-compatible fields, and the patch version for clarifications that do not change data shape or meaning.

## Offline verification

Install the repository package, then run:

```console
aelira-evidence-verify evidence-package.zip
```

The verifier reads the archive without extracting it. It rejects malformed ZIP or JSON, duplicate or unsafe member names, unsupported schema majors, a missing declared file, an invalid size or SHA-256 field, a byte-size mismatch, a checksum mismatch, and any undeclared archive member. A successful result reports the schema version and scan ID; it does not validate the truth or accessibility quality of the recorded observations.

Library callers can use `verify_evidence_package(package_bytes)` from `src.education.reports.evidence_package`. The function returns the parsed manifest only after all structural and included-byte checks pass.
