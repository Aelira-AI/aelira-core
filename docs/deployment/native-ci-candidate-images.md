# Native CI candidate images

CI's manual `workflow_dispatch` input `retain_native_images` is off by default.
When enabled, each native Docker job builds the API and dashboard twice without
cache, compares their platform manifest digests, and retains one of the equal
OCI archives. The API runtime, package, and layer checks use that imported API
archive. The dashboard archive is imported and started, its `/health` endpoint
is checked, and both running containers' platform manifest descriptors are
compared with the OCI archives. The ordinary CI and tag release paths are
unchanged. This path does not publish or promote an image.

The successful amd64 and arm64 jobs each upload separate API and dashboard
artifacts after their native checks. Each artifact contains an OCI tar, a
receipt, and runtime evidence. The receipt binds the public repository, actual
checkout commit and tree, workflow run ID and attempt, platform, archive size
and SHA-256, platform manifest and config digests, and test evidence hash. It
requires the workflow head to equal the actual checkout commit. Artifacts
expire after three days. ZIP compression is disabled because OCI layers are
already compressed; the native job logs archive byte sizes and free disk space.

Only a **fully successful** `CI` workflow run is eligible. Use its numeric run
ID and the numeric IDs of its four artifacts. List them with
`gh api "repos/Aelira-AI/aelira-core/actions/runs/$RUN_ID/artifacts" --jq '.artifacts[] | [.id, .name, .size_in_bytes] | @tsv'`.
Never select a mutable branch or
tag as the image identity. The API and dashboard pair must come from the same
run, attempt, checkout tree, and platform. Repeat this procedure for the other
platform with its distinct IDs.

From a checkout containing the candidate commit, set the exact values below.
The artifact IDs are available from the successful run's artifact list. This
example retrieves arm64; change `PLATFORM` and both IDs for amd64.

```bash
RUN_ID=123456789
API_ARTIFACT_ID=123456790
DASHBOARD_ARTIFACT_ID=123456791
COMMIT=0123456789abcdef0123456789abcdef01234567
PLATFORM=linux/arm64
TREE=$(git rev-parse "$COMMIT^{tree}")
WORK=$(mktemp -d)

gh api "repos/Aelira-AI/aelira-core/actions/runs/$RUN_ID" > "$WORK/run.json"
gh api "repos/Aelira-AI/aelira-core/actions/artifacts/$API_ARTIFACT_ID" > "$WORK/api-artifact.json"
gh api "repos/Aelira-AI/aelira-core/actions/artifacts/$DASHBOARD_ARTIFACT_ID" > "$WORK/dashboard-artifact.json"
gh api "repos/Aelira-AI/aelira-core/actions/artifacts/$API_ARTIFACT_ID/zip" > "$WORK/api.zip"
gh api "repos/Aelira-AI/aelira-core/actions/artifacts/$DASHBOARD_ARTIFACT_ID/zip" > "$WORK/dashboard.zip"

# Inspect the ZIP member lists before copying either untrusted archive to disk.
test "$(unzip -Z1 "$WORK/api.zip" | LC_ALL=C sort)" = \
  "$(printf '%s\n' api.evidence.txt api.oci.tar api.receipt.json | LC_ALL=C sort)"
test "$(unzip -Z1 "$WORK/dashboard.zip" | LC_ALL=C sort)" = \
  "$(printf '%s\n' dashboard.evidence.txt dashboard.oci.tar dashboard.receipt.json | LC_ALL=C sort)"
for kind in api dashboard; do
  for suffix in oci.tar receipt.json evidence.txt; do
    unzip -p "$WORK/$kind.zip" "$kind.$suffix" > "$WORK/$kind.$suffix"
  done
done

bun scripts/candidate_image_bundle.ts verify \
  --archive "$WORK/api.oci.tar" --receipt "$WORK/api.receipt.json" \
  --evidence "$WORK/api.evidence.txt" \
  --peer-archive "$WORK/dashboard.oci.tar" \
  --peer-receipt "$WORK/dashboard.receipt.json" \
  --peer-evidence "$WORK/dashboard.evidence.txt" \
  --run-json "$WORK/run.json" \
  --artifact-json "$WORK/api-artifact.json" --artifact-id "$API_ARTIFACT_ID" \
  --peer-artifact-json "$WORK/dashboard-artifact.json" \
  --peer-artifact-id "$DASHBOARD_ARTIFACT_ID" \
  --checkout-commit "$COMMIT" --checkout-tree "$TREE" \
  --platform "$PLATFORM" --kind api
```

The verifier rejects missing or corrupted OCI blobs, archive and evidence hash
mismatches, wrong source or tree, wrong platform, mixed attempts or artifact
IDs, and incomplete or failed runs. It validates tar paths and entry types
without extracting the OCI tar, and hashes the archive and blobs in bounded
chunks. Run it **before** `docker load`. After a successful verification, load
each tar and compare Docker's containerd platform descriptors again:

```bash
docker load --input "$WORK/api.oci.tar"
docker load --input "$WORK/dashboard.oci.tar"
bun scripts/candidate_image_bundle.ts check-docker \
  --archive "$WORK/api.oci.tar" --platform "$PLATFORM" \
  --image aelira-worker-isolation:test
bun scripts/candidate_image_bundle.ts check-docker \
  --archive "$WORK/dashboard.oci.tar" --platform "$PLATFORM" \
  --image aelira-dashboard-candidate:test
```

Docker must use the containerd image store and report platform descriptors.
The opt-in CI job enables that store with Docker's
[documented GitHub Actions setup](https://docs.docker.com/build/ci/github-actions/multi-platform/#build-and-load-multi-platform-images).
The native CI job also checks each running container's `ImageManifestDescriptor`.
Rehearsals should use those exact imported images and continue to apply the
running descriptor checks described in the
[upgrade and recovery rehearsal](upgrade-recovery-rehearsal.md).
