# Release integrity

A release is accepted only when every gate below succeeds. The policy is fail closed: a missing policy, receipt, signature, SBOM, audit result, or reproducibility result blocks publication.

## Shipped dependency surfaces

There are four shipped dependency surfaces, and CI audits each one rather than scanning unrelated developer manifests:

1. Python runtime dependencies in `requirements.txt` with `pip-audit --requirement requirements.txt --strict`.
2. CLI runtime dependencies in `cli/package-lock.json` with `npm --prefix cli audit --audit-level=high`.
3. Dashboard runtime dependencies in `dashboard/package-lock.json` with `npm --prefix dashboard audit --audit-level=high`.
4. The image's Pa11y runtime in `pa11y-runtime/package-lock.json` with `npm --prefix pa11y-runtime audit --audit-level=high`.

Pull requests also run dependency review and fail at high severity. Audit failure is never advisory.
The same CI job separately audits `requirements-dev.txt`, which includes the
runtime set and adds contributor tooling; development packages are not a
shipped dependency surface or part of the release SBOM.

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

The runtime image upgrades available packages from its own pinned distribution before installing its runtime package set. Each native image digest is scanned by pinned Trivy 0.74.0 with `severity: HIGH,CRITICAL`, `ignore-unfixed: false`, explicit vulnerability/secret scanners and the comment-only `.trivyignore.inventory`. Its complete unsuppressed inventory is retained as a 90-day workflow artifact before `scripts/verify_trivy_inventory.ts` rejects any HIGH or CRITICAL vulnerability row. Missing or malformed reports, a mismatched image, scanner version or native platform, and a missing OS package result also block publication. FixedVersion may be absent, empty or populated: fixed/actionable and currently-unfixed findings both block this gate, with one narrow exception. A finding in an operating-system package with no FixedVersion stops counting as unresolved only when `.trivy-unfixed-dispositions.json` lists its exact advisory ID, OS family and every matching package name and installed version, with a status, a reason, a review date and an expiry at most 120 days later, and the named disclosure file names the advisory. Status `affected` is accepted for HIGH findings only. Status `not_affected` is accepted for HIGH or CRITICAL findings and requires a VEX justification (`component_not_present`, `vulnerable_code_not_present`, `vulnerable_code_not_in_execute_path`, `vulnerable_code_cannot_be_controlled_by_adversary` or `inline_mitigations_already_exist`). Language-package findings and any finding with a fix available never qualify. An expired, future-dated or malformed disposition fails the gate, and CI fails once the checked-in list expires. Accepted rows are printed by the gate and remain in the retained inventory.

All reported HIGH/CRITICAL findings remain visible in that inventory; neither the dispositions nor repository exemptions remove them from it. The inventories are not part of the exact seven GitHub Release SBOM assets. A clean report establishes only that the configured scanner reported no findings at those severities. Native libraries embedded in wheels or binaries require separate identification, advisory assessment and verified repairs; undetected components and a distribution's lower severity assignment do not resolve a known affected library.

The existing fixed/actionable blocking scan remains an additional gate, using `ignore-unfixed: true` and the repository `.trivyignore`. Findings are not silently exempted: `scripts/verify_trivy_allowlist.py` requires a nonempty owner and justification plus an ISO-formatted future expiry for each entry. Its two active bounded false-positive exemptions cover historical lower-layer attributions: `GHSA-6v7p-g79w-8964` for `msgpack==1.1.2` and `CVE-2025-47273` for `setuptools==70.3.0`, owned by Security Engineering and expiring on 2026-10-31. Their earlier ARM64 reassessment is documented in issue #160. That reassessment used Trivy 0.74.0 and Syft 1.52.0: Syft reported only `msgpack==1.2.2` and `setuptools==84.0.0` in both squashed and all-layer scopes of the then-tested image. It did not rebuild current main or scan AMD64, and excluded JAR files from the reassessment scan; the release scan remains unchanged. This historical evidence does not establish the state of a later image. These `.trivyignore` exemptions cannot bypass the complete-inventory gate, which applies only the unfixed-package dispositions described above. Passing either scan alone does not satisfy release acceptance.

