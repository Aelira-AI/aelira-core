"""Alembic revision identifiers must fit the existing version table."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).parents[1]


def test_every_revision_id_fits_alembic_version_column():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    oversized = {
        revision.revision: len(revision.revision)
        for revision in scripts.walk_revisions(base="base", head="heads")
        if len(revision.revision) > 32
    }

    assert oversized == {}
