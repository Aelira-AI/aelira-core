"""Exercise the real Alembic environment without opening database connections."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """These configuration checks must never run the shared database setup."""
    yield


ENVIRONMENT_CHECK = """
import io
import logging
import os
import sys
from unittest.mock import patch

from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

config = Config('alembic.ini', output_buffer=io.StringIO())
scripts = ScriptDirectory.from_config(config)
logger = logging.getLogger('alembic_configuration_application_logger')
visited = []

def check_context(revision, context):
    expected = os.environ['DATABASE_URL']
    assert config.get_main_option('sqlalchemy.url') == expected
    assert config.get_section(config.config_ini_section)['sqlalchemy.url'] == expected
    assert context.dialect.name == 'postgresql'
    assert context.opts['target_metadata'].tables
    assert context.opts['literal_binds'] is True
    assert not logger.disabled
    visited.append(True)
    return []

try:
    with patch.object(Engine, 'connect', side_effect=AssertionError('unexpected database connection')):
        with EnvironmentContext(config, scripts, fn=check_context, as_sql=True):
            scripts.run_env()
    assert visited == [True]
except Exception as error:
    # ConfigParser errors can include credentials. Report only the error type.
    if isinstance(error, ValueError) and str(error).startswith(
        'DATABASE_URL environment variable must be set.'
    ):
        print('DATABASE_URL is required')
    else:
        print(type(error).__name__)
    sys.exit(1)
print('configured')
"""


def run_environment(database_url):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    environment.pop("DATABASE_URL", None)
    if database_url is not None:
        environment["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-c", ENVIRONMENT_CHECK],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://synthetic:synthetic-password@localhost:1/aelira_test",
        "postgresql://synthetic%40user:synthetic%25%40%3A%2Fpassword@localhost:1/aelira_test",
    ],
    ids=["plain", "percent-encoded"],
)
def test_environment_preserves_database_url(database_url):
    result = run_environment(database_url)
    assert result.returncode == 0, "Alembic environment configuration failed"
    assert result.stdout.strip() == "configured"
    assert database_url not in result.stdout + result.stderr


def test_environment_requires_exported_database_url():
    result = run_environment(None)
    assert result.returncode == 1
    assert result.stdout.strip() == "DATABASE_URL is required"


def test_heads_has_no_deprecated_path_configuration():
    result = subprocess.run(
        [sys.executable, "-W", "error::DeprecationWarning", "-m", "alembic", "heads"],
        cwd=ROOT,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, "Alembic heads rejected its path configuration"
    assert result.stdout.count("(head)") == 1
    assert not result.stderr
