"""Historical tenant repair for issue-tracking collaboration references."""

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text


def test_issue_tracking_tenant_repair_is_idempotent_and_fail_closed():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = scripts.get_revision("20260828_issue_tenant_repair")
    assert revision is not None
    assert revision.down_revision == "20260828_region_provenance"

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE scans (id TEXT PRIMARY KEY, department_id TEXT NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE users (id TEXT PRIMARY KEY, department_id TEXT NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE issue_tracking ("
                "id TEXT PRIMARY KEY, scan_id TEXT NOT NULL, department_id TEXT NOT NULL, "
                "assigned_to TEXT, assigned_by TEXT, resolved_by TEXT)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO scans VALUES "
                "('scan-one', 'department-one'), "
                "('scan-two', 'department-two')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users VALUES "
                "('user-one', 'department-one'), "
                "('user-two', 'department-two')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO issue_tracking VALUES "
                "('issue-misaligned', 'scan-one', 'department-two', "
                "'user-two', 'user-two', 'user-two'), "
                "('issue-valid', 'scan-two', 'department-two', "
                "'user-two', 'user-two', 'user-two')"
            )
        )

        module = revision.module
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        module.upgrade()

        rows = connection.execute(
            text(
                "SELECT id, department_id, assigned_to, assigned_by, resolved_by "
                "FROM issue_tracking ORDER BY id"
            )
        ).all()

        assert rows == [
            ("issue-misaligned", "department-one", None, None, None),
            (
                "issue-valid",
                "department-two",
                "user-two",
                "user-two",
                "user-two",
            ),
        ]

        module.downgrade()
        assert (
            connection.execute(
                text(
                    "SELECT department_id FROM issue_tracking "
                    "WHERE id = 'issue-misaligned'"
                )
            ).scalar_one()
            == "department-one"
        )

    engine.dispose()
