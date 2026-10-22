"""Behaviour of scripts/ci_apt_install.sh against a stand-in apt-get."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci_apt_install.sh"
AZURE = "http://azure.archive.ubuntu.com/ubuntu/"
FALLBACK = "http://archive.ubuntu.com/ubuntu/"


def _gnu_sed() -> str | None:
    for name in ("sed", "gsed"):
        path = shutil.which(name)
        if path is None:
            continue
        result = subprocess.run([path, "--version"], capture_output=True, check=False)
        if result.returncode == 0 and b"GNU" in result.stdout:
            return path
    return None


def _write(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def harness(tmp_path: Path):
    sed = _gnu_sed()
    if sed is None:
        pytest.skip("GNU sed is required to run the mirror rewrite")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "apt.log"
    _write(bin_dir / "sudo", '#!/usr/bin/env bash\nexec "$@"\n')
    os.symlink(sed, bin_dir / "sed")
    # Stand-in for timeout(1): skip the duration and run the command.
    _write(bin_dir / "timeout", '#!/usr/bin/env bash\nshift\nexec "$@"\n')
    # Stand-in apt-get: logs each call and, while the sources still name the
    # Azure mirror and HANG_ON_AZURE is set, reports the download as timed out.
    _write(
        bin_dir / "apt-get",
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "{log}"
if [[ " $* " == *" --download-only "* ]] && [ -n "${{HANG_ON_AZURE:-}}" ] \\
  && grep -rqs azure.archive "$APT_SOURCES_DIR" "$APT_MIRRORS_FILE"; then
  exit 124
fi
exit 0
""",
    )
    sources = tmp_path / "apt"
    (sources / "sources.list.d").mkdir(parents=True)
    (sources / "sources.list.d" / "ubuntu.sources").write_text(
        f"Types: deb\nURIs: {AZURE}\nSuites: resolute\n"
    )
    mirrors = tmp_path / "apt-mirrors.txt"
    mirrors.write_text(f"{AZURE}\tpriority:1\n")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "APT_SOURCES_DIR": str(sources),
        "APT_MIRRORS_FILE": str(mirrors),
    }

    def run(*args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env={**env, **extra},
            capture_output=True,
            text=True,
            check=False,
        )

    return run, log, sources, mirrors


def test_a_stalled_mirror_is_replaced_and_install_runs_once(harness) -> None:
    run, log, sources, mirrors = harness
    result = run("--no-install-recommends", "binutils", "gpgv", HANG_ON_AZURE="1")

    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    downloads = [call for call in calls if "--download-only" in call]
    installs = [call for call in calls if "--no-download" in call]
    assert len(downloads) == 2
    assert len(installs) == 1
    assert installs[0].endswith(
        "install --no-download -y --no-install-recommends binutils gpgv"
    )
    assert calls.index(installs[0]) == len(calls) - 1
    assert mirrors.read_text() == f"{FALLBACK}\tpriority:1\n"
    assert "azure" not in (sources / "sources.list.d" / "ubuntu.sources").read_text()
    assert f"switched to {FALLBACK}" in result.stdout


def test_a_healthy_mirror_is_left_alone(harness) -> None:
    run, log, sources, mirrors = harness
    result = run("pandoc")

    assert result.returncode == 0, result.stderr
    assert len([c for c in log.read_text().splitlines() if "--download-only" in c]) == 1
    assert mirrors.read_text() == f"{AZURE}\tpriority:1\n"
    assert "switched" not in result.stdout


def test_install_never_runs_when_every_download_attempt_fails(harness) -> None:
    run, log, _, _ = harness
    result = run("pandoc", HANG_ON_AZURE="1", APT_FALLBACK_MIRROR=AZURE)

    assert result.returncode == 1
    assert "not downloaded after 3 attempts" in result.stderr
    assert not [c for c in log.read_text().splitlines() if "--no-download" in c]


@pytest.mark.parametrize("args", [(), ("--yes", "pandoc")])
def test_bad_arguments_are_refused(harness, args: tuple[str, ...]) -> None:
    run, log, _, _ = harness
    result = run(*args)

    assert result.returncode == 2
    assert not log.exists()
