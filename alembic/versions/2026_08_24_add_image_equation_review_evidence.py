"""Add durable image-equation evidence and review provenance.

Revision ID: 20260824_task8_review
Revises: 20260822_v095_job_quarantine
"""

from alembic import op
import hashlib
import json
import os
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260824_task8_review"
down_revision = "20260822_v095_job_quarantine"
branch_labels = None
depends_on = None

_TABLE = "scan_fixes"
_SOURCE_CONSTRAINT = "ck_scan_fixes_source_kind"
_OCCURRENCE_CONSTRAINT = "uq_scan_fixes_scan_occurrence"
_OCCURRENCE_INDEX = "ux_scan_fixes_scan_occurrence_task8"
_EVIDENCE_JSON = sa.JSON().with_variant(JSONB(), "postgresql")
POSTGRES_DEFAULT_MAX_ROWS = 100_000
POSTGRES_BACKFILL_CHUNK_ROWS = 1_000
POSTGRES_LOCK_TIMEOUT_MS = 5_000
POSTGRES_STATEMENT_TIMEOUT_MS = 900_000


def _positive_operator_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


def _operator_override() -> bool:
    return os.getenv("TASK8_REVIEW_MIGRATION_ALLOW_LARGE_TABLE", "").lower() in {
        "1",
        "true",
        "yes",
    }


def _configure_postgres_timeouts(bind: sa.engine.Connection) -> None:
    lock_ms = _positive_operator_int(
        "TASK8_REVIEW_MIGRATION_LOCK_TIMEOUT_MS", POSTGRES_LOCK_TIMEOUT_MS
    )
    statement_ms = _positive_operator_int(
        "TASK8_REVIEW_MIGRATION_STATEMENT_TIMEOUT_MS",
        POSTGRES_STATEMENT_TIMEOUT_MS,
    )
    bind.execute(sa.text(f"SET LOCAL lock_timeout = '{lock_ms}ms'"))
    bind.execute(sa.text(f"SET LOCAL statement_timeout = '{statement_ms}ms'"))


def _postgres_preflight(bind: sa.engine.Connection) -> int:
    """Bound table work or fail with the exact operator override contract."""
    _configure_postgres_timeouts(bind)
    row_count = bind.execute(sa.text("SELECT count(*) FROM scan_fixes")).scalar_one()
    maximum = _positive_operator_int(
        "TASK8_REVIEW_MIGRATION_MAX_ROWS", POSTGRES_DEFAULT_MAX_ROWS
    )
    if row_count > maximum and not _operator_override():
        raise RuntimeError(
            "Task8 review migration refused: scan_fixes has "
            f"{row_count} rows (configured maximum {maximum}). Run in a planned "
            "maintenance window after capacity review, then set operator override "
            "TASK8_REVIEW_MIGRATION_ALLOW_LARGE_TABLE=true."
        )
    return row_count


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


def _index_names() -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(_TABLE)
        if index.get("name")
    }


def _occurrence_key(issue_id: object, location: object, page_number: object) -> str:
    payload = json.dumps(
        [issue_id, location, page_number], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _backfill_occurrences(existing: set[str]) -> None:
    """Backfill in bounded reads while reconciling duplicates deterministically."""
    bind = op.get_bind()
    identity_columns = {"issue_id", "location", "page_number"}
    selected = (
        "id, scan_id, issue_id, location, page_number"
        if identity_columns <= existing
        else "id, scan_id"
    )
    keepers: dict[tuple[str, str], str] = {}
    table_names = set(sa.inspect(bind).get_table_names())
    last_id: str | None = None
    while True:
        rows = list(
            bind.execute(
                sa.text(
                    f"SELECT {selected} FROM scan_fixes "
                    "WHERE (:last_id IS NULL OR id > :last_id) "
                    "ORDER BY id LIMIT :chunk_rows"
                ),
                {"last_id": last_id, "chunk_rows": POSTGRES_BACKFILL_CHUNK_ROWS},
            ).mappings()
        )
        if not rows:
            break
        for row in rows:
            key = (
                _occurrence_key(row.issue_id, row.location, row.page_number)
                if identity_columns <= existing
                else _occurrence_key(row.id, None, None)
            )
            group = (row.scan_id, key)
            keeper = keepers.setdefault(group, row.id)
            if keeper != row.id:
                stale = [row.id]
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
                            f"UPDATE scan_fixes SET {', '.join(assignments)} "
                            "WHERE id = :id"
                        ),
                        {"id": keeper},
                    )
                bind.execute(
                    sa.text("DELETE FROM scan_fixes WHERE id IN :stale").bindparams(
                        sa.bindparam("stale", expanding=True)
                    ),
                    {"stale": stale},
                )
                continue
            bind.execute(
                sa.text(
                    "UPDATE scan_fixes SET occurrence_key = :key WHERE id = :keeper"
                ),
                {"key": key, "keeper": keeper},
            )
        last_id = rows[-1].id


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
        if _OCCURRENCE_INDEX not in _index_names():
            op.create_index(
                _OCCURRENCE_INDEX,
                _TABLE,
                ["scan_id", "occurrence_key"],
                unique=True,
            )
        op.execute(
            sa.text(
                f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_OCCURRENCE_CONSTRAINT} "
                f"UNIQUE USING INDEX {_OCCURRENCE_INDEX}"
            )
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
    """Run bounded DDL/backfill in a planned maintenance window.

    PostgreSQL operators must ensure writers can tolerate a brief bounded lock.
    Large tables fail before DDL; after capacity review, the error documents the
    explicit operator override. Timeout failures are safe to retry because every
    schema step is introspection-guarded and transactional.
    """
    if op.get_bind().dialect.name == "postgresql":
        _postgres_preflight(op.get_bind())
    existing = _column_names()
    additions = (
        sa.Column("provider_used", sa.String(64), nullable=True),
        sa.Column("source_kind", sa.String(32), nullable=True),
        sa.Column("verification_evidence", _EVIDENCE_JSON, nullable=True),
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
    """Reverse inside the same bounded PostgreSQL maintenance-window contract."""
    if op.get_bind().dialect.name == "postgresql":
        _postgres_preflight(op.get_bind())
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
