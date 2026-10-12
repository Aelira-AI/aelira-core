"""Benign orchestration and trust-boundary controls; no native build is claimed."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

MODULE = Path(__file__).with_name("run.py")
SPEC = importlib.util.spec_from_file_location("python_native_ci_runner", MODULE)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
H40 = "a" * 40
H64 = "b" * 64


class BoundaryTests(unittest.TestCase):
    def test_native_git_source_identity_positive_then_dirty_and_changed_refusals(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp).resolve()
            mode = {"dirty": False, "changed": False}

            def fake_git(_repo: Path, *args: str) -> str:
                if args == ("rev-parse", "--show-toplevel"):
                    return str(repo)
                if args == ("remote", "get-url", "origin"):
                    return runner.ORIGIN.removesuffix(".git")
                if args == ("rev-parse", "HEAD"):
                    return H40
                if args == ("rev-parse", "HEAD^{tree}"):
                    return "c" * 40
                if args == ("status", "--porcelain", "--untracked-files=all"):
                    return " M requirements.txt" if mode["dirty"] else ""
                if args[:2] == ("ls-files", "--error-unmatch"):
                    return args[-1]
                if args[:2] == ("hash-object", "--"):
                    return "d" * 40 if mode["changed"] else "e" * 40
                if args[0] == "rev-parse" and args[1].startswith("HEAD:"):
                    return "e" * 40
                raise AssertionError(args)

            with (
                patch.object(runner.os, "getuid", return_value=1000),
                patch.object(runner.platform, "system", return_value="Linux"),
                patch.object(runner.platform, "machine", return_value="aarch64"),
                patch.object(runner, "git", side_effect=fake_git),
                patch.object(runner, "probe", return_value="arm64"),
                patch.object(runner, "canonical_file"),
            ):
                accepted = runner.identity(repo, "arm64", H40)
                self.assertEqual(accepted["origin"], runner.ORIGIN.removesuffix(".git"))
                self.assertEqual(accepted["tree"], "c" * 40)
                mode["dirty"] = True
                with self.assertRaisesRegex(ValueError, "clean"):
                    runner.identity(repo, "arm64", H40)
                mode["dirty"] = False
                mode["changed"] = True
                with self.assertRaisesRegex(ValueError, "bytes"):
                    runner.identity(repo, "arm64", H40)

    def test_exact_origin_and_clean_committed_source_required(self) -> None:
        repo = Path("/public/repo")
        calls = []

        def fake_git(_repo: Path, *args: str) -> str:
            calls.append(args)
            if args == ("rev-parse", "--show-toplevel"):
                return str(repo)
            if args == ("remote", "get-url", "origin"):
                return "https://github.com/foreign/project.git"
            raise AssertionError("untrusted origin should stop inspection")

        with (
            patch.object(runner.os, "getuid", return_value=1000),
            patch.object(runner.platform, "system", return_value="Linux"),
            patch.object(Path, "resolve", return_value=repo),
            patch.object(runner, "git", side_effect=fake_git),
        ):
            with self.assertRaisesRegex(ValueError, "origin"):
                runner.identity(repo, "amd64", H40)
        self.assertEqual(calls[-1], ("remote", "get-url", "origin"))

    def test_zip_exact_members_and_alias_refusals(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            archive = root / "accepted.zip"
            names = [
                f"build-a/{p}_4.7.2-0aelira1_arm64.deb"
                for p in ("libtiff6", "libtiffxx6", "libtiff-dev")
            ]

            def member(name: str, mode: int = stat.S_IFREG | 0o644) -> zipfile.ZipInfo:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = mode << 16
                return info

            def make(extra: tuple[str, int] | None = None) -> None:
                with zipfile.ZipFile(archive, "w") as output:
                    for name in names:
                        output.writestr(member(name), b"benign")
                    if extra:
                        output.writestr(member(*extra), b"benign")

            make()
            with patch.dict(
                runner.ZIP_SHA256,
                {"arm64": hashlib.sha256(archive.read_bytes()).hexdigest()},
            ):
                runner.extract_tiff(archive, root / "selected", "arm64")
            self.assertEqual(
                {p.name for p in (root / "selected").iterdir()},
                {Path(n).name for n in names},
            )
            make(("./" + names[0], stat.S_IFREG | 0o644))
            with patch.dict(
                runner.ZIP_SHA256,
                {"arm64": hashlib.sha256(archive.read_bytes()).hexdigest()},
            ):
                with self.assertRaisesRegex(ValueError, "unsafe or duplicated"):
                    runner.extract_tiff(archive, root / "duplicate", "arm64")
            make((names[0], stat.S_IFLNK | 0o777))
            with patch.dict(
                runner.ZIP_SHA256,
                {"arm64": hashlib.sha256(archive.read_bytes()).hexdigest()},
            ):
                with self.assertRaisesRegex(ValueError, "unsafe or duplicated"):
                    runner.extract_tiff(archive, root / "symlink", "arm64")

    def test_source_and_baseline_schema_refusals(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = {
                "files": 130,
                "src_files": 120,
                "selected_pins": 5,
                "context_manifest_sha256": H64,
                "requirements_blob": H40,
                "src_tree": H40,
                "archive_sha256": H64,
                "commit": H40,
                "origin": runner.ORIGIN,
                "native_bootstrap_sha256": H64,
                "external_integrity_sha256": H64,
            }
            path = root / "source.json"
            path.write_text(json.dumps(source))
            fixture = root / "runtime-security" / "python-consumer-fixture"
            fixture.mkdir(parents=True)
            (fixture / "native.py").write_bytes(b"native")
            (fixture / "integrity.py").write_bytes(b"integrity")
            with (
                patch.object(runner, "git", side_effect=[H40, H40]),
                patch.object(runner, "digest", side_effect=[H64, H64]),
            ):
                accepted = runner.prepare_receipt(
                    path, {"revision": H40, "origin": runner.ORIGIN}, fixture
                )
            self.assertEqual(accepted["archive_sha256"], H64)
            baseline = {
                "status": "official_wheel_baseline_pass",
                "architecture": "arm64",
                "base_manifest": runner.BASE,
                "base_image_id": "sha256:" + H64,
                "image_id": "sha256:" + H64,
                "source": {
                    key: accepted[key]
                    for key in (
                        "files",
                        "src_files",
                        "selected_pins",
                        "context_manifest_sha256",
                        "requirements_blob",
                        "src_tree",
                    )
                },
                "commit": H40,
                "origin": runner.ORIGIN,
                "archive_sha256": H64,
                "frozen_input": {"input_manifest_sha256": H64},
                "official_component_wheels": {
                    "pillow": {"sha256": H64},
                    "psycopg2-binary": {"sha256": H64},
                },
                "baseline": {
                    "passed": True,
                    "state": "official-wheel-before-replacement",
                    "private_library_dirs_present": [
                        "pillow.libs",
                        "psycopg2_binary.libs",
                    ],
                },
            }
            baseline_path = root / "baseline.json"
            baseline_path.write_text(json.dumps(baseline))
            self.assertEqual(
                runner.baseline_receipt(
                    baseline_path,
                    accepted,
                    {"revision": H40, "origin": runner.ORIGIN, "architecture": "arm64"},
                )["image_id"],
                "sha256:" + H64,
            )
            baseline["source"]["src_tree"] = "0" * 40
            baseline_path.write_text(json.dumps(baseline))
            with self.assertRaisesRegex(ValueError, "baseline/source"):
                runner.baseline_receipt(
                    baseline_path,
                    accepted,
                    {"revision": H40, "origin": runner.ORIGIN, "architecture": "arm64"},
                )
            path.write_text(path.read_text().rstrip()[:-1] + ',"commit":"' + H40 + '"}')
            with self.assertRaisesRegex(ValueError, "duplicate"):
                runner.strict_json(path)
            baseline_path.write_text("{}")
            with self.assertRaisesRegex(ValueError, "receipt"):
                runner.baseline_receipt(
                    baseline_path, source, {"revision": H40, "origin": runner.ORIGIN}
                )

    def test_subprocess_original_nonzero_unavailable_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            scratch = Path(temp)
            (scratch / "logs").mkdir()
            failed = runner.run_stage(
                "failed", [sys.executable, "-c", "raise SystemExit(37)"], scratch
            )
            self.assertEqual(
                failed, {"exit": 37, "state": "failed", "reason": "process_nonzero"}
            )
            missing = runner.run_stage("missing", [str(scratch / "absent")], scratch)
            self.assertEqual(
                missing,
                {"exit": None, "state": "unavailable", "reason": "command_missing"},
            )
            timeout = runner.run_stage(
                "timeout",
                [sys.executable, "-c", "import time; time.sleep(2)"],
                scratch,
                timeout=0.01,
            )
            self.assertEqual(
                timeout, {"exit": None, "state": "unavailable", "reason": "timeout"}
            )
            self.assertNotIn(
                "raise SystemExit", (scratch / "logs" / "failed.log").read_text()
            )


class OrchestrationTests(unittest.TestCase):
    def run_case(
        self, exits: dict[str, int]
    ) -> tuple[int, dict, list[list[str]], dict, set[str]]:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            repo = root / "repo"
            repo.mkdir()
            scratch = root / "scratch"
            dest = root / "public"
            tiff = root / "tiff.zip"
            tiff.write_bytes(b"placeholder")
            observed: list[list[str]] = []
            source = {
                "files": 130,
                "src_files": 120,
                "selected_pins": 5,
                "context_manifest_sha256": H64,
                "requirements_blob": H40,
                "src_tree": H40,
                "archive_sha256": H64,
            }
            baseline = {
                "status": "official_wheel_baseline_pass",
                "image_id": "sha256:" + H64,
                "archive_sha256": H64,
                "input_manifest_sha256": H64,
                "official_wheel_sha256": {"pillow": H64, "psycopg2-binary": H64},
            }

            def fake_stage(
                name: str, command: list[str], _scratch: Path, **_kwargs: object
            ) -> dict[str, object]:
                observed.append(command)
                if name == "source_export":
                    (scratch / "fixture-export.tar").write_bytes(b"benign")
                rc = exits.get(name, 0)
                if name == "pillow_export":
                    candidate = scratch / "pillow-public-candidate"
                    candidate.mkdir()
                    (
                        candidate / ("ci-status.json" if rc == 0 else "private-raw.log")
                    ).write_text("{}")
                if name == "psyc_export":
                    candidate = scratch / "psyc-public-candidate"
                    candidate.mkdir()
                    (
                        candidate / ("summary.json" if rc == 0 else "private-raw.log")
                    ).write_text("{}")
                return {
                    "exit": rc,
                    "state": "passed" if rc == 0 else "failed",
                    "reason": "none" if rc == 0 else "process_nonzero",
                }

            ident = {
                "revision": H40,
                "tree": H40,
                "architecture": "arm64",
                "origin": runner.ORIGIN,
            }
            with (
                patch.object(runner, "identity", return_value=ident),
                patch.object(runner, "canonical_file"),
                patch.object(
                    runner,
                    "digest",
                    side_effect=lambda path: (
                        runner.ZIP_SHA256["arm64"] if path == tiff else H64
                    ),
                ),
                patch.object(runner, "run_stage", side_effect=fake_stage),
                patch.object(runner, "prepare_receipt", return_value=source),
                patch.object(runner, "baseline_receipt", return_value=baseline),
                patch.object(runner, "extract_tiff"),
                patch.object(
                    runner, "probe", side_effect=[baseline["image_id"], "arm64"]
                ),
            ):
                rc = runner.execute(repo, "arm64", H40, scratch, tiff, dest)
            return (
                rc,
                json.loads((dest / "summary.json").read_text()),
                observed,
                json.loads((dest / "fixture.json").read_text()),
                {p.name for p in dest.iterdir()},
            )

    def test_success_uses_one_baseline_for_both_components(self) -> None:
        rc, summary, commands, fixture, entries = self.run_case({})
        self.assertEqual(rc, 0)
        self.assertEqual(summary["disposition"], "component_evidence_complete")
        baseline = fixture["image_id"]
        self.assertEqual(sum(baseline in command for command in commands), 2)
        self.assertFalse(summary["full_runtime_cleared"])
        self.assertFalse(summary["release_security_cleared"])
        self.assertEqual(
            entries, {"summary.json", "fixture.json", "pillow", "psycopg2"}
        )

    def test_consumer_and_build_failures_preserve_original_exits(self) -> None:
        rc, summary, commands, _, entries = self.run_case(
            {"pillow_consumer": 9, "psyc_build": 37, "psyc_export": 1}
        )
        self.assertEqual(rc, 1)
        self.assertEqual(summary["stages"]["pillow_consumer"]["exit"], 9)
        self.assertEqual(summary["stages"]["psyc_build"]["exit"], 37)
        self.assertEqual(summary["stages"]["psyc_consumer"]["exit"], 125)
        exporter = next(command for command in commands if "--build-exit" in command)
        self.assertEqual(exporter[exporter.index("--build-exit") + 1], "37")
        self.assertEqual(exporter[exporter.index("--consumer-exit") + 1], "125")
        self.assertNotIn(
            "pillow_export",
            [
                key
                for key, value in summary["stages"].items()
                if value["state"] != "not_run"
            ],
        )
        self.assertEqual(entries, {"summary.json", "fixture.json"})

    def test_failed_exporter_partial_output_stays_private(self) -> None:
        rc, summary, _, _, entries = self.run_case({"pillow_export": 33})
        self.assertEqual(rc, 1)
        self.assertEqual(summary["stages"]["pillow_export"]["exit"], 33)
        self.assertEqual(summary["stages"]["pillow_publish"]["exit"], 125)
        self.assertEqual(entries, {"summary.json", "fixture.json", "psycopg2"})

    def test_early_failure_keeps_bounded_receipts(self) -> None:
        rc, summary, _, fixture, entries = self.run_case({"fixture_test_prepare": 18})
        self.assertEqual(rc, 1)
        self.assertEqual(summary["stages"]["fixture_test_prepare"]["exit"], 18)
        self.assertEqual(summary["stages"]["fixture_native"]["exit"], 125)
        self.assertEqual(fixture, {"status": "incomplete"})
        self.assertEqual(entries, {"summary.json", "fixture.json"})


if __name__ == "__main__":
    unittest.main()
