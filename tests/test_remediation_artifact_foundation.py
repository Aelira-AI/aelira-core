"""Task16A managed remediation artifact model, migration, and settings contracts."""

import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import BigInteger

from src.config.settings import Settings
from src.db import models

ROOT = Path(__file__).parents[1]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "2026_08_21_remediation_artifacts.py"

OWNERSHIP_FOREIGN_KEYS = {
    "department_id": ("departments.id", "fk_remediation_artifacts_department"),
    "scan_id": ("scans.id", "fk_remediation_artifacts_scan"),
    "cloud_file_id": ("cloud_files.id", "fk_remediation_artifacts_cloud_file"),
    "remediation_job_id": (
        "cloud_job_queue.id",
        "fk_remediation_artifacts_remediation_job",
    ),
}


def _settings_kwargs(**overrides):
    kwargs = {
        "database_url": "postgresql://user:pass@localhost:5432/aelira",
        "jwt_secret": "a-real-secret-that-is-not-a-placeholder-12345",
        "smtp_host": "smtp.example.com",
    }
    kwargs.update(overrides)
    return kwargs


def _load_migration():
    assert MIGRATION_PATH.exists(), "Task16A migration is absent"
    spec = importlib.util.spec_from_file_location(
        "remediation_artifact_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_artifact_model_has_authority_fields_constraints_indexes_and_relationships():
    assert hasattr(models, "RemediationArtifact"), "artifact ORM authority is absent"
    artifact = models.RemediationArtifact
    table = artifact.__table__
    columns = table.c

    assert set(columns.keys()) == {
        "id",
        "department_id",
        "scan_id",
        "cloud_file_id",
        "remediation_job_id",
        "created_by_id",
        "provider",
        "scan_type",
        "publication_token",
        "publication_heartbeat_at",
        "published_at",
        "storage_backend",
        "storage_key",
        "filename",
        "mime_type",
        "size_bytes",
        "sha256",
        "lifecycle_status",
        "review_status",
        "approval_checksum",
        "approved_by_id",
        "approved_by_ref",
        "approved_at",
        "rejected_by_id",
        "rejected_by_ref",
        "rejected_at",
        "written_back_at",
        "cleanup_claimed_at",
        "deleted_at",
        "provider_result",
        "expires_at",
        "created_at",
        "updated_at",
    }
    assert isinstance(columns.size_bytes.type, BigInteger)
    assert columns.remediation_job_id.unique is True
    assert columns.storage_key.unique is True
    assert columns.storage_backend.default.arg == "local"
    assert str(columns.storage_backend.server_default.arg).strip("'") == "local"
    assert columns.lifecycle_status.default.arg == "staging"
    assert str(columns.lifecycle_status.server_default.arg).strip("'") == "staging"
    assert columns.review_status.default.arg == "pending"
    assert columns.approved_by_ref.type.length == 255
    assert columns.rejected_by_ref.type.length == 255
    assert columns.cleanup_claimed_at.index is True

    foreign_keys = {
        column.name: next(iter(column.foreign_keys)).target_fullname
        for column in columns
        if column.foreign_keys
    }
    assert foreign_keys == {
        "department_id": "departments.id",
        "scan_id": "scans.id",
        "cloud_file_id": "cloud_files.id",
        "remediation_job_id": "cloud_job_queue.id",
        "created_by_id": "users.id",
        "approved_by_id": "users.id",
        "rejected_by_id": "users.id",
    }

    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if constraint.name and constraint.name.startswith("ck_remediation_artifacts_")
    }
    assert {
        "ck_remediation_artifacts_provider",
        "ck_remediation_artifacts_scan_type",
        "ck_remediation_artifacts_publication_lease",
        "ck_remediation_artifacts_storage_backend",
        "ck_remediation_artifacts_storage_key",
        "ck_remediation_artifacts_size",
        "ck_remediation_artifacts_sha256",
        "ck_remediation_artifacts_lifecycle",
        "ck_remediation_artifacts_review",
        "ck_remediation_artifacts_review_metadata",
        "ck_remediation_artifacts_written",
        "ck_remediation_artifacts_deleted",
        "ck_remediation_artifacts_expiry",
    } <= checks.keys()
    assert all(
        value in checks["ck_remediation_artifacts_lifecycle"]
        for value in ("available", "staging", "expired", "deleted", "superseded")
    )
    assert all(
        value in checks["ck_remediation_artifacts_review"]
        for value in ("pending", "approved", "rejected")
    )
    metadata_check = checks["ck_remediation_artifacts_review_metadata"]
    assert "approved_by_ref IS NOT NULL" in metadata_check
    assert "rejected_by_ref IS NOT NULL" in metadata_check
    assert "approved_by_id IS NOT NULL" not in metadata_check
    assert "rejected_by_id IS NOT NULL" not in metadata_check

    index_columns = {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    }
    assert index_columns["ix_remediation_artifacts_department_lifecycle_expires"] == (
        "department_id",
        "lifecycle_status",
        "expires_at",
    )
    assert index_columns["ix_remediation_artifacts_scan_created"] == (
        "scan_id",
        "created_at",
    )
    assert index_columns["ix_remediation_artifacts_cloud_file_review"] == (
        "cloud_file_id",
        "review_status",
    )
    assert index_columns["ix_remediation_artifacts_cleanup_claimed_at"] == (
        "cleanup_claimed_at",
    )

    assert artifact.department.property.back_populates == "remediation_artifacts"
    assert artifact.scan.property.back_populates == "remediation_artifacts"
    assert artifact.cloud_file.property.back_populates == "remediation_artifacts"
    assert models.CloudFile.current_remediation_artifact.property.post_update is True
    assert (
        models.CloudFile.current_remediation_artifact.property.back_populates
        == "current_for_cloud_files"
    )


