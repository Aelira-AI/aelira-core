"""Contract tests for portable, versioned review-evidence packages."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from src.education.reports.evidence_package import (
    EvidenceFile,
    EvidencePackageError,
    SCHEMA_VERSION,
    build_evidence_package,
    verify_evidence_package,
)

pytestmark = pytest.mark.unit

_GENERATED_AT = "2026-09-05T00:00:00+00:00"


def _report(*, artifact: dict | None = None) -> dict:
    return {
        "report_generated_at": _GENERATED_AT,
        "scan": {
            "id": "scan-001",
            "file_name": "source.pdf",
            "scan_type": "PDF",
            "status": "COMPLETED",
            "created_at": "2026-09-04T00:00:00+00:00",
            "completed_at": "2026-09-04T00:01:00+00:00",
        },
        "source": {
            "availability": "available",
            "document_id": "document-001",
            "document_source": "standalone",
            "filename": "source.pdf",
            "media_type": "application/pdf",
            "size_bytes": 6,
            "sha256": hashlib.sha256(b"source").hexdigest(),
            "created_at": "2026-09-04T00:00:00+00:00",
            "completed_at": "2026-09-04T00:01:00+00:00",
        },
        "artifact": artifact or {"availability": "unavailable"},
        "summary": {"total_issues": 1, "is_conformance_determination": False},
        "machine_observations": [{"id": "fix-001", "severity": "serious"}],
        "reviewer_decisions": [{"fix_id": "fix-001", "review_status": "approved"}],
        "audit_trail": [{"id": "audit-001", "action": "fix_approve"}],
        "validator_observations": [{"checkpoint_id": "01-001", "status": "pass"}],
        "limitations": "Evidence only; no conformance determination.",
    }


def _artifact() -> dict:
    return {
        "availability": "available",
        "id": "artifact-001",
        "filename": "remediated.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 6,
        "sha256": hashlib.sha256(b"output").hexdigest(),
        "review_status": "approved",
        "approval_review_digest": "a" * 64,
        "created_at": "2026-09-04T00:02:00+00:00",
        "updated_at": "2026-09-04T00:03:00+00:00",
        "expires_at": "2026-10-04T00:00:00+00:00",
        "written_back_at": "not recorded",
    }


def _build(**kwargs) -> bytes:
    return build_evidence_package(
        _report(artifact=kwargs.pop("artifact", None)),
        generated_at=_GENERATED_AT,
        tool_version="0.9.7",
        **kwargs,
    )


def _members(package: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _rewrite(package: bytes, *, manifest_update=None, file_update=None) -> bytes:
    members = _members(package)
    manifest = json.loads(members["manifest.json"])
    if manifest_update:
        manifest_update(manifest)
    members["manifest.json"] = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode()
    if file_update:
        file_update(members, manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return output.getvalue()


def test_manifest_is_versioned_and_preserves_recorded_evidence():
    manifest = verify_evidence_package(_build(artifact=_artifact()))

    assert manifest["schema_version"] == SCHEMA_VERSION == "1.0.0"
    assert manifest["package"]["tool"] == {"name": "aelira-core", "version": "0.9.7"}
    assert manifest["source"]["identity"] == "document-001"
    assert manifest["output"]["identity"] == "artifact-001"
    assert manifest["evidence"]["machine_observations"][0]["id"] == "fix-001"
    assert manifest["evidence"]["reviewer_decisions"][0]["review_status"] == "approved"
    assert manifest["evidence"]["validator_results"][0]["status"] == "pass"


def test_partial_evidence_is_explicit_without_invented_values():
    report = _report()
    report["source"] = {
        "availability": "not_recorded",
        "document_id": "unavailable",
        "document_source": "not recorded",
        "sha256": "unavailable",
    }
    package = build_evidence_package(
        report, generated_at=_GENERATED_AT, tool_version="0.9.7"
    )
    manifest = verify_evidence_package(package)

    assert manifest["source"]["availability"] == "not_recorded"
    assert manifest["source"]["identity"] is None
    assert manifest["source"]["sha256"] is None
    assert manifest["source"]["included"] is False
    assert manifest["output"]["availability"] == "unavailable"
    assert manifest["output"]["identity"] is None


def test_document_bytes_are_excluded_by_default_and_explicit_when_supplied():
    assert set(_members(_build(artifact=_artifact()))) == {"manifest.json"}

    package = _build(
        artifact=_artifact(),
        source_file=EvidenceFile("source.pdf", "application/pdf", b"source"),
        output_file=EvidenceFile("remediated.pdf", "application/pdf", b"output"),
    )
    manifest = verify_evidence_package(package)
    members = _members(package)

    assert manifest["source"]["included"] is True
    assert manifest["output"]["included"] is True
    assert set(members) == {
        "manifest.json",
        manifest["source"]["path"],
        manifest["output"]["path"],
    }


def test_fixed_inputs_produce_byte_identical_packages_and_bounded_names():
    source = EvidenceFile("../" + "x" * 200 + ".pdf", "application/pdf", b"source")
    first = _build(source_file=source)
    second = _build(source_file=source)
    manifest = verify_evidence_package(first)

    assert first == second
    assert manifest["source"]["path"].startswith("files/source-")
    assert ".." not in manifest["source"]["path"]
    assert len(manifest["source"]["path"]) <= 128


def test_verifier_accepts_compatible_minor_and_rejects_unsupported_major():
    package = _build()
    compatible = _rewrite(
        package,
        manifest_update=lambda manifest: manifest.update(schema_version="1.8.4"),
    )
    assert verify_evidence_package(compatible)["schema_version"] == "1.8.4"

    unsupported = _rewrite(
        package,
        manifest_update=lambda manifest: manifest.update(schema_version="2.0.0"),
    )
    with pytest.raises(EvidencePackageError, match="unsupported schema major"):
        verify_evidence_package(unsupported)


@pytest.mark.parametrize("package", [b"not-a-zip", b""])
def test_verifier_rejects_malformed_packages(package):
    with pytest.raises(EvidencePackageError, match="malformed evidence package"):
        verify_evidence_package(package)


def test_verifier_rejects_malformed_manifest_json():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", b"{")
    with pytest.raises(EvidencePackageError, match="malformed manifest"):
        verify_evidence_package(output.getvalue())

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "manifest.json",
            b'{"schema_version":"1.0.0","schema_version":"1.0.1"}',
        )
    with pytest.raises(EvidencePackageError, match="malformed manifest"):
        verify_evidence_package(output.getvalue())


def test_verifier_rejects_incomplete_or_invalid_known_manifest_fields():
    incomplete = _rewrite(
        _build(), manifest_update=lambda manifest: manifest.pop("package")
    )
    with pytest.raises(EvidencePackageError, match="malformed manifest"):
        verify_evidence_package(incomplete)

    invalid_digest = _rewrite(
        _build(),
        manifest_update=lambda manifest: manifest["source"].update(sha256="invalid"),
    )
    with pytest.raises(EvidencePackageError, match="malformed manifest"):
        verify_evidence_package(invalid_digest)

    invalid_version_type = _rewrite(
        _build(), manifest_update=lambda manifest: manifest.update(schema_version=1)
    )
    with pytest.raises(EvidencePackageError, match="malformed manifest"):
        verify_evidence_package(invalid_version_type)


def test_verifier_rejects_missing_included_file():
    package = _build(
        source_file=EvidenceFile("source.pdf", "application/pdf", b"source")
    )
    members = _members(package)
    members.pop(json.loads(members["manifest.json"])["source"]["path"])
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    with pytest.raises(EvidencePackageError, match="missing included file"):
        verify_evidence_package(output.getvalue())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [("size_bytes", 99, "size mismatch"), ("sha256", "0" * 64, "checksum mismatch")],
)
def test_verifier_rejects_included_file_metadata_tamper(field, value, message):
    package = _build(
        source_file=EvidenceFile("source.pdf", "application/pdf", b"source")
    )
    tampered = _rewrite(
        package,
        manifest_update=lambda manifest: manifest["source"].update({field: value}),
    )
    with pytest.raises(EvidencePackageError, match=message):
        verify_evidence_package(tampered)


def test_verifier_rejects_unsafe_and_duplicate_members():
    for unsafe_name in ("../source.pdf", "/source.pdf", "files/../source.pdf"):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("manifest.json", b"{}")
            archive.writestr(unsafe_name, b"source")
        with pytest.raises(EvidencePackageError, match="unsafe archive member"):
            verify_evidence_package(output.getvalue())

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", b"{}")
        archive.writestr("manifest.json", b"{}")
    with pytest.raises(EvidencePackageError, match="duplicate archive member"):
        verify_evidence_package(output.getvalue())


def test_package_redacts_historical_server_paths_and_secret_fields():
    report = _report()
    report["audit_trail"][0]["details"] = {
        "storage_path": "/app/uploads/customer/source.pdf",
        "api_token": "customer-secret-value",
        "error": "failed at /opt/aelira/private.pdf",
        "safe": "review retained",
    }

    package = build_evidence_package(
        report, generated_at=_GENERATED_AT, tool_version="0.9.7"
    )
    manifest_bytes = _members(package)["manifest.json"]
    manifest = verify_evidence_package(package)
    details = manifest["evidence"]["audit_trail"][0]["details"]

    assert b"/app/uploads" not in manifest_bytes
    assert b"/opt/aelira" not in manifest_bytes
    assert b"customer-secret-value" not in manifest_bytes
    assert details == {
        "api_token": "redacted",
        "error": "redacted",
        "safe": "review retained",
        "storage_path": "redacted",
    }


def test_builder_rejects_included_bytes_that_disagree_with_recorded_evidence():
    with pytest.raises(EvidencePackageError, match="source checksum mismatch"):
        _build(source_file=EvidenceFile("source.pdf", "application/pdf", b"tamper"))


def test_command_line_verifier_reports_validity_without_extracting(tmp_path, capsys):
    from src.education.reports.evidence_package import main

    package_path = tmp_path / "evidence.zip"
    package_path.write_bytes(_build())

    assert main([str(package_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "scan_id": "scan-001",
        "schema_version": "1.0.0",
        "valid": True,
    }

    package_path.write_bytes(b"tampered")
    assert main([str(package_path)]) == 1
    assert "invalid evidence package" in capsys.readouterr().err


def test_public_contract_documents_version_compatibility_and_claim_boundaries():
    documentation = (
        Path(__file__).parents[1] / "docs" / "EVIDENCE_PACKAGES.md"
    ).read_text()

    for required in (
        "Manifest version 1.0.0",
        "A later `1.x.y` manifest is compatible",
        "Document bytes are excluded by default",
        "aelira-evidence-verify evidence-package.zip",
        "not a digital signature",
        "accessibility conformance certificate",
    ):
        assert required in documentation
