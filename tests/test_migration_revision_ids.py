"""Alembic revision identifiers must fit the existing version table."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).parents[1]


def test_every_revision_and_down_revision_fit_alembic_version_column():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    oversized = {}
    for revision in scripts.walk_revisions(base="base", head="heads"):
        if len(revision.revision) > 32:
            oversized[f"revision:{revision.revision}"] = len(revision.revision)

        down_revisions = revision.down_revision
        if isinstance(down_revisions, str):
            down_revisions = (down_revisions,)
        for down_revision in down_revisions or ():
            if len(down_revision) > 32:
                oversized[f"down_revision:{down_revision}"] = len(down_revision)

    assert oversized == {}