After each immutable API image is built and pushed, the release workflow pulls that exact digest and runs `scripts/verify_final_python_packages.py` with both the global interpreter and the copied virtualenv. Global and virtualenv msgpack must match the exact requirements pin, currently `1.2.3`. Global pip, setuptools and urllib3 must be absent. The virtualenv retains the exact reviewed runtime pins, including `urllib3==2.8.0`, `piper-tts==1.8.0` and `setuptools==84.0.0`, with pip absent. The verifier inspects package metadata and pip-vendored copies in each interpreter's site-packages. This final-filesystem check runs before SBOM generation, vulnerability scanning, attestation, signing, or receipt creation.

## Signing, identity, provenance, and tags

Release entry requires a GitHub-verified **signed annotated tag** matching strict `vMAJOR.MINOR.PATCH` and the CLI package and lock versions. `release.yml` is the sole version-tag trigger; the Docker and npm workflows are call-only and expose no second publication path.

Each image digest and promoted version index is signed keylessly with cosign through the GitHub Actions OIDC issuer `https://token.actions.githubusercontent.com`. Verification binds the certificate identity to this repository's `.github/workflows/publish-docker.yml` at the release ref and binds the issuer exactly. Build provenance is attested against the immutable subject name and digest, pushed to the registry, and cosign verification must succeed before a receipt can be uploaded.

## Coordinated gated publication and receipts

The workflow provides coordinated gated publication, not an atomic registry mutation. Native builds independently push content-addressed image digests and produce exactly **four receipts**: API and dashboard on `linux/amd64` and `linux/arm64`. Each receipt binds source commit, platform, image name, and immutable digest. All four receipts from the same workflow run and their registry digests are validated before the single promotion job can create version, minor, and latest indexes for either image. A missing or inconsistent leg prevents both image promotions. Publication is then ordered image promotion, npm publication, and GitHub Release creation, so downstream nodes cannot run after a failed prerequisite.

Because immutable matrix legs push before the receipt gate, an interruption or failed leg can leave partial registry state consisting of some unpromoted content-addressed digests. It cannot expose a complete release through the gated version indexes, npm package, or GitHub Release, but the registry changes themselves are not rolled back. Reconciliation is a rerun of the release workflow for the same signed tag: each rerun attempt uses an isolated artifact namespace, regenerates and validates all four receipts, verifies all four referenced registry digests, and only then recreates the coordinated indexes and continues publication. Canonical receipt and SBOM payload filenames stay unchanged inside that namespace. Operators must not promote a partial set or combine artifacts from separate workflow run attempts.

## Reproducibility matrix

CI runs both production Dockerfiles natively on `linux/amd64` and `linux/arm64`. For each of the four image/platform combinations, `scripts/verify_reproducible_image.sh` performs two independent no-cache Buildx builds with `--pull=false`, provenance generation disabled, and SBOM generation disabled; it exports OCI archives, extracts the exact single manifest digest from each `index.json`, and requires equality. `--pull=false` does not prohibit fetching a pinned base image that is absent from the builder: it means Buildx does not force a fresh pull when that immutable base reference is already available. These CI builds do not publish images.

## Requirements hash evaluation

`requirements.txt` contains **148** runtime dependency entries.
`requirements-dev.txt` includes that runtime file and adds **30** development-only
entries. All 178 application dependencies use exact `==` pins. There are no hashes.
Hashes are deferred; they must not be fabricated from one
workstation or one platform. Exact pins plus the strict `pip-audit` gates are
the current controls, but they are not represented as hash-locked installs.

Security Engineering owns the hash-lock follow-up. Hash enforcement is accepted only after a platform-complete lock is generated and tested on Linux `linux/amd64` and `linux/arm64` for the supported Python 3.12, 3.13, and 3.14 matrix. Acceptance is fail closed and requires all of the following:

1. Every selected distribution artifact for every supported Python/platform combination has an authenticated hash; no unhashed dependency, editable source, floating URL, or unconstrained transitive dependency remains.
2. Clean installs succeed with `pip --require-hashes` on every matrix leg and fail when a required hash is removed or altered.
3. The generated environment matches the reviewed exact dependency set and strict `pip-audit` passes on every matrix leg.
4. CI enforces the lock and its negative tests before documentation may claim hash protection.

Until those criteria are implemented and verified, reviews must state that hashes are deferred and retain the exact-pin and strict-audit controls.
