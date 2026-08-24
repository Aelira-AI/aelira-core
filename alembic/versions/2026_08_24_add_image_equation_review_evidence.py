"""Add durable image-equation evidence and review provenance.

Revision ID: 20260824_task8_review
Revises: 20260822_v095_job_quarantine
"""

from alembic import op
import hashlib
import json
import sqlalchemy as sa

revision = "20260824_task8_review"
down_revision = "20260822_v095_job_quarantine"
branch_labels = None
depends_on = None

_TABLE = "scan_fixes"
_SOURCE_CONSTRAINT = "ck_scan_fixes_source_kind"
_OCCURRENCE_CONSTRAINT = "uq_scan_fixes_scan_occurrence"


def _column_names() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def _check_names() -> set[str]:
    return {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_check_constraints(_TABLE)
        if constraint.get("name")
    }


def _unique_names() -> set[str]:
    return {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_unique_constraints(_TABLE)
        if constraint.get("name")
    }


def _occurrence_key(issue_id: object, location: object, page_number: object) -> str:
    payload = json.dumps(
        [issue_id, location, page_number], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _backfill_occurrences(existing: set[str]) -> None:
    bind = op.get_bind()
    identity_columns = {"issue_id", "location", "page_number"}
    if identity_columns <= existing:
        rows = bind.execute(
            sa.text(
                "SELECT id, scan_id, issue_id, location, page_number "
                "FROM scan_fixes ORDER BY id"
            )
        ).mappings()
        keyed = [
            (row, _occurrence_key(row.issue_id, row.location, row.page_number))
            for row in rows
        ]
    else:
        rows = bind.execute(
            sa.text("SELECT id, scan_id FROM scan_fixes ORDER BY id")
        ).mappings()
        keyed = [(row, _occurrence_key(row.id, None, None)) for row in rows]

    groups: dict[tuple[str, str], list[str]] = {}
    for row, key in keyed:
        groups.setdefault((row.scan_id, key), []).append(row.id)
    table_names = set(sa.inspect(bind).get_table_names())
    for (_, key), ids in groups.items():
        keeper = min(ids)
        if len(ids) > 1:
            stale = [row_id for row_id in ids if row_id != keeper]
            if "review_audit_log" in table_names:
                bind.execute(
                    sa.text(
                        "UPDATE review_audit_log SET fix_id = :keeper "
                        "WHERE fix_id IN :stale"
                    ).bindparams(sa.bindparam("stale", expanding=True)),
                    {"keeper": keeper, "stale": stale},
                )
            if {"review_status", "needs_review"} <= existing:
                assignments = ["review_status = 'pending'", "needs_review = true"]
                for column in ("reviewed_by", "reviewed_at", "review_notes"):
                    if column in existing:
                        assignments.append(f"{column} = NULL")
                bind.execute(
                    sa.text(
                        f"UPDATE scan_fixes SET {', '.join(assignments)} WHERE id = :id"
                    ),
                    {"id": keeper},
                )
            bind.execute(
                sa.text("DELETE FROM scan_fixes WHERE id IN :stale").bindparams(
                    sa.bindparam("stale", expanding=True)
                ),
                {"stale": stale},
            )
        bind.execute(
            sa.text(
                "UPDATE scan_fixes SET occurrence_key = :key WHERE id = :keeper"
            ),
            {"key": key, "keeper": keeper},
        )


def _create_occurrence_constraint() -> None:
    if op.get_bind().dialect.name == "sqlite":
        bind = op.get_bind()
        audit_links = (
            bind.execute(
                sa.text(
                    "SELECT id, fix_id FROM review_audit_log WHERE fix_id IS NOT NULL"
                )
            ).all()
            if "review_audit_log" in sa.inspect(bind).get_table_names()
            else []
        )
        with op.batch_alter_table(_TABLE) as batch:
            batch.alter_column(
                "occurrence_key", existing_type=sa.String(64), nullable=False
            )
            batch.create_unique_constraint(
                _OCCURRENCE_CONSTRAINT, ["scan_id", "occurrence_key"]
            )
        for audit_id, fix_id in audit_links:
            bind.execute(
                sa.text("UPDATE review_audit_log SET fix_id = :fix_id WHERE id = :id"),
                {"id": audit_id, "fix_id": fix_id},
            )
    else:
        op.alter_column(
            _TABLE,
            "occurrence_key",
            existing_type=sa.String(64),
            nullable=False,
        )
        op.create_unique_constraint(
            _OCCURRENCE_CONSTRAINT, _TABLE, ["scan_id", "occurrence_key"]
        )


def _drop_occurrence_constraint() -> None:
    if op.get_bind().dialect.name == "sqlite":
        bind = op.get_bind()
        audit_links = (
            bind.execute(
                sa.text(
                    "SELECT id, fix_id FROM review_audit_log WHERE fix_id IS NOT NULL"
                )
            ).all()
            if "review_audit_log" in sa.inspect(bind).get_table_names()
            else []
        )
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_constraint(_OCCURRENCE_CONSTRAINT, type_="unique")
        for audit_id, fix_id in audit_links:
            bind.execute(
                sa.text("UPDATE review_audit_log SET fix_id = :fix_id WHERE id = :id"),
                {"id": audit_id, "fix_id": fix_id},
            )
    else:
        op.drop_constraint(_OCCURRENCE_CONSTRAINT, _TABLE, type_="unique")


def upgrade() -> None:
    """Add nullable fields safely when replayed by recovery tooling."""
    existing = _column_names()
    additions = (
        sa.Column("provider_used", sa.String(64), nullable=True),
        sa.Column("source_kind", sa.String(32), nullable=True),
        sa.Column("verification_evidence", sa.JSON(), nullable=True),
        sa.Column("occurrence_key", sa.String(64), nullable=True),
    )
    for column in additions:
        if column.name not in existing:
            op.add_column(_TABLE, column)

    existing = _column_names()
    if _OCCURRENCE_CONSTRAINT not in _unique_names():
        _backfill_occurrences(existing)
        _create_occurrence_constraint()

    if (
        op.get_bind().dialect.name != "sqlite"
        and _SOURCE_CONSTRAINT not in _check_names()
    ):
        op.create_check_constraint(
            _SOURCE_CONSTRAINT,
            _TABLE,
            "source_kind IS NULL OR source_kind = 'image_equation'",
        )


def downgrade() -> None:
    """Remove only fields that still exist; safe for interrupted rollbacks."""
    if _SOURCE_CONSTRAINT in _check_names():
        op.drop_constraint(_SOURCE_CONSTRAINT, _TABLE, type_="check")

    if _OCCURRENCE_CONSTRAINT in _unique_names():
        _drop_occurrence_constraint()

    existing = _column_names()
    for name in (
        "occurrence_key",
        "verification_evidence",
        "source_kind",
        "provider_used",
    ):
        if name in existing:
            op.drop_column(_TABLE, name)