def test_cloud_file_current_artifact_pointer_is_nullable_and_set_null():
    column = models.CloudFile.__table__.c.current_remediation_artifact_id
    foreign_key = next(iter(column.foreign_keys))

    assert column.type.length == 36
    assert column.nullable is True
    assert column.index is True
    assert foreign_key.target_fullname == "remediation_artifacts.id"
    assert foreign_key.ondelete == "SET NULL"


def test_no_cascade_ownership_foreign_keys_static_guard():
    columns = models.RemediationArtifact.__table__.c

    for column_name, (target, constraint_name) in OWNERSHIP_FOREIGN_KEYS.items():
        foreign_key = next(iter(columns[column_name].foreign_keys))
        assert foreign_key.target_fullname == target
        assert foreign_key.ondelete == "RESTRICT"
        assert foreign_key.constraint.name == constraint_name


def test_parent_relationships_never_orm_delete_managed_artifacts():
    relationships = (
        models.Department.remediation_artifacts,
        models.Scan.remediation_artifacts,
        models.CloudFile.remediation_artifacts,
        models.RemediationArtifact.remediation_job,
    )

    for relationship in relationships:
        assert "delete" not in relationship.property.cascade
        assert "delete-orphan" not in relationship.property.cascade


def test_actor_foreign_keys_are_nullable_set_null_snapshots():
    columns = models.RemediationArtifact.__table__.c

    for actor in ("created", "approved", "rejected"):
        actor_id = columns[f"{actor}_by_id"]
        foreign_key = next(iter(actor_id.foreign_keys))
        assert actor_id.nullable is True
        assert foreign_key.ondelete == "SET NULL"

    for actor in ("approved", "rejected"):
        actor_ref = columns[f"{actor}_by_ref"]
        assert actor_ref.nullable is True
        assert actor_ref.type.length == 255


def test_migration_chain_schema_order_constraints_indexes_and_reversal(monkeypatch):
    migration = _load_migration()
    calls = []

    for name in (
        "create_table",
        "create_index",
        "add_column",
        "create_foreign_key",
        "drop_constraint",
        "drop_column",
        "drop_index",
        "drop_table",
    ):
        monkeypatch.setattr(
            migration.op,
            name,
            lambda *args, _name=name, **kwargs: calls.append((_name, args, kwargs)),
        )

    migration.upgrade()
    upgrade = list(calls)
    calls.clear()
    migration.downgrade()
    downgrade = list(calls)

    assert migration.down_revision == "20260821_lms_policy_rev"
    assert len(migration.revision) <= 32
    assert upgrade[0][0:2] == (
        "create_table",
        ("remediation_artifacts",) + upgrade[0][1][1:],
    )
    table_call = upgrade[0]
    table_items = table_call[1][1:]
    migration_columns = {
        item.name: item for item in table_items if hasattr(item, "type")
    }
    assert set(models.RemediationArtifact.__table__.c.keys()) == set(migration_columns)
    assert isinstance(migration_columns["size_bytes"].type, BigInteger)
    assert migration_columns["approved_by_ref"].type.length == 255
    assert migration_columns["rejected_by_ref"].type.length == 255

    table_constraints = [item for item in table_items if hasattr(item, "name")]
    actor_fks = {
        tuple(constraint.column_keys): constraint
        for constraint in table_constraints
        if constraint.__class__.__name__ == "ForeignKeyConstraint"
        and tuple(constraint.column_keys)
        in {("created_by_id",), ("approved_by_id",), ("rejected_by_id",)}
    }
    assert actor_fks[("created_by_id",)].ondelete == "SET NULL"
    assert actor_fks[("approved_by_id",)].ondelete == "SET NULL"
    assert actor_fks[("rejected_by_id",)].ondelete == "SET NULL"

    ownership_fks = {
        tuple(constraint.column_keys)[0]: constraint
        for constraint in table_constraints
        if constraint.__class__.__name__ == "ForeignKeyConstraint"
        and tuple(constraint.column_keys)[0] in OWNERSHIP_FOREIGN_KEYS
    }
    for column_name, (_, constraint_name) in OWNERSHIP_FOREIGN_KEYS.items():
        constraint = ownership_fks[column_name]
        assert constraint.ondelete == "RESTRICT"
        assert constraint.name == constraint_name

    operation_names = [call[0] for call in upgrade]
    assert operation_names.index("create_table") < operation_names.index("add_column")
    assert operation_names.index("add_column") < operation_names.index(
        "create_foreign_key"
    )
    assert upgrade[-1][0] == "create_foreign_key"

    assert [call[0] for call in downgrade[-3:]] == [
        "drop_constraint",
        "drop_column",
        "drop_table",
    ]
    assert downgrade[-3][1][0] == migration.CURRENT_ARTIFACT_FK
    assert downgrade[-2][1] == ("cloud_files", "current_remediation_artifact_id")


