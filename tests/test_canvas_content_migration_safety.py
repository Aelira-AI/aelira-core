"""Safety contract for the published Canvas-content migration."""

import importlib.util
import inspect
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect as sqlalchemy_inspect, text
from conftest import require_disposable_postgres_url

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "2026_08_18_canvas_content_schema.py"
)
ROOT = Path(__file__).parents[1]
PRIOR_REVISION = "2026_03_19_lti_auth"
TARGET_REVISION = "2026_08_18_canvas_content"
EXPECTED_HEAD = "20260831_institution_scope"
MIGRATION_DATABASE_URL = os.getenv("TEST_MIGRATION_DATABASE_URL")
DOWNGRADE_REFUSAL = (
    "Downgrade from 2026_08_18_canvas_content is refused: "
    "the published schema cannot be safely rolled back"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "canvas_content_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_downgrade_refuses_before_any_destructive_alembic_operation(monkeypatch):
    migration = _load_migration()
    calls = []

    monkeypatch.setattr(
        migration,
        "_tables",
        lambda: {
            "wcag_guidelines",
            "content_writeback_log",
            "cloud_files",
            "email_alert_settings",
            "scans",
        },
    )
    monkeypatch.setattr(
        migration,
        "_existing",
        lambda table: {
            column.name
            for column in migration.CLOUD_FILE_COLUMNS + migration.ALERT_COLUMNS
        },
    )
    for operation in (
        "drop_index",
        "drop_table",
        "drop_column",
        "alter_column",
        "execute",
    ):
        monkeypatch.setattr(
            migration.op,
            operation,
            lambda *args, _operation=operation, **kwargs: calls.append(
                (_operation, args, kwargs)
            ),
        )

    with pytest.raises(RuntimeError, match=f"^{DOWNGRADE_REFUSAL}$"):
        migration.downgrade()

    assert calls == []


def test_downgrade_source_documents_why_revision_marker_must_not_move():
    migration = _load_migration()
    source = inspect.getsource(migration.downgrade)

    assert "revision marker" in source
    assert "must not move" in source
    assert "older migrations" in source
    assert "destructive" in source


def test_canvas_revision_is_on_the_single_linear_head_chain():
    config = Config(str(ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == [EXPECTED_HEAD]
    canvas_revision = scripts.get_revision(TARGET_REVISION)
    assert canvas_revision is not None
    assert canvas_revision.down_revision == PRIOR_REVISION
    assert TARGET_REVISION in {
        revision.revision
        for revision in scripts.walk_revisions(base="base", head=EXPECTED_HEAD)
    }


def _reset_public_schema(engine, database_url):
    _require_disposable_database(database_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(text("GRANT ALL ON SCHEMA public TO public"))


def _require_disposable_database(database_url):
    require_disposable_postgres_url(database_url, destructive=True)


def test_schema_reset_checks_disposable_database_before_connecting():
    source = inspect.getsource(_reset_public_schema)

    assert source.index("_require_disposable_database") < source.index("engine.begin")


def _seed_pre_existing_schema_and_data(engine):
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE cloud_files ADD COLUMN IF NOT EXISTS content_body TEXT")
        )
        connection.execute(text("""
                INSERT INTO departments (id, name, institution, contact_email)
                VALUES ('task9-dept', 'Task9', 'Migration Test', 'task9@example.test')
                """))
        connection.execute(text("""
                INSERT INTO cloud_oauth_credentials (
                    id, department_id, provider, access_token, refresh_token,
                    token_expires_at
                ) VALUES (
                    'task9-credential', 'task9-dept', 'google', 'access',
                    'refresh', CURRENT_TIMESTAMP + INTERVAL '1 hour'
                )
                """))
        connection.execute(text("""
                INSERT INTO cloud_files (
                    id, department_id, credential_id, provider,
                    provider_file_id, file_name, file_type, content_body
                ) VALUES (
                    'task9-cloud-file', 'task9-dept', 'task9-credential',
                    'google', 'provider-task9', 'task9.html', 'html',
                    '<p>preserve me</p>'
                )
                """))


def _seed_target_data(engine):
    with engine.begin() as connection:
        connection.execute(text("""
                UPDATE cloud_files
                SET provider_metadata = '{"course_id": "42"}'::json
                WHERE id = 'task9-cloud-file'
                """))
        connection.execute(text("""
                INSERT INTO wcag_guidelines (
                    rule_id, wcag_criterion, wcag_level, title, description,
                    principle, guideline, severity_criteria
                ) VALUES (
                    'task9-rule', '1.1.1', 'A', 'Preserve', 'Preserve row',
                    'Perceivable', 'Text alternatives', '{}'::jsonb
                )
                """))
        connection.execute(text("""
                INSERT INTO content_writeback_log (
                    id, cloud_file_id, original_body, remediated_body
                ) VALUES (
                    'task9-audit', 'task9-cloud-file', '<p>before</p>',
                    '<p>after</p>'
                )
                """))
        connection.execute(text("""
                INSERT INTO scans (
                    id, scan_type, file_name, user_id, department_id
                ) VALUES (
                    'task9-scan', 'CANVAS_CONTENT', 'task9.html', NULL,
                    'task9-dept'
                )
                """))


def _preservation_snapshot(engine):
    inspector = sqlalchemy_inspect(engine)
    tables = set(inspector.get_table_names())
    wcag_indexes = {index["name"] for index in inspector.get_indexes("wcag_guidelines")}
    cloud_columns = {column["name"] for column in inspector.get_columns("cloud_files")}
    alert_columns = {
        column["name"] for column in inspector.get_columns("email_alert_settings")
    }
    scans = {column["name"]: column for column in inspector.get_columns("scans")}
    with engine.connect() as connection:
        return {
            "tables": {
                "wcag_guidelines",
                "content_writeback_log",
            }
            <= tables,
            "wcag_indexes": {
                "idx_wcag_rule_id",
                "idx_wcag_criterion",
                "idx_wcag_level",
            }
            <= wcag_indexes,
            "cloud_columns": {"content_body", "provider_metadata"} <= cloud_columns,
            "alert_columns": {"weekly_summary_day", "weekly_summary_hour"}
            <= alert_columns,
            "scans_user_nullable": scans["user_id"]["nullable"],
            "wcag_row": connection.execute(
                text(
                    "SELECT description FROM wcag_guidelines "
                    "WHERE rule_id = 'task9-rule'"
                )
            ).scalar_one(),
            "audit_row": connection.execute(
                text(
                    "SELECT remediated_body FROM content_writeback_log "
                    "WHERE id = 'task9-audit'"
                )
            ).scalar_one(),
            "cloud_row": connection.execute(
                text(
                    "SELECT content_body FROM cloud_files "
                    "WHERE id = 'task9-cloud-file'"
                )
            ).scalar_one(),
            "null_scan_user": connection.execute(
                text("SELECT user_id FROM scans WHERE id = 'task9-scan'")
            ).scalar_one_or_none(),
            "enum_values": tuple(
                connection.execute(
                    text(
                        "SELECT enumlabel FROM pg_enum "
                        "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                        "WHERE pg_type.typname = 'scantype' ORDER BY enumsortorder"
                    )
                ).scalars()
            ),
        }


@pytest.mark.integration
@pytest.mark.skipif(
    not MIGRATION_DATABASE_URL or os.getenv("ALLOW_DESTRUCTIVE_MIGRATION_TESTS") != "1",
    reason=(
        "TEST_MIGRATION_DATABASE_URL and "
        "ALLOW_DESTRUCTIVE_MIGRATION_TESTS=1 are required"
    ),
)
def test_postgresql_refused_downgrade_preserves_revision_schema_and_data(
    monkeypatch,
):
    _require_disposable_database(MIGRATION_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", MIGRATION_DATABASE_URL)
    assert os.environ["DATABASE_URL"] == MIGRATION_DATABASE_URL

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    assert config.get_main_option("sqlalchemy.url") == MIGRATION_DATABASE_URL
    engine = create_engine(MIGRATION_DATABASE_URL)

    try:
        _reset_public_schema(engine, MIGRATION_DATABASE_URL)
        command.upgrade(config, PRIOR_REVISION)
        _seed_pre_existing_schema_and_data(engine)
        command.upgrade(config, TARGET_REVISION)
        _seed_target_data(engine)

        expected = _preservation_snapshot(engine)
        assert expected == {
            "tables": True,
            "wcag_indexes": True,
            "cloud_columns": True,
            "alert_columns": True,
            "scans_user_nullable": True,
            "wcag_row": "Preserve row",
            "audit_row": "<p>after</p>",
            "cloud_row": "<p>preserve me</p>",
            "null_scan_user": None,
            "enum_values": expected["enum_values"],
        }
        assert {"CANVAS_CONTENT", "MULTIMEDIA"} <= set(expected["enum_values"])

        with pytest.raises(RuntimeError, match=f"^{DOWNGRADE_REFUSAL}$"):
            command.downgrade(config, PRIOR_REVISION)

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == TARGET_REVISION
            )
        assert _preservation_snapshot(engine) == expected

        command.upgrade(config, TARGET_REVISION)
        assert _preservation_snapshot(engine) == expected
    finally:
        _reset_public_schema(engine, MIGRATION_DATABASE_URL)
        engine.dispose()
