# Release integrity

A release is accepted only when every gate below succeeds. The policy is fail closed: a missing policy, receipt, signature, SBOM, audit result, or reproducibility result blocks publication.

## Shipped dependency surfaces

There are exactly three shipped dependency surfaces, and CI audits each one rather than scanning unrelated developer manifests:

1. Python runtime dependencies in `requirements.txt` with `pip-audit --requirement requirements.txt --strict`.
2. CLI runtime dependencies in `cli/package-lock.json` with `npm --prefix cli audit --audit-level=high`.
3. Dashboard runtime dependencies in `dashboard/package-lock.json` with `npm --prefix dashboard audit --audit-level=high`.

Pull requests also run dependency review and fail at high severity. Audit failure is never advisory.

## Immutable inputs and tools

External GitHub Actions are pinned to reviewed 40-character commit SHAs, with the reviewed release recorded in a comment. Production Docker base images are pinned by `sha256` digest. Release and audit tools installed during workflows use exact versions, not floating tags. Files downloaded into the API image have fixed SHA-256 checksums that are verified before the image changes to its unprivileged user. Changing any action, base, tool, or checksum requires review of the immutable replacement.

## SBOMs and release assets

The preflight creates three dependency SBOMs in **CycloneDX JSON**:

- `python.cdx.json`
- `cli.cdx.json`
- `dashboard.cdx.json`

The four immutable image legs create **SPDX JSON** assets:

- `aelira-core-api-amd64.spdx.json`
- `aelira-core-api-arm64.spdx.json`
- `aelira-core-dashboard-amd64.spdx.json`
- `aelira-core-dashboard-arm64.spdx.json`

The GitHub Release verifies this exact seven-file set and attaches all seven assets. Missing or additional files fail the release.

## Vulnerability policy and Trivy findings

The runtime image upgrades all available Debian packages before installing its runtime package set. Trivy then scans each immutable image digest with `severity: HIGH,CRITICAL`, `ignore-unfixed: true`, and a nonzero exit code; fixed/actionable HIGH/CRITICAL findings block publication.

HIGH/CRITICAL findings that are currently-unfixed remain visible: affected components are recorded in the attached SBOMs and their vulnerability status remains available through scanner intelligence as fixes become available. Each image/platform leg also uploads a non-blocking JSON inventory of all HIGH/CRITICAL findings as a 90-day workflow artifact. These inventories are not part of the exact seven GitHub Release SBOM assets. Findings are not silently exempted. The publication workflow supplies no `.trivyignore` exemptions, `.trivyignore` contains no CVE entries, and `scripts/verify_trivy_allowlist.py` enforces that policy in CI.

## Signing, identity, provenance, and tags

Release entry requires a GitHub-verified **signed annotated tag** matching strict `vMAJOR.MINOR.PATCH` and the CLI package and lock versions. `release.yml` is the sole version-tag trigger; the Docker and npm workflows are call-only and expose no second publication path.

Each image digest and promoted version index is signed keylessly with cosign through the GitHub Actions OIDC issuer `https://token.actions.githubusercontent.com`. Verification binds the certificate identity to this repository's `.github/workflows/publish-docker.yml` at the release ref and binds the issuer exactly. Build provenance is attested against the immutable subject name and digest, pushed to the registry, and cosign verification must succeed before a receipt can be uploaded.

## Coordinated gated publication and receipts

The workflow provides coordinated gated publication, not an atomic registry mutation. Native builds independently push content-addressed image digests and produce exactly **four receipts**: API and dashboard on `linux/amd64` and `linux/arm64`. Each receipt binds source commit, platform, image name, and immutable digest. All four receipts from the same workflow run and their registry digests are validated before the single promotion job can create version, minor, and latest indexes for either image. A missing or inconsistent leg prevents both image promotions. Publication is then ordered image promotion, npm publication, and GitHub Release creation, so downstream nodes cannot run after a failed prerequisite.

Because immutable matrix legs push before the receipt gate, an interruption or failed leg can leave partial registry state consisting of some unpromoted content-addressed digests. It cannot expose a complete release through the gated version indexes, npm package, or GitHub Release, but the registry changes themselves are not rolled back. Reconciliation is a rerun of the release workflow for the same signed tag: each rerun attempt uses an isolated artifact namespace, regenerates and validates all four receipts, verifies all four referenced registry digests, and only then recreates the coordinated indexes and continues publication. Canonical receipt and SBOM payload filenames stay unchanged inside that namespace. Operators must not promote a partial set or combine artifacts from separate workflow run attempts.

## Reproducibility matrix

CI runs both production Dockerfiles natively on `linux/amd64` and `linux/arm64`. For each of the four image/platform combinations, `scripts/verify_reproducible_image.sh` performs two independent no-cache Buildx builds with `--pull=false`, provenance generation disabled, and SBOM generation disabled; it exports OCI archives, extracts the exact single manifest digest from each `index.json`, and requires equality. `--pull=false` does not prohibit fetching a pinned base image that is absent from the builder: it means Buildx does not force a fresh pull when that immutable base reference is already available. These CI builds do not publish images.

## Requirements hash evaluation

`requirements.txt` currently contains **155** dependency entries, all exact `==` pins, and **no hashes**. Hashes are deferred; they must not be fabricated from one workstation or one platform. Exact pins plus the strict `pip-audit` gate are the current controls, but they are not represented as hash-locked installs.

Security Engineering owns the hash-lock follow-up. Hash enforcement is accepted only after a platform-complete lock is generated and tested on Linux `linux/amd64` and `linux/arm64` for the supported Python 3.12, 3.13, and 3.14 matrix. Acceptance is fail closed and requires all of the following:

1. Every selected distribution artifact for every supported Python/platform combination has an authenticated hash; no unhashed dependency, editable source, floating URL, or unconstrained transitive dependency remains.
2. Clean installs succeed with `pip --require-hashes` on every matrix leg and fail when a required hash is removed or altered.
3. The generated environment matches the reviewed exact dependency set and strict `pip-audit` passes on every matrix leg.
4. CI enforces the lock and its negative tests before documentation may claim hash protection.

Until those criteria are implemented and verified, reviews must state that hashes are deferred and retain the exact-pin and strict-audit controls.