def test_artifact_settings_defaults():
    settings = Settings(**_settings_kwargs())

    assert settings.remediation_artifact_dir == "/app/uploads/remediation-artifacts"
    assert settings.remediation_artifact_retention_days == 30
    assert settings.remediation_artifact_approved_retention_days == 30
    assert settings.remediation_artifact_written_retention_days == 7
    assert settings.remediation_artifact_max_bytes == 500 * 1024 * 1024
    assert settings.remediation_artifact_cleanup_batch_size == 100
    assert settings.remediation_artifact_staging_grace_seconds == 3600


@pytest.mark.parametrize(
    ("field", "valid_low", "valid_high", "invalid_low", "invalid_high"),
    [
        ("remediation_artifact_retention_days", 1, 3650, 0, 3651),
        ("remediation_artifact_approved_retention_days", 1, 3650, 0, 3651),
        ("remediation_artifact_written_retention_days", 1, 3650, 0, 3651),
        ("remediation_artifact_max_bytes", 1024, 5 * 1024**3, 1023, 5 * 1024**3 + 1),
        ("remediation_artifact_cleanup_batch_size", 1, 1000, 0, 1001),
        ("remediation_artifact_staging_grace_seconds", 60, 86400, 59, 86401),
    ],
)
def test_artifact_settings_strict_bounds(
    field, valid_low, valid_high, invalid_low, invalid_high
):
    assert (
        getattr(Settings(**_settings_kwargs(**{field: valid_low})), field) == valid_low
    )
    assert (
        getattr(Settings(**_settings_kwargs(**{field: valid_high})), field)
        == valid_high
    )
    with pytest.raises(ValidationError):
        Settings(**_settings_kwargs(**{field: invalid_low}))
    with pytest.raises(ValidationError):
        Settings(**_settings_kwargs(**{field: invalid_high}))


@pytest.mark.parametrize(
    "invalid_root",
    ["relative/path", "/app/uploads/../escape", "/app/uploads/artifacts/" + "x" * 5000],
)
def test_artifact_root_must_be_canonical_absolute_path(invalid_root):
    with pytest.raises(ValidationError):
        Settings(**_settings_kwargs(remediation_artifact_dir=invalid_root))


def test_postgresql_migration_upgrade_and_model_query_optional():
    import os

    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL for PostgreSQL Task16A verification")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.skip("Task16A migration verification requires PostgreSQL")

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, select
    from sqlalchemy.orm import Session

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    artifact_fks = {
        tuple(foreign_key["constrained_columns"]): foreign_key
        for foreign_key in inspector.get_foreign_keys("remediation_artifacts")
    }
    for column_name, (_, constraint_name) in OWNERSHIP_FOREIGN_KEYS.items():
        foreign_key = artifact_fks[(column_name,)]
        assert foreign_key["name"] == constraint_name
        assert foreign_key["options"]["ondelete"] == "RESTRICT"
    for column_name in ("created_by_id", "approved_by_id", "rejected_by_id"):
        assert artifact_fks[(column_name,)]["options"]["ondelete"] == "SET NULL"
    review_check = next(
        check["sqltext"]
        for check in inspector.get_check_constraints("remediation_artifacts")
        if check["name"] == "ck_remediation_artifacts_review_metadata"
    )
    assert "approved_by_ref IS NOT NULL" in review_check
    assert "rejected_by_ref IS NOT NULL" in review_check
    assert "approved_by_id IS NOT NULL" not in review_check
    assert "rejected_by_id IS NOT NULL" not in review_check
    with Session(engine) as session:
        session.execute(select(models.RemediationArtifact.id).limit(0))
        session.execute(
            select(models.CloudFile.current_remediation_artifact_id).limit(0)
        )


