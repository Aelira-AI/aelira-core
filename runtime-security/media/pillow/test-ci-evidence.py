#!/usr/bin/env python3
"""Public Pillow copy and refusal controls using a clean synthetic checkout."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
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
        wheel = "pillow-12.3.0-cp314-cp314-linux_aarch64.whl"
        chain = (
            "accepted_tiff_input=pass architecture=arm64 packages=3",
            "frozen_input_chain=pass architecture=arm64 signed_packages=1 signed_releases=1 pypi_inputs=4 tiff_packages=3",
        )
        wheel_marker = "wheel_record=pass entries=119 arch=arm64 elf_modules=5 libtiff_needed=libtiff.so.6"
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
            "record_entries": 122,
            "wheel_payload_entries": 119,
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
        self.put("input/python/pillow-12.3.0.tar.gz", b"changed after producer proof")
        self.reject("proof value mismatch")
        self.put("input/python/pillow-12.3.0.tar.gz")
        for run in "ab":
            self.put(
                f"build-{run}/wheels/pillow-12.3.0-cp314-cp314-linux_aarch64.whl",
                b"equal but different wheel bytes",
            )
        self.reject("proof value mismatch")
        for run in "ab":
            self.put(
                f"build-{run}/wheels/pillow-12.3.0-cp314-cp314-linux_aarch64.whl",
                b"identical synthetic wheel",
            )
        self.put("installed-image-id.txt", ("sha256:" + "b" * 64 + "\n").encode())
        self.reject("proof value mismatch")

    def test_copied_execution_input_is_the_bound_source(self) -> None:
        prepared = Path(self.temp.name) / "prepared-origin"
        shutil.copytree(self.source / "input", prepared)
        (prepared / "python/pillow-12.3.0.tar.gz").write_bytes(
            b"changed original after copy"
        )
        result, _ = self.export()
        self.assertEqual(result["status"], "complete")
        self.put("input/python/pillow-12.3.0.tar.gz", b"changed copied execution input")
        self.reject("proof value mismatch")

    def test_incomplete_record_elf_and_semantics_refuse(self) -> None:
        self.put(
            "build-b/wheel-verification.log",
            b"wheel_record=pass entries=119 arch=arm64 elf_modules=0 libtiff_needed=missing\n",
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


if __name__ == "__main__":
    unittest.main()
