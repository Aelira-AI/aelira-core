"""Execute publication plans and workflow commands without a registry write."""

import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/release_channel.ts"


def plan(tag: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", "--experimental-strip-types", str(SCRIPT), tag],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("version", ["0.0.0", "0.9.12", "1.0.0", "12.34.56"])
def test_stable_preserves_existing_channels(version):
    result = plan(f"v{version}")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["version"] == version
    assert data["dockerTags"] == [version, version.rsplit(".", 1)[0], "latest"]
    assert data["npmTag"] == "latest"
    assert data["githubArgs"] == ["--prerelease=false"]


@pytest.mark.parametrize(
    "version", ["0.9.12-alpha.0", "1.0.0-alpha.1", "12.34.56-alpha.99"]
)
def test_alpha_is_exact_and_opt_in(version):
    result = plan(f"v{version}")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["version"] == version
    assert data["dockerTags"] == [version]
    assert data["npmTag"] == "alpha"
    assert data["githubArgs"] == ["--prerelease", "--latest=false"]


@pytest.mark.parametrize(
    "tag",
    [
        "",
        "1.0.0",
        "v01.0.0",
        "v1.01.0",
        "v1.0.01",
        "v1.0",
        "v1.0.0-alpha",
        "v1.0.0-alpha.01",
        "v1.0.0-alpha.-1",
        "v1.0.0-alpha.1.extra",
        "v1.0.0-beta.1",
        "v1.0.0-rc.1",
        "v1.0.0+build",
        "v1.0.0-alpha.1+build",
        "v1.0.0\n",
        "v1.0.0 latest",
        "refs/tags/v1.0.0",
        "--latest",
        "v1.0.0;echo invalid",
    ],
)
def test_invalid_tags_produce_no_publication_plan(tag):
    result = plan(tag)
    assert result.returncode != 0
    assert not result.stdout


def workflow_step(file, job, name):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / file).read_text())
    return next(
        s["run"] for s in workflow["jobs"][job]["steps"] if s.get("name") == name
    )


def test_all_publish_boundaries_recompute_the_validated_plan():
    for file, job, name in [
        (
            "publish-docker.yml",
            "promote-all-images",
            "Promote both images from exact verified digests",
        ),
        ("publish-docker.yml", "promote-all-images", "Verify promoted indexes"),
        ("publish-npm.yml", "publish", "Publish"),
        ("release.yml", "github-release", "Create GitHub Release"),
    ]:
        script = workflow_step(file, job, name)
        assert 'release_channel.ts "$TAG_NAME"' in script
        assert "set -euo pipefail" in script


def test_default_deployment_refs_remain_stable():
    compose = (ROOT / "docker-compose.prod.yml").read_text()
    assert compose.count("${AELIRA_VERSION:-0.9.11}") == 3
    assert "alpha" not in compose


@pytest.fixture
def publisher_sandbox(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/release_channel.ts").write_text(SCRIPT.read_text())
    (tmp_path / "cli").mkdir()
    (tmp_path / "receipts").mkdir()
    for index, (image, arch) in enumerate(
        (image, arch)
        for image in ["aelira-core-api", "aelira-core-dashboard"]
        for arch in ["amd64", "arm64"]
    ):
        (tmp_path / "receipts" / f"{image}-{arch}.json").write_text(
            json.dumps(
                {
                    "image": f"ghcr.io/aelira-ai/{image}",
                    "platform": f"linux/{arch}",
                    "digest": "sha256:" + str(index + 1) * 64,
                }
            )
        )
    (tmp_path / "docs/releases").mkdir(parents=True)
    (tmp_path / "release-assets").mkdir()
    for name in ["python.cdx.json", "cli.cdx.json", "dashboard.cdx.json"]:
        (tmp_path / "release-assets" / name).write_text("{}")
    for image in ["aelira-core-api", "aelira-core-dashboard"]:
        for arch in ["amd64", "arm64"]:
            (tmp_path / "release-assets" / f"{image}-{arch}.spdx.json").write_text("{}")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Registry commands are inert capture programs; Node/jq/bash remain real.
    recorder = """#!/usr/bin/env node
const fs = require('node:fs');
fs.appendFileSync(process.env.CAPTURE_FILE, JSON.stringify({
  name: require('node:path').basename(process.argv[1]), args: process.argv.slice(2)
}) + '\\n');
"""
    for name in ["docker", "npm", "gh"]:
        path = bin_dir / name
        path.write_text(recorder)
        path.chmod(0o755)
    return tmp_path


@pytest.mark.parametrize("tag", ["v1.2.3", "v1.2.3-alpha.1", "v1.2.3-alpha.01"])
def test_actual_publication_steps_select_only_expected_channels(publisher_sandbox, tag):
    root = publisher_sandbox
    capture = root / "captured.jsonl"
    (root / "docs/releases" / f"{tag}.md").write_text("Synthetic release notes")
    env = {
        **os.environ,
        "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
        "RUNNER_TEMP": str(root),
        "TAG_NAME": tag,
        "CAPTURE_FILE": str(capture),
    }
    steps = [
        (
            "publish-docker.yml",
            "promote-all-images",
            "Promote both images from exact verified digests",
            root,
        ),
        ("publish-npm.yml", "publish", "Publish", root / "cli"),
        ("release.yml", "github-release", "Create GitHub Release", root),
    ]
    for file, job, name, cwd in steps:
        result = subprocess.run(
            ["bash", "-c", workflow_step(file, job, name)],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if tag.endswith(".01"):
            assert result.returncode != 0
        else:
            assert result.returncode == 0, result.stderr
    if tag.endswith(".01"):
        assert not capture.exists(), "Invalid tags must reach no publisher"
        return
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert [c["name"] for c in calls] == ["docker", "docker", "npm", "gh"]
    version = tag[1:]
    alpha = "-alpha." in tag
    tags = [version] if alpha else [version, "1.2", "latest"]
    for image, call in zip(["aelira-core-api", "aelira-core-dashboard"], calls[:2]):
        args = call["args"]
        assert args[:3] == ["buildx", "imagetools", "create"]
        assert [args[i + 1] for i, value in enumerate(args) if value == "--tag"] == [
            f"ghcr.io/aelira-ai/{image}:{value}" for value in tags
        ]
        assert len([v for v in args if "@sha256:" in v]) == 2
    assert calls[2]["args"] == [
        "publish",
        "--access",
        "public",
        "--tag",
        "alpha" if alpha else "latest",
    ]
    github_args = calls[3]["args"]
    assert github_args[:3] == ["release", "create", tag]
    assert "--verify-tag" in github_args
    assert sum(v.endswith(".json") for v in github_args) == 7
    assert ("--latest=false" in github_args) is alpha
    assert ("--prerelease" in github_args) is alpha
    assert ("--prerelease=false" in github_args) is not alpha


@pytest.mark.parametrize("lock_version", ["1.2.3", "1.2.3-alpha.2"])
def test_package_version_mismatch_is_rejected(publisher_sandbox, lock_version):
    root = publisher_sandbox
    (root / "cli/package.json").write_text(json.dumps({"version": "1.2.3-alpha.1"}))
    (root / "cli/package-lock.json").write_text(
        json.dumps(
            {
                "version": lock_version,
                "packages": {"": {"version": lock_version}},
            }
        )
    )
    script = workflow_step("publish-npm.yml", "publish", "Verify exact release version")
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=root / "cli",
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "TAG_NAME": "v1.2.3-alpha.1", "RUNNER_TEMP": str(root)},
    )
    assert result.returncode != 0
    assert "Version mismatch" in result.stderr
