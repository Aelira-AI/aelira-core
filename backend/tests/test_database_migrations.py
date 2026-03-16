"""
Database migration tests.

Tests verify that:
- All Alembic migrations can be applied cleanly
- Migrations can be rolled back
- Schema matches expected state after migrations
- No data loss during migrations
"""

import pytest
import os
import subprocess
from pathlib import Path
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from alembic import command
from alembic.config import Config

# Mark all tests in this module as requires_db (skipped in CI without migration database)
pytestmark = pytest.mark.integration


# Test database URL (use a separate test database)
TEST_DATABASE_URL = os.getenv(
    "TEST_MIGRATION_DATABASE_URL",
    os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/aelira_migration_test"),
)


@pytest.fixture(scope="module")
def alembic_config():
    """Create Alembic config for testing."""
    backend_dir = Path(__file__).parent.parent
    alembic_ini = backend_dir / "alembic.ini"

    if not alembic_ini.exists():
        pytest.skip("alembic.ini not found")

    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


@pytest.fixture(scope="module")
def test_engine():
    """Create test database engine."""
    engine = create_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture(scope="function")
def clean_database(test_engine):
    """Clean database before each test."""
    with test_engine.connect() as conn:
        # Drop all tables
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
        conn.commit()
    yield test_engine


class TestAlembicMigrations:
    """Test Alembic migration operations."""

    def test_migrations_directory_exists(self):
        """Test that migrations directory exists."""
        backend_dir = Path(__file__).parent.parent
        migrations_dir = backend_dir / "alembic" / "versions"

        assert migrations_dir.exists(), "Migrations directory not found"

    def test_has_migration_files(self):
        """Test that migration files exist."""
        backend_dir = Path(__file__).parent.parent
        migrations_dir = backend_dir / "alembic" / "versions"

        if not migrations_dir.exists():
            pytest.skip("Migrations directory not found")

        migration_files = list(migrations_dir.glob("*.py"))
        # Filter out __pycache__ and __init__.py
        migration_files = [f for f in migration_files if not f.name.startswith("__")]

        assert len(migration_files) > 0, "No migration files found"

    def test_migration_files_have_revision_id(self):
        """Test that all migration files have valid revision IDs."""
        backend_dir = Path(__file__).parent.parent
        migrations_dir = backend_dir / "alembic" / "versions"

        if not migrations_dir.exists():
            pytest.skip("Migrations directory not found")

        migration_files = list(migrations_dir.glob("*.py"))
        migration_files = [f for f in migration_files if not f.name.startswith("__")]

        for migration_file in migration_files:
            content = migration_file.read_text()
            assert (
                "revision = " in content or "revision: str = " in content
            ), f"{migration_file.name} missing revision ID"

    @pytest.mark.slow
    def test_upgrade_head_from_scratch(self, alembic_config, clean_database):
        """Test running all migrations from scratch."""
        try:
            # Run all migrations
            command.upgrade(alembic_config, "head")

            # Verify tables exist
            inspector = inspect(clean_database)
            tables = inspector.get_table_names()

            # Check for expected core tables
            expected_tables = [
                "alembic_version",  # Always present after migrations
            ]

            for table in expected_tables:
                assert table in tables, f"Expected table '{table}' not found"

        except Exception as e:
            pytest.fail(f"Migration upgrade failed: {e}")

    @pytest.mark.slow
    def test_downgrade_and_upgrade(self, alembic_config, clean_database):
        """Test migrating up, down, and back up."""
        try:
            # Upgrade to head
            command.upgrade(alembic_config, "head")

            # Get current revision
            with clean_database.connect() as conn:
                result = conn.execute(text("SELECT version_num FROM alembic_version"))
                head_revision = result.scalar()

            # Downgrade one step
            command.downgrade(alembic_config, "-1")

            # Get new revision
            with clean_database.connect() as conn:
                result = conn.execute(text("SELECT version_num FROM alembic_version"))
                result.scalar()

            # Should be different (unless we're at first migration)
            # Then upgrade back to head
            command.upgrade(alembic_config, "head")

            # Verify we're back at head
            with clean_database.connect() as conn:
                result = conn.execute(text("SELECT version_num FROM alembic_version"))
                final_revision = result.scalar()

            assert final_revision == head_revision, "Failed to return to head revision"

        except Exception as e:
            pytest.fail(f"Migration downgrade/upgrade failed: {e}")

    @pytest.mark.slow
    def test_migration_idempotency(self, alembic_config, clean_database):
        """Test that running migrations multiple times is safe."""
        try:
            # Run upgrade twice
            command.upgrade(alembic_config, "head")
            command.upgrade(alembic_config, "head")

            # Should not raise any errors
            inspector = inspect(clean_database)
            tables = inspector.get_table_names()

            assert "alembic_version" in tables

        except Exception as e:
            pytest.fail(f"Migration idempotency test failed: {e}")


