"""Preserve unreported confidence and correct ambiguous legacy AI defaults.

Revision ID: 20260917_unknown_confidence
Revises: 20260905_visual_analysis

Run with remediation/review writers quiesced. The data correction is conservative
and audited; it cannot infer whether a legacy generic AI score was explicit.
"""

from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa

revision = "20260917_unknown_confidence"
down_revision = "20260905_visual_analysis"
branch_labels = None
depends_on = None

# Frozen scan-fix-review-v1 fields. Do not import changing application code into
# historical migrations. Only generic (non-visual) rows are corrected here.
_CANONICAL_FIELDS = (
    "issue_id",
    "occurrence_key",
    "category",
    "severity",
    "description",
    "location",
    "original_content",
    "fixed_content",
    "fix_method",
    "provider_used",
    "model_used",
    "source_kind",
    "source_locator",
    "verification_evidence",
    "visual_semantic_contract",
    "confidence",
    "needs_review",
    "wcag_criteria",
    "page_number",
)
_PRIOR_REVIEW_FIELDS = (
    "confidence",
    "needs_review",
    "review_status",
    "reviewed_by",
    "reviewed_at",
    "review_notes",
    "review_digest",
    "approved_review_digest",
)


def _digest_value(value):
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return {
            "text_sha256": hashlib.sha256(encoded).hexdigest(),
            "utf8_bytes": len(encoded),
        }
    if isinstance(value, dict):
        return {key: _digest_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_digest_value(item) for item in value]
    return value


def _review_digest(row):
    material = {"review_contract_version": "scan-fix-review-v1"}
    material.update({field: row[field] for field in _CANONICAL_FIELDS})
    payload = json.dumps(
        _digest_value(material),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _prior_values(row, fields):
    return {
        field: value.isoformat() if isinstance(value, datetime) else value
        for field in fields
        for value in (row[field],)
    }


def upgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("scan_fixes") as batch:
        batch.alter_column(
            "confidence", existing_type=sa.Float(), nullable=True, server_default=None
        )

    metadata = sa.MetaData()
    fixes = sa.Table("scan_fixes", metadata, autoload_with=bind)
    audit = sa.Table("review_audit_log", metadata, autoload_with=bind)
    scans = sa.Table("scans", metadata, autoload_with=bind)
    artifacts = sa.Table("remediation_artifacts", metadata, autoload_with=bind)
    clouds = sa.Table("cloud_files", metadata, autoload_with=bind)
    now = datetime.now(timezone.utc)
    affected_scans = set()
    candidates = (
        bind.execute(
            sa.select(fixes).where(
                fixes.c.fix_method == "ai_generated",
                fixes.c.source_kind.is_(None),
                fixes.c.confidence == 1.0,
            )
        )
        .mappings()
        .all()
    )
    for old in candidates:
        updated = dict(old)
        updated.update(confidence=None, needs_review=True, approved_review_digest=None)
        # Rejections remain rejections. No accepted decision can continue to
        # authorize output using the old, unsupported numeric evidence.
        if old["review_status"] != "rejected":
            updated.update(
                review_status="pending",
                reviewed_by=None,
                reviewed_at=None,
                review_notes=None,
            )
        updated["review_digest"] = _review_digest(updated)
        bind.execute(
            audit.insert().values(
                id=str(uuid.uuid4()),
                scan_id=old["scan_id"],
                fix_id=old["id"],
                user_id=None,
                action="confidence_corrected",
                created_at=now,
                details={
                    "policy": revision,
                    "reason": "ambiguous_legacy_generic_ai_default",
                    "previous": _prior_values(old, _PRIOR_REVIEW_FIELDS),
                    "confidence": None,
                    "review_digest": updated["review_digest"],
                },
            )
        )
        values = {field: updated[field] for field in _PRIOR_REVIEW_FIELDS}
        values["updated_at"] = now
        bind.execute(fixes.update().where(fixes.c.id == old["id"]).values(**values))
        affected_scans.add(old["scan_id"])

    for scan_id in sorted(affected_scans):
        current_ids = sa.union(
            sa.select(scans.c.current_remediation_artifact_id).where(
                scans.c.id == scan_id
            ),
            sa.select(clouds.c.current_remediation_artifact_id)
            .join(artifacts, artifacts.c.id == clouds.c.current_remediation_artifact_id)
            .where(artifacts.c.scan_id == scan_id),
        )
        current = (
            bind.execute(
                sa.select(artifacts).where(
                    artifacts.c.id.in_(current_ids),
                    artifacts.c.review_status == "approved",
                    artifacts.c.written_back_at.is_(None),
                )
            )
            .mappings()
            .all()
        )
        for artifact in current:
            bind.execute(
                audit.insert().values(
                    id=str(uuid.uuid4()),
                    scan_id=scan_id,
                    fix_id=None,
                    user_id=None,
                    action="artifact_approval_invalidated",
                    created_at=now,
                    details={
                        "artifact_id": artifact["id"],
                        "reason": revision,
                        "previous": _prior_values(
                            artifact,
                            (
                                "review_status",
                                "approval_checksum",
                                "approval_review_digest",
                                "approved_by_id",
                                "approved_by_ref",
                                "approved_at",
                            ),
                        ),
                    },
                )
            )
            bind.execute(
                artifacts.update()
                .where(artifacts.c.id == artifact["id"])
                .values(
                    review_status="pending",
                    approval_checksum=None,
                    approval_review_digest=None,
                    approved_by_id=None,
                    approved_by_ref=None,
                    approved_at=None,
                )
            )
            bind.execute(
                clouds.update()
                .where(clouds.c.current_remediation_artifact_id == artifact["id"])
                .values(
                    writeback_status="pending_review",
                    has_remediated_version=False,
                    remediation_origin=None,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    unknown = bind.scalar(
        sa.text("SELECT count(*) FROM scan_fixes WHERE confidence IS NULL")
    )
    if unknown:
        raise RuntimeError(
            "Cannot downgrade unknown confidence without inventing scores. "
            "Retain this schema or restore a coordinated pre-migration backup."
        )
    with op.batch_alter_table("scan_fixes") as batch:
        batch.alter_column(
            "confidence", existing_type=sa.Float(), nullable=False, server_default="1.0"
        )