def test_postgresql_parent_delete_requires_service_cleanup_optional(tmp_path):
    import hashlib
    import os
    import uuid
    from datetime import datetime, timedelta, timezone

    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL for PostgreSQL parent-delete verification")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.skip("Parent-delete verification requires PostgreSQL")

    from sqlalchemy import create_engine, delete, select
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    from src.services.remediation_artifact_service import RemediationArtifactService

    marker = str(uuid.uuid4())
    ids = {
        name: str(uuid.uuid4())
        for name in (
            "department",
            "user",
            "scan",
            "credential",
            "cloud_file",
            "job",
            "artifact",
        )
    }
    storage_key = f"{ids['department']}/{ids['scan']}/{ids['artifact']}/{marker}.docx"
    service = RemediationArtifactService(
        root=tmp_path / "artifacts",
        max_bytes=1024 * 1024,
        retention_days=30,
        staging_grace_seconds=3600,
    )
    artifact_path = service.root / storage_key
    artifact_path.parent.mkdir(parents=True)
    payload = b"managed artifact bytes"
    artifact_path.write_bytes(payload)

    engine = create_engine(database_url)
    with Session(engine) as session:
        department = models.Department(
            id=ids["department"],
            name=f"artifact-fk-{marker}",
            institution="FK Test",
            contact_email=f"{marker}@example.com",
        )
        user = models.User(
            id=ids["user"],
            email=f"{marker}@example.com",
            department_id=ids["department"],
        )
        scan = models.Scan(
            id=ids["scan"],
            scan_type=models.ScanType.WORD,
            file_name="source.docx",
            user_id=ids["user"],
            department_id=ids["department"],
        )
        credential = models.CloudOAuthCredentials(
            id=ids["credential"],
            department_id=ids["department"],
            provider="canvas",
            access_token="test",
            refresh_token="test",
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        cloud_file = models.CloudFile(
            id=ids["cloud_file"],
            department_id=ids["department"],
            credential_id=ids["credential"],
            provider="canvas",
            provider_file_id=marker,
            file_name="source.docx",
            file_type="docx",
            last_scan_id=ids["scan"],
        )
        job = models.CloudJobQueue(
            id=ids["job"],
            department_id=ids["department"],
            job_type="remediate",
            cloud_file_id=ids["cloud_file"],
            credential_id=ids["credential"],
        )
        artifact = models.RemediationArtifact(
            id=ids["artifact"],
            department_id=ids["department"],
            scan_id=ids["scan"],
            cloud_file_id=ids["cloud_file"],
            remediation_job_id=ids["job"],
            created_by_id=ids["user"],
            provider="canvas",
            scan_type="WORD",
            publication_token="b" * 64,
            publication_heartbeat_at=datetime.now(timezone.utc),
            storage_key=storage_key,
            filename="fixed.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        session.add_all((department, user, scan, credential, cloud_file, job, artifact))
        session.flush()
        cloud_file.current_remediation_artifact_id = ids["artifact"]
        session.commit()

        with pytest.raises(IntegrityError):
            session.execute(delete(models.Scan).where(models.Scan.id == ids["scan"]))
            session.commit()
        session.rollback()

        assert (
            session.scalar(
                select(models.RemediationArtifact.id).where(
                    models.RemediationArtifact.id == ids["artifact"]
                )
            )
            == ids["artifact"]
        )
        assert artifact_path.read_bytes() == payload

        artifact = session.get(models.RemediationArtifact, ids["artifact"])
        assert artifact is not None
        assert service.delete_known(artifact) is True
        session.execute(
            delete(models.RemediationArtifact).where(
                models.RemediationArtifact.id == ids["artifact"]
            )
        )
        session.commit()
        assert not artifact_path.exists()

        session.execute(delete(models.Scan).where(models.Scan.id == ids["scan"]))
        session.commit()
        assert session.get(models.Scan, ids["scan"]) is None

        session.execute(
            delete(models.CloudJobQueue).where(models.CloudJobQueue.id == ids["job"])
        )
        session.execute(
            delete(models.CloudFile).where(models.CloudFile.id == ids["cloud_file"])
        )
        session.execute(
            delete(models.CloudOAuthCredentials).where(
                models.CloudOAuthCredentials.id == ids["credential"]
            )
        )
        session.execute(delete(models.User).where(models.User.id == ids["user"]))
        session.execute(
            delete(models.Department).where(models.Department.id == ids["department"])
        )
        session.commit()