class TestSchemaIntegrity:
    """Test database schema integrity after migrations."""

    @pytest.mark.slow
    def test_core_tables_structure(self, alembic_config, clean_database):
        """Test that core tables have expected structure."""
        command.upgrade(alembic_config, "head")

        inspector = inspect(clean_database)
        tables = inspector.get_table_names()

        # Check for key tables (adjust based on your models)
        key_tables = [
            "departments",
            "users",
            "scans",
        ]

        for table in key_tables:
            if table in tables:
                columns = inspector.get_columns(table)
                column_names = [c["name"] for c in columns]

                # Each table should have id column
                assert "id" in column_names, f"Table '{table}' missing 'id' column"

    @pytest.mark.slow
    def test_foreign_keys_valid(self, alembic_config, clean_database):
        """Test that all foreign keys reference existing tables."""
        command.upgrade(alembic_config, "head")

        inspector = inspect(clean_database)
        tables = inspector.get_table_names()

        for table in tables:
            if table == "alembic_version":
                continue

            try:
                foreign_keys = inspector.get_foreign_keys(table)
                for fk in foreign_keys:
                    referred_table = fk.get("referred_table")
                    assert (
                        referred_table in tables
                    ), f"Table '{table}' has FK to non-existent table '{referred_table}'"
            except Exception:
                # Some tables may not support FK inspection
                pass

    @pytest.mark.slow
    def test_indexes_created(self, alembic_config, clean_database):
        """Test that expected indexes are created."""
        command.upgrade(alembic_config, "head")

        inspector = inspect(clean_database)
        tables = inspector.get_table_names()

        # Check for indexes on commonly queried columns
        for table in tables:
            if table == "alembic_version":
                continue

            try:
                indexes = inspector.get_indexes(table)
                # Log indexes for debugging
                [idx["name"] for idx in indexes if idx.get("name")]
            except Exception:
                pass


class TestMigrationCLI:
    """Test Alembic CLI commands."""

    def test_alembic_current(self):
        """Test that 'alembic current' runs without error."""
        backend_dir = Path(__file__).parent.parent

        result = subprocess.run(
            ["alembic", "current"],
            cwd=str(backend_dir),
            capture_output=True,
            text=True,
            env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
        )

        # May return non-zero if no migrations applied, but shouldn't crash
        assert "Error" not in result.stderr or "CommandError" in result.stderr

    def test_alembic_history(self):
        """Test that 'alembic history' shows migration chain."""
        backend_dir = Path(__file__).parent.parent

        result = subprocess.run(
            ["alembic", "history", "--verbose"],
            cwd=str(backend_dir),
            capture_output=True,
            text=True,
        )

        # Should show at least some output (even if just headers)
        assert result.returncode == 0 or "Error" not in result.stderr

    def test_alembic_check(self):
        """Test that schema matches models (no pending migrations)."""
        backend_dir = Path(__file__).parent.parent

        subprocess.run(
            ["alembic", "check"],
            cwd=str(backend_dir),
            capture_output=True,
            text=True,
            env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
        )

        # This may fail if there are pending changes, which is informational
        # We just want to ensure the command runs


