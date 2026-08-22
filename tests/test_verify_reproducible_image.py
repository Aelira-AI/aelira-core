"""Behavior tests for the reproducible OCI image verifier."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "verify_reproducible_image.sh"


def _fake_docker(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True)
    binary = tmp_path / "docker"
    binary.write_text("""#!/usr/bin/env python3
import io
import json
import os
from pathlib import Path
import sys
import tarfile

required = {"--no-cache", "--pull=false", "--provenance=false", "--sbom=false"}
if sys.argv[1:3] != ["buildx", "build"] or not required.issubset(sys.argv):
    raise SystemExit(64)
platform = sys.argv[sys.argv.index("--platform") + 1]
dockerfile = sys.argv[sys.argv.index("--file") + 1]
if platform != os.environ["EXPECTED_PLATFORM"] or dockerfile != os.environ["EXPECTED_DOCKERFILE"]:
    raise SystemExit(65)
output = sys.argv[sys.argv.index("--output") + 1]
destination = output.removeprefix("type=oci,dest=")
state = Path(os.environ["FAKE_DOCKER_STATE"])
invocation = int(state.read_text()) if state.exists() else 0
state.write_text(str(invocation + 1))
digest = os.environ["FAKE_DIGESTS"].split(",")[invocation]
index = json.dumps({"schemaVersion": 2, "manifests": [{"digest": digest}]}).encode()
with tarfile.open(destination, "w") as archive:
    info = tarfile.TarInfo("index.json")
    info.size = len(index)
    archive.addfile(info, io.BytesIO(index))
""")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return binary


def _run_verifier(
    tmp_path: Path, digests: tuple[str, str]
) -> subprocess.CompletedProcess[str]:
    _fake_docker(tmp_path)
    context = tmp_path / "context"
    context.mkdir()
    dockerfile = context / "Containerfile"
    dockerfile.write_text("FROM scratch\n")
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_DOCKER_STATE": str(tmp_path / "state"),
        "FAKE_DIGESTS": ",".join(digests),
        "EXPECTED_PLATFORM": "linux/arm64",
        "EXPECTED_DOCKERFILE": str(dockerfile),
    }
    return subprocess.run(
        [str(SCRIPT), str(context), str(dockerfile), "linux/arm64"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_verifier_accepts_equal_manifests_and_rejects_mismatch(tmp_path: Path) -> None:
    digest_a = f"sha256:{'a' * 64}"
    digest_b = f"sha256:{'b' * 64}"

    equal = _run_verifier(tmp_path / "equal", (digest_a, digest_a))
    mismatch = _run_verifier(tmp_path / "mismatch", (digest_a, digest_b))

    assert equal.returncode == 0, equal.stderr
    assert digest_a in equal.stdout
    assert mismatch.returncode != 0
    assert "not reproducible" in mismatch.stderr.lower()
    assert digest_a in mismatch.stderr
    assert digest_b in mismatch.stderr
