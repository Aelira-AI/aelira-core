from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.jobs.contracts import FailureKind, JobFailure, JobSuccess
from src.jobs.local_scan_job import (
    LOCAL_SCAN_KINDS,
    LocalScanJobError,
    enqueue_local_scan_job,
    handle_local_scan_job,
    materialize_verified_scan_input,
    normalize_local_scan_options,
)

EXPECTED_LOCAL_SCAN_KINDS = frozenset(
    {
        "local_pdf",
        "local_powerpoint",
        "local_word",
        "local_excel",
        "local_latex",
        "local_latex_pdf",
        "local_code",
        "local_multimedia",
        "local_web",
        "local_web_batch",
        "local_web_sitemap",
    }
)


def test_local_scan_kind_registry_is_closed() -> None:
    assert LOCAL_SCAN_KINDS == EXPECTED_LOCAL_SCAN_KINDS


@pytest.mark.parametrize(
    ("scan_kind", "options"),
    [
        ("local_pdf", {"generate_alt_text": False, "enhance_descriptions": True}),
        ("local_latex", {"use_ollama": True}),
        (
            "local_multimedia",
            {
                "generate_captions": True,
                "generate_audio_descriptions": True,
                "generate_spoken_descriptions": False,
                "detect_flashing": True,
                "generate_transcript": False,
                "whisper_model": "base",
            },
        ),
        (
            "local_web",
            {
                "url": "https://example.edu",
                "mode": "quick",
                "scan_images": False,
                "scan_multimedia": False,
                "scan_math": False,
                "validate_alt_text": False,
                "max_depth": 1,
                "max_pages": 10,
                "generate_code_fixes": True,
                "capture_screenshots": True,
            },
        ),
    ],
)
def test_options_accept_only_the_exact_per_kind_schema(
    scan_kind: str, options: dict[str, object]
) -> None:
    assert normalize_local_scan_options(scan_kind, options) == options


@pytest.mark.parametrize(
    ("scan_kind", "options", "code"),
    [
        ("unknown", {}, "local_scan_kind_invalid"),
        ("local_pdf", {"generate_alt_text": 1}, "local_scan_options_invalid"),
        (
            "local_pdf",
            {
                "generate_alt_text": False,
                "enhance_descriptions": True,
                "surprise": True,
            },
            "local_scan_options_invalid",
        ),
        (
            "local_multimedia",
            {
                "generate_captions": True,
                "generate_audio_descriptions": True,
                "generate_spoken_descriptions": False,
                "detect_flashing": True,
                "generate_transcript": False,
                "whisper_model": "unbounded",
            },
            "local_scan_options_invalid",
        ),
    ],
)
def test_invalid_options_fail_with_bounded_codes(
    scan_kind: str, options: dict[str, object], code: str
) -> None:
    with pytest.raises(LocalScanJobError) as exc_info:
        normalize_local_scan_options(scan_kind, options)
    assert exc_info.value.code == code


def test_materialized_input_is_tenant_scoped_hash_verified_and_disposable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage_root = tmp_path / "uploads"
    source_dir = storage_root / "dept-a" / "scan-a"
    source_dir.mkdir(parents=True)
    source = source_dir / "document.pdf"
    content = b"durable source bytes"
    source.write_bytes(content)

    monkeypatch.setattr("src.jobs.local_scan_job.UPLOAD_BASE_DIR", storage_root)
    scan = SimpleNamespace(
        id="scan-a", department_id="dept-a", storage_path=str(source)
    )

    with materialize_verified_scan_input(
        scan, hashlib.sha256(content).hexdigest()
    ) as materialized:
        work_path, loaded = materialized
        assert work_path != str(source)
        assert Path(work_path).read_bytes() == content
        assert loaded == content
        assert Path(work_path).exists()

    assert source.read_bytes() == content
    assert not Path(work_path).exists()


@pytest.mark.parametrize(
    ("storage_path", "expected_hash", "code"),
    [
        ("other/scan-a/document.pdf", "0" * 64, "local_scan_input_scope_invalid"),
        ("dept-a/scan-a/missing.pdf", "0" * 64, "local_scan_input_unavailable"),
    ],
)
def test_materialized_input_rejects_invalid_scope_or_missing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    storage_path: str,
    expected_hash: str,
    code: str,
) -> None:
    storage_root = tmp_path / "uploads"
    monkeypatch.setattr("src.jobs.local_scan_job.UPLOAD_BASE_DIR", storage_root)
    scan = SimpleNamespace(
        id="scan-a",
        department_id="dept-a",
        storage_path=str(storage_root / storage_path),
    )
    with pytest.raises(LocalScanJobError) as exc_info:
        with materialize_verified_scan_input(scan, expected_hash):
            pass
    assert exc_info.value.code == code


