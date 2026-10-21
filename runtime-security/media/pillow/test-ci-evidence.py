#!/usr/bin/env python3
"""Public Pillow copy and refusal controls using a clean synthetic checkout."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import proof
from strict_json import loads, integer

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("pillow_ci", HERE / "ci-evidence.py")
assert spec and spec.loader
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
lock_spec = importlib.util.spec_from_file_location(
    "pillow_lock", HERE / "recipe-lock.py"
)
assert lock_spec and lock_spec.loader
lock = importlib.util.module_from_spec(lock_spec)
lock_spec.loader.exec_module(lock)


def h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ExportControls(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        )
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.source = root / "source"
        self.repo.mkdir()
        self.source.mkdir()
        recipe = self.repo / "runtime-security/media/pillow"
        recipe.mkdir(parents=True)
        for name in ci.RECIPE:
            (recipe / name).write_bytes((HERE / name).read_bytes())
        for name in ci.CONSUMERS:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((HERE.parents[2] / name).read_bytes())
        self.run_git("init", "-q")
        self.run_git("add", ".")
        self.run_git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        )
        self.revision = self.git("HEAD")
        self.tree = self.git("HEAD^{tree}")
        self.put(
            "recipe/manifest.json",
            json.dumps(
                {
                    "revision": self.revision,
                    "tree": self.tree,
                    "files": {name: ci.sha(recipe / name) for name in ci.RECIPE},
                }
            ).encode(),
        )
        for name in ci.RECIPE:
            self.put(f"recipe/{name}", (recipe / name).read_bytes())
        for name in ci.STATIC:
            if name in (
                "recipe/manifest.json",
                "build-proof.json",
                "installed-proof.json",
            ):
                continue
            data = b"fixture\n"
            if name == "build-exit.txt" or name == "consumer-exit.txt":
                data = b"0\n"
            elif name in (
                "baseline-image-id.txt",
                "installed-image-id.txt",
                "provenance/frozen-builder-id.txt",
            ):
                data = ("sha256:" + "a" * 64 + "\n").encode()
            elif name == "provenance/base-image.txt":
                data = ("ubuntu@sha256:" + "b" * 64 + "\n").encode()
            elif name == "host-machine.txt":
                data = b"aarch64\n"
            elif name == "daemon-architecture.txt":
                data = b"arm64\n"
            elif name == "consumer-source-sha256.tsv":
                data = "".join(
                    f"{path}\t{digest}\n"
                    for path, digest in ci.ACCEPTED_CONSUMERS.items()
                ).encode()
            self.put(name, data)
        index = "ports.ubuntu.com_ubuntu-ports_dists_resolute_InRelease"
        packages = "ports.ubuntu.com_ubuntu-ports_dists_resolute_main_binary-arm64_Packages.lz4"
        for name in (index, packages):
            self.put(f"input/apt-lists/{name}")
        deb = "libfoo_1.0_arm64.deb"
        self.put(f"input/apt-debs/{deb}")
        tiff = [
            f"{p}_4.7.2-0aelira1_arm64.deb"
            for p in ("libtiff6", "libtiffxx6", "libtiff-dev")
        ]
        for name in tiff:
            self.put(f"input/tiff/{name}")
        self.manifest(
            "provenance/apt-index-sha256.txt", "input/apt-lists", (index, packages)
        )
        self.manifest("provenance/toolchain-debs.sha256", "input/apt-debs", (deb,))
        self.manifest("input/tiff/SHA256SUMS", "input/tiff", tiff)
        for name in ci.PYTHON_INPUTS:
            self.put(f"input/python/{name}")
        self.put("input/python/extra-private.json", b"private")
        wheel = "pillow-13.0.0-cp314-cp314-linux_aarch64.whl"
        chain = (
            "accepted_tiff_input=pass architecture=arm64 packages=3",
            "frozen_input_chain=pass architecture=arm64 signed_packages=1 signed_releases=1 pypi_inputs=4 tiff_packages=3",
        )
        wheel_marker = "wheel_record=pass entries=118 arch=arm64 elf_modules=5 libtiff_needed=libtiff.so.6"
        for run in "ab":
            self.put(f"build-{run}/wheels/{wheel}", b"identical synthetic wheel")
            self.put(f"build-{run}/input-chain.log", ("\n".join(chain) + "\n").encode())
            self.put(
                f"build-{run}/wheel-verification.log", (wheel_marker + "\n").encode()
            )
            self.put(f"build-{run}/build-packages.tsv")
            self.put(f"build-{run}/build-python-packages.txt")
        semantics = {
            "schema": "pillow-installed-semantics-v1",
            "record_entries": 121,
            "wheel_payload_entries": 118,
            "wheel_sha256": h(b"identical synthetic wheel"),
            "features_enabled": {name: True for name in proof.FEATURES},
            "features_disabled": {"libimagequant": False, "tkinter": False},
            "roundtrip_bytes": {name: 100 for name in proof.FORMATS},
            "tiff_pages": 2,
            "tiff_pixels": [[255, 0, 0], [0, 255, 0]],
            "loaded_tiff_sha256": proof.TIFF_HASH["arm64"],
            "font_colour": True,
            "malformed_refused": {name: True for name in proof.MALFORMED},
            "aelira_pdf": True,
            "aelira_suitability": True,
            "private_directory_absent": True,
        }
        self.put(
            "installed-consumer.log",
            b"\n".join(
                x.encode()
                for x in (
                    "Pillow wheel-private library directory absent",
                    "RECORD verified:",
                    "multiframe TIFF Deflate: 2 pages decoded",
                    "Aelira PDF image verifier:",
                    "Aelira handwritten math decoder:",
                    "loaded TIFF:",
                    "FreeType/Raqm text and LittleCMS transform: ok",
                    *(
                        f"feature {feature}:"
                        for feature in (
                            "jpg",
                            "zlib",
                            "libtiff",
                            "webp",
                            "jpg_2000",
                            "freetype2",
                            "littlecms2",
                            "raqm",
                            "avif",
                            "xcb",
                        )
                    ),
                    *(
                        f"roundtrip {format}:"
                        for format in (
                            "PNG",
                            "JPEG",
                            "WEBP",
                            "JPEG2000",
                            "AVIF",
                            "TIFF",
                        )
                    ),
                    *(
                        f"refused malformed {format}"
                        for format in ("PNG", "JPEG", "TIFF", "random")
                    ),
                    "baseline-disabled feature libimagequant: absent",
                    "baseline-disabled feature tkinter: absent",
                )
            )
            + b"\npillow_installed_receipt="
            + json.dumps(semantics, sort_keys=True).encode()
            + b"\n",
        )
        self.chain_patch = patch.object(proof, "run_chain", return_value=chain)
        self.wheel_patch = patch.object(proof, "run_wheel", return_value=wheel_marker)
        self.chain_patch.start()
        self.wheel_patch.start()
        self.addCleanup(self.chain_patch.stop)
        self.addCleanup(self.wheel_patch.stop)
        proof.capture_inventory(self.source)
        proof.capture_build(self.source, "arm64")
        proof.capture_installed(self.source, "arm64")
        self.serial = 0

    def put(self, name: str, data: bytes = b"fixture\n") -> None:
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def manifest(
        self, name: str, directory: str, entries: tuple[str, ...] | list[str]
    ) -> None:
        self.put(
            name,
            "".join(
                f"{ci.sha(self.source / directory / entry)}  ./{entry}\n"
                for entry in entries
            ).encode(),
        )

    def run_git(self, *args: str) -> None:
        subprocess.run(["/usr/bin/git", "-C", str(self.repo), *args], check=True)

    def git(self, ref: str) -> str:
        return subprocess.check_output(
            ["/usr/bin/git", "-C", str(self.repo), "rev-parse", ref], text=True
        ).strip()

    def export(self, **kwargs: object) -> tuple[dict, Path]:
        self.serial += 1
        dest = Path(self.temp.name) / f"public-{self.serial}"
        params = {
            "source": self.source,
            "dest": dest,
            "repo": self.repo,
            "revision": self.revision,
            "tree": self.tree,
            "arch": "arm64",
        }
        params.update(kwargs)
        return ci.export(**params), dest

    def reject(self, phrase: str, **kwargs: object) -> None:
        with self.assertRaisesRegex(ValueError, phrase):
            self.export(**kwargs)

    def test_positive_copy_and_hash_equality(self) -> None:
        result, dest = self.export()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["build_exit"], 0)
        self.assertEqual(result["consumer_exit"], 0)
        self.assertEqual(result["wheel_sha256"], h(b"identical synthetic wheel"))
        for item in result["files"]:
            self.assertEqual(ci.sha(dest / item["path"]), item["sha256"])
            self.assertEqual((dest / item["path"]).stat().st_size, item["bytes"])
        self.assertFalse((dest / "provenance/frozen-builder.tar").exists())
        self.assertFalse((dest / "installed-consumer.log").exists())
        self.assertFalse((dest / "provenance/frozen-builder.inspect.json").exists())
        ci.check_export_closure(
            dest, result["files"], (dest / "ci-status.json").read_bytes()
        )

    def test_replay_generated_entries_refuse_without_complete_status(self) -> None:
        original = proof.public_replay
        for kind in ("extra", "directory", "symlink", "special", "missing", "mutated"):
            with self.subTest(kind=kind):
                self.serial += 1
                dest = Path(self.temp.name) / f"replay-{self.serial}"

                def replay(root: Path, arch: str) -> None:
                    original(root, arch)
                    if kind == "extra":
                        (root / "recipe/__pycache__").mkdir()
                        (root / "recipe/__pycache__/strict_json.pyc").write_bytes(
                            b"bytecode"
                        )
                    elif kind == "directory":
                        (root / "extra-empty-directory").mkdir()
                    elif kind == "symlink":
                        (root / "recipe/alias").symlink_to(
                            root / "recipe", target_is_directory=True
                        )
                    elif kind == "special":
                        os.mkfifo(root / "recipe/pipe")
                    elif kind == "missing":
                        (root / "recipe/README.md").unlink()
                    else:
                        (root / "recipe/README.md").write_bytes(b"changed")

                with patch.object(proof, "public_replay", side_effect=replay):
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        ci.export(
                            self.source,
                            dest,
                            self.repo,
                            self.revision,
                            self.tree,
                            "arm64",
                        )
                self.assertFalse((dest / "ci-status.json").exists())

    def test_unreadable_enumeration_and_post_status_refuse(self) -> None:
        original = ci.check_export_closure
        self.serial += 1
        dest = Path(self.temp.name) / f"unreadable-{self.serial}"

        def unreadable(
            root: Path, inventory: list[dict], status_bytes: bytes | None = None
        ) -> None:
            with patch.object(ci.os, "scandir", side_effect=PermissionError("denied")):
                original(root, inventory, status_bytes)

        with patch.object(ci, "check_export_closure", side_effect=unreadable):
            with self.assertRaises(PermissionError):
                ci.export(
                    self.source, dest, self.repo, self.revision, self.tree, "arm64"
                )
        self.assertFalse((dest / "ci-status.json").exists())

        self.serial += 1
        dest = Path(self.temp.name) / f"post-status-{self.serial}"

        def closure(
            root: Path, inventory: list[dict], status_bytes: bytes | None = None
        ) -> None:
            original(root, inventory, status_bytes)
            if status_bytes is None:
                (root / "unexpected-after-replay").write_bytes(
                    b"retained failure evidence"
                )

        with patch.object(ci, "check_export_closure", side_effect=closure):
            with self.assertRaisesRegex(ValueError, "unexpected public evidence file"):
                ci.export(
                    self.source, dest, self.repo, self.revision, self.tree, "arm64"
                )
        self.assertFalse((dest / "ci-status.json").exists())
        self.assertTrue((dest / "unexpected-after-replay").exists())

    def test_failed_replay_keeps_failure_without_complete_status(self) -> None:
        self.serial += 1
        dest = Path(self.temp.name) / f"failed-replay-{self.serial}"
        with patch.object(
            proof, "public_replay", side_effect=ValueError("replay failed")
        ):
            with self.assertRaisesRegex(ValueError, "replay failed"):
                ci.export(
                    self.source, dest, self.repo, self.revision, self.tree, "arm64"
                )
        self.assertTrue(dest.is_dir())
        self.assertFalse((dest / "ci-status.json").exists())

    def test_source_changed_after_replay_refuses_without_complete_status(self) -> None:
        self.serial += 1
        dest = Path(self.temp.name) / f"source-drift-{self.serial}"
        original = proof.public_replay

        def replay(root: Path, arch: str) -> None:
            original(root, arch)
            (self.source / "recipe/README.md").write_bytes(b"changed after copy")

        with patch.object(proof, "public_replay", side_effect=replay):
            with self.assertRaises(ValueError):
                ci.export(
                    self.source, dest, self.repo, self.revision, self.tree, "arm64"
                )
        self.assertFalse((dest / "ci-status.json").exists())

    def test_revision_recipe_and_source_refusals(self) -> None:
        self.reject("revision/tree", revision="0" * 40)
        self.reject("revision/tree", tree="0" * 40)
        self.put("recipe/verify-wheel.py", b"tamper")
        self.reject("recipe source hash")
        self.put("recipe/verify-wheel.py", (HERE / "verify-wheel.py").read_bytes())
        (self.repo / "runtime-security/media/pillow/verify-wheel.py").write_text(
            "live edit"
        )
        self.reject("not clean")

    def test_snapshot_and_live_source_binding(self) -> None:
        snapshot = self.source / "recipe"
        live = self.repo / "runtime-security/media/pillow"
        lock.verify(snapshot)
        lock.live_matches(snapshot, live, self.repo)
        (live / "verify-wheel.py").write_text("changed live source")
        with self.assertRaisesRegex(ValueError, "live recipe changed"):
            lock.compare(snapshot, live)
        (live / "verify-wheel.py").write_bytes((HERE / "verify-wheel.py").read_bytes())
        (snapshot / "verify-wheel.py").write_text("tampered frozen source")
        with self.assertRaisesRegex(ValueError, "recipe hash mismatch"):
            lock.verify(snapshot)

    def test_post_producer_source_wheel_and_candidate_drift(self) -> None:
        self.put("input/python/pillow-13.0.0.tar.gz", b"changed after producer proof")
        self.reject("proof value mismatch")
        self.put("input/python/pillow-13.0.0.tar.gz")
        for run in "ab":
            self.put(
                f"build-{run}/wheels/pillow-13.0.0-cp314-cp314-linux_aarch64.whl",
                b"equal but different wheel bytes",
            )
        self.reject("proof value mismatch")
        for run in "ab":
            self.put(
                f"build-{run}/wheels/pillow-13.0.0-cp314-cp314-linux_aarch64.whl",
                b"identical synthetic wheel",
            )
        self.put("installed-image-id.txt", ("sha256:" + "b" * 64 + "\n").encode())
        self.reject("proof value mismatch")

    def test_copied_execution_input_is_the_bound_source(self) -> None:
        prepared = Path(self.temp.name) / "prepared-origin"
        shutil.copytree(self.source / "input", prepared)
        (prepared / "python/pillow-13.0.0.tar.gz").write_bytes(
            b"changed original after copy"
        )
        result, _ = self.export()
        self.assertEqual(result["status"], "complete")
        self.put("input/python/pillow-13.0.0.tar.gz", b"changed copied execution input")
        self.reject("proof value mismatch")

    def test_incomplete_record_elf_and_semantics_refuse(self) -> None:
        self.put(
            "build-b/wheel-verification.log",
            b"wheel_record=pass entries=118 arch=arm64 elf_modules=0 libtiff_needed=missing\n",
        )
        self.reject("producer wheel RECORD/ELF log mismatch")
        current = self.source / "installed-consumer.log"
        receipt = loads(current.read_text().split("pillow_installed_receipt=")[-1])
        for field, value in (
            ("wheel_payload_entries", 0),
            ("record_entries", True),
            ("tiff_pages", 1.0),
            ("aelira_pdf", False),
        ):
            with self.subTest(field=field):
                altered = dict(receipt)
                altered[field] = value
                with self.assertRaises(ValueError):
                    proof.semantic_result(
                        altered, "arm64", h(b"identical synthetic wheel")
                    )

    def test_strict_json_privacy_and_counter_types(self) -> None:
        for payload in (
            b'{"urls":[{"filename":"safe","filename":"private"}]}',
            b'{"outer":{"sha256":"one","sha256":"two"}}',
            b'{"value":NaN}',
            b'{"value":Infinity}',
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    loads(payload)
        for value in (True, False, 1.0, "1"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    integer(value)
        self.put("build-proof.json", b'{"schema":"first","schema":"second"}')
        self.reject("duplicate JSON key")

    def test_early_incomplete_run_and_unsafe_name_refuse(self) -> None:
        (self.source / "build-exit.txt").unlink()
        with self.assertRaises(FileNotFoundError):
            self.export()
        self.put("build-exit.txt", b"0\n")
        self.put("input/apt-debs/private-key.pem", b"private")
        with self.assertRaisesRegex(ValueError, "unapproved name"):
            ci.names(self.source, "input/apt-debs", ci.DEB, "arm64")

    def test_failures_and_missing_proof(self) -> None:
        self.put("build-exit.txt", b"17\n")
        self.reject("build command did not exit zero")
        self.put("build-exit.txt", b"0\n")
        self.put("consumer-exit.txt", b"1\n")
        self.reject("installed command did not exit zero")
        self.put("consumer-exit.txt", b"0\n")
        self.put("consumer-source-sha256.tsv", b"mismatch\n")
        self.reject("candidate consumer source receipt invalid")
        self.put(
            "consumer-source-sha256.tsv",
            "".join(
                f"{path}\t{digest}\n" for path, digest in ci.ACCEPTED_CONSUMERS.items()
            ).encode(),
        )
        (self.source / "installed-consumer.log").unlink()
        with self.assertRaises(FileNotFoundError):
            self.export()

    def test_symlink_and_path_refusals(self) -> None:
        self.put("input/apt-debs/extra_1.0_arm64.deb")
        self.reject("proof list mismatch")
        (self.source / "input/apt-debs/extra_1.0_arm64.deb").unlink()
        outside = Path(self.temp.name) / "private"
        outside.write_text("private")
        package = self.source / "input/apt-debs/libfoo_1.0_arm64.deb"
        package.unlink()
        package.symlink_to(outside)
        self.reject("nonregular proof input")
        package.unlink()
        package.write_bytes(b"fixture\n")
        folder = self.source / "input/apt-debs"
        moved = self.source / "moved-debs"
        folder.rename(moved)
        folder.symlink_to(moved, target_is_directory=True)
        self.reject("symlink input directory")
        with self.assertRaisesRegex(ValueError, "unsafe path"):
            ci.safe(self.source, "input/../private")
        alias = Path(self.temp.name) / "source-alias"
        alias.symlink_to(self.source, target_is_directory=True)
        self.reject("symlink in evidence root", source=alias)

    def test_architecture_budget_and_privacy_refusals(self) -> None:
        self.reject("unsupported architecture", arch="ppc64")
        self.reject("host/daemon architecture mismatch", arch="amd64")
        self.put("host-machine.txt", b"x86_64\n")
        self.reject("host/daemon architecture mismatch")
        self.put("host-machine.txt", b"aarch64\n")
        self.reject("budget", max_file=4)
        self.reject("budget", max_total=200)
        self.reject("budget", max_count=3)
        # Arbitrary siblings are never selected for export.
        result, dest = self.export()
        self.assertEqual(result["status"], "complete")
        self.assertFalse((dest / "input/python/extra-private.json").exists())
        self.put("input/apt-debs/private-key.pem", b"private")
        self.reject("proof list mismatch")


class BytecodeProcessControls(unittest.TestCase):
    """Exercise real interpreter imports and child inheritance, not media validation."""

    def test_replay_subprocesses_do_not_write_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe = root / "recipe"
            recipe.mkdir()
            (recipe / "strict_json.py").write_text("VALUE = 1\n")
            (recipe / "child.py").write_text("import strict_json\n")
            prelude = (
                "import strict_json, subprocess, sys\n"
                "from pathlib import Path\n"
                "subprocess.check_call([sys.executable, str(Path(__file__).with_name('child.py'))])\n"
            )
            chain = recipe / "verify-frozen-inputs.py"
            chain.write_text(
                prelude
                + "print('accepted_tiff_input=pass architecture=arm64 packages=3')\n"
                + "print('frozen_input_chain=pass architecture=arm64 signed_packages=1 signed_releases=4 pypi_inputs=4 tiff_packages=3')\n"
            )
            wheel_check = recipe / "verify-wheel.py"
            wheel_check.write_text(
                prelude
                + "print('wheel_record=pass entries=118 arch=arm64 elf_modules=5 libtiff_needed=libtiff.so.6')\n"
            )
            debs = root / "input/apt-debs"
            lists = root / "input/apt-lists"
            debs.mkdir(parents=True)
            lists.mkdir()
            (debs / "package.deb").write_bytes(b"fixture")
            for index in range(4):
                (lists / f"release-{index}_InRelease").write_bytes(b"fixture")
            wheel = root / "wheel.whl"
            wheel.write_bytes(b"fixture")
            environment = os.environ.copy()
            environment.pop("PYTHONDONTWRITEBYTECODE", None)
            environment.pop("PYTHONPYCACHEPREFIX", None)
            subprocess.check_output([sys.executable, str(chain)], env=environment)
            self.assertTrue((recipe / "__pycache__").exists())
            shutil.rmtree(recipe / "__pycache__")
            with patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "0"}):
                self.assertEqual(len(proof.run_chain(root, "arm64")), 2)
                self.assertIn(
                    "wheel_record=pass", proof.run_wheel(root, wheel, "arm64")
                )
            self.assertFalse((recipe / "__pycache__").exists())


class ConsumerStageControls(unittest.TestCase):
    """Exercise the real shell wrapper with synthetic native command results."""

    BASELINE = "sha256:" + "a" * 64
    CANDIDATE = "sha256:" + "b" * 64
    PRIVATE_MARKER = "PRIVATE_LOG_MARKER_NEVER_IN_STAGE_RECEIPT"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recipe = self.root / "recipe"
        self.recipe.mkdir()
        shutil.copyfile(HERE / "test-installed.sh", self.recipe / "test-installed.sh")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.stub(
            "python3",
            'if [ "$FAIL_AT" = preflight ] && [ "$2" = compare ]; then exit 37; fi\n'
            'if [ "$FAIL_AT" = final_proof ] && [ "$2" = installed ]; then exit 37; fi',
        )
        self.stub("uname", "echo aarch64")
        self.stub("sha256sum", 'echo "synthetic-wheel-hash  $1"')
        self.stub(
            "mktemp",
            'if [ "${WRITER_FAIL:-0}" = 1 ]; then exit 29; fi\n'
            'exec /usr/bin/mktemp "$@"',
        )
        self.stub(
            "docker",
            f"""printf '%s\\n' "$*" >> "$DOCKER_TRACE"