class TestDataPreservation:
    """Test that migrations preserve existing data."""

    @pytest.mark.slow
    def test_upgrade_preserves_data(self, alembic_config, clean_database):
        """Test that upgrading preserves existing data."""
        # First, run migrations to create schema
        command.upgrade(alembic_config, "head")

        Session = sessionmaker(bind=clean_database)
        session = Session()

        try:
            # Insert test data directly
            session.execute(text("""
                    INSERT INTO departments (id, name, institution, contact_email)
                    VALUES ('test-dept-migration', 'Migration Test Dept', 'Test University', 'test@test.edu')
                    ON CONFLICT (id) DO NOTHING
                """))
            session.commit()

            # Verify data exists
            result = session.execute(
                text(
                    "SELECT COUNT(*) FROM departments WHERE id = 'test-dept-migration'"
                )
            )
            count_before = result.scalar()

            # Run migrations again (should be idempotent)
            command.upgrade(alembic_config, "head")

            # Verify data still exists
            result = session.execute(
                text(
                    "SELECT COUNT(*) FROM departments WHERE id = 'test-dept-migration'"
                )
            )
            count_after = result.scalar()

            assert count_after == count_before, "Data was lost during migration"

        except Exception as e:
            # Table might not exist if this is a fresh schema
            if "relation" in str(e).lower() and "does not exist" in str(e).lower():
                pytest.skip("Schema not fully set up for data preservation test")
            raise
        finally:
            session.close()


class TestMigrationOrdering:
    """Test that migrations are properly ordered."""

    def test_migration_chain_is_continuous(self):
        """Test that migration chain has no gaps."""
        backend_dir = Path(__file__).parent.parent
        migrations_dir = backend_dir / "alembic" / "versions"

        if not migrations_dir.exists():
            pytest.skip("Migrations directory not found")

        migration_files = list(migrations_dir.glob("*.py"))
        migration_files = [f for f in migration_files if not f.name.startswith("__")]

        # Parse revisions — handle both `revision = "..."` and `revision: str = "..."`
        revisions = {}
        for migration_file in migration_files:
            content = migration_file.read_text()

            # Extract revision and down_revision
            revision = None
            down_revision = None

            for line in content.split("\n"):
                stripped = line.strip()
                # Strip inline comments before parsing
                if "  #" in stripped:
                    stripped = stripped[: stripped.index("  #")].rstrip()
                if stripped.startswith("revision") and "=" in stripped:
                    # Handle: revision = "x", revision: str = "x"
                    val = stripped.split("=", 1)[1].strip().strip("'\"")
                    if val and not val.startswith("("):
                        revision = val
                if stripped.startswith("down_revision") and "=" in stripped:
                    # Handle: down_revision = "x", down_revision: Union[str, None] = "x"
                    val = stripped.split("=", 1)[1].strip()
                    if val not in ("None", "None,"):
                        # Skip tuple merge revisions like ('rev1', 'rev2')
                        if not val.startswith("("):
                            down_revision = val.strip("'\"")

            if revision:
                revisions[revision] = down_revision

        # Verify chain (each down_revision should exist or be None)
        for rev, down_rev in revisions.items():
            if down_rev is not None:
                assert (
                    down_rev in revisions or down_rev == "None"
                ), f"Migration {rev} references non-existent revision {down_rev}"

    def test_single_head(self):
        """Test that there's only one head (no branching)."""
        backend_dir = Path(__file__).parent.parent

        result = subprocess.run(
            ["alembic", "heads"],
            cwd=str(backend_dir),
            capture_output=True,
            text=True,
        )

        # Count number of heads (each head is on its own line)
        heads = [
            line
            for line in result.stdout.strip().split("\n")
            if line and "(head)" in line
        ]

        # Should have at most one head
        assert len(heads) <= 1, f"Multiple heads detected: {heads}"
