"""Offline contracts for the Task23 release-integrity boundary."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def test_every_external_action_is_pinned_to_a_commented_full_sha() -> None:
    external_uses: list[tuple[Path, int, str]] = []
    for workflow in WORKFLOWS.glob("*.yml"):
        for line_number, line in enumerate(workflow.read_text().splitlines(), 1):
            match = re.search(r"\buses:\s*([^\s#]+)(?:\s+#\s*(\S+))?", line)
            if not match or match.group(1).startswith("./"):
                continue
            external_uses.append((workflow, line_number, line))
            action, separator, revision = match.group(1).partition("@")
            assert separator and FULL_SHA.fullmatch(
                revision
            ), f"{workflow.name}:{line_number} must pin {action} to a full SHA"
            assert match.group(
                2
            ), f"{workflow.name}:{line_number} must comment the reviewed release"

    assert external_uses, "expected at least one external action"


def test_dependency_gates_audit_only_the_three_shipped_surfaces_and_block() -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text()
    dependency_job = workflow.split("  dependency-security:\n", 1)[1].split(
        "\n  lint:\n", 1
    )[0]
    audit_prerequisites = "sudo apt-get install -y libcairo2-dev pkg-config"
    strict_python_audit = "pip-audit --requirement requirements.txt --strict"

    assert "pip-audit==2.10.0" in workflow
    assert audit_prerequisites in dependency_job
    assert strict_python_audit in dependency_job
    assert dependency_job.index(audit_prerequisites) < dependency_job.index(
        strict_python_audit
    )
    assert "npm --prefix cli audit --audit-level=high" in workflow
    assert "npm --prefix dashboard audit --audit-level=high" in workflow
    assert workflow.count(" audit --audit-level=high") == 2
    assert "dependency-review-action@" in workflow
    assert "fail-on-severity: high" in workflow
    assert "allow-licenses:" not in workflow


def test_production_dockerfiles_pin_bases_and_downloaded_voice_bytes() -> None:
    api = (ROOT / "Dockerfile").read_text()
    dashboard = (ROOT / "dashboard" / "Dockerfile").read_text()

    python_base = (
        "python:3.14-slim@sha256:"
        "ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4"
    )
    assert api.count(f"FROM {python_base}") == 2
    assert "pip install --no-cache-dir piper-tts==1.6.0" in api
    assert "npm install -g pa11y@9.0.1" in api
    assert api.count("curl -fL") == 2
    assert (
        "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f  "
        "/app/data/piper-voices/en_US-lessac-medium.onnx"
    ) in api
    assert (
        "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0  "
        "/app/data/piper-voices/en_US-lessac-medium.onnx.json"
    ) in api
    assert api.count("sha256sum -c -") == 2
    assert api.index("sha256sum -c -") < api.index("USER aelira")

    assert (
        "FROM node:22-alpine@sha256:"
        "c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32"
        " AS builder"
    ) in dashboard
    assert (
        "FROM nginx:alpine@sha256:"
        "db35bfc6b2951e7f8a72db5db120288c127ffaeeb4a6d4b95a26fead017d5913"
    ) in dashboard


def test_api_dockerfile_normalizes_content_level_build_nondeterminism() -> None:
    api = (ROOT / "Dockerfile").read_text()

    assert api.count("ARG SOURCE_DATE_EPOCH=0") == 2
    assert api.count("PYTHONHASHSEED=0") == 2
    assert api.count("FORCE_SOURCE_DATE=1") == 1
    assert api.count("PERL_HASH_SEED=0") == 1
    assert api.count("PERL_PERTURB_KEYS=0") == 1
    assert api.count('export SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH"') == 2
    assert "apt-get install -y --no-install-recommends" in api
    assert "&& update-language" in api
    assert "find /var/lib/texmf/web2c -type f -name '*.fmt' -delete" in api
    assert "/var/cache/fontconfig/*" in api
    assert "/var/cache/ldconfig/aux-cache" in api
    assert "/var/lib/texmf/ls-R" in api
    assert "/var/log/apt/*" in api
    assert "/var/log/alternatives.log" in api
    assert "/var/log/dpkg.log" in api
    assert "find /var/lib/texmf -type f -name '*.log' -delete" in api
    assert "npm cache clean --force" in api
    assert "rm -rf /root/.npm" in api
    assert "ENV SOURCE_DATE_EPOCH" not in api


def test_api_runtime_upgrades_available_debian_packages_before_install() -> None:
    api = (ROOT / "Dockerfile").read_text()
    runtime = api.split("# Stage 2: Runtime", 1)[1]
    update = "apt-get update"
    bounded_upgrade = (
        "timeout 10m apt-get -o Acquire::Retries=2 "
        "-o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 upgrade -y"
    )
    install = "apt-get install -y --no-install-recommends"

    assert runtime.count(bounded_upgrade) == 1
    assert runtime.index(update) < runtime.index(bounded_upgrade) < runtime.index(install)


def test_ci_reproducibility_jobs_use_an_oci_capable_buildx_driver() -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text()
    docker_job = workflow.split("  docker:\n", 1)[1]
    setup = (
        "uses: docker/setup-buildx-action@"
        "8d2750c68a42422c14e847fe6c8ac0403b4cbd6f # v3"
    )

    assert setup in docker_job
    assert docker_job.index(setup) < docker_job.index(
        "scripts/verify_reproducible_image.sh"
    )


def test_immutable_image_gate_precedes_receipts_and_signs_version_index() -> None:
    workflow = (WORKFLOWS / "publish-docker.yml").read_text()

    for action in (
        "anchore/sbom-action@e22c389904149dbc22b58101806040fa8d37a610 # v0.24.0",
        "aquasecurity/trivy-action@57a97c7e7821a5776cebc9bb87c984fa69cba8f1 # 0.35.0",
        "sigstore/cosign-installer@7e8b541eb2e61bf99390e1afd4be13a184e9ebc5 # v3.10.1",
        "actions/attest-build-provenance@96278af6caaf10aea03fd8d33a09a777ca52d62f # v3.2.0",
    ):
        assert action in workflow
    pinned_cosign_install = (
        "uses: sigstore/cosign-installer@"
        "7e8b541eb2e61bf99390e1afd4be13a184e9ebc5 # v3.10.1\n"
        "        with:\n"
        "          cosign-release: 'v2.6.1'"
    )
    assert workflow.count(pinned_cosign_install) == 2
    assert "format: spdx-json" in workflow
    assert "exit-code: '1'" in workflow
    assert "severity: HIGH,CRITICAL" in workflow
    assert "ignore-unfixed: true" in workflow
    assert "trivyignores:" not in workflow
    assert "subject-digest: ${{ steps.build.outputs.digest }}" in workflow
    assert "push-to-registry: true" in workflow
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    assert "GITHUB_REPOSITORY: ${{ github.repository }}" in workflow
    assert 'gh attestation verify "oci://$IMAGE@$DIGEST"' in workflow
    assert '--repo "$GITHUB_REPOSITORY"' in workflow
    assert workflow.index("gh attestation verify") > workflow.index(
        "Attest immutable image provenance"
    )
    assert workflow.index("Write digest receipt") > workflow.index(
        "gh attestation verify"
    )
    assert 'cosign sign --yes "$IMAGE@$DIGEST"' in workflow
    assert "cosign verify" in workflow
    assert "https://token.actions.githubusercontent.com" in workflow
    assert workflow.index("Upload digest receipt") > workflow.index("cosign verify")

    assert "id-token: write" in workflow
    assert "attestations: write" in workflow
    assert "VERSION_DIGEST=" in workflow
    assert 'cosign sign --yes "$image@$VERSION_DIGEST"' in workflow
    assert 'cosign verify "$image@$VERSION_DIGEST"' in workflow


def test_trivy_preserves_unfixed_inventory_and_blocks_actionable_findings() -> None:
    workflow = (WORKFLOWS / "publish-docker.yml").read_text()
    inventory = workflow.split(
        "      - name: Inventory all high and critical vulnerabilities\n", 1
    )[1].split("\n      - name: Upload Trivy vulnerability inventory", 1)[0]
    upload = workflow.split(
        "      - name: Upload Trivy vulnerability inventory\n", 1
    )[1].split("\n      - name: Scan immutable image", 1)[0]
    blocking = workflow.split(
        "      - name: Scan immutable image for fixed high and critical vulnerabilities\n",
        1,
    )[1].split("\n      - name: Attest immutable image provenance", 1)[0]

    for expected in (
        "format: json",
        "output: ${{ matrix.image }}-${{ matrix.arch }}.trivy.json",
        "exit-code: '0'",
        "severity: HIGH,CRITICAL",
        "ignore-unfixed: false",
    ):
        assert expected in inventory
    for expected in (
        "name: trivy-${{ matrix.image }}-${{ matrix.arch }}-${{ github.run_attempt }}",
        "path: ${{ matrix.image }}-${{ matrix.arch }}.trivy.json",
        "retention-days: 90",
        "if-no-files-found: error",
        "overwrite: false",
    ):
        assert expected in upload
    for expected in (
        "format: table",
        "exit-code: '1'",
        "severity: HIGH,CRITICAL",
        "ignore-unfixed: true",
    ):
        assert expected in blocking
    assert "trivyignores:" not in workflow
    assert workflow.index("Inventory all high and critical vulnerabilities") < workflow.index(
        "Scan immutable image for fixed high and critical vulnerabilities"
    )


def test_release_requires_verified_annotated_tag_and_attaches_exact_sboms() -> None:
    workflow = (WORKFLOWS / "release.yml").read_text()

    assert workflow.count('      - "v*"') == 1
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    assert "GITHUB_REPOSITORY: ${{ github.repository }}" in workflow
    assert 'git rev-parse "$TAG_NAME^{tag}"' in workflow
    assert 'gh api "repos/$GITHUB_REPOSITORY/git/tags/$TAG_OBJECT"' in workflow
    assert ".verification.verified == true" in workflow

    assert "cyclonedx-bom==7.3.1" in workflow
    assert "@cyclonedx/cyclonedx-npm@6.0.1" in workflow
    assert "requirements.txt" in workflow
    assert "cli/package-lock.json" in workflow
    assert "dashboard/package-lock.json" in workflow
    assert "python.cdx.json" in workflow
    assert "cli.cdx.json" in workflow
    assert "dashboard.cdx.json" in workflow
    assert "name: dependency-sboms-${{ github.run_attempt }}" in workflow
    assert (
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4"
        in workflow
    )

    assert (
        "actions/download-artifact@634f93cb2916e3fdff6788551b99b062d0335ce0 # v5"
        in workflow
    )
    assert "pattern: sbom-*-${{ github.run_attempt }}" in workflow
    assert "EXPECTED_CYCLONEDX=3" in workflow
    assert "EXPECTED_SPDX=4" in workflow
    assert "${#CYCLONEDX[@]}" in workflow
    assert "${#SPDX[@]}" in workflow
    assert 'gh release create "$TAG_NAME"' in workflow
    assert "--verify-tag" in workflow
    assert '"${CYCLONEDX[@]}"' in workflow
    assert '"${SPDX[@]}"' in workflow

    assert workflow.index("git/tags/$TAG_OBJECT") < workflow.index("docker-publish:")
    assert workflow.index("docker-publish:") < workflow.index("npm-publish:")
    assert workflow.index("npm-publish:") < workflow.index("github-release:")


def test_release_artifact_names_are_isolated_by_run_attempt() -> None:
    docker = (WORKFLOWS / "publish-docker.yml").read_text()
    release = (WORKFLOWS / "release.yml").read_text()
    attempt = "${{ github.run_attempt }}"

    artifact_blocks = re.findall(
        r"(?ms)^      - name: .+?\n"
        r"        uses: actions/(?:upload|download)-artifact@.+?"
        r"(?=^      - |\Z)",
        docker + "\n" + release,
    )
    assert len(artifact_blocks) == 9
    for block in artifact_blocks:
        selectors = re.findall(
            r"^          (?:name|pattern): (.+)$", block, re.MULTILINE
        )
        assert len(selectors) == 1
        assert attempt in selectors[0]

    for expected in (
        "name: sbom-${{ matrix.image }}-${{ matrix.arch }}-" + attempt,
        "name: receipt-${{ matrix.image }}-${{ matrix.arch }}-" + attempt,
        f"pattern: receipt-*-{attempt}",
        f"name: verified-digest-receipts-{attempt}",
    ):
        assert expected in docker
    assert docker.count(f"name: verified-digest-receipts-{attempt}") == 2
    assert f"pattern: sbom-*-{attempt}" in release
    assert release.count(f"name: dependency-sboms-{attempt}") == 2

    # Attempt isolation belongs to the artifact namespace, not release payload names.
    for canonical_payload in (
        "path: ${{ matrix.image }}-${{ matrix.arch }}.spdx.json",
        "path: ${{ matrix.image }}-${{ matrix.arch }}.json",
        "path: dependency-sboms/*.cdx.json",
    ):
        assert canonical_payload in docker + release
    assert "github.run_attempt }}.json" not in docker


def test_ci_is_read_only_and_runs_reproducibility_and_allowlist_gates() -> None:
    workflow = (WORKFLOWS / "ci.yml").read_text()

    assert re.search(r"^permissions:\n  contents: read$", workflow, re.MULTILINE)
    assert "python scripts/verify_trivy_allowlist.py .trivyignore" in workflow
    assert workflow.count("scripts/verify_reproducible_image.sh") == 2
    assert "scripts/verify_reproducible_image.sh . Dockerfile" in workflow
    assert (
        "scripts/verify_reproducible_image.sh dashboard "
        "dashboard/Dockerfile" in workflow
    )
    assert workflow.count("${{ matrix.platform }}") >= 2


def test_release_integrity_documentation_is_fail_closed_and_truthful() -> None:
    documentation = (ROOT / "docs" / "RELEASE_INTEGRITY.md").read_text()
    requirements = [
        line.split("#", 1)[0].strip()
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.split("#", 1)[0].strip()
    ]

    assert len(requirements) == 155
    assert all(
        re.fullmatch(r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^=\s]+", line)
        for line in requirements
    )
    assert not any("--hash=" in line for line in requirements)
    for required_text in (
        "requirements.txt",
        "cli/package-lock.json",
        "dashboard/package-lock.json",
        "CycloneDX JSON",
        "SPDX JSON",
        "HIGH,CRITICAL",
        "fixed/actionable",
        "currently-unfixed",
        "remain visible",
        "not silently exempted",
        "90-day",
        "not part of the exact seven",
        "https://token.actions.githubusercontent.com",
        "signed annotated tag",
        "four receipts",
        "coordinated gated publication",
        "partial registry state",
        "not an atomic registry mutation",
        "rerun",
        "isolated artifact namespace",
        "regenerates and validates all four receipts",
        "linux/amd64",
        "linux/arm64",
        "`--pull=false`",
        "155",
        "no hashes",
        "deferred",
        "Python 3.12, 3.13, and 3.14",
        "fail closed",
    ):
        assert required_text in documentation
    assert "Atomic publication" not in documentation


def test_release_has_one_tag_trigger_and_no_second_publication_path() -> None:
    tag_triggers: list[str] = []
    publication_commands: dict[str, set[str]] = {
        "gh release create": set(),
        "npm publish --access public": set(),
        "docker buildx imagetools create": set(),
    }
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text()
        if re.search(r"(?m)^\s+tags:\s*$", text):
            tag_triggers.append(path.name)
        for command in publication_commands:
            if command in text:
                publication_commands[command].add(path.name)

    assert tag_triggers == ["release.yml"]
    assert publication_commands == {
        "gh release create": {"release.yml"},
        "npm publish --access public": {"publish-npm.yml"},
        "docker buildx imagetools create": {"publish-docker.yml"},
    }
