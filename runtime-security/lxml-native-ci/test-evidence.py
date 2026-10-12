"""Local refusal checks for the public evidence boundary."""

from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location(
    "lxml_evidence", Path(__file__).with_name("evidence.py")
)
assert SPEC and SPEC.loader
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


class EvidenceBoundaryTests(unittest.TestCase):
    def test_confined_paths(self) -> None:
        self.assertTrue(evidence.confined("apt-lists/index_Packages.lz4"))
        for value in (
            "../secrets",
            "foo/../../secrets",
            "/etc/passwd",
            "foo//bar",
            "foo\\bar",
            "foo/./bar",
        ):
            self.assertFalse(evidence.confined(value))

    def test_duplicate_json_and_constant_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / "receipt.json"
            for payload in ('{"schema":1,"schema":1}', '{"value":NaN}'):
                path.write_text(payload)
                with self.assertRaises(ValueError):
                    evidence.strict_json(path)

    def test_failed_and_boolean_status_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            log = root / "original.log"
            log.write_text("synthetic completed command\n")
            row = {
                "exit_code": 0,
                "log_path": str(log),
                "log_sha256": evidence.sha(log),
            }
            status = {
                "schema": 1,
                "commands": {name: row.copy() for name in evidence.STATUS_NAMES},
            }
            path = root / "status.json"
            path.write_text(json.dumps(status))
            sanitized = evidence.status_receipt(path)
            self.assertEqual(set(sanitized), evidence.STATUS_NAMES)
            self.assertNotIn(str(root), json.dumps(sanitized))
            for wrong in (1, False):
                status["commands"]["accept-native"]["exit_code"] = wrong
                path.write_text(json.dumps(status))
                with self.assertRaises(ValueError):
                    evidence.status_receipt(path)

    def test_symlink_and_hardlink_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            original = root / "original"
            original.write_text("synthetic")
            linked = root / "linked"
            linked.hardlink_to(original)
            with self.assertRaises(ValueError):
                evidence.regular(original)
            linked.unlink()
            symbolic = root / "symbolic"
            symbolic.symlink_to(original)
            with self.assertRaises(ValueError):
                evidence.regular(symbolic)

    def test_oversize_json_and_unsafe_copy_leave_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            metadata = root / "metadata.json"
            metadata.write_bytes(b" " * (evidence.MAX_JSON + 1))
            with self.assertRaises(ValueError):
                evidence.strict_json(metadata)
            source = root / "source"
            source.write_text("synthetic public input")
            unsafe = root / "unsafe"
            unsafe.symlink_to(source)
            destination = root / "published" / "source"
            with self.assertRaises(ValueError):
                evidence.copy_one(unsafe, destination)
            self.assertFalse(destination.exists())

    def test_apt_selection_exact_rows_and_duplicate_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / "apt-simulation.txt"
            text = "Reading package lists...\nSolving dependencies...\nInst libxml2-16 (2.15.2+dfsg-0.1ubuntu0.3 Ubuntu:26.04/resolute [arm64])\n"
            path.write_text(text)
            self.assertEqual(
                evidence.apt_selection(path),
                [["libxml2-16", "2.15.2+dfsg-0.1ubuntu0.3", "arm64"]],
            )
            path.write_text(text + text.splitlines()[-1] + "\n")
            with self.assertRaises(ValueError):
                evidence.apt_selection(path)

    def test_closed_fields_and_typed_counts(self) -> None:
        self.assertEqual(
            evidence.fields({"files": 173}, {"files"}, "inventory"), {"files": 173}
        )
        for value in (None, {}, {"files": 173, "private_note": "synthetic"}):
            with self.assertRaises(ValueError):
                evidence.fields(value, {"files"}, "inventory")
        for value in (None, False, True, 0, 181):
            with self.assertRaises(ValueError):
                evidence.number(value, 1, 180, "selected")

    def test_lexical_aliases_and_ancestor_links_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            real = root / "real"
            real.mkdir()
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            for raw in (
                str(alias / "new"),
                str(real) + "/../real/new",
                str(real) + "//new",
                str(real) + "/new/",
            ):
                with self.assertRaises(ValueError):
                    evidence.cli_path(raw, may_be_new=True)
            self.assertEqual(
                evidence.cli_path(str(real / "new"), may_be_new=True), real / "new"
            )

    def test_wheel_filename_tag_and_distribution_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            name = evidence.native_wheel_name("arm64")
            wheel = root / name
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "lxml-6.1.3.dist-info/WHEEL",
                    "Wheel-Version: 1.0\nTag: cp314-cp314-linux_aarch64\n",
                )
                archive.writestr(
                    "lxml-6.1.3.dist-info/METADATA", "Name: lxml\nVersion: 6.1.3\n"
                )
            evidence.wheel_metadata(wheel, "arm64")
            renamed = root / "lxml-6.1.3-PRIVATE_MARKER-aarch64.whl"
            wheel.rename(renamed)
            with self.assertRaises(ValueError):
                evidence.wheel_metadata(renamed, "arm64")
            with self.assertRaises(ValueError):
                evidence.native_wheel_name("other")

    def test_loaded_library_path_is_canonical(self) -> None:
        evidence.canonical_library_path(
            "/usr/lib/aarch64-linux-gnu/libxml2.so.16.0.0", "libxml2.so.16", "arm64"
        )
        for value in (
            "/usr/lib/PRIVATE_MARKER/libxml2.so.16.0.0",
            "/usr/lib/aarch64-linux-gnu/PRIVATE_MARKER/libxml2.so.16.0.0",
            "/usr/lib/aarch64-linux-gnu/libxml2.so.16.PRIVATE_MARKER",
            "/usr/lib/aarch64-linux-gnu//libxml2.so.16.0.0",
            "/usr/lib/aarch64-linux-gnu/./libxml2.so.16.0.0",
            "/usr/lib/x86_64-linux-gnu/libxml2.so.16.0.0",
        ):
            with self.assertRaises(ValueError):
                evidence.canonical_library_path(value, "libxml2.so.16", "arm64")

    def test_installed_record_additions_are_exact(self) -> None:
        wheel_name = evidence.native_wheel_name("arm64")
        wheel_hash = "a" * 64
        info = "lxml-6.1.3.dist-info/"
        direct = {
            "archive_info": {
                "hash": "sha256=" + wheel_hash,
                "hashes": {"sha256": wheel_hash},
            },
            "url": "file:///wheels/" + wheel_name,
        }
        content = {
            "INSTALLER": b"pip\n",
            "REQUESTED": b"",
            "direct_url.json": json.dumps(direct, sort_keys=True).encode(),
        }
        additions = [
            {
                "path": info + name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
            for name, payload in sorted(content.items())
        ]
        installed = {"record_hashes_verified": 4, "record_additions": additions}
        wheel_rows = [["lxml/example.so", "sha256=digest", "1"]]
        evidence.installed_additions(installed, wheel_name, wheel_hash, wheel_rows)
        installed["record_hashes_verified"] = 5
        with self.assertRaises(ValueError):
            evidence.installed_additions(installed, wheel_name, wheel_hash, wheel_rows)
        installed["record_hashes_verified"] = 4
        additions[0]["sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            evidence.installed_additions(installed, wheel_name, wheel_hash, wheel_rows)
        additions[0]["sha256"] = hashlib.sha256(content["INSTALLER"]).hexdigest()
        additions[0]["path"] = info + "PRIVATE_MARKER"
        with self.assertRaises(ValueError):
            evidence.installed_additions(installed, wheel_name, wheel_hash, wheel_rows)


if __name__ == "__main__":
    unittest.main()