def test_enqueue_local_scan_job_uses_scan_tenant_and_deterministic_dedupe() -> None:
    db = MagicMock()
    scan = SimpleNamespace(id="scan-a", department_id="dept-a", status="PENDING")
    db.scalar.return_value = scan
    enqueue = MagicMock(return_value=SimpleNamespace(id="job-a"))
    options = {"generate_alt_text": False, "enhance_descriptions": True}

    job = enqueue_local_scan_job(
        db,
        scan=scan,
        scan_kind="local_pdf",
        options=options,
        input_sha256="a" * 64,
        enqueue=enqueue,
    )

    assert job.id == "job-a"
    locked_query = db.scalar.call_args.args[0]
    assert locked_query.get_execution_options()["populate_existing"] is True
    enqueue.assert_called_once_with(
        db,
        department_id="dept-a",
        job_type="scan",
        payload={
            "scan_kind": "local_pdf",
            "scan_id": "scan-a",
            "options": options,
            "input_sha256": "a" * 64,
        },
        dedupe_key="local-scan:scan-a",
    )


def _job(payload: dict[str, object], *, department_id: str = "dept-a"):
    return SimpleNamespace(
        id="job-a",
        payload=payload,
        department_id=department_id,
        claim_token="claim-a",
        worker_id="worker-a",
        attempt_count=1,
        max_retries=1,
        provider=None,
        credential_id=None,
        cloud_file_id=None,
        provider_file_id=None,
    )


def _db_for(scan, results: list[object | None]):
    db = MagicMock()
    db.get.return_value = scan
    db.query.return_value.filter.return_value.first.side_effect = results
    return db


@pytest.mark.asyncio
async def test_completed_scan_replay_short_circuits_without_claiming_committed_queue() -> (
    None
):
    scan = SimpleNamespace(id="scan-a", department_id="dept-a", status="COMPLETED")
    db = _db_for(scan, [SimpleNamespace(scan_id="scan-a")])
    payload = {
        "scan_kind": "local_web",
        "scan_id": "scan-a",
        "options": {
            "url": "https://example.edu",
            "mode": "quick",
            "scan_images": False,
            "scan_multimedia": False,
            "scan_math": False,
            "validate_alt_text": False,
            "max_depth": 1,
            "max_pages": 10,
            "generate_code_fixes": True,
            "capture_screenshots": True,
        },
    }

    result = await handle_local_scan_job(_job(payload), db)

    assert result == JobSuccess({"success": True, "scan_id": "scan-a"})
    assert result.handler_committed is False


@pytest.mark.asyncio
async def test_local_scan_handler_rejects_cross_tenant_scan() -> None:
    scan = SimpleNamespace(id="scan-a", department_id="dept-b", status="PROCESSING")
    db = _db_for(scan, [])
    payload = {
        "scan_kind": "local_pdf",
        "scan_id": "scan-a",
        "options": {"generate_alt_text": False, "enhance_descriptions": True},
        "input_sha256": "a" * 64,
    }

    result = await handle_local_scan_job(_job(payload), db)

    assert result == JobFailure.deterministic("local_scan_scope_invalid")
    assert result.kind is FailureKind.DETERMINISTIC


@pytest.mark.asyncio
async def test_file_scan_executes_disposable_copy_and_finishes_queue_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage_root = tmp_path / "uploads"
    source_dir = storage_root / "dept-a" / "scan-a"
    source_dir.mkdir(parents=True)
    source = source_dir / "document.pdf"
    content = b"verified input"
    source.write_bytes(content)
    scan = SimpleNamespace(
        id="scan-a",
        department_id="dept-a",
        user_id="user-a",
        file_name="document.pdf",
        storage_path=str(source),
        status="PROCESSING",
    )
    db = _db_for(scan, [None, SimpleNamespace(scan_id="scan-a")])
    assert_owned = AsyncMock()
    payload = {
        "scan_kind": "local_pdf",
        "scan_id": "scan-a",
        "options": {"generate_alt_text": False, "enhance_descriptions": True},
        "input_sha256": hashlib.sha256(content).hexdigest(),
    }
    job = _job(payload)
    job._assert_owned = assert_owned
    observed: dict[str, object] = {}

    async def complete(**kwargs):
        observed["work_path"] = kwargs["work_path"]
        observed["loaded"] = Path(kwargs["work_path"]).read_bytes()
        scan.status = "COMPLETED"

    monkeypatch.setattr("src.jobs.local_scan_job.UPLOAD_BASE_DIR", storage_root)
    monkeypatch.setattr(
        "src.jobs.local_scan_subprocess.run_local_scan_subprocess", complete
    )

    result = await handle_local_scan_job(job, db)

    assert result == JobSuccess({"success": True, "scan_id": "scan-a"})
    assert result.handler_committed is False
    assert observed["loaded"] == content
    assert not Path(observed["work_path"]).exists()
    assert source.read_bytes() == content
    assert assert_owned.await_count == 2


