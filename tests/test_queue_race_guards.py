"""The required PostgreSQL race lane must fail rather than silently skip."""

from unittest.mock import MagicMock

import pytest

import conftest


@pytest.fixture(params=["TEST_DATABASE_URL", "TEST_MIGRATION_DATABASE_URL"])
def variable(request, monkeypatch):
    monkeypatch.setenv("REQUIRE_QUEUE_POSTGRES_TESTS", "1")
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_MIGRATION_TESTS", "1")
    monkeypatch.delenv(request.param, raising=False)
    return request.param


def test_required_race_without_explicit_database_fails(variable):
    with pytest.raises(pytest.fail.Exception, match="is missing"):
        conftest.queue_race_engine(variable)


def test_optional_race_without_explicit_database_has_visible_skip(
    variable, monkeypatch
):
    monkeypatch.delenv("REQUIRE_QUEUE_POSTGRES_TESTS")
    with pytest.raises(pytest.skip.Exception, match=f"requires {variable}"):
        conftest.queue_race_engine(variable)


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///:memory:",
        "postgresql://localhost/app",
        "postgresql://localhost/production_test",
    ],
)
def test_race_refuses_unsafe_database_before_connecting(variable, monkeypatch, url):
    connect = MagicMock()
    monkeypatch.setenv(variable, url)
    monkeypatch.setattr(conftest, "create_engine", connect)
    with pytest.raises(RuntimeError):
        conftest.queue_race_engine(variable)
    connect.assert_not_called()


def test_race_requires_explicit_destructive_opt_in(variable, monkeypatch):
    monkeypatch.setenv(variable, "postgresql://localhost/queue_races_test")
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_MIGRATION_TESTS")
    connect = MagicMock()
    monkeypatch.setattr(conftest, "create_engine", connect)
    with pytest.raises(RuntimeError, match="ALLOW_DESTRUCTIVE_MIGRATION_TESTS"):
        conftest.queue_race_engine(variable)
    connect.assert_not_called()


def test_required_race_connection_failure_is_not_skipped(variable, monkeypatch):
    monkeypatch.setenv(variable, "postgresql://localhost/queue_races_test")
    engine = MagicMock()
    engine.connect.side_effect = RuntimeError("synthetic connection detail")
    monkeypatch.setattr(conftest, "create_engine", lambda _url: engine)
    with pytest.raises(pytest.fail.Exception, match="unavailable: RuntimeError") as exc:
        conftest.queue_race_engine(variable)
    assert "synthetic" not in str(exc.value)
    engine.dispose.assert_called_once()


def test_available_race_database_returns_connected_engine(variable, monkeypatch):
    monkeypatch.setenv(variable, "postgresql://localhost/queue_races_test")
    engine = MagicMock()
    monkeypatch.setattr(conftest, "create_engine", lambda _url: engine)
    assert conftest.queue_race_engine(variable) is engine
    engine.connect.return_value.__enter__.return_value.exec_driver_sql.assert_called_once_with(
        "SELECT 1"
    )
    engine.dispose.assert_not_called()