case "$1" in
  info) echo aarch64 ;;
  image)
    if [ "$4" = '{{{{.Id}}}}' ]; then
      if [ "$5" = '{self.CANDIDATE}' ]; then echo {self.CANDIDATE}; else echo {self.BASELINE}; fi
    else echo arm64; fi ;;
  commit)
    if [ "$FAIL_AT" = candidate_commit ]; then echo {self.PRIVATE_MARKER} >&2; exit 37; fi
    echo {self.CANDIDATE} ;;
  rm) exit 0 ;;
  run)
    case " $* " in
      *" --name "*)
        if [ "$FAIL_AT" = candidate_install ]; then echo {self.PRIVATE_MARKER} >&2; exit 37; fi ;;
      *" /usr/bin/sha256sum "*)
        if [ "$FAIL_AT" = source_identity ]; then echo {self.PRIVATE_MARKER} >&2; exit 37; fi
        for last do :; done
        case "$last" in
          *pdf_remediator.py) echo 'da46a6e8b7e40ca0db085a5970c727a336e86cf2207dc70aaa7114642efa56a9  file' ;;
          *handwritten_math_suitability.py) echo '310e239e7837e12e22a3ee14a831f38cf163360959675f7ae0ad149c4a5ddf50  file' ;;
          *) exit 37 ;;
        esac ;;
      *" /opt/venv/bin/python "*)
        case " $* " in
          *" --import-diagnostic "*)
            if [ "$FAIL_AT" = import40_probe_fail ]; then exit 19; fi
            echo '{{"schema":"synthetic-only"}}'
            exit 0 ;;
        esac
        if [ "$FAIL_AT" = import40 ] || [ "$FAIL_AT" = import40_probe_fail ]; then
          echo {self.PRIVATE_MARKER} >&2
          exit 40
        fi
        if [ "$FAIL_AT" = installed_verifier ]; then echo {self.PRIVATE_MARKER} >&2; exit 37; fi
        echo 'Pillow wheel-private library directory absent'
        echo '{self.PRIVATE_MARKER}' ;;
      *) exit 37 ;;
    esac ;;
  *) exit 37 ;;