@pytest.mark.asyncio
async def test_child_response_loss_reloads_and_preserves_committed_scan_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processing = SimpleNamespace(
        id="scan-a", department_id="dept-a", status="PROCESSING"
    )
    completed = SimpleNamespace(id="scan-a", department_id="dept-a", status="COMPLETED")
    committed_result = SimpleNamespace(scan_id="scan-a")
    db = MagicMock()
    db.get.side_effect = [processing, completed]
    db.query.return_value.filter.return_value.first.side_effect = [
        None,
        committed_result,
    ]
    payload = {
        "scan_kind": "local_web",
        "scan_id": "scan-a",
        "options": {
            "url": "https://example.edu",
            "mode": "quick",
            "scan_images": False,
            "scan_multimedia": False,
            "scan_math": False,
            "validate_alt_text": False,
            "max_depth": 1,
            "max_pages": 10,
            "generate_code_fixes": True,
            "capture_screenshots": True,
        },
    }
    job = _job(payload)
    job.id = "job-a"
    job.claim_token = "claim-a"
    job.worker_id = "worker-a"
    job.attempt_count = 1
    job.max_retries = 1
    monkeypatch.setattr(
        "src.jobs.local_scan_subprocess.run_local_scan_subprocess",
        AsyncMock(side_effect=RuntimeError("response transport lost")),
    )
    monkeypatch.setattr(
        "src.utils.security.validate_url_not_private",
        MagicMock(return_value="https://example.edu"),
    )

    result = await handle_local_scan_job(job, db)

    assert result == JobSuccess({"success": True, "scan_id": "scan-a"})
    db.rollback.assert_called_once_with()
    db.expire_all.assert_called_once_with()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_worker_revalidates_web_target_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scan = SimpleNamespace(id="scan-a", department_id="dept-a", status="PROCESSING")
    db = _db_for(scan, [None])
    payload = {
        "scan_kind": "local_web",
        "scan_id": "scan-a",
        "options": {
            "url": "https://example.edu",
            "mode": "quick",
            "scan_images": False,
            "scan_multimedia": False,
            "scan_math": False,
            "validate_alt_text": False,
            "max_depth": 1,
            "max_pages": 10,
            "generate_code_fixes": True,
            "capture_screenshots": True,
        },
    }
    validate = MagicMock(side_effect=ValueError("private address"))
    execute = AsyncMock()
    monkeypatch.setattr("src.utils.security.validate_url_not_private", validate)
    monkeypatch.setattr(
        "src.jobs.local_scan_subprocess.run_local_scan_subprocess", execute
    )

    result = await handle_local_scan_job(_job(payload), db)

    assert result == JobFailure.deterministic("local_scan_url_invalid")
    validate.assert_called_once_with("https://example.edu")
    execute.assert_not_awaited()
    assert scan.status == "PROCESSING"
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_scan_registry_dispatches_local_job_without_cloud_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.jobs.cloud_scan_job import handle_scan_job

    expected = JobSuccess({"success": True, "scan_id": "scan-a"})
    local_handler = AsyncMock(return_value=expected)
    monkeypatch.setattr("src.jobs.local_scan_job.handle_local_scan_job", local_handler)
    job = _job(
        {
            "scan_kind": "local_pdf",
            "scan_id": "scan-a",
            "options": {
                "generate_alt_text": False,
                "enhance_descriptions": True,
            },
            "input_sha256": "a" * 64,
        }
    )
    db = MagicMock()

    result = await handle_scan_job(job, db, MagicMock())

    assert result == expected
    local_handler.assert_awaited_once_with(job, db)
    db.query.assert_not_called()


def test_long_running_route_launchers_are_fully_removed() -> None:
    root = Path(__file__).parents[1] / "src" / "api" / "education"
    sources = "\n".join(
        (root / name).read_text()
        for name in ("scan_routes.py", "web_scan_routes.py", "multimedia_routes.py")
    )
    assert "background_tasks.add_task(" not in sources
    assert "multiprocessing.get_context(" not in sources
    assert "BackgroundTasks" not in sources
    assert "enqueue_local_scan_job(" in sources


def test_transcription_route_delegates_to_durable_multimedia_scan() -> None:
    import ast

    source = (
        Path(__file__).parents[1] / "src" / "api" / "education" / "multimedia_routes.py"
    ).read_text()
    tree = ast.parse(source)
    route = next(
        node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == "transcribe_multimedia"
    )
    calls = {
        node.func.id
        for node in ast.walk(route)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "scan_multimedia" in calls
    assert "MultimediaProcessor" not in calls


def test_single_worker_durability_guard_is_removed_from_runtime_and_docs() -> None:
    root = Path(__file__).parents[1]
    sources = "\n".join(
        (root / path).read_text()
        for path in (
            "Dockerfile",
            "entrypoint.sh",
            "docker-compose.prod.yml",
            "docs/deployment/self-hosting.md",
            "docs/development/onboarding.md",
        )
    )
    assert "job-claim race" not in sources
    assert "BackgroundTasks directly" not in sources
    assert "UVICORN_WORKERS:-1" not in sources
    assert "UVICORN_WORKERS: 1" not in sources
