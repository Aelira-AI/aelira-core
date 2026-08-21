"""Task16A DB-first remediation artifact service contracts."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid
import zipfile

import pytest
from sqlalchemy.exc import IntegrityError

from src.db.models import RemediationArtifact, ScanType
from src.services import remediation_artifact_service as module

DEPARTMENT_ID = "11111111-1111-4111-8111-111111111111"
SCAN_ID = "22222222-2222-4222-8222-222222222222"
CLOUD_FILE_ID = "33333333-3333-4333-8333-333333333333"
JOB_ID = "44444444-4444-4444-8444-444444444444"
USER_ID = "55555555-5555-4555-8555-555555555555"


def _service(tmp_path, **overrides):
    values = dict(
        root=tmp_path / "artifacts",
        max_bytes=2 * 1024 * 1024,
        retention_days=30,
        written_retention_days=7,
        staging_grace_seconds=3600,
    )
    values.update(overrides)
    return module.RemediationArtifactService(**values)


def _source(tmp_path):
    root = tmp_path / "trusted"
    root.mkdir(exist_ok=True)
    path = root / "result.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    return root, path


def _pdf_source(tmp_path):
    root = tmp_path / "trusted-pdf"
    root.mkdir(exist_ok=True)
    path = root / "result.pdf"
    path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    return root, path


def _parents(**overrides):
    values = {
        "department": SimpleNamespace(id=DEPARTMENT_ID),
        "scan": SimpleNamespace(
            id=SCAN_ID, department_id=DEPARTMENT_ID, scan_type=ScanType.WORD
        ),
        "cloud": SimpleNamespace(
            id=CLOUD_FILE_ID,
            department_id=DEPARTMENT_ID,
            last_scan_id=SCAN_ID,
            provider="canvas",
            current_remediation_artifact_id=None,
            has_remediated_version=False,
        ),
        "job": SimpleNamespace(
            id=JOB_ID,
            department_id=DEPARTMENT_ID,
            cloud_file_id=CLOUD_FILE_ID,
            job_type="remediate",
            provider="canvas",
            execution_context={"scan_id": SCAN_ID},
        ),
    }
    values.update(overrides)
    return values


def _query(row):
    query = MagicMock()
    locked = query.filter.return_value.with_for_update.return_value
    locked.populate_existing.return_value.one_or_none.return_value = row
    return query


def _prepare(service, source, root, **overrides):
    values = dict(
        department_id=DEPARTMENT_ID,
        scan_id=SCAN_ID,
        cloud_file_id=CLOUD_FILE_ID,
        remediation_job_id=JOB_ID,
        created_by_id=USER_ID,
        provider="canvas",
        scan_type=ScanType.WORD,
        filename="fixed.docx",
        provider_result={"provider_version": "v2"},
    )
    values.update(overrides)
    with service._open_source_fd(source, root) as fd:
        return service._prepare(fd, **values)


def _artifact(prepared, **overrides):
    values = prepared.as_model_kwargs()
    values.update(overrides)
    return RemediationArtifact(**values)


def _publish(service, artifact, **kwargs):
    parents = _parents()
    service._lock_existing_artifact = MagicMock(
        return_value=(
            parents["department"],
            parents["scan"],
            parents["cloud"],
            parents["job"],
            artifact,
        )
    )
    db = MagicMock()
    service._test_db = db
    service.publish_claimed(
        db,
        artifact,
        publication_token=artifact.publication_token,
        **kwargs,
    )


def _claim_db(parents, existing=None):
    db = MagicMock()
    db.query.side_effect = [
        _query(parents["department"]),
        _query(parents["scan"]),
        _query(parents["cloud"]),
        _query(parents["job"]),
        _query(existing),
    ]
    return db


def test_source_metadata_is_computed_before_db_claim_and_claim_is_staging(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)

    assert prepared.lifecycle_status == "staging"
    assert prepared.size_bytes == source.stat().st_size
    assert prepared.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert prepared.mime_type.endswith("wordprocessingml.document")
    assert prepared.scan_type == "WORD"
    assert prepared.cleanup_claimed_at is None


def test_locked_scan_type_mismatch_rejects_before_row_commit_or_publication(
    tmp_path, monkeypatch
):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    parents = _parents(
        scan=SimpleNamespace(
            id=SCAN_ID, department_id=DEPARTMENT_ID, scan_type=ScanType.PDF
        )
    )
    db = _claim_db(parents)
    publish = MagicMock()
    monkeypatch.setattr(service, "_publish_fd", publish)

    with pytest.raises(
        module.ArtifactAuthorizationError,
        match="prepared artifact scan type does not match locked scan authority",
    ):
        service.claim_and_publish(
            db,
            source_path=source,
            trusted_temp_root=root,
            department_id=DEPARTMENT_ID,
            scan_id=SCAN_ID,
            cloud_file_id=CLOUD_FILE_ID,
            remediation_job_id=JOB_ID,
            created_by_id=USER_ID,
            provider="canvas",
            scan_type=ScanType.WORD,
            filename="fixed.docx",
        )

    db.add.assert_not_called()
    db.commit.assert_not_called()
    publish.assert_not_called()
    assert not service.root.exists()


@pytest.mark.parametrize(
    "asserted_type", [ScanType.WORD, "WORD", "word", "DOCX", "docx"]
)
@pytest.mark.parametrize("locked_type", [ScanType.WORD, "WORD", "word", "DOCX", "docx"])
def test_documented_word_aliases_share_one_locked_authority(
    tmp_path, asserted_type, locked_type
):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root, scan_type=asserted_type)
    parents = _parents(
        scan=SimpleNamespace(
            id=SCAN_ID, department_id=DEPARTMENT_ID, scan_type=locked_type
        )
    )
    db = _claim_db(parents)

    claim = service.claim(db, prepared)

    assert prepared.scan_type == "WORD"
    assert claim.owned is True
    db.add.assert_called_once()
    db.commit.assert_called_once()


@pytest.mark.parametrize("locked_type", [None, True, False, "", "unknown", object()])
def test_unknown_or_malformed_locked_scan_type_rejects_before_side_effects(
    tmp_path, locked_type
):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    parents = _parents(
        scan=SimpleNamespace(
            id=SCAN_ID, department_id=DEPARTMENT_ID, scan_type=locked_type
        )
    )
    db = _claim_db(parents)

    with pytest.raises(
        module.ArtifactAuthorizationError, match="locked scan type is invalid"
    ):
        service.claim(db, prepared)

    db.add.assert_not_called()
    db.commit.assert_not_called()
    assert not service.root.exists()


def test_locked_scan_type_owns_prepared_filename_compatibility(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = replace(_prepare(service, source, root), filename="forged.bin")
    db = _claim_db(_parents())

    with pytest.raises(
        module.ArtifactAuthorizationError,
        match="prepared artifact is incompatible with locked scan authority",
    ):
        service.claim(db, prepared)

    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("asserted_type", [ScanType.PDF, "PDF", "pdf"])
def test_pdf_enum_and_case_aliases_normalize_during_preparation(
    tmp_path, asserted_type
):
    service = _service(tmp_path)
    root, source = _pdf_source(tmp_path)

    prepared = _prepare(
        service,
        source,
        root,
        scan_type=asserted_type,
        filename="fixed.pdf",
    )

    assert prepared.scan_type == "PDF"
    assert prepared.mime_type == "application/pdf"


@pytest.mark.parametrize(
    "asserted_type", [None, True, False, "", " unknown ", object()]
)
def test_unknown_missing_or_boolean_asserted_scan_type_is_rejected(
    tmp_path, asserted_type
):
    service = _service(tmp_path)
    root, source = _source(tmp_path)

    with pytest.raises(module.ArtifactValidationError, match="scan type is invalid"):
        _prepare(service, source, root, scan_type=asserted_type)


def test_claim_commits_staging_row_before_caller_can_publish(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    db = _claim_db(_parents())

    claim = service.claim(db, prepared)

    assert claim.owned is True
    assert claim.status == "staging"
    db.add.assert_called_once_with(claim.artifact)
    db.flush.assert_called_once()
    db.commit.assert_called_once()
    assert not (service.root / prepared.storage_key).exists()


@pytest.mark.parametrize(
    ("parent", "replacement"),
    [
        ("scan", SimpleNamespace(id=SCAN_ID, department_id=str(uuid.uuid4()))),
        (
            "cloud",
            SimpleNamespace(
                id=CLOUD_FILE_ID,
                department_id=DEPARTMENT_ID,
                last_scan_id=str(uuid.uuid4()),
                provider="canvas",
            ),
        ),
        (
            "job",
            SimpleNamespace(
                id=JOB_ID,
                department_id=DEPARTMENT_ID,
                cloud_file_id=str(uuid.uuid4()),
                job_type="remediate",
                provider="canvas",
                execution_context={"scan_id": SCAN_ID},
            ),
        ),
    ],
)
def test_mixed_authority_graph_is_rejected_before_row_or_bytes(
    tmp_path, parent, replacement
):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    parents = _parents(**{parent: replacement})
    db = _claim_db(parents)

    with pytest.raises(module.ArtifactAuthorizationError):
        service.claim(db, prepared)

    db.add.assert_not_called()
    db.commit.assert_not_called()
    assert not service.root.exists()


def test_existing_staging_claim_is_idempotent_in_progress(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    existing = _artifact(prepared)
    db = _claim_db(_parents(), existing)

    claim = service.claim(db, prepared)

    assert claim.artifact is existing
    assert claim.owned is False
    assert claim.status == "in_progress"
    db.add.assert_not_called()


def test_existing_available_claim_is_reusable(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    existing = _artifact(prepared, lifecycle_status="available")
    db = _claim_db(_parents(), existing)

    claim = service.claim(db, prepared)

    assert claim.status == "available"
    assert claim.owned is False


def test_unique_race_loser_rolls_back_relocks_and_reloads_existing(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    winner = _artifact(prepared)
    parents = _parents()
    db = MagicMock()
    db.query.side_effect = [
        *[_query(parents[name]) for name in ("department", "scan", "cloud", "job")],
        _query(None),
        *[_query(parents[name]) for name in ("department", "scan", "cloud", "job")],
        _query(winner),
    ]
    db.flush.side_effect = IntegrityError("insert", {}, Exception("unique"))

    claim = service.claim(db, prepared)

    db.rollback.assert_called_once()
    assert claim.artifact is winner
    assert claim.status == "in_progress"


def test_crash_after_claim_before_publish_leaves_db_known_staging_row(
    tmp_path, monkeypatch
):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    db = _claim_db(_parents())
    claim = service.claim(db, prepared)
    monkeypatch.setattr(service, "_publish_fd", MagicMock(side_effect=OSError("crash")))

    with service._open_source_fd(source, root) as fd:
        with pytest.raises(OSError, match="crash"):
            service._publish_fd(db, claim.artifact, claim.publication_token, fd)

    assert claim.artifact.lifecycle_status == "staging"
    db.commit.assert_called_once()
    assert not (service.root / prepared.storage_key).exists()


def test_publish_uses_claimed_key_and_never_overwrites(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    artifact = _artifact(prepared)

    _publish(service, artifact, source_path=source, trusted_temp_root=root)
    final = service.root / prepared.storage_key
    assert final.read_bytes() == source.read_bytes()
    assert not list(final.parent.glob("*.partial"))

    with pytest.raises(module.ArtifactIntegrityError, match="already exists"):
        _publish(service, artifact, source_path=source, trusted_temp_root=root)
    assert final.read_bytes() == source.read_bytes()


def test_partial_publish_failure_leaves_no_final_but_staging_claim(
    tmp_path, monkeypatch
):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    artifact = _artifact(prepared)
    real_write = module.os.write
    calls = 0

    def interrupted(fd, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("interrupted")
        return real_write(fd, data[:1])

    monkeypatch.setattr(module.os, "write", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        _publish(service, artifact, source_path=source, trusted_temp_root=root)

    assert artifact.lifecycle_status == "staging"
    assert not (service.root / prepared.storage_key).exists()
    assert not list(service.root.rglob("*.partial"))


def test_publication_partial_name_is_exactly_derived_from_storage_key(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    artifact = _artifact(prepared)
    partial = service.root / f"{prepared.storage_key}.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"prior crashed publisher")

    with pytest.raises(module.ArtifactIntegrityError, match="already exists"):
        _publish(service, artifact, source_path=source, trusted_temp_root=root)

    assert partial.read_bytes() == b"prior crashed publisher"
    assert not (service.root / prepared.storage_key).exists()


def test_delete_rejects_deterministic_partial_symlink_without_escaping(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    artifact = _artifact(prepared)
    partial = service.root / f"{prepared.storage_key}.partial"
    partial.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_bytes(b"must survive")
    partial.symlink_to(outside)

    with pytest.raises(module.ArtifactIntegrityError, match="nonregular"):
        service.delete_known(artifact)

    assert outside.read_bytes() == b"must survive"
    assert partial.is_symlink()


def test_source_outside_trusted_root_and_symlink_are_rejected(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    outside = tmp_path / "outside.docx"
    outside.write_bytes(source.read_bytes())
    link = root / "link.docx"
    link.symlink_to(outside)

    for bad in (outside, link):
        with pytest.raises(module.ArtifactValidationError):
            _prepare(service, bad, root)


def test_bytes_first_apis_are_disabled(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(module.ArtifactValidationError, match="bytes-first"):
        service.persist()
    with pytest.raises(module.ArtifactValidationError, match="detached"):
        service.create_row(MagicMock(), MagicMock())


def test_resolve_returns_metadata_and_open_verified_binds_descriptor(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    artifact = _artifact(prepared)
    _publish(service, artifact, source_path=source, trusted_temp_root=root)
    artifact.lifecycle_status = "available"

    assert (
        service.resolve_record(
            service._test_db,
            artifact,
            department_id=DEPARTMENT_ID,
            scan_id=SCAN_ID,
            cloud_file_id=CLOUD_FILE_ID,
        )
        is artifact
    )
    with service.open_verified(
        service._test_db,
        artifact,
        department_id=DEPARTMENT_ID,
        scan_id=SCAN_ID,
        cloud_file_id=CLOUD_FILE_ID,
    ) as stream:
        assert stream.read() == source.read_bytes()
    assert stream.closed


def test_resolve_rejects_tamper_expiry_and_cleanup_claim(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    artifact = _artifact(prepared)
    _publish(service, artifact, source_path=source, trusted_temp_root=root)
    artifact.lifecycle_status = "available"
    final = service.root / artifact.storage_key
    scope = dict(
        department_id=DEPARTMENT_ID,
        scan_id=SCAN_ID,
        cloud_file_id=CLOUD_FILE_ID,
    )

    final.write_bytes(b"tamper")
    with pytest.raises(module.ArtifactIntegrityError):
        service.resolve_record(service._test_db, artifact, **scope)
    final.write_bytes(source.read_bytes())
    artifact.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(module.ArtifactExpiredError):
        service.resolve_record(service._test_db, artifact, **scope)
    artifact.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    artifact.cleanup_claimed_at = datetime.now(timezone.utc)
    with pytest.raises(module.ArtifactAuthorizationError, match="cleanup"):
        service.resolve_record(service._test_db, artifact, **scope)


def test_delete_is_confined_fd_relative_and_idempotent(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    prepared = _prepare(service, source, root)
    artifact = _artifact(prepared)
    _publish(service, artifact, source_path=source, trusted_temp_root=root)

    assert service.delete_known(artifact) is True
    assert service.delete_known(artifact) is False
    artifact.storage_key = "../outside.docx"
    with pytest.raises(module.ArtifactIntegrityError):
        service.delete_known(artifact)


def test_delete_treats_absent_storage_tree_as_missing(tmp_path):
    service = _service(tmp_path)
    root, source = _source(tmp_path)
    artifact = _artifact(_prepare(service, source, root))

    assert not service.root.exists()
    assert service.delete_known(artifact) is False
