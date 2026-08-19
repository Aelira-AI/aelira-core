"""Contracts that keep publication ordered and all release artifacts coherent."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
DOCKER = WORKFLOWS / "publish-docker.yml"
NPM = WORKFLOWS / "publish-npm.yml"
CI = WORKFLOWS / "ci.yml"
CI_GATE = WORKFLOWS / "ci-gate.yml"


def load_workflow(path: Path) -> dict:
    workflow = yaml.safe_load(path.read_text())
    # PyYAML implements YAML 1.1 and parses the unquoted key `on` as True.
    workflow["on"] = workflow.pop(True, workflow.get("on"))
    return workflow


def triggers(workflow: dict) -> dict:
    value = workflow["on"]
    return value if isinstance(value, dict) else {value: None}


def job_needs(job: dict) -> set[str]:
    needs = job.get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def test_release_is_the_only_tag_trigger_and_reusables_are_call_only() -> None:
    tag_publishers = []
    for path in WORKFLOWS.glob("*.yml"):
        workflow_triggers = triggers(load_workflow(path))
        tags = workflow_triggers.get("push", {}).get("tags", [])
        if "v*" in tags:
            tag_publishers.append(path.name)

    assert sorted(tag_publishers) == ["release.yml"]
    assert set(triggers(load_workflow(DOCKER))) == {"workflow_call"}
    assert set(triggers(load_workflow(NPM))) == {"workflow_call"}
    assert "workflow_dispatch" not in RELEASE.read_text()


def test_release_dag_orders_ci_preflight_docker_npm_and_github_release() -> None:
    workflow = load_workflow(RELEASE)
    jobs = workflow["jobs"]

    assert job_needs(jobs["preflight"]) == {"ci-gate"}
    assert job_needs(jobs["docker-publish"]) == {"preflight"}
    assert jobs["docker-publish"]["uses"].endswith("publish-docker.yml")
    assert job_needs(jobs["npm-publish"]) == {"docker-publish"}
    assert jobs["npm-publish"]["uses"].endswith("publish-npm.yml")
    assert job_needs(jobs["github-release"]) == {"npm-publish"}
    assert jobs["github-release"]["environment"] == "release"

    assert workflow["concurrency"] == {
        "group": "release-publication",
        "cancel-in-progress": False,
    }
    assert workflow["permissions"] == {"contents": "read"}


def test_preflight_requires_stable_semver_matching_package_and_lock() -> None:
    text = RELEASE.read_text()

    assert "^v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$" in text
    assert "cli/package.json" in text
    assert "cli/package-lock.json" in text
    assert "scripts/verify_release_safety.py" in text


def test_preflight_uses_protected_environment_policy_and_cleans_it() -> None:
    workflow = load_workflow(RELEASE)
    preflight = workflow["jobs"]["preflight"]
    assert preflight["environment"] == "release"
    assert job_needs(preflight) == {"ci-gate"}

    validation = next(
        step
        for step in preflight["steps"]
        if step.get("name") == "Validate stable version and release safety"
    )
    env = validation["env"]
    script = validation["run"]

    assert env["RELEASE_DENYLIST_JSON"] == "${{ secrets.RELEASE_DENYLIST_JSON }}"
    assert 'if [ -z "$RELEASE_DENYLIST_JSON" ]' in script
    assert 'POLICY_PATH="$RUNNER_TEMP/' in script
    assert "printf '%s' \"$RELEASE_DENYLIST_JSON\"" in script
    assert 'chmod 600 "$POLICY_PATH"' in script
    assert "unset RELEASE_DENYLIST_JSON" in script
    assert script.index("unset RELEASE_DENYLIST_JSON") > script.index(
        "printf '%s' \"$RELEASE_DENYLIST_JSON\""
    )
    assert "trap " in script
    assert 'rm -f -- "$POLICY_PATH"' in script
    assert "--strict-policy" in script
    assert '--denylist "$POLICY_PATH"' in script
    assert "python scripts/verify_release_safety.py\n" not in script
    assert 'echo "$RELEASE_DENYLIST_JSON"' not in script


def test_security_docs_require_protected_release_environment_configuration() -> None:
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    example = (ROOT / ".release-denylist.local.json.example").read_text(
        encoding="utf-8"
    )

    assert "environment secret" in security
    assert "release" in security
    assert "required reviewers" in security
    assert "deployment restrictions" in security
    assert "repository secret" not in security
    assert "environment secret" in example
    assert "release" in example
    assert "repository secret" not in example


def test_docker_matrix_is_exact_native_two_by_two_with_bounded_timeouts() -> None:
    workflow = load_workflow(DOCKER)
    build = workflow["jobs"]["build"]
    matrix = build["strategy"]["matrix"]["include"]

    observed = {
        (leg["image"], leg["platform"], leg["runner"], leg["timeout"]) for leg in matrix
    }
    assert observed == {
        ("aelira-core-api", "linux/amd64", "ubuntu-24.04", 120),
        ("aelira-core-api", "linux/arm64", "ubuntu-24.04-arm", 120),
        ("aelira-core-dashboard", "linux/amd64", "ubuntu-24.04", 30),
        ("aelira-core-dashboard", "linux/arm64", "ubuntu-24.04-arm", 30),
    }
    assert build["strategy"]["fail-fast"] is False
    assert build["runs-on"] == "${{ matrix.runner }}"
    assert build["timeout-minutes"] == "${{ matrix.timeout }}"
    assert "qemu" not in DOCKER.read_text().lower()


def test_build_legs_only_push_canonical_digests_and_upload_receipts() -> None:
    workflow = load_workflow(DOCKER)
    build = workflow["jobs"]["build"]
    build_step = next(step for step in build["steps"] if step.get("id") == "build")
    options = build_step["with"]
    text = DOCKER.read_text()

    assert options["push"] is True
    assert options["provenance"] is False
    assert "push-by-digest=true" in options["outputs"]
    assert "name-canonical=true" in options["outputs"]
    assert "name=ghcr.io/aelira-ai/${{ matrix.image }}" in options["outputs"]
    assert "tags" not in options
    assert "source_sha" in text
    assert "platform" in text
    assert "digest" in text
    assert "retention-days: 1" in text
    assert "overwrite: false" in text
    assert "latest" not in str(build)


def test_exactly_four_receipts_are_verified_before_single_promotion() -> None:
    workflow = load_workflow(DOCKER)
    jobs = workflow["jobs"]
    verify = jobs["verify-digests"]
    promote = jobs["promote-all-images"]
    text = DOCKER.read_text()

    assert job_needs(verify) == {"build"}
    assert job_needs(promote) == {"build", "verify-digests"}
    assert "strategy" not in promote
    assert "EXPECTED_RECEIPTS=4" in text
    assert "sha256:" in text
    assert "docker buildx imagetools inspect" in text
    assert "docker buildx imagetools create" in text


def test_promotion_creates_only_full_minor_latest_for_both_images() -> None:
    workflow = load_workflow(DOCKER)
    promote_text = str(workflow["jobs"]["promote-all-images"])

    assert "${VERSION}" in promote_text
    assert "${MINOR}" in promote_text
    assert "latest" in promote_text
    assert "aelira-core-api" in promote_text
    assert "aelira-core-dashboard" in promote_text
    assert "MAJOR" not in promote_text
    assert "Verify promoted indexes" in promote_text


def test_npm_is_pinned_validated_and_runs_full_publish_checklist() -> None:
    workflow = load_workflow(NPM)
    publish = workflow["jobs"]["publish"]
    text = NPM.read_text()

    assert set(triggers(workflow)) == {"workflow_call"}
    assert "npm@11.6.2" in text
    assert "npm@latest" not in text
    for command in (
        "npm ci",
        "npm run build",
        "npm run lint",
        "npm test",
        "npm pack --dry-run",
        "npm publish --access public",
    ):
        assert command in text
    assert "workflow_dispatch" not in text
    assert publish["permissions"] == {"contents": "read", "id-token": "write"}
    assert publish["environment"] == "release"


def test_public_docker_promotion_uses_protected_release_environment() -> None:
    workflow = load_workflow(DOCKER)

    assert workflow["jobs"]["promote-all-images"]["environment"] == "release"
    assert "environment" not in workflow["jobs"]["build"]
    assert "environment" not in workflow["jobs"]["verify-digests"]


def test_release_critical_jobs_are_bounded_and_permissions_are_least_privilege() -> (
    None
):
    release = load_workflow(RELEASE)
    docker = load_workflow(DOCKER)
    npm = load_workflow(NPM)
    gate = load_workflow(CI_GATE)

    for name in ("preflight", "github-release"):
        assert release["jobs"][name]["timeout-minutes"] > 0
    for name in ("verify-digests", "promote-all-images"):
        assert docker["jobs"][name]["timeout-minutes"] > 0
    assert npm["jobs"]["publish"]["timeout-minutes"] > 0
    assert gate["jobs"]["wait-for-ci"]["timeout-minutes"] > 0

    assert release["jobs"]["preflight"].get("permissions", {}) in (
        {},
        {"contents": "read"},
    )
    assert release["jobs"]["github-release"]["permissions"] == {"contents": "write"}
    assert docker["permissions"] == {"contents": "read", "packages": "write"}


def test_ci_builds_both_images_on_every_released_architecture_with_timeouts() -> None:
    workflow = load_workflow(CI)
    docker = workflow["jobs"]["docker"]
    text = str(docker)

    assert docker["timeout-minutes"] > 0
    matrix = docker["strategy"]["matrix"]["include"]
    assert {(leg["platform"], leg["runner"]) for leg in matrix} == {
        ("linux/amd64", "ubuntu-24.04"),
        ("linux/arm64", "ubuntu-24.04-arm"),
    }
    assert docker["runs-on"] == "${{ matrix.runner }}"
    assert "Dockerfile" in text
    assert "dashboard/Dockerfile" in text
    assert "${{ matrix.platform }}" in text


def test_ci_system_package_install_has_bounded_network_retries() -> None:
    workflow = load_workflow(CI)
    system_dependencies = next(
        step
        for step in workflow["jobs"]["test"]["steps"]
        if step.get("name") == "Install system dependencies"
    )["run"]

    assert "Acquire::Retries=2" in system_dependencies
    assert "Acquire::http::Timeout=20" in system_dependencies
    assert "Acquire::https::Timeout=20" in system_dependencies
    assert system_dependencies.count("timeout 5m") == 2


def test_dashboard_failure_makes_every_publication_node_unreachable() -> None:
    release = load_workflow(RELEASE)
    docker = load_workflow(DOCKER)
    dependencies = {
        "ci-gate": set(),
        "preflight": job_needs(release["jobs"]["preflight"]),
        "docker-build": set(),
        "verify-digests": {"docker-build"},
        "promote-all-images": {
            "docker-build" if need == "build" else need
            for need in job_needs(docker["jobs"]["promote-all-images"])
        },
        "docker-publish": {"promote-all-images"},
        "npm-publish": job_needs(release["jobs"]["npm-publish"]),
        "github-release": job_needs(release["jobs"]["github-release"]),
    }
    successful = {"ci-gate", "preflight"}  # dashboard matrix leg failed

    changed = True
    while changed:
        changed = False
        for node, needs in dependencies.items():
            if (
                node != "docker-build"
                and node not in successful
                and needs <= successful
            ):
                successful.add(node)
                changed = True

    assert "promote-all-images" not in successful
    assert "docker-publish" not in successful
    assert "npm-publish" not in successful
    assert "github-release" not in successful
