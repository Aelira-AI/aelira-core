"""Guard SQLAlchemy 2.1 driver selection at the engine boundary."""

import os
import subprocess
import sys

import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url

from src.database_url import engine_url


def test_url_helper_import_does_not_initialize_database() -> None:
    environment = {
        key: value for key, value in os.environ.items() if key != "DATABASE_URL"
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import src.database_url; import sys; assert 'src.db' not in sys.modules",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("raw_url", "driver"),
    [
        ("postgresql://user:password@localhost/db", "psycopg2"),
        ("postgresql+psycopg2://user:password@localhost/db", "psycopg2"),
        ("sqlite:///:memory:", "pysqlite"),
    ],
)
def test_engine_uses_requested_or_legacy_driver(raw_url: str, driver: str) -> None:
    engine = create_engine(engine_url(raw_url))
    try:
        assert engine.dialect.driver == driver
    finally:
        engine.dispose()


def test_explicit_psycopg_driver_is_not_rewritten() -> None:
    assert engine_url("postgresql+psycopg://user:password@localhost/db").drivername == (
        "postgresql+psycopg"
    )


def test_url_object_and_encoded_components_survive_driver_selection() -> None:
    original = URL.create(
        "postgresql",
        username="u@ser",
        password="p%:word",
        host="localhost",
        database="db/name",
        query={"sslmode": "require", "application_name": "a&b"},
    )
    selected = engine_url(original)
    assert selected.drivername == "postgresql+psycopg2"
    assert selected.username == original.username
    assert selected.password == original.password
    assert selected.database == original.database
    assert selected.query == original.query
    assert make_url(selected.render_as_string(hide_password=False)) == selected


def test_alembic_config_interpolation_preserves_encoded_url() -> None:
    raw = "postgresql://u%40ser:p%25%3Aword@localhost/db%2Fname?application_name=a%26b"
    config = Config()
    config.set_main_option("sqlalchemy.url", raw.replace("%", "%%"))
    section = config.get_section(config.config_ini_section, {})
    selected = engine_url(section["sqlalchemy.url"])
    assert selected.drivername == "postgresql+psycopg2"
    assert selected.username == "u@ser"
    assert selected.password == "p%:word"
    assert selected.database == "db/name"
    assert selected.query["application_name"] == "a&b"
    offline_url = selected.render_as_string(hide_password=False)
    assert make_url(offline_url) == selected
