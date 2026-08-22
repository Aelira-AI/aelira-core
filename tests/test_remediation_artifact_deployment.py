"""Task16A durable-volume and environment documentation contracts."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def _compose(name):
    return yaml.safe_load((ROOT / name).read_text())


def test_dev_and_quickstart_mount_durable_artifact_storage():
    for name in ("docker-compose.dev.yml", "docker-compose.quickstart.yml"):
        compose = _compose(name)
        api = compose["services"]["api"]
        assert "upload_data:/app/uploads" in api["volumes"]
        assert "upload_data" in compose["volumes"]
        environment = api["environment"]
        assert environment["REMEDIATION_ARTIFACT_DIR"] == (
            "/app/uploads/remediation-artifacts"
        )


def test_environment_template_documents_every_artifact_setting_and_shared_storage():
    template = (ROOT / ".env.example").read_text()

    for setting in (
        "REMEDIATION_ARTIFACT_DIR=/app/uploads/remediation-artifacts",
        "REMEDIATION_ARTIFACT_RETENTION_DAYS=30",
        "REMEDIATION_ARTIFACT_APPROVED_RETENTION_DAYS=30",
        "REMEDIATION_ARTIFACT_WRITTEN_RETENTION_DAYS=7",
        "REMEDIATION_ARTIFACT_MAX_BYTES=524288000",
        "REMEDIATION_ARTIFACT_CLEANUP_BATCH_SIZE=100",
        "REMEDIATION_ARTIFACT_STAGING_GRACE_SECONDS=3600",
    ):
        assert setting in template
    assert "shared storage" in template.lower()
    assert "all api and worker replicas" in template.lower()
