"""Private synthetic controls for lxml native CI orchestration; no build claim."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

MODULE = Path(__file__).with_name("run.py")
SPEC = importlib.util.spec_from_file_location("lxml_native_runner", MODULE)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["/usr/bin/git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="lxml-native-ci-private-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "synthetic-public-shape"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "synthetic@example.invalid")
        git(self.repo, "config", "user.name", "Synthetic Test")
        git(self.repo, "remote", "add", "origin", runner.ORIGIN)
        files = (
            {runner.RECIPE / name for name in runner.RECIPE_FILES}
            | {runner.RUNNER / name for name in runner.RUNNER_FILES}
            | {Path(name) for name in runner.SOURCE_FILES}
        )
        for name in files:
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"synthetic test byte for {name}\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "test: private synthetic fixture")
        self.commit = git(self.repo, "rev-parse", "HEAD")
        self.tree = git(self.repo, "rev-parse", "HEAD^{tree}")

    def outputs(self) -> tuple[Path, Path]:
        return self.root / "new-private", self.root / "new-public"


class IdentityTests(Fixture):
    def test_caller_contract_matches_accepted_exporter_and_recipe(self) -> None:
        exporter_path = MODULE.with_name("evidence.py")
        spec = importlib.util.spec_from_file_location(
            "lxml_native_exporter", exporter_path
        )
        assert spec and spec.loader
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        self.assertEqual(runner.RECIPE_FILES, exporter.RECIPE)
        self.assertEqual(runner.SOURCE_FILES, exporter.SOURCE)
        self.assertEqual(runner.RECEIPTS, exporter.STRUCTURED | {"apt-selections.json"})
        self.assertEqual(
            {"prepare-native", "accept-native", "run-consumer"}, exporter.STATUS_NAMES
        )
        self.assertEqual(
            (runner.MAX_PUBLIC_FILES, runner.MAX_PUBLIC_FILE, runner.MAX_PUBLIC_TOTAL),
            (exporter.MAX_FILES, exporter.MAX_FILE, exporter.MAX_TOTAL),
        )
        actual_recipe = MODULE.parent.parent / "lxml"
        from_runner = runner.recipe_digest(actual_recipe.parent.parent)
        from_recipe = subprocess.run(
            [
                sys.executable,
                "-I",
                str(actual_recipe / "input-gate.py"),
                "recipe-id",
                str(actual_recipe),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(from_runner, from_recipe)

    def test_trusted_origin_variants_and_exact_git_bytes(self) -> None:
        first = runner.identity(self.repo, "amd64", self.commit, self.tree)
        self.assertEqual(first["recipe_sha256"], runner.recipe_digest(self.repo))
        git(
            self.repo,
            "remote",
            "set-url",
            "origin",
            "https://github.com/Aelira-AI/aelira-core",
        )
        second = runner.identity(self.repo, "amd64", self.commit, self.tree)
        self.assertEqual(
            second["observed_origin"], "https://github.com/Aelira-AI/aelira-core"
        )
        self.assertEqual(
            runner.normalize_origin(self.repo, second["observed_origin"]),
            "normalized_trusted_https",
        )
        self.assertEqual(git(self.repo, "remote", "get-url", "origin"), runner.ORIGIN)

    def test_refuse_foreign_origin_changed_head_tree_dirty_source(self) -> None:
        git(
            self.repo,
            "remote",
            "set-url",
            "origin",
            "https://github.com/foreign/aelira-core.git",
        )
        with self.assertRaises(ValueError):
            runner.identity(self.repo, "amd64", self.commit, self.tree)
        git(self.repo, "remote", "set-url", "origin", runner.ORIGIN)
        with self.assertRaises(ValueError):
            runner.identity(self.repo, "amd64", "a" * 40, self.tree)
        with self.assertRaises(ValueError):
            runner.identity(self.repo, "amd64", self.commit, "b" * 40)
        source = self.repo / "requirements.txt"
        source.write_text("changed\n")
        with self.assertRaises(ValueError):
            runner.identity(self.repo, "amd64", self.commit, self.tree)
        git(self.repo, "checkout", "--", "requirements.txt")
        (self.repo / "unsafe-untracked").write_text("x")
        with self.assertRaises(ValueError):
            runner.identity(self.repo, "amd64", self.commit, self.tree)

    def test_output_paths_are_new_owned_separate_and_outside_repo(self) -> None:
        scratch, dest = self.outputs()
        runner.outputs(self.repo, scratch, dest)
        with self.assertRaises(ValueError):
            runner.outputs(self.repo, self.repo / "within", dest)
        dest.touch()
        with self.assertRaises(ValueError):
            runner.outputs(self.repo, scratch, dest)


class StageTests(Fixture):
    def test_boolean_exit_is_not_success(self) -> None:
        self.assertFalse(runner.passed({"state": "passed", "exit_code": True}))

    def test_real_benign_process_outcomes(self) -> None:
        scratch = self.root / "processes"
        (scratch / "logs").mkdir(parents=True)
        cases = [
            ("zero", [sys.executable, "-c", "print('okay')"], 0, "passed", "none", 5),
            (
                "nonzero",
                [sys.executable, "-c", "raise SystemExit(7)"],
                7,
                "failed",
                "process_nonzero",
                5,
            ),
            (
                "missing",
                ["/no/such/benign-command"],
                None,
                "unavailable",
                "command_missing",
                5,
            ),
            (
                "timeout",
                [sys.executable, "-c", "import time; time.sleep(1)"],
                None,
                "unavailable",
                "timeout",
                0.01,
            ),
            (
                "signal",
                [
                    sys.executable,
                    "-c",
                    "import os,signal; os.kill(os.getpid(), signal.SIGTERM)",
                ],
                None,
                "unavailable",
                "terminated_by_signal",
                5,
            ),
        ]
        for name, argv, code, state, reason, timeout in cases:
            with self.subTest(name=name):
                public, private = runner.stage(name, argv, scratch, timeout)
                self.assertEqual(
                    (public["exit_code"], public["state"], public["reason"]),
                    (code, state, reason),
                )
                self.assertEqual(
                    private["log_sha256"], sha(Path(private["log_path"]).read_bytes())
                )


class OrchestrationTests(Fixture):
    def synthetic_stage(
        self, name: str, argv: list[str], scratch: Path, _: int
    ) -> tuple[dict[str, object], dict[str, object]]:
        native = scratch / "native"
        if name == "prepare-native":
            frozen = native / "frozen"
            frozen.mkdir(parents=True)
            for source, target in (
                *(
                    (self.repo / runner.RECIPE / item, frozen / "recipe" / item)
                    for item in runner.RECIPE_FILES
                ),
                *(
                    (self.repo / item, frozen / "source" / item)
                    for item in runner.SOURCE_FILES
                ),
            ):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            for item, value in (
                ("architecture.txt", "amd64\n"),
                ("source-commit.txt", self.commit + "\n"),
                ("source-tree.txt", self.tree + "\n"),
            ):
                (frozen / item).write_text(value)
            entries = {
                path.relative_to(frozen).as_posix(): runner.digest(path)
                for path in frozen.rglob("*")
                if path.is_file()
            }
            entries["verifier/image.tar"] = sha(b"synthetic omitted verifier")
            (frozen / "frozen.sha256").write_text(
                "".join(f"{entries[item]}  {item}\n" for item in sorted(entries))
            )
        if name == "accept-native":
            for build in ("build-a", "build-b"):
                directory = native / build
                directory.mkdir(parents=True)
                wheel = directory / "lxml-6.1.3-cp314-cp314-linux_x86_64.whl"
                wheel.write_bytes(b"synthetic wheel bytes")
                (directory / "wheel.sha256").write_text(
                    f"{runner.digest(wheel)}  /out/{wheel.name}\n"
                )
        if name == "evidence-export":
            candidate = scratch / "public-candidate"
            (candidate / "frozen").mkdir(parents=True)
            for source in (native / "frozen").rglob("*"):
                if source.is_file():
                    target = (
                        candidate / "frozen" / source.relative_to(native / "frozen")
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
            for item in runner.RECEIPTS:
                proof = candidate / "receipts" / item
                proof.parent.mkdir(parents=True, exist_ok=True)
                proof.write_text('{"synthetic":true}\n')
            wheel_name = "lxml-6.1.3-cp314-cp314-linux_x86_64.whl"
            for build in ("build-a", "build-b"):
                target = candidate / "wheels" / build / wheel_name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"synthetic wheel bytes")
            status = json.loads((scratch / "original-status.json").read_text())
            manifest = {
                "schema": 1,
                "scope": "public-commit",
                "origin": runner.ORIGIN,
                "commit": self.commit,
                "tree": self.tree,
                "recipe_sha256": runner.recipe_digest(self.repo),
                "frozen_sha256": runner.digest(native / "frozen" / "frozen.sha256"),
                "wheel_sha256": runner.digest(
                    native / "build-a" / "lxml-6.1.3-cp314-cp314-linux_x86_64.whl"
                ),
                "architecture": "amd64",
                "excluded": ["verifier/image.tar", "raw logs", "installer-venv.tar"],
                "original_commands": {
                    key: {
                        "exit_code": value["exit_code"],
                        "log_sha256": value["log_sha256"],
                    }
                    for key, value in status["commands"].items()
                },
                "files": {
                    path.relative_to(candidate).as_posix(): {
                        "sha256": runner.digest(path),
                        "size": path.stat().st_size,
                    }
                    for path in candidate.rglob("*")
                    if path.is_file()
                },
            }
            runner.write_json(candidate / "bundle.json", manifest)
        log = scratch / "logs" / f"{name}.log"
        log.write_text(f"synthetic {name}\n")
        public = {
            "exit_code": 0,
            "state": "passed",
            "reason": "none",
            "log_sha256": runner.digest(log),
        }
        private = {
            "exit_code": 0,
            "log_path": str(log),
            "log_sha256": runner.digest(log),
        }
        return public, private

    def test_positive_synthetic_orchestration_before_refusals(self) -> None:
        scratch, dest = self.outputs()
        with (
            mock.patch.object(runner, "native_preflight"),
            mock.patch.object(
                runner, "stage", side_effect=self.synthetic_stage
            ) as stages,
        ):
            result = runner.execute(
                self.repo, "amd64", self.commit, self.tree, scratch, dest
            )
        summary = json.loads((dest / "summary.json").read_text())
        self.assertEqual(result, 0, summary)
        self.assertEqual(summary["disposition"], "component_evidence_complete")
        self.assertTrue(summary["source_bound"])
        self.assertEqual(summary["stages"]["run-consumer"]["exit_code"], 0)
        self.assertTrue((dest / "evidence" / "bundle.json").is_file())
        self.assertFalse(summary["full_runtime_cleared"])
        calls = {call.args[0]: call.args[1] for call in stages.call_args_list}
        expected_recipe = runner.recipe_digest(self.repo)
        common = [
            str(scratch / "native"),
            str(self.repo),
            self.commit,
            self.tree,
            expected_recipe,
        ]
        self.assertEqual(calls["prepare-native"][2:], common)
        self.assertEqual(
            calls["accept-native"][2:], common + [summary["frozen_sha256"]]
        )
        self.assertEqual(
            calls["run-consumer"][2:],
            common + [summary["frozen_sha256"], summary["wheel_sha256"]],
        )

        def option(argv: list[str], name: str) -> str:
            return argv[argv.index(name) + 1]

        self.assertEqual(calls["evidence-export"][3], "export")
        self.assertEqual(calls["evidence-check"][3], "check")
        self.assertEqual(option(calls["evidence-export"], "--scope"), "public-commit")
        self.assertEqual(
            option(calls["evidence-export"], "--status"),
            str(scratch / "original-status.json"),
        )
        self.assertEqual(
            option(calls["evidence-export"], "--out"), str(scratch / "public-candidate")
        )
        self.assertEqual(
            option(calls["evidence-check"], "--bundle"),
            str(scratch / "public-candidate"),
        )
        for flag, value in (
            ("--repo", str(self.repo)),
            ("--commit", self.commit),
            ("--tree", self.tree),
            ("--recipe-sha256", expected_recipe),
            ("--frozen-sha256", summary["frozen_sha256"]),
            ("--wheel-sha256", summary["wheel_sha256"]),
        ):
            self.assertEqual(option(calls["evidence-export"], flag), value)
            self.assertEqual(option(calls["evidence-check"], flag), value)
        expected = {
            "commit": self.commit,
            "tree": self.tree,
            "recipe_sha256": runner.recipe_digest(self.repo),
            "frozen_sha256": summary["frozen_sha256"],
            "wheel_sha256": summary["wheel_sha256"],
            "architecture": "amd64",
        }
        original = json.loads((scratch / "original-status.json").read_text())[
            "commands"
        ]
        receipt = dest / "evidence" / "receipts" / "apt-selections.json"
        original_receipt = receipt.read_bytes()
        receipt.write_text("changed")
        with self.assertRaises(ValueError):
            runner.safe_public_bundle(dest / "evidence", self.repo, expected, original)
        receipt.write_bytes(original_receipt)
        bundle_file = dest / "evidence" / "bundle.json"
        bundle = json.loads(bundle_file.read_text())
        bundle["schema"] = True
        runner.write_json(bundle_file, bundle)
        with self.assertRaises(ValueError):
            runner.safe_public_bundle(dest / "evidence", self.repo, expected, original)
        bundle["schema"] = 1
        runner.write_json(bundle_file, bundle)
        manifest = dest / "evidence" / "frozen" / "frozen.sha256"
        manifest.write_text(f"{'b' * 64}  verifier/image.tar\n")
        with self.assertRaises(ValueError):
            runner.safe_public_bundle(
                dest / "evidence",
                self.repo,
                expected,
                original,
            )
        manifest.write_bytes(
            (scratch / "native" / "frozen" / "frozen.sha256").read_bytes()
        )
        source_name = "requirements.txt"
        source = dest / "evidence" / "frozen" / "source" / source_name
        source.write_text("changed synthetic source\n")
        frozen = dest / "evidence" / "frozen" / "frozen.sha256"
        lines = frozen.read_text().splitlines(keepends=True)
        frozen.write_text(
            "".join(
                (
                    f"{runner.digest(source)}  source/{source_name}\n"
                    if line.endswith(f"  source/{source_name}\n")
                    else line
                )
                for line in lines
            )
        )
        bundle = json.loads(bundle_file.read_text())
        for name, path in (
            (f"frozen/source/{source_name}", source),
            ("frozen/frozen.sha256", frozen),
        ):
            bundle["files"][name] = {
                "sha256": runner.digest(path),
                "size": path.stat().st_size,
            }
        bundle["frozen_sha256"] = runner.digest(frozen)
        runner.write_json(bundle_file, bundle)
        with self.assertRaisesRegex(ValueError, "frozen application source"):
            runner.safe_public_bundle(
                dest / "evidence",
                self.repo,
                {**expected, "frozen_sha256": runner.digest(frozen)},
                original,
            )

    def test_original_stage_failures_keep_dependent_steps_not_run(self) -> None:
        for index, failing in enumerate(
            ("accept-native", "run-consumer", "evidence-check")
        ):
            with self.subTest(stage=failing):
                scratch, dest = (
                    self.root / f"private-{index}",
                    self.root / f"public-{index}",
                )

                def fail_stage(name: str, argv: list[str], root: Path, timeout: int):
                    public, private = self.synthetic_stage(name, argv, root, timeout)
                    if name == failing:
                        return (
                            {
                                **public,
                                "exit_code": 19,
                                "state": "failed",
                                "reason": "process_nonzero",
                            },
                            {**private, "exit_code": 19},
                        )
                    return public, private

                with (
                    mock.patch.object(runner, "native_preflight"),
                    mock.patch.object(
                        runner, "stage", side_effect=fail_stage
                    ) as stages,
                ):
                    self.assertEqual(
                        runner.execute(
                            self.repo, "amd64", self.commit, self.tree, scratch, dest
                        ),
                        1,
                    )
                summary = json.loads((dest / "summary.json").read_text())
                self.assertEqual(summary["stages"][failing]["exit_code"], 19)
                self.assertEqual(
                    summary["stages"]["evidence-publish"]["exit_code"], 125
                )
                self.assertEqual(
                    set(path.name for path in dest.iterdir()), {"summary.json"}
                )
                called = [call.args[0] for call in stages.call_args_list]
                self.assertEqual(called[-1], failing)
                private_status = json.loads(
                    (scratch / "original-status.json").read_text()
                )
                if failing == "accept-native":
                    self.assertEqual(
                        private_status["commands"][failing]["exit_code"], 19
                    )
                elif failing == "run-consumer":
                    self.assertEqual(
                        private_status["commands"][failing]["exit_code"], 19
                    )
                else:
                    self.assertEqual(
                        {
                            row["exit_code"]
                            for row in private_status["commands"].values()
                        },
                        {0},
                    )

    def test_original_failure_keeps_only_safe_outer_status(self) -> None:
        scratch, dest = self.outputs()

        def fail_first(name: str, argv: list[str], root: Path, timeout: int):
            if name == "prepare-native":
                log = root / "logs" / f"{name}.log"
                log.write_text("private path and synthetic secret token\n")
                return (
                    {
                        "exit_code": 9,
                        "state": "failed",
                        "reason": "process_nonzero",
                        "log_sha256": runner.digest(log),
                    },
                    {
                        "exit_code": 9,
                        "log_path": str(log),
                        "log_sha256": runner.digest(log),
                    },
                )
            self.fail("dependent step ran")

        with (
            mock.patch.object(runner, "native_preflight"),
            mock.patch.object(runner, "stage", side_effect=fail_first),
        ):
            self.assertEqual(
                runner.execute(
                    self.repo, "amd64", self.commit, self.tree, scratch, dest
                ),
                1,
            )
        summary = json.loads((dest / "summary.json").read_text())
        self.assertEqual(summary["stages"]["prepare-native"]["exit_code"], 9)
        self.assertEqual(summary["stages"]["accept-native"]["exit_code"], 125)
        self.assertNotIn("synthetic secret", (dest / "summary.json").read_text())
        self.assertNotIn(str(scratch), (dest / "summary.json").read_text())
        self.assertEqual(set(path.name for path in dest.iterdir()), {"summary.json"})
        self.assertTrue((scratch / "original-status.json").is_file())

    def test_identity_failure_cannot_claim_bound_source(self) -> None:
        scratch, dest = self.outputs()
        git(
            self.repo,
            "remote",
            "set-url",
            "origin",
            "https://example.invalid/foreign.git",
        )
        with mock.patch.object(runner, "native_preflight"):
            self.assertEqual(
                runner.execute(
                    self.repo, "amd64", self.commit, self.tree, scratch, dest
                ),
                1,
            )
        summary = json.loads((dest / "summary.json").read_text())
        self.assertFalse(summary["source_bound"])
        self.assertNotIn("commit", summary)
        self.assertEqual(summary["disposition"], "refused")

    def test_preflight_refusal_leaves_trusted_origin_unchanged(self) -> None:
        scratch, dest = self.outputs()
        no_suffix = "https://github.com/Aelira-AI/aelira-core"
        git(self.repo, "remote", "set-url", "origin", no_suffix)
        with mock.patch.object(
            runner, "native_preflight", side_effect=ValueError("not native")
        ):
            self.assertEqual(
                runner.execute(
                    self.repo, "amd64", self.commit, self.tree, scratch, dest
                ),
                1,
            )
        self.assertEqual(git(self.repo, "remote", "get-url", "origin"), no_suffix)
        summary = json.loads((dest / "summary.json").read_text())
        self.assertFalse(summary["source_bound"])
        self.assertEqual(summary["origin_normalization"], "not_run")

    def test_unsafe_or_changed_export_never_published(self) -> None:
        scratch, dest = self.outputs()

        def bad_export(name: str, argv: list[str], root: Path, timeout: int):
            result = self.synthetic_stage(name, argv, root, timeout)
            if name == "evidence-export":
                (root / "public-candidate" / "receipts" / "private.log").write_text(
                    "secret"
                )
            return result

        with (
            mock.patch.object(runner, "native_preflight"),
            mock.patch.object(runner, "stage", side_effect=bad_export),
        ):
            self.assertEqual(
                runner.execute(
                    self.repo, "amd64", self.commit, self.tree, scratch, dest
                ),
                1,
            )
        summary = json.loads((dest / "summary.json").read_text())
        self.assertEqual(summary["disposition"], "refused")
        self.assertNotIn("bundle", summary)
        self.assertEqual(set(path.name for path in dest.iterdir()), {"summary.json"})

    def test_export_failure_preserves_original_success_and_private_partial(
        self,
    ) -> None:
        scratch, dest = self.outputs()

        def fail_export(name: str, argv: list[str], root: Path, timeout: int):
            result = self.synthetic_stage(name, argv, root, timeout)
            if name == "evidence-export":
                public, private = result
                public = {
                    **public,
                    "exit_code": 17,
                    "state": "failed",
                    "reason": "process_nonzero",
                }
                private = {**private, "exit_code": 17}
                return public, private
            return result

        with (
            mock.patch.object(runner, "native_preflight"),
            mock.patch.object(runner, "stage", side_effect=fail_export),
        ):
            self.assertEqual(
                runner.execute(
                    self.repo, "amd64", self.commit, self.tree, scratch, dest
                ),
                1,
            )
        summary = json.loads((dest / "summary.json").read_text())
        for name in ("prepare-native", "accept-native", "run-consumer"):
            self.assertEqual(summary["stages"][name]["exit_code"], 0)
        self.assertEqual(summary["stages"]["evidence-export"]["exit_code"], 17)
        self.assertEqual(summary["stages"]["evidence-publish"]["exit_code"], 125)
        self.assertEqual(set(path.name for path in dest.iterdir()), {"summary.json"})
        self.assertTrue((scratch / "public-candidate" / "bundle.json").exists())


if __name__ == "__main__":
    unittest.main()
