"""Release-blocking contracts for issue #263 operational health alerting."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry, generate_latest

from src.api.main import app, get_db_dependency, get_redis_client


def _now() -> datetime:
    return datetime(2026, 8, 30, 4, 30, tzinfo=timezone.utc)


def test_api_liveness_stays_up_when_readiness_dependencies_fail() -> None:
    db = MagicMock()
    db.execute.side_effect = RuntimeError("database unavailable: private detail")
    app.dependency_overrides[get_db_dependency] = lambda: db
    app.dependency_overrides[get_redis_client] = lambda: None
    try:
        client = TestClient(app)
        live = client.get("/live")
        ready = client.get("/ready")
    finally:
        app.dependency_overrides.pop(get_db_dependency, None)
        app.dependency_overrides.pop(get_redis_client, None)

    assert live.status_code == 200
    assert live.json() == {"status": "alive", "message": "API process is alive"}
    assert ready.status_code == 503
    assert ready.json()["status"] == "not_ready"
    assert "private detail" not in ready.text


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({"live_workers": 0}, "worker_unavailable"),
        ({"expired_processing": 1}, "expired_lease"),
        ({"stalled_processing": 1}, "stuck_processing"),
        (
            {
                "runnable_pending": 1,
                "latest_progress": _now() - timedelta(minutes=3),
            },
            "stuck_runnable_backlog",
        ),
        ({"processing_count": 1}, "healthy_processing"),
        (
            {"runnable_pending": 1, "latest_progress": _now()},
            "healthy_advancing",
        ),
        ({}, "healthy_idle"),
    ],
)
def test_worker_health_classifier_covers_every_alert_state(facts, expected) -> None:
    from src.jobs.operational_health import classify_worker_health

    defaults = {
        "live_workers": 1,
        "runnable_pending": 0,
        "processing_count": 0,
        "expired_processing": 0,
        "stalled_processing": 0,
        "latest_progress": None,
        "cutoff": _now() - timedelta(minutes=2),
    }
    defaults.update(facts)
    assert classify_worker_health(**defaults) == expected


def test_snapshot_reports_bounded_ages_counts_and_recovery() -> None:
    from src.jobs.operational_health import build_worker_health_snapshot

    now = _now()
    degraded = build_worker_health_snapshot(
        now=now,
        heartbeat_cutoff=now - timedelta(minutes=2),
        execution_seconds=3600,
        queue_counts={"pending": 2, "processing": 1, "completed": 8, "failed": 1},
        live_workers=1,
        draining_workers=0,
        latest_heartbeat=now - timedelta(seconds=25),
        latest_progress=now - timedelta(seconds=40),
        jobs_claimed=10,
        jobs_completed=8,
        jobs_failed=1,
        runnable_pending=2,
        processing_jobs=(
            SimpleNamespace(
                claimed_at=now - timedelta(minutes=70),
                heartbeat_at=now - timedelta(seconds=30),
                lease_expires_at=now - timedelta(seconds=1),
            ),
        ),
        oldest_pending=now - timedelta(minutes=5),
    )
    recovered = build_worker_health_snapshot(
        now=now,
        heartbeat_cutoff=now - timedelta(minutes=2),
        execution_seconds=3600,
        queue_counts={"pending": 0, "processing": 0, "completed": 9, "failed": 1},
        live_workers=1,
        draining_workers=0,
        latest_heartbeat=now,
        latest_progress=now,
        jobs_claimed=10,
        jobs_completed=9,
        jobs_failed=1,
        runnable_pending=0,
        processing_jobs=(),
        oldest_pending=None,
    )

    assert degraded.health_state == "expired_lease"
    assert degraded.latest_heartbeat_age_seconds == 25
    assert degraded.oldest_running_job_age_seconds == 4200
    assert degraded.expired_processing == 1
    assert degraded.stalled_processing == 0
    assert recovered.health_state == "healthy_idle"
    assert recovered.expired_processing == 0
    assert recovered.oldest_running_job_age_seconds is None


def _snapshot(**overrides):
    from src.jobs.operational_health import WorkerHealthSnapshot

    values = {
        "health_state": "healthy_idle",
        "queue": {"pending": 0, "processing": 0, "completed": 9, "failed": 1},
        "live_workers": 1,
        "draining_workers": 0,
        "latest_heartbeat_at": _now(),
        "latest_heartbeat_age_seconds": 0.0,
        "latest_progress_at": _now(),
        "latest_progress_age_seconds": 0.0,
        "oldest_pending_created_at": None,
        "oldest_pending_age_seconds": None,
        "oldest_processing_heartbeat_at": None,
        "oldest_running_job_age_seconds": None,
        "runnable_pending": 0,
        "expired_processing": 0,
        "stalled_processing": 0,
        "jobs_claimed": 10,
        "jobs_completed": 9,
        "jobs_failed": 1,
    }
    values.update(overrides)
    return WorkerHealthSnapshot(**values)


def test_prometheus_collector_exports_only_bounded_operational_series() -> None:
    from src.monitoring.worker_health import WorkerHealthCollector

    registry = CollectorRegistry()
    registry.register(
        WorkerHealthCollector(
            lambda: _snapshot(
                health_state="stuck_processing",
                latest_heartbeat_age_seconds=45.0,
                latest_progress_age_seconds=70.0,
                oldest_running_job_age_seconds=3900.0,
                runnable_pending=2,
                expired_processing=1,
                stalled_processing=1,
            )
        )
    )
    exposition = generate_latest(registry).decode()

    for series in (
        "aelira_worker_health_collection_success 1.0",
        "aelira_worker_live_workers 1.0",
        "aelira_worker_latest_heartbeat_age_seconds 45.0",
        "aelira_worker_oldest_running_job_age_seconds 3900.0",
        "aelira_worker_expired_leases 1.0",
        "aelira_worker_stalled_jobs 1.0",
        'aelira_worker_health_state{state="stuck_processing"} 1.0',
    ):
        assert series in exposition
    for forbidden in (
        "tenant_id",
        "department_id",
        "worker_id",
        "job_id",
        "document_id",
        "scan_id",
        "provider",
        "credential",
        "file_name",
    ):
        assert forbidden not in exposition


def test_prometheus_collection_failure_is_bounded() -> None:
    from src.monitoring.worker_health import WorkerHealthCollector

    def fail():
        raise RuntimeError("postgresql://secret@private-host/customer")

    registry = CollectorRegistry()
    registry.register(WorkerHealthCollector(fail))
    exposition = generate_latest(registry).decode()

    assert "aelira_worker_health_collection_success 0.0" in exposition
    assert "secret" not in exposition
    assert "private-host" not in exposition


def test_metrics_endpoint_registers_the_worker_health_collector() -> None:
    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    assert "aelira_worker_health_collection_success" in response.text
    assert "postgresql://" not in response.text


def test_worker_probe_json_is_bounded_and_modes_are_distinct(capsys) -> None:
    from src.jobs import healthcheck

    now = datetime.now(timezone.utc)
    heartbeat = SimpleNamespace(
        status="running",
        heartbeat_at=now,
        metadata_json={"progress_watermark_at": now.isoformat()},
    )
    db = MagicMock()
    db.get.return_value = heartbeat
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db

    with (
        patch.dict("os.environ", {"JOB_WORKER_ID": "private-worker-name"}),
        patch("src.jobs.healthcheck.SessionLocal", factory),
        pytest.raises(SystemExit) as result,
    ):
        healthcheck.main(["--mode", "liveness", "--json"])

    assert result.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "mode": "liveness",
        "status": "healthy",
        "health_state": "live",
    }
    assert "private-worker-name" not in json.dumps(payload)
    db.query.assert_not_called()


@pytest.mark.parametrize(
    "heartbeat",
    [
        None,
        SimpleNamespace(
            status="stopped",
            heartbeat_at=datetime.now(timezone.utc),
            metadata_json={},
        ),
        SimpleNamespace(
            status="running",
            heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=3),
            metadata_json={},
        ),
    ],
)
def test_worker_liveness_rejects_missing_stopped_or_stale_heartbeat(
    heartbeat, capsys
) -> None:
    from src.jobs import healthcheck

    db = MagicMock()
    db.get.return_value = heartbeat
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    with (
        patch.dict("os.environ", {"JOB_WORKER_ID": "private-worker-name"}),
        patch("src.jobs.healthcheck.SessionLocal", factory),
        pytest.raises(SystemExit) as result,
    ):
        healthcheck.main(["--mode", "liveness", "--json"])

    assert result.value.code == 1
    assert json.loads(capsys.readouterr().out)["health_state"] == "worker_unavailable"
    db.query.assert_not_called()


def test_shipped_prometheus_rules_are_sustained_and_recoverable() -> None:
    rule_path = Path("ops/prometheus/aelira-alerts.yml")
    rules = yaml.safe_load(rule_path.read_text())
    alerts = {
        rule["alert"]: rule for group in rules["groups"] for rule in group["rules"]
    }

    expected = {
        "AeliraApiUnavailable": 'up{job="aelira-api"} == 0',
        "AeliraWorkerUnavailable": "aelira_worker_live_workers < 1",
        "AeliraWorkerExpiredLease": "aelira_worker_expired_leases > 0",
        "AeliraWorkerStalled": "aelira_worker_stalled_jobs > 0",
    }
    assert set(alerts) == set(expected)
    for name, expression in expected.items():
        assert expression in alerts[name]["expr"]
        assert alerts[name]["for"] in {"2m", "3m", "5m"}
        assert alerts[name]["annotations"]["summary"]
        assert alerts[name]["annotations"]["recovery"]


def test_compose_surfaces_readiness_for_api_and_workers() -> None:
    for name in (
        "docker-compose.prod.yml",
        "docker-compose.quickstart.yml",
        "docker-compose.dev.yml",
    ):
        compose = yaml.safe_load(Path(name).read_text())
        api_probe = " ".join(compose["services"]["api"]["healthcheck"]["test"])
        worker_probe = " ".join(compose["services"]["worker"]["healthcheck"]["test"])
        assert "/ready" in api_probe
        assert "--mode readiness" in worker_probe


def test_self_hosting_documents_monitoring_and_resolved_recovery() -> None:
    guide = Path("docs/deployment/self-hosting.md").read_text()
    for required in (
        "## Monitoring and sustained alerts",
        "docker compose -f docker-compose.prod.yml ps",
        "ops/prometheus/aelira-alerts.yml",
        "send_resolved: true",
        "/live",
        "/ready",
        "Gatus",
    ):
        assert required in guide


def test_alert_surfaces_import_one_worker_health_classifier() -> None:
    route = Path("src/api/job_worker_routes.py").read_text()
    probe = Path("src/jobs/healthcheck.py").read_text()
    metrics = Path("src/monitoring/worker_health.py").read_text()
    health = Path("src/jobs/operational_health.py").read_text()

    assert "def _worker_health_state(" not in route
    assert "collect_worker_health_snapshot" in route
    assert "classify_worker_health" in probe
    assert "collect_worker_health_snapshot" in metrics
    assert health.count("def classify_worker_health(") == 1
