#!/usr/bin/env python3
"""Focused controls for the public TIFF evidence exporter."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "tiff_ci_evidence", HERE / "ci-evidence.py"
)
assert SPEC and SPEC.loader
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


class ExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.source = root / "run"
        self.source.mkdir()
        self.repo = root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        recipe = self.repo / "runtime-security/media/tiff"
        recipe.mkdir(parents=True)
        (recipe / "fixture.txt").write_text("reviewed recipe\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        self.revision = self.git("HEAD")
        self.tree = self.git("HEAD^{tree}")
        self.arch = "arm64"
        self.make_fixture()
        self.serial = 0

    def git(self, ref: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", ref], text=True
        ).strip()

    def put(self, relative: str, data: bytes = b"fixture\n") -> None:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def manifest(self, relative: str, names: list[str], directory: str) -> None:
        lines = []
        for name in names:
            data = (self.source / directory / name).read_bytes()
            lines.append(f"{hashlib.sha256(data).hexdigest()}  ./{name}\n")
        self.put(relative, "".join(lines).encode())

    def make_fixture(self) -> None:
        paths = evidence.file_plan(self.arch)
        for relative in paths:
            if relative.startswith("build-") and relative.endswith("build.log"):
                data = (
                    b"# TOTAL: 109\n# PASS:  109\n# SKIP:  0\n# FAIL:  0\n# ERROR: 0\n"
                )
            elif relative.endswith("ubuntu-source-chain.log"):
                data = b"ubuntu_source_chain=pass releases=4\n"
            elif relative.endswith("ubuntu-binary-chain.log"):
                data = b"ubuntu_binary_chain=pass packages=1 releases=4\n"
            elif relative == "build-a/installed-console.log":
                data = b"installed_tiff_acceptance=pass architecture=arm64 version=4.7.2-0aelira1\n"
            else:
                data = b"fixture\n"
            self.put(relative, data)
        self.put(
            "provenance/apt-lists/ports.ubuntu.com_ubuntu-ports_dists_resolute_InRelease",
            b"signed index fixture\n",
        )
        self.put(
            "provenance/toolchain-debs/libfoo_1%3a1.0_arm64.deb", b"toolchain fixture\n"
        )
        self.manifest(
            "provenance/apt-index-sha256.txt",
            ["ports.ubuntu.com_ubuntu-ports_dists_resolute_InRelease"],
            "provenance/apt-lists",
        )
        self.manifest(
            "provenance/toolchain-debs.sha256",
            ["libfoo_1%3a1.0_arm64.deb"],
            "provenance/toolchain-debs",
        )
        self.manifest(
            "provenance/source-input.sha256", ["tiff_4.7.2.orig.tar.xz"], "source-input"
        )

    def run_export(self, **changes: object) -> tuple[int, dict[str, object], Path]:
        self.serial += 1
        dest = Path(self.tmp.name) / f"export-{self.serial}"
        data = dict(
            source=str(self.source),
            dest=str(dest),
            repo=str(self.repo),
            arch=self.arch,
            revision=self.revision,
            tree=self.tree,
            build_exit=0,
            max_file=evidence.MAX_FILE,
            max_total=evidence.MAX_TOTAL,
            max_count=evidence.MAX_COUNT,
        )
        data.update(changes)
        code = evidence.export(argparse.Namespace(**data))
        return code, json.loads((dest / "ci-status.json").read_text()), dest

    def test_complete_export_replays_relative_paths_and_hashes(self) -> None:
        code, report, dest = self.run_export()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "complete")
        for item in report["files"]:
            path = dest / item["path"]
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(), item["sha256"]
            )
        self.assertFalse((dest / "provenance/frozen-builder.inspect.json").exists())
        self.assertFalse((dest / "provenance/frozen-builder.tar").exists())

    def test_failed_build_exports_available_evidence_and_status(self) -> None:
        (self.source / "build-b/build.log").unlink()
        code, report, dest = self.run_export(build_exit=17)
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("build-b/build.log", report["missing"])
        self.assertTrue((dest / "build-a/build.log").exists())
        self.assertIn("build exited 17", report["errors"])

    def test_missing_required_proof_fails(self) -> None:
        (self.source / "build-a/installed-console.log").unlink()
        code, report, _ = self.run_export()
        self.assertEqual(code, 1)
        self.assertIn("build-a/installed-console.log", report["missing"])

    def test_symlink_escape_rejected_without_copying_target(self) -> None:
        path = self.source / "source-input/tiff_4.7.2.orig.tar.xz"
        path.unlink()
        outside = Path(self.tmp.name) / "secret"
        outside.write_text("secret")
        path.symlink_to(outside)
        code, report, dest = self.run_export()
        self.assertEqual(code, 1)
        self.assertTrue(any("symlink" in error for error in report["errors"]))
        self.assertFalse((dest / "source-input/tiff_4.7.2.orig.tar.xz").exists())

    def test_parent_symlink_does_not_disclose_external_names(self) -> None:
        provenance = self.source / "provenance"
        outside = Path(self.tmp.name) / "external-provenance"
        provenance.rename(outside)
        (outside / "apt-lists/private-sentinel-name.txt").write_text("private")
        provenance.symlink_to(outside, target_is_directory=True)
        code, report, dest = self.run_export()
        self.assertEqual(code, 1)
        self.assertTrue(
            any("unsafe evidence directory" in error for error in report["errors"])
        )
        self.assertNotIn("private-sentinel-name", (dest / "ci-status.json").read_text())

    def test_unknown_manifest_archive_rejected(self) -> None:
        self.put("provenance/toolchain-debs/evil.sh", b"echo secret\n")
        self.manifest(
            "provenance/toolchain-debs.sha256", ["evil.sh"], "provenance/toolchain-debs"
        )
        code, report, dest = self.run_export()
        self.assertEqual(code, 1)
        self.assertTrue(
            any("invalid manifest entry" in error for error in report["errors"])
        )
        self.assertFalse((dest / "provenance/toolchain-debs/evil.sh").exists())

    def test_individual_aggregate_and_count_budgets(self) -> None:
        for limits in (
            {"max_file": 8},
            {"max_total": evidence.STATUS_LIMIT + 100},
            {"max_count": 5},
        ):
            with self.subTest(limits=limits):
                code, report, _ = self.run_export(**limits)
                self.assertEqual(code, 1)
                self.assertTrue(
                    any("budget exceeded" in error for error in report["errors"])
                )

    def test_wrong_architecture_and_revision_fail(self) -> None:
        for overrides in (
            {"arch": "bogus"},
            {"revision": "0" * 40},
            {"tree": "0" * 40},
        ):
            with self.subTest(overrides=overrides):
                if overrides.get("arch") == "bogus":
                    with self.assertRaisesRegex(ValueError, "architecture"):
                        self.run_export(**overrides)
                else:
                    code, report, _ = self.run_export(**overrides)
                    self.assertEqual(code, 1)
                    self.assertTrue(
                        any("differs" in error for error in report["errors"])
                    )

    def test_bad_acceptance_marker_fails(self) -> None:
        self.put("build-b/ubuntu-binary-chain.log", b"ubuntu_binary_chain=fail\n")
        code, report, _ = self.run_export()
        self.assertEqual(code, 1)
        self.assertTrue(
            any("missing acceptance marker" in error for error in report["errors"])
        )

    def test_different_valid_toolchain_count_passes(self) -> None:
        self.put("provenance/toolchain-debs/libbar_2.0_arm64.deb", b"second package\n")
        self.manifest(
            "provenance/toolchain-debs.sha256",
            [
                "libfoo_1%3a1.0_arm64.deb",
                "libbar_2.0_arm64.deb",
            ],
            "provenance/toolchain-debs",
        )
        for run in "ab":
            self.put(
                f"build-{run}/ubuntu-binary-chain.log",
                b"ubuntu_binary_chain=pass packages=2 releases=4\n",
            )
        code, report, _ = self.run_export()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "complete")

    def test_forged_toolchain_count_fails(self) -> None:
        for run in "ab":
            self.put(
                f"build-{run}/ubuntu-binary-chain.log",
                b"ubuntu_binary_chain=pass packages=250 releases=4\n",
            )
        code, report, _ = self.run_export()
        self.assertEqual(code, 1)
        self.assertTrue(
            any("missing acceptance marker" in error for error in report["errors"])
        )

    def test_actual_amd64_security_index_names_allowed(self) -> None:
        names = [
            "security.ubuntu.com_ubuntu_dists_resolute-security_InRelease",
            "security.ubuntu.com_ubuntu_dists_resolute-security_main_binary-amd64_Packages.lz4",
            "security.ubuntu.com_ubuntu_dists_resolute-security_universe_source_Sources.lz4",
        ]
        for name in names:
            self.put(f"provenance/apt-lists/{name}")
        self.manifest("provenance/apt-index-sha256.txt", names, "provenance/apt-lists")
        exported = evidence.manifest_names(
            self.source,
            "provenance/apt-index-sha256.txt",
            "provenance/apt-lists",
            evidence.INDEX_NAME,
            evidence.MAX_FILE,
            "amd64",
        )
        self.assertEqual(exported, [f"provenance/apt-lists/{name}" for name in names])

    def test_security_index_wrong_arch_or_host_rejected(self) -> None:
        for name in (
            "security.ubuntu.com_ubuntu_dists_resolute-security_main_binary-arm64_Packages.lz4",
            "evil.ubuntu.com_ubuntu_dists_resolute-security_main_binary-amd64_Packages.lz4",
            "security.ubuntu.com_ubuntu_dists_resolute-updates_main_binary-amd64_Packages.lz4",
        ):
            with self.subTest(name=name):
                self.put(f"provenance/apt-lists/{name}")
                self.manifest(
                    "provenance/apt-index-sha256.txt", [name], "provenance/apt-lists"
                )
                with self.assertRaisesRegex(ValueError, "manifest|architecture"):
                    evidence.manifest_names(
                        self.source,
                        "provenance/apt-index-sha256.txt",
                        "provenance/apt-lists",
                        evidence.INDEX_NAME,
                        evidence.MAX_FILE,
                        "amd64",
                    )

    def test_malformed_proof_text_still_writes_partial_status(self) -> None:
        for relative in (
            "provenance/source-input.sha256",
            "build-a/ubuntu-binary-chain.log",
            "build-a/installed-console.log",
        ):
            with self.subTest(relative=relative):
                original = (self.source / relative).read_bytes()
                self.put(relative, b"\xff\n")
                code, report, dest = self.run_export()
                self.assertEqual(code, 1)
                self.assertEqual(report["status"], "incomplete")
                self.assertTrue(
                    any(
                        "proof validation failed: UnicodeDecodeError" in error
                        for error in report["errors"]
                    )
                )
                self.assertTrue((dest / "ci-status.json").exists())
                self.put(relative, original)


if __name__ == "__main__":
    unittest.main()