esac""",
        )

    def stub(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def execute(
        self,
        name: str,
        *,
        writer_fail: bool = False,
        diagnostic_writer_fail: bool = False,
    ) -> tuple[int, Path]:
        output = self.root / name
        for directory in (
            output / "recipe",
            output / "build-a/wheels",
            output / "build-b/wheels",
        ):
            directory.mkdir(parents=True)
        if diagnostic_writer_fail:
            (output / "pillow-import-diagnostic.json").mkdir()
        wheel = "pillow-13.0.0-cp314-cp314-linux_aarch64.whl"
        for run in "ab":
            (output / f"build-{run}/wheels/{wheel}").write_bytes(b"same wheel")
        (output / "build-a/wheel-verification.log").write_text("verified\n")
        (output / "reproducible-sha256.txt").write_text("synthetic-wheel-hash\n")
        (output / "build-exit.txt").write_text("0\n")
        env = os.environ.copy()
        env.update(
            PATH=str(self.bin) + os.pathsep + env["PATH"],
            FAIL_AT=name.split("-")[0],
            WRITER_FAIL="1" if writer_fail else "0",
            DOCKER_TRACE=str(output / "docker-trace-private.log"),
        )
        result = subprocess.run(
            [
                "bash",
                str(self.recipe / "test-installed.sh"),
                str(output),
                self.BASELINE,
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode, output

    def test_fixed_stage_and_original_exit_for_each_command_group(self) -> None:
        for stage in (
            "preflight",
            "candidate_install",
            "candidate_commit",
            "source_identity",
            "installed_verifier",
            "final_proof",
            "complete",
        ):
            with self.subTest(stage=stage):
                rc, output = self.execute(stage)
                expected = 0 if stage == "complete" else 37
                self.assertEqual(rc, expected)
                self.assertEqual((output / "consumer-exit.txt").read_text(), f"{rc}\n")
                receipt = (output / "consumer-stage.json").read_bytes()
                self.assertLessEqual(len(receipt), 128)
                self.assertEqual(
                    json.loads(receipt),
                    {"schema": 1, "stage": stage, "exit": rc},
                )
                self.assertNotIn(self.PRIVATE_MARKER.encode(), receipt)

    def test_writer_failure_preserves_original_exit_and_no_receipt(self) -> None:
        rc, output = self.execute("source_identity-writer", writer_fail=True)
        self.assertEqual(rc, 37)
        self.assertEqual((output / "consumer-exit.txt").read_text(), "37\n")
        self.assertFalse((output / "consumer-stage.json").exists())

    def test_secondary_probe_preserves_original_import_exit(self) -> None:
        for name, secondary in (("import40", "0\n"), ("import40_probe_fail", "19\n")):
            with self.subTest(name=name):
                rc, output = self.execute(name)
                self.assertEqual(rc, 40)
                self.assertEqual((output / "consumer-exit.txt").read_text(), "40\n")
                self.assertEqual(
                    json.loads((output / "consumer-stage.json").read_text()),
                    {"schema": 1, "stage": "installed_verifier", "exit": 40},
                )
                self.assertEqual(
                    (output / "pillow-import-diagnostic-exit.txt").read_text(),
                    secondary,
                )
                invocations = (
                    (output / "docker-trace-private.log").read_text().splitlines()
                )
                consumer_calls = [
                    line for line in invocations if " /tmp/verify-installed.py" in line
                ]
                self.assertEqual(len(consumer_calls), 2)
                primary, probe = consumer_calls
                self.assertEqual(
                    probe.removesuffix(f" --import-diagnostic {self.CANDIDATE}"),
                    primary,
                )
                for required in (
                    "--platform linux/arm64",
                    "--network none",
                    "--read-only",
                    "--user 1000:1000",
                    "--cap-drop ALL",
                    "--security-opt no-new-privileges",
                    "--tmpfs /tmp:rw,nosuid,nodev,size=128m",
                    self.CANDIDATE,
                ):
                    self.assertIn(required, primary)
        rc, output = self.execute("complete")
        self.assertEqual(rc, 0)
        self.assertFalse((output / "pillow-import-diagnostic.json").exists())
        self.assertNotIn(
            " --import-diagnostic ",
            (output / "docker-trace-private.log").read_text(),
        )
        rc, output = self.execute("import40-writer", diagnostic_writer_fail=True)
        self.assertEqual(rc, 40)
        self.assertEqual((output / "consumer-exit.txt").read_text(), "40\n")
        self.assertNotEqual(
            (output / "pillow-import-diagnostic-exit.txt").read_text(), "0\n"
        )

    def test_existing_receipt_is_never_overwritten(self) -> None:
        output = self.root / "installed_verifier-existing"
        output.mkdir()
        existing = output / "consumer-stage.json"
        existing.write_bytes(b"sentinel\n")
        rc, observed = self.execute("installed_verifier-existing")
        self.assertEqual(rc, 37)
        self.assertEqual(observed, output)
        self.assertEqual(existing.read_bytes(), b"sentinel\n")


class InstalledCheckExitControls(unittest.TestCase):
    """Run each fixed exit branch with synthetic Pillow and application results."""

    MARKER = "PRIVATE_CHECK_MARKER"

    def setUp(self) -> None:
        fake = types.ModuleType("PIL")
        fake.Image = types.SimpleNamespace()
        fake.ImageCms = types.SimpleNamespace()
        fake.ImageFile = types.SimpleNamespace(LOAD_TRUNCATED_IMAGES=False)
        fake.ImageFont = types.SimpleNamespace(Layout=types.SimpleNamespace(RAQM=1))
        fake.features = types.SimpleNamespace()
        spec = importlib.util.spec_from_file_location(
            "pillow_installed_verifier_test", HERE / "verify-installed.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"PIL": fake}):
            spec.loader.exec_module(module)
        self.verifier = module

    def assert_exit(self, expected: int, action) -> None:
        private_log = io.StringIO()
        with redirect_stderr(private_log), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                action()
        self.assertEqual(raised.exception.code, expected)
        self.assertTrue(private_log.getvalue())

    def test_fixed_mapping_and_exception_propagation(self) -> None:
        codes = self.verifier.CHECK_EXIT_CODES
        self.assertEqual(set(codes.values()), set(range(40, 73)))
        for name, code in codes.items():
            if name == "import_setup":
                continue
            with self.subTest(name=name):

                def fail() -> None:
                    with self.verifier.diagnosed(name):
                        raise AssertionError(self.MARKER)

                self.assert_exit(code, fail)
        for exceptional in (SystemExit(97), KeyboardInterrupt()):
            with self.subTest(exception=type(exceptional).__name__):
                with self.assertRaises(type(exceptional)) as raised:
                    with self.verifier.diagnosed("record"):
                        raise exceptional
                if isinstance(exceptional, SystemExit):
                    self.assertEqual(raised.exception.code, 97)
        with self.assertRaises(AssertionError):
            raise AssertionError(self.MARKER)  # Unmapped work keeps its failure.

    def test_tiff_loader_diagnostic_producer_and_reader_boundary(self) -> None:
        verifier = self.verifier
        cases = (
            (
                "libjbig.so.0: cannot open shared object file: No such file or directory",
                "missing_file",
                "libjbig.so.0",
            ),
            ("libtiff.so.6: Permission denied", "permission", "libtiff.so.6"),
            ("libtiff.so.6: invalid ELF header", "invalid_elf", "libtiff.so.6"),
            (
                "libtiff.so.6: undefined symbol: PRIVATE_SYMBOL",
                "undefined_symbol",
                "libtiff.so.6",
            ),
            (
                "libtiff.so.6: version `PRIVATE_VERSION' not found",
                "version_mismatch",
                "libtiff.so.6",
            ),
            ("libtiff.so.6: failed to map segment", "mapping_failure", "libtiff.so.6"),
            ("PRIVATE_LOG_MARKER", "other", None),
            (
                "libtiff.so.6 libjbig.so.0: No such file or directory",
                "missing_file",
                None,
            ),
        )
        for message, category, related in cases:
            with self.subTest(category=category, related=related):
                self.assertEqual(
                    verifier.loader_category(OSError(message)), (category, related)
                )

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            target = directory / "libtiff.so.6.3.0"
            target.write_bytes(b"synthetic")
            target.chmod(0o644)
            (directory / "libtiff.so.6").symlink_to(target.name)
            observed = verifier.tiff_file_identity(directory, "arm64")
            self.assertTrue(observed["directory_canonical"])
            self.assertEqual(observed["soname_entry"], "expected")
            self.assertEqual(observed["target_entry"], "regular")
            self.assertEqual(observed["sha256"], h(b"synthetic"))
            self.assertFalse(observed["matches_accepted"])
            alias = directory.parent / (directory.name + "-alias")
            alias.symlink_to(directory, target_is_directory=True)
            try:
                with (
                    patch.object(
                        Path, "lstat", side_effect=AssertionError("child lstat")
                    ),
                    patch.object(
                        Path, "open", side_effect=AssertionError("child open")
                    ),
                ):
                    aliased = verifier.tiff_file_identity(alias, "arm64")
                self.assertEqual(
                    aliased,
                    {
                        "directory_canonical": False,
                        "soname_entry": "unavailable",
                        "target_entry": "unavailable",
                        "size": None,
                        "mode": None,
                        "owner_root": None,
                        "sha256": None,
                        "matches_accepted": False,
                    },
                )
            finally:
                alias.unlink()
            target.write_bytes(b"x" * 2_000_001)
            with patch.object(
                Path, "open", side_effect=AssertionError("oversized open")
            ):
                oversized = verifier.tiff_file_identity(directory, "arm64")
            self.assertEqual(oversized["size"], 2_000_001)
            self.assertIsNone(oversized["sha256"])
            self.assertFalse(oversized["matches_accepted"])
            target.write_bytes(b"synthetic")
            target.chmod(0)
            self.assertEqual(verifier.tiff_file_identity(directory, "arm64")["mode"], 0)
            target.chmod(0o644)
            target.write_bytes(b"corrupted")
            self.assertEqual(
                verifier.tiff_file_identity(directory, "arm64")["sha256"],
                h(b"corrupted"),
            )
            (directory / "libtiff.so.6").unlink()
            (directory / "libtiff.so.6").symlink_to("private-target")
            self.assertEqual(
                verifier.tiff_file_identity(directory, "arm64")["soname_entry"],
                "unexpected_target",
            )

            runner_path = HERE.parents[1] / "python-native-ci/run.py"
            runner_spec = importlib.util.spec_from_file_location(
                "native_reader", runner_path
            )
            assert runner_spec and runner_spec.loader
            runner = importlib.util.module_from_spec(runner_spec)
            runner_spec.loader.exec_module(runner)
            self.assertEqual(verifier.TIFF_DEPENDENCIES, runner.TIFF_DEPENDENCIES)
            self.assertEqual(verifier.TIFF_LOADER, runner.TIFF_LOADER)
            for arch in ("arm64", "amd64"):
                self.assertEqual(
                    verifier.TIFF_TARGETS[arch][1:], runner.TIFF_TARGETS[arch]
                )
                self.assertEqual(verifier.TIFF_TARGETS[arch][2], proof.TIFF_HASH[arch])
            image = "sha256:" + "b" * 64
            file_receipt = {
                "directory_canonical": True,
                "soname_entry": "expected",
                "target_entry": "regular",
                "size": 592088,
                "mode": 0o644,
                "owner_root": True,
                "sha256": verifier.TIFF_TARGETS["arm64"][2],
                "matches_accepted": True,
            }

            def import_module(name: str) -> object:
                if name in {"PIL.Image", "PIL._imaging"}:
                    raise ImportError("PRIVATE_LOG_MARKER")
                return object()

            def load(name: str) -> object:
                if name in {"libtiff.so.6", "libjbig.so.0"}:
                    raise OSError(
                        "libjbig.so.0: cannot open shared object file: No such file or directory"
                    )
                return object()

            with (
                patch("importlib.import_module", side_effect=import_module),
                patch.object(verifier.ctypes, "CDLL", side_effect=load),
                patch.object(verifier.platform, "machine", return_value="aarch64"),
                patch.object(verifier.os, "getpid", return_value=123),
                patch.object(verifier, "tiff_file_identity", return_value=file_receipt),
                patch.object(
                    verifier,
                    "tiff_package_identity",
                    return_value={"package": "expected", "files": "clean"},
                ),
                redirect_stdout(io.StringIO()) as output,
            ):
                verifier.import_diagnostic(image)
            text = output.getvalue()
            self.assertLessEqual(len(text.encode()), 8192)
            self.assertNotIn("PRIVATE_LOG_MARKER", text)
            receipt = json.loads(text)
            self.assertEqual(receipt["libraries"][0]["loader"], "missing_file")
            self.assertEqual(receipt["tiff_dependencies_loaded"], 10)
            (directory / "installed-image-id.txt").write_text(image + "\n")
            (directory / "pillow-import-diagnostic-exit.txt").write_text("0\n")
            (directory / "pillow-import-diagnostic.json").write_text(text)
            consumer = {
                "state": "reported",
                "stage": "installed_verifier",
                "exit": 40,
                "check_code": "import_setup",
            }
            parsed = runner.pillow_import_diagnostic(directory, consumer, "arm64")
            self.assertEqual(parsed["state"], "reported")
            self.assertEqual(parsed["observation"], receipt)
            with (
                patch("importlib.import_module", side_effect=import_module),
                patch.object(verifier.ctypes, "CDLL", side_effect=load),
                patch.object(verifier.platform, "machine", return_value="aarch64"),
                patch.object(verifier.os, "getpid", return_value=123),
                patch.object(verifier, "tiff_file_identity", return_value=oversized),
                patch.object(
                    verifier,
                    "tiff_package_identity",
                    return_value={"package": "expected", "files": "clean"},
                ),
                redirect_stdout(io.StringIO()) as oversized_output,
            ):
                verifier.import_diagnostic(image)
            oversized_receipt = json.loads(oversized_output.getvalue())
            (directory / "pillow-import-diagnostic.json").write_text(
                oversized_output.getvalue()
            )
            self.assertEqual(
                runner.pillow_import_diagnostic(directory, consumer, "arm64")[
                    "observation"
                ],
                oversized_receipt,
            )
            oversized_receipt["tiff_file"]["sha256"] = h(b"synthetic")
            (directory / "pillow-import-diagnostic.json").write_text(
                json.dumps(oversized_receipt)
            )
            self.assertEqual(
                runner.pillow_import_diagnostic(directory, consumer, "arm64")["state"],
                "unavailable",
            )
            receipt["tiff_file"] = aliased
            (directory / "pillow-import-diagnostic.json").write_text(
                json.dumps(receipt)
            )
            self.assertEqual(
                runner.pillow_import_diagnostic(directory, consumer, "arm64")["state"],
                "reported",
            )

    def test_tiff_package_warnings_are_unavailable(self) -> None:
        verifier = self.verifier
        expected = b"install ok installed\t4.7.2-0aelira1\tarm64"
        for query_stderr, verify_stderr, wanted in (
            (b"PRIVATE_WARNING", b"", {"package": "unavailable", "files": "clean"}),
            (b"", b"PRIVATE_WARNING", {"package": "expected", "files": "unavailable"}),
        ):
            with (
                self.subTest(wanted=wanted),
                patch.object(
                    verifier.subprocess,
                    "run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, expected, query_stderr),
                        subprocess.CompletedProcess([], 0, b"", verify_stderr),
                    ],
                ),
            ):
                self.assertEqual(verifier.tiff_package_identity("arm64"), wanted)

    def test_import_failure_and_signal_are_not_misclassified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "PIL"
            package.mkdir()
            (package / "__init__.py").write_text(
                f'raise RuntimeError("{self.MARKER}")\n'
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = temporary
            result = subprocess.run(
                [sys.executable, str(HERE / "verify-installed.py")],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 40)
            self.assertIn(self.MARKER, result.stderr)
            self.assertNotIn(self.MARKER, result.stdout)
            script = f"""import importlib.util, os, signal, sys, types
