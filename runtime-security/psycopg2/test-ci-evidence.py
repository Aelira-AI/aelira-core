#!/usr/bin/env python3
"""Synthetic, source-bound regression checks for the bounded evidence copier."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "psycopg2_ci_evidence", Path(__file__).with_name("ci-evidence.py")
)
assert SPEC and SPEC.loader
e = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(e)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(root: Path, name: str, data: bytes | str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def commit(repo: Path) -> tuple[str, str]:
    subprocess.run(["/usr/bin/git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "add", "runtime-security/psycopg2"],
        check=True,
    )
    subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@invalid.local",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )

    def rev(ref: str) -> str:
        return subprocess.check_output(
            ["/usr/bin/git", "-C", str(repo), "rev-parse", ref], text=True
        ).strip()

    return rev("HEAD"), rev("HEAD^{tree}")


class EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        )
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "source"
        self.repo = self.base / "repo"
        self.dest = self.base / "export"
        self.source.mkdir()
        self.repo.mkdir()
        self.arch = "arm64"
        frozen: dict[str, str] = {}
        for name in e.RECIPE:
            data = (Path(__file__).parent / name).read_bytes()
            put(self.repo, "runtime-security/psycopg2/" + name, data)
            put(self.source, "frozen/recipe/" + name, data)
            frozen["recipe/" + name] = sha(data)
        pin_lines = (Path(__file__).parent / "inputs.sha256").read_text().splitlines()
        for line in pin_lines:
            digest, name = line.split("  ")
            # Fixture bytes match the pinned hash only through a local override
            # of the public recipe pin, then both copies are committed together.
            data = ("synthetic " + name).encode()
            put(self.source, "frozen/python/" + name, data)
            frozen["python/" + name] = sha(data)
        pins = "".join(
            f"{frozen['python/' + line.split('  ')[1]]}  {line.split('  ')[1]}\n"
            for line in pin_lines
        )
        put(self.repo, "runtime-security/psycopg2/inputs.sha256", pins)
        put(self.source, "frozen/recipe/inputs.sha256", pins)
        frozen["recipe/inputs.sha256"] = sha(pins.encode())
        self.index = "ports.ubuntu.com_ubuntu-ports_dists_resolute_main_binary-arm64_Packages.lz4"
        self.release = "ports.ubuntu.com_ubuntu-ports_dists_resolute_InRelease"
        self.deb = "libpq-dev_18.6-0ubuntu0.26.04.1_arm64.deb"
        signed_index_hash = sha(b"synthetic uncompressed index")
        release_data = f"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\nSHA256:\n {signed_index_hash} 28 main/binary-arm64/Packages\n-----BEGIN PGP SIGNATURE-----\nfixture\n"
        for group, name, data in (
            ("apt-lists", self.index, b"synthetic index"),
            ("apt-lists", self.release, release_data.encode()),
            ("debs", self.deb, b"synthetic package"),
        ):
            put(self.source, f"frozen/{group}/{name}", data)
            frozen[f"{group}/{name}"] = sha(data)
        keyring = b"synthetic public keyring fixture" * 50
        put(self.source, "frozen/keyring/ubuntu-archive-keyring.gpg", keyring)
        frozen["keyring/ubuntu-archive-keyring.gpg"] = sha(keyring)
        put(self.source, "frozen/architecture.txt", "arm64\n")
        manifest = "".join(f"{v}  {k}\n" for k, v in sorted(frozen.items()))
        put(self.source, "frozen/frozen.sha256", manifest)
        wheel_name = "psycopg2_binary-2.9.13-cp314-cp314-linux_aarch64.whl"
        wheel_data = b"synthetic native wheel bytes"
        wheel_hash = sha(wheel_data)
        auth = {
            "keyring_sha256": frozen["keyring/ubuntu-archive-keyring.gpg"],
            "release": [
                {
                    "file": self.release,
                    "sha256": frozen["apt-lists/" + self.release],
                    "signed_indexes": 1,
                }
            ],
            "indexes": [
                {
                    "file": self.index,
                    "release": self.release,
                    "path": "main/binary-arm64/Packages",
                    "sha256": signed_index_hash,
                    "size": 28,
                }
            ],
            "debs": [
                {
                    "file": self.deb,
                    "package": "libpq-dev",
                    "version": "18.6-0ubuntu0.26.04.1",
                    "architecture": "arm64",
                    "sha256": frozen["debs/" + self.deb],
                    "size": len(b"synthetic package"),
                    "signed_index_filename": "pool/main/p/postgresql/libpq-dev.deb",
                }
            ],
        }
        self.wheel = wheel_name
        self.wheel_hash = wheel_hash
        for run in "ab":
            put(self.source, f"build-{run}/{wheel_name}", wheel_data)
            put(
                self.source,
                f"build-{run}/wheel.sha256",
                f"{wheel_hash}  {self.source}/build-{run}/{wheel_name}\n",
            )
            put(
                self.source,
                f"build-{run}/authenticated-packages.json",
                json.dumps(auth),
            )
            put(
                self.source,
                f"build-{run}/installed-build-packages.txt",
                "".join(f"libfixture{i}:arm64\t1.0\n" for i in range(20)),
            )
            put(
                self.source,
                f"build-{run}/python-build-tools.json",
                json.dumps(
                    [
                        {"name": "pip", "version": "26.2.1"},
                        {"name": "setuptools", "version": "84.0.0"},
                    ]
                ),
            )
        libs = {
            "libpq.so.5": sha(b"pq"),
            "libssl.so.3": sha(b"ssl"),
            "libcrypto.so.3": sha(b"crypto"),
        }
        installed = {
            "package": "psycopg2-binary",
            "version": "2.9.13",
            "record_hashes_verified": 19,
            "extension_sha256": sha(b"extension"),
            "compiled_libpq": 180006,
            "runtime_libpq": 180006,
            "loaded_library_sha256": libs,
            "loaded_library_packages": {k: "libfixture:arm64" for k in libs},
        }
        consumer = {
            "passed": True,
            "driver_version": "2.9.13",
            "compiled_libpq": 180006,
            "runtime_libpq": 180006,
            "postgres_server_major": 16,
            "tls_protocol": "TLSv1.3",
            "controls": list(e.CONTROLS),
        }
        put(self.source, "synthetic-db/installed.json", json.dumps(installed))
        put(self.source, "synthetic-db/consumers.json", json.dumps(consumer))
        identity = {
            "host_uid": "1000",
            "host_gid": "1000",
            "probe_uid": "1000",
            "probe_gid": "1000",
            "architecture": "arm64",
            "frozen_manifest_sha256": sha(manifest.encode()),
            "frozen_consumer_sha256": frozen["recipe/consumer.py"],
            "frozen_verifier_sha256": frozen["recipe/verify_installed.py"],
            "wheel_sha256": wheel_hash,
            "runtime_image_id": "sha256:" + "a" * 64,
        }
        put(
            self.source,
            "synthetic-db/harness-identity.txt",
            "".join(f"{k}={v}\n" for k, v in identity.items()),
        )
        put(self.source, "synthetic-db/pg-password", "synthetic-private-password")
        put(self.source, "synthetic-db/tls/server.key", "synthetic-private-key")
        put(self.source, "unrelated.log", "dsn=secret")
        self.revision, self.tree = commit(self.repo)

    def run_export(self, **changes):
        params = dict(
            source=self.source,
            dest=self.dest,
            repo=self.repo,
            arch=self.arch,
            revision=self.revision,
            tree=self.tree,
            build_exit=0,
            consumer_exit=0,
        )
        params.update(changes)
        return e.export(**params)

    def assert_refused(self, error=ValueError, **changes):
        with self.assertRaises(error):
            self.run_export(**changes)
        summary = json.loads((self.dest / "summary.json").read_text())
        self.assertEqual(summary["disposition"], "refused")
        self.assertFalse(summary["evidence_complete"])
        self.assertEqual([p.name for p in self.dest.iterdir()], ["summary.json"])
        shutil.rmtree(self.dest)

    def test_positive_exact_bytes_and_private_omission(self):
        result = self.run_export()
        self.assertTrue(result["component_accepted"])
        manifest = json.loads((self.dest / "manifest.json").read_text())
        self.assertEqual(result["file_count"], len(manifest["files"]))
        for item in manifest["files"]:
            self.assertEqual(
                (self.source / item["path"]).read_bytes(),
                (self.dest / item["path"]).read_bytes(),
            )
            self.assertEqual(
                sha((self.dest / item["path"]).read_bytes()), item["sha256"]
            )
        for forbidden in (
            "synthetic-db/pg-password",
            "synthetic-db/tls/server.key",
            "unrelated.log",
            "build-a/wheel.sha256",
        ):
            self.assertFalse((self.dest / forbidden).exists())

    def test_original_failure_status_preserved(self):
        result = self.run_export(build_exit=1, consumer_exit=9)
        self.assertFalse(result["component_accepted"])
        summary = json.loads((self.dest / "summary.json").read_text())
        self.assertEqual((summary["build_exit"], summary["consumer_exit"]), (1, 9))

    def test_actual_early_build_failure_keeps_only_status(self):
        (self.source / "build-b" / self.wheel).unlink()
        with self.assertRaises(ValueError):
            self.run_export(build_exit=12, consumer_exit=125)
        summary = json.loads((self.dest / "summary.json").read_text())
        self.assertEqual(
            (summary["build_exit"], summary["consumer_exit"], summary["disposition"]),
            (12, 125, "refused"),
        )
        self.assertEqual([p.name for p in self.dest.iterdir()], ["summary.json"])

    def test_failed_consumer_keeps_original_status(self):
        path = self.source / "synthetic-db/consumers.json"
        report = json.loads(path.read_text())
        report["passed"] = False
        put(self.source, "synthetic-db/consumers.json", json.dumps(report))
        with self.assertRaises(ValueError):
            self.run_export(consumer_exit=9)
        summary = json.loads((self.dest / "summary.json").read_text())
        self.assertEqual(
            (summary["build_exit"], summary["consumer_exit"], summary["disposition"]),
            (0, 9, "refused"),
        )
        self.assertEqual([p.name for p in self.dest.iterdir()], ["summary.json"])

    def test_missing_and_corrupt_required_evidence(self):
        (self.source / "build-b" / self.wheel).unlink()
        with self.assertRaises(ValueError):
            self.run_export()
        self.assertEqual(
            json.loads((self.dest / "summary.json").read_text())["disposition"],
            "refused",
        )
        self.assertEqual([p.name for p in self.dest.iterdir()], ["summary.json"])

    def test_missing_keyring_and_corrupt_wheel_manifest(self):
        (self.source / "frozen/keyring/ubuntu-archive-keyring.gpg").unlink()
        self.assert_refused(FileNotFoundError)
        put(
            self.source,
            "frozen/keyring/ubuntu-archive-keyring.gpg",
            b"synthetic public keyring fixture" * 50,
        )
        put(
            self.source,
            "build-a/wheel.sha256",
            "0" * 64 + "  /private/absolute/other.whl\n",
        )
        self.assert_refused()

    def test_stale_revision_tree_and_live_source(self):
        self.assert_refused(revision="0" * 40)
        self.assert_refused(tree="0" * 40)
        put(self.repo, "runtime-security/psycopg2/consumer.py", "changed")
        self.assert_refused()

    def test_frozen_probe_mismatch(self):
        put(
            self.source,
            "synthetic-db/harness-identity.txt",
            (self.source / "synthetic-db/harness-identity.txt")
            .read_text()
            .replace("frozen_consumer_sha256=", "frozen_consumer_sha256=" + "0"),
        )
        self.assert_refused()

    def test_invalid_json_and_secret_disclosure(self):
        put(self.source, "synthetic-db/installed.json", "{")
        self.assert_refused(json.JSONDecodeError)
        put(
            self.source,
            "synthetic-db/installed.json",
            json.dumps({"dsn": "postgres://secret"}),
        )
        self.assert_refused()

    def test_unsafe_allowlisted_scalar(self):
        path = self.source / "synthetic-db/harness-identity.txt"
        put(
            self.source,
            "synthetic-db/harness-identity.txt",
            path.read_text() + "dsn=postgres://secret\n",
        )
        self.assert_refused()

    def test_duplicate_json_keys_each_family_and_nested_canary(self):
        canary = "PRIVATE_SYNTHETIC_CANARY"
        cases = (
            (
                "synthetic-db/installed.json",
                lambda s: s.replace(
                    '"package":', f'"package":"{canary}","package":', 1
                ),
            ),
            (
                "synthetic-db/installed.json",
                lambda s: s.replace(
                    '"libpq.so.5":', f'"libpq.so.5":"{canary}","libpq.so.5":', 1
                ),
            ),
            (
                "synthetic-db/consumers.json",
                lambda s: s.replace('"passed":', f'"passed":"{canary}","passed":', 1),
            ),
            (
                "build-a/authenticated-packages.json",
                lambda s: s.replace('"file":', f'"file":"{canary}","file":', 1),
            ),
            (
                "build-a/python-build-tools.json",
                lambda s: s.replace('"name":', f'"name":"{canary}","name":', 1),
            ),
        )
        for relative, edit in cases:
            with self.subTest(relative=relative, edit=edit):
                original = (self.source / relative).read_text()
                edited = edit(original)
                put(self.source, relative, edited)
                if relative.startswith("build-a/"):
                    put(self.source, relative.replace("build-a/", "build-b/"), edited)
                self.assert_refused()
                put(self.source, relative, original)
                if relative.startswith("build-a/"):
                    put(self.source, relative.replace("build-a/", "build-b/"), original)

    def test_nonstandard_json_and_exact_scalar_types(self):
        installed_path = self.source / "synthetic-db/installed.json"
        original_installed = installed_path.read_text()
        put(
            self.source,
            "synthetic-db/installed.json",
            original_installed.replace(
                '"record_hashes_verified": 19', '"record_hashes_verified": 19.5'
            ),
        )
        self.assert_refused()
        put(
            self.source,
            "synthetic-db/installed.json",
            original_installed.replace(
                '"record_hashes_verified": 19', '"record_hashes_verified": NaN'
            ),
        )
        self.assert_refused()
        put(self.source, "synthetic-db/installed.json", original_installed)
        for run in "ab":
            path = self.source / f"build-{run}/authenticated-packages.json"
            put(
                self.source,
                f"build-{run}/authenticated-packages.json",
                path.read_text().replace(
                    '"signed_indexes": 1', '"signed_indexes": true'
                ),
            )
        self.assert_refused()

    def test_reported_index_digest_must_match_signed_release(self):
        for run in "ab":
            path = self.source / f"build-{run}/authenticated-packages.json"
            report = json.loads(path.read_text())
            report["indexes"][0]["sha256"] = "0" * 64
            put(
                self.source,
                f"build-{run}/authenticated-packages.json",
                json.dumps(report),
            )
        self.assert_refused()

    def test_manifest_duplicate_traversal_and_arch(self):
        path = self.source / "frozen/frozen.sha256"
        original = path.read_text()
        first = original.splitlines()[0]
        for changed in (
            original + first + "\n",
            original + "a" * 64 + "  ../secret\n",
            original.replace("arm64", "amd64"),
        ):
            put(self.source, "frozen/frozen.sha256", changed)
            self.assert_refused()

    def test_symlink_file_and_directory(self):
        wheel = self.source / "build-a" / self.wheel
        wheel.unlink()
        wheel.symlink_to(self.source / "build-b" / self.wheel)
        self.assert_refused()
        wheel.unlink()
        shutil.rmtree(self.source / "build-a")
        (self.source / "build-a").symlink_to(
            self.source / "build-b", target_is_directory=True
        )
        self.assert_refused()

    def test_wrong_arch_size_and_count(self):
        self.assert_refused(arch="amd64")
        original = e.MAX_TOTAL
        try:
            e.MAX_TOTAL = 100
            self.assert_refused()
        finally:
            e.MAX_TOTAL = original
        original = e.MAX_FILE
        try:
            e.MAX_FILE = 100
            self.assert_refused()
        finally:
            e.MAX_FILE = original
        original = e.MAX_COUNT
        try:
            e.MAX_COUNT = 10
            self.assert_refused()
        finally:
            e.MAX_COUNT = original


if __name__ == "__main__":
    unittest.main()