p = types.ModuleType('PIL')
for name in ('Image', 'ImageCms', 'ImageFile', 'ImageFont', 'features'):
    setattr(p, name, types.SimpleNamespace())
sys.modules['PIL'] = p
s = importlib.util.spec_from_file_location('v', {str(HERE / 'verify-installed.py')!r})
v = importlib.util.module_from_spec(s)
s.loader.exec_module(v)
with v.diagnosed('record'):
    os.kill(os.getpid(), signal.SIGTERM)
"""
            terminated = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(terminated.returncode, -signal.SIGTERM)

    def test_main_record_wheel_roundtrips_private_and_success(self) -> None:
        verifier = self.verifier
        names = {
            "PNG": "roundtrip_png",
            "JPEG": "roundtrip_jpeg",
            "WEBP": "roundtrip_webp",
            "JPEG2000": "roundtrip_jpeg2000",
            "AVIF": "roundtrip_avif",
            "TIFF": "roundtrip_tiff",
        }

        def execute(failing: str | None) -> str:
            def selected(name: str, result):
                if failing == name:
                    raise AssertionError(self.MARKER)
                return result

            def roundtrip(fmt: str) -> bytes:
                return selected(names[fmt], b"synthetic")

            def private_exists(path: Path) -> bool:
                return failing == "private_directory" and path.name == "pillow.libs"

            with (
                patch.object(
                    verifier,
                    "check_record",
                    side_effect=lambda: selected("record", 121),
                ),
                patch.object(
                    verifier,
                    "check_wheel_payloads",
                    side_effect=lambda: selected(
                        "wheel_payloads", (118, "synthetic-hash")
                    ),
                ),
                patch.object(verifier, "check_features", return_value=({}, {})),
                patch.object(verifier, "round_trip", side_effect=roundtrip),
                patch.object(verifier, "check_tiff", return_value="synthetic-tiff"),
                patch.object(verifier, "check_font_and_colour", return_value=True),
                patch.object(verifier, "check_rejection", return_value={}),
                patch.object(Path, "exists", private_exists),
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    verifier.main()
                return output.getvalue()

        for name in ("record", "wheel_payloads", *names.values(), "private_directory"):
            with self.subTest(name=name):
                self.assert_exit(verifier.CHECK_EXIT_CODES[name], lambda: execute(name))
        success = execute(None)
        marker = "pillow_installed_receipt="
        self.assertEqual(success.count(marker), 1)
        receipt = json.loads(success.split(marker, 1)[1])
        self.assertEqual(receipt["schema"], "pillow-installed-semantics-v1")
        self.assertEqual(receipt["record_entries"], 121)
        self.assertEqual(receipt["wheel_payload_entries"], 118)

    def test_each_feature_exit_uses_the_original_assertion(self) -> None:
        verifier = self.verifier
        required = (
            "jpg",
            "zlib",
            "libtiff",
            "webp",
            "jpg_2000",
            "freetype2",
            "littlecms2",
            "raqm",
            "avif",
            "xcb",
        )
        for feature in (*required, "libimagequant", "tkinter", "truncated_images"):
            with self.subTest(feature=feature):

                def check(name: str) -> bool:
                    if name == feature:
                        return name not in required
                    return name in required

                with (
                    patch.object(
                        verifier.features, "check", side_effect=check, create=True
                    ),
                    patch.object(
                        verifier.features,
                        "version",
                        return_value="synthetic",
                        create=True,
                    ),
                    patch.object(
                        verifier.ImageFile,
                        "LOAD_TRUNCATED_IMAGES",
                        feature == "truncated_images",
                    ),
                ):
                    name = (
                        feature
                        if feature == "truncated_images"
                        else ("feature_" if feature in required else "disabled_")
                        + feature
                    )
                    self.assert_exit(
                        verifier.CHECK_EXIT_CODES[name], verifier.check_features
                    )

    def test_tiff_font_colour_and_malformed_branches(self) -> None:
        verifier = self.verifier
        self.assert_exit(
            verifier.CHECK_EXIT_CODES["tiff_pages"],
            lambda: self._tiff_with_new(lambda *args: self._fail()),
        )

        class Image:
            n_frames = 2

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def save(self, output, **_kwargs):
                output.write(b"synthetic")

            def seek(self, frame):
                self.frame = frame

            def load(self):
                pass

            def getpixel(self, _point):
                return ((255, 0, 0), (0, 255, 0))[self.frame]

        with (
            patch.object(verifier.Image, "new", return_value=Image(), create=True),
            patch.object(verifier.Image, "open", return_value=Image(), create=True),
            patch.object(verifier.ctypes, "CDLL", side_effect=OSError(self.MARKER)),
        ):
            self.assert_exit(
                verifier.CHECK_EXIT_CODES["tiff_library"], verifier.check_tiff
            )
        with patch.object(
            verifier.ImageFont,
            "truetype",
            side_effect=OSError(self.MARKER),
            create=True,
        ):
            self.assert_exit(
                verifier.CHECK_EXIT_CODES["font"], verifier.check_font_and_colour
            )
        font = types.SimpleNamespace(getlength=lambda _text: 1)
        with (
            patch.object(
                verifier.ImageFont, "truetype", return_value=font, create=True
            ),
            patch.object(
                verifier.ImageCms,
                "createProfile",
                side_effect=OSError(self.MARKER),
                create=True,
            ),
        ):
            self.assert_exit(
                verifier.CHECK_EXIT_CODES["colour"], verifier.check_font_and_colour
            )

        class AcceptedMalformed:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def load(self):
                pass

        formats = ("PNG", "JPEG", "TIFF", "random")
        for target in formats:
            with self.subTest(format=target):
                seen = iter(formats)

                def open_image(_payload):
                    name = next(seen)
                    if name == target:
                        return AcceptedMalformed()
                    raise OSError("synthetic refusal")

                with patch.object(
                    verifier.Image, "open", side_effect=open_image, create=True
                ):
                    self.assert_exit(
                        verifier.CHECK_EXIT_CODES[f"malformed_{target.lower()}"],
                        lambda: verifier.check_rejection(b"png", b"jpeg", b"tiff"),
                    )

    def test_actual_application_consumer_exit_branches(self) -> None:
        verifier = self.verifier
        png, jpeg, tiff = b"p" * 50, b"j" * 50, b"t" * 50
        modules = {
            name: types.ModuleType(name)
            for name in (
                "src",
                "src.education",
                "src.education.remediation",
                "src.education.remediation.pdf_remediator",
                "src.education.handwritten_math_suitability",
            )
        }
        pdf = modules["src.education.remediation.pdf_remediator"]
        suitability = modules["src.education.handwritten_math_suitability"]
        suitability.SuitabilityInputRejected = type(
            "SuitabilityInputRejected", (ValueError,), {}
        )
        suitability._decode_image = lambda _payload: (
            types.SimpleNamespace(size=(0, 0)),
            "PNG",
        )
        with (
            patch.dict(sys.modules, modules),
            patch.object(
                verifier.Image, "open", side_effect=OSError("refused"), create=True
            ),
        ):
            pdf._is_verified_image = lambda *_args: False
            self.assert_exit(
                verifier.CHECK_EXIT_CODES["aelira_pdf"],
                lambda: verifier.check_rejection(png, jpeg, tiff),
            )
            pdf._is_verified_image = (
                lambda payload, fmt: payload == png and fmt == "png"
            )
            self.assert_exit(
                verifier.CHECK_EXIT_CODES["aelira_suitability"],
                lambda: verifier.check_rejection(png, jpeg, tiff),
            )

    def _fail(self):
        raise AssertionError(self.MARKER)

    def _tiff_with_new(self, replacement):
        with patch.object(
            self.verifier.Image, "new", side_effect=replacement, create=True
        ):
            self.verifier.check_tiff()


if __name__ == "__main__":
    unittest.main()
