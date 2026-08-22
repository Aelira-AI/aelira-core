"""Task17B bounded artifact maintenance and orphan quarantine tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _artifact(storage_key: str):
    return SimpleNamespace(
        storage_key=storage_key,
        lifecycle_status="available",
        publication_token=None,
        cleanup_claimed_at=None,
    )


def _db_with_artifacts(artifacts):
    key_query = MagicMock()
    key_query.filter.return_value = key_query
    key_query.first.return_value = artifacts[0] if artifacts else None
    purge_query = MagicMock()
    purge_query.filter.return_value = purge_query
    purge_query.order_by.return_value = purge_query
    purge_query.limit.return_value = purge_query
    purge_query.with_for_update.return_value = purge_query
    purge_query.all.return_value = [
        item for item in artifacts if getattr(item, "status", None) == "reviewed"
    ]
    db = MagicMock()
    db.query.side_effect = lambda model: (
        purge_query
        if getattr(model, "__name__", "") == "ArtifactOrphanQuarantine"
        else key_query
    )
    db.get.return_value = None
    return db


def test_orphan_scanner_quarantines_only_old_regular_expected_layout(tmp_path):
    from src.db.models import ArtifactOrphanQuarantine
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    department = "11111111-1111-4111-8111-111111111111"
    scan = "22222222-2222-4222-8222-222222222222"
    artifact = "33333333-3333-4333-8333-333333333333"
    known_leaf = "44444444-4444-4444-8444-444444444444.pdf"
    unknown_leaf = "55555555-5555-4555-8555-555555555555.pdf"
    directory = tmp_path / department / scan / artifact
    directory.mkdir(parents=True)
    known = directory / known_leaf
    unknown = directory / unknown_leaf
    symlink = directory / "66666666-6666-4666-8666-666666666666.pdf"
    known.write_bytes(b"known")
    unknown.write_bytes(b"orphan")
    symlink.symlink_to(unknown_leaf)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    os.utime(known, (old, old))
    os.utime(unknown, (old, old))

    db = _db_with_artifacts([_artifact(f"{department}/{scan}/{artifact}/{known_leaf}")])
    scanner = ArtifactOrphanScanner(
        root=tmp_path, batch_size=10, grace_seconds=300, retention_days=7
    )

    result = scanner.run_batch(db, now=datetime.now(timezone.utc))

    assert result["quarantined"] == 1, result
    assert result["ignored_special"] == 1
    assert result["failed"] == 0
    assert result["complete"] is True
    assert known.read_bytes() == b"known"
    assert symlink.is_symlink()
    assert not unknown.exists()
    row = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], ArtifactOrphanQuarantine)
    )
    assert row.original_key.endswith(unknown_leaf)
    assert row.quarantine_key.startswith(".quarantine/")
    assert row.size_bytes == len(b"orphan")
    assert row.kind == "regular_file"
    assert row.status == "quarantined"
    assert row.intent_token
    assert row.source_mtime_ns > 0
    assert (tmp_path / row.quarantine_key).read_bytes() == b"orphan"


def test_orphan_scanner_treats_live_partial_as_known(tmp_path):
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    department = "11111111-1111-4111-8111-111111111111"
    scan = "22222222-2222-4222-8222-222222222222"
    artifact = "33333333-3333-4333-8333-333333333333"
    leaf = "44444444-4444-4444-8444-444444444444.pdf"
    directory = tmp_path / department / scan / artifact
    directory.mkdir(parents=True)
    partial = directory / f"{leaf}.partial"
    partial.write_bytes(b"publishing")
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    os.utime(partial, (old, old))
    live = _artifact(f"{department}/{scan}/{artifact}/{leaf}")
    live.lifecycle_status = "staging"
    live.publication_token = "a" * 64
    db = _db_with_artifacts([live])

    result = ArtifactOrphanScanner(
        root=tmp_path, batch_size=10, grace_seconds=300, retention_days=7
    ).run_batch(db, now=datetime.now(timezone.utc))

    assert result["quarantined"] == 0
    assert partial.read_bytes() == b"publishing"


def test_orphan_scan_sorted_snapshot_covers_low_names_after_high_names_across_ticks(
    tmp_path, monkeypatch
):
    from src.db.models import ArtifactOrphanQuarantine, MaintenanceCursor
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    department = "11111111-1111-4111-8111-111111111111"
    scan = "22222222-2222-4222-8222-222222222222"
    artifact = "33333333-3333-4333-8333-333333333333"
    leaves = [
        "aaaaaaaa-4444-4444-8444-444444444444.pdf",
        "11111111-4444-4444-8444-444444444444.pdf",
    ]
    directory = tmp_path / department / scan / artifact
    directory.mkdir(parents=True)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    for leaf in leaves:
        path = directory / leaf
        path.write_bytes(leaf.encode())
        os.utime(path, (old, old))

    class ReverseEntries:
        def __init__(self, directory_fd):
            with os.scandir(directory_fd) as entries:
                self.items = sorted(
                    list(entries), key=lambda item: item.name, reverse=True
                )

        def __enter__(self):
            return iter(self.items)

        def __exit__(self, *_args):
            return None

    cursor = MaintenanceCursor(key="artifact_orphan_scan", cursor_json={})
    db = _db_with_artifacts([])
    db.get.side_effect = lambda model, key: (
        cursor if model is MaintenanceCursor else None
    )
    scanner = ArtifactOrphanScanner(
        root=tmp_path,
        batch_size=1,
        grace_seconds=300,
        retention_days=7,
        max_visited_entries=6,
        max_visited_directories=3,
        max_directory_entries=4,
    )
    monkeypatch.setattr(scanner, "_entries", ReverseEntries)

    results = [scanner.run_batch(db) for _ in range(12)]
    quarantined = {
        call.args[0].original_key
        for call in db.add.call_args_list
        if isinstance(call.args[0], ArtifactOrphanQuarantine)
    }

    assert quarantined == {f"{department}/{scan}/{artifact}/{leaf}" for leaf in leaves}
    assert all(result["visited_entries"] <= 6 for result in results)
    assert all(result["visited_directories"] <= 3 for result in results)


def test_orphan_directory_overflow_fails_closed_without_quarantine(tmp_path):
    from src.db.models import ArtifactOrphanQuarantine, MaintenanceCursor
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    for index in range(4):
        (tmp_path / f"{index + 1:08x}-1111-4111-8111-111111111111").mkdir()
    cursor = MaintenanceCursor(key="artifact_orphan_scan", cursor_json={})
    db = _db_with_artifacts([])
    db.get.side_effect = lambda model, key: (
        cursor if model is MaintenanceCursor else None
    )

    result = ArtifactOrphanScanner(
        root=tmp_path,
        batch_size=10,
        grace_seconds=300,
        retention_days=7,
        max_visited_entries=10,
        max_visited_directories=3,
        max_directory_entries=3,
    ).run_batch(db)

    assert result["overflow_manual"] == 1
    assert result["complete"] is False
    assert all(
        path.is_dir() for path in tmp_path.iterdir() if path.name != ".quarantine"
    )
    assert not any(
        isinstance(call.args[0], ArtifactOrphanQuarantine)
        for call in db.add.call_args_list
    )


def test_orphan_snapshot_mutation_restarts_before_considering_stale_names(tmp_path):
    from src.db.models import MaintenanceCursor
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    department = "11111111-1111-4111-8111-111111111111"
    scan = "22222222-2222-4222-8222-222222222222"
    artifact = "33333333-3333-4333-8333-333333333333"
    relative = f"{department}/{scan}/{artifact}"
    directory = tmp_path / relative
    directory.mkdir(parents=True)
    old_leaf = "44444444-4444-4444-8444-444444444444.pdf"
    old_path = directory / old_leaf
    old_path.write_bytes(b"old")
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    os.utime(old_path, (old, old))
    before = directory.stat()
    cursor = MaintenanceCursor(
        key="artifact_orphan_scan",
        cursor_json={
            "generation": 1,
            "queue": [],
            "active": {
                "relative": relative,
                "names": [old_leaf],
                "index": 0,
                "signature": [before.st_dev, before.st_ino, before.st_mtime_ns],
            },
        },
    )
    fresh_leaf = "55555555-5555-4555-8555-555555555555.pdf"
    fresh_path = directory / fresh_leaf
    fresh_path.write_bytes(b"fresh mutation")
    db = _db_with_artifacts([])
    db.get.side_effect = lambda model, key: (
        cursor if model is MaintenanceCursor else None
    )

    result = ArtifactOrphanScanner(
        root=tmp_path,
        batch_size=10,
        grace_seconds=300,
        retention_days=7,
        max_visited_entries=10,
        max_visited_directories=3,
        max_directory_entries=4,
    ).run_batch(db)

    assert result["mutation_restarts"] >= 1
    assert fresh_path.read_bytes() == b"fresh mutation"


def test_orphan_quarantine_transfer_uses_atomic_noreplace_rename():
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    source = inspect.getsource(ArtifactOrphanScanner._consider_file)
    assert "_rename_noreplace" in source
    assert "os.link(" not in source
    assert "os.unlink(name" not in source


def _orphan_candidate(tmp_path):
    department = "11111111-1111-4111-8111-111111111111"
    scan = "22222222-2222-4222-8222-222222222222"
    artifact = "33333333-3333-4333-8333-333333333333"
    leaf = "55555555-5555-4555-8555-555555555555.pdf"
    relative = f"{department}/{scan}/{artifact}"
    directory = tmp_path / relative
    directory.mkdir(parents=True)
    path = directory / leaf
    path.write_bytes(b"durable orphan")
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    os.utime(path, (old, old))
    return directory, path, f"{relative}/{leaf}"


def _captured_intent(db):
    from src.db.models import ArtifactOrphanQuarantine

    return next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], ArtifactOrphanQuarantine)
    )


def test_quarantine_crash_before_move_leaves_durable_pending_intent(
    tmp_path, monkeypatch
):
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    _directory, path, _key = _orphan_candidate(tmp_path)
    db = _db_with_artifacts([])
    scanner = ArtifactOrphanScanner(
        root=tmp_path, batch_size=10, grace_seconds=300, retention_days=7
    )

    def crash(_row):
        assert db.commit.call_count >= 1
        assert path.exists()
        raise SystemExit("crash before move")

    monkeypatch.setattr(scanner, "_after_intent_commit", crash)
    with pytest.raises(SystemExit, match="crash before move"):
        scanner.run_batch(db)

    row = _captured_intent(db)
    assert row.status == "pending_move"
    assert path.read_bytes() == b"durable orphan"
    assert not (tmp_path / row.quarantine_key).exists()


def test_quarantine_crash_after_move_is_recovered_and_finalized(tmp_path, monkeypatch):
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    _directory, path, _key = _orphan_candidate(tmp_path)
    db = _db_with_artifacts([])
    scanner = ArtifactOrphanScanner(
        root=tmp_path, batch_size=10, grace_seconds=300, retention_days=7
    )

    def crash(_row):
        raise SystemExit("crash after move")

    monkeypatch.setattr(scanner, "_after_move", crash)
    with pytest.raises(SystemExit, match="crash after move"):
        scanner.run_batch(db)

    row = _captured_intent(db)
    assert row.status == "pending_move"
    assert not path.exists()
    assert (tmp_path / row.quarantine_key).read_bytes() == b"durable orphan"

    db.get.side_effect = lambda model, key, **_kwargs: row
    monkeypatch.setattr(scanner, "_after_move", lambda _row: None)
    result = scanner.recover_pending(db)

    assert result["finalized"] == 1
    assert row.status == "quarantined"
    assert row.quarantined_at is not None


def test_quarantine_intent_commit_failure_never_moves_file(tmp_path):
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    _directory, path, _key = _orphan_candidate(tmp_path)
    db = _db_with_artifacts([])
    db.commit.side_effect = RuntimeError("database unavailable")

    result = ArtifactOrphanScanner(
        root=tmp_path, batch_size=10, grace_seconds=300, retention_days=7
    ).run_batch(db)

    assert result["failed"] == 1
    assert path.read_bytes() == b"durable orphan"
    assert not any((tmp_path / ".quarantine").iterdir())


@pytest.mark.parametrize(
    ("original", "target", "tamper", "expected_error"),
    [
        (False, False, None, "missing"),
        (True, True, None, "both_exist"),
        (True, False, b"replacement", "metadata_mismatch"),
    ],
)
def test_pending_intent_unsafe_states_require_restore_without_move(
    tmp_path, original, target, tamper, expected_error
):
    from src.db.models import ArtifactOrphanQuarantine
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    _directory, path, key = _orphan_candidate(tmp_path)
    state = path.stat()
    row = ArtifactOrphanQuarantine(
        id="77777777-7777-4777-8777-777777777777",
        intent_token="a" * 32,
        original_key=key,
        quarantine_key=(".quarantine/77777777-7777-4777-8777-777777777777." + "a" * 32),
        size_bytes=state.st_size,
        source_mtime=datetime.fromtimestamp(state.st_mtime, timezone.utc),
        source_mtime_ns=state.st_mtime_ns,
        source_device=state.st_dev,
        source_inode=state.st_ino,
        kind="regular_file",
        status="pending_move",
        reason="not_in_canonical_database_snapshot",
    )
    quarantine = tmp_path / ".quarantine"
    quarantine.mkdir()
    if target:
        (tmp_path / row.quarantine_key).write_bytes(path.read_bytes())
    if not original:
        path.unlink()
    elif tamper is not None:
        path.write_bytes(tamper)
    db = _db_with_artifacts([])
    db.get.side_effect = lambda model, row_id, **_kwargs: row
    pending_query = MagicMock()
    pending_query.filter.return_value = pending_query
    pending_query.order_by.return_value = pending_query
    pending_query.limit.return_value = pending_query
    pending_query.with_for_update.return_value = pending_query
    pending_query.all.return_value = [row]
    db.query.side_effect = None
    db.query.return_value = pending_query

    result = ArtifactOrphanScanner(
        root=tmp_path, batch_size=10, grace_seconds=300, retention_days=7
    ).recover_pending(db)

    assert result["restore_required"] == 1
    assert row.status == "restore_required"
    assert row.recovery_error == expected_error
    if original:
        assert path.exists()


def test_legacy_untracked_quarantine_entry_gets_bounded_restore_row(tmp_path):
    from src.db.models import ArtifactOrphanQuarantine
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    quarantine = tmp_path / ".quarantine"
    quarantine.mkdir()
    legacy = quarantine / "88888888-8888-4888-8888-888888888888"
    legacy.write_bytes(b"legacy")
    db = _db_with_artifacts([])

    result = ArtifactOrphanScanner(
        root=tmp_path, batch_size=1, grace_seconds=300, retention_days=7
    ).recover_pending(db)

    rows = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], ArtifactOrphanQuarantine)
    ]
    assert result["intaken"] == 1
    assert len(rows) == 1
    assert rows[0].status == "restore_required"
    assert rows[0].quarantine_key == ".quarantine/" + legacy.name
    assert legacy.read_bytes() == b"legacy"


def test_orphan_purge_requires_review_and_retention_age(tmp_path):
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    scanner = ArtifactOrphanScanner(
        root=tmp_path, batch_size=10, grace_seconds=300, retention_days=7
    )
    quarantine = tmp_path / ".quarantine"
    quarantine.mkdir()
    old_key = ".quarantine/11111111-1111-4111-8111-111111111111"
    pending_key = ".quarantine/22222222-2222-4222-8222-222222222222"
    (tmp_path / old_key).write_bytes(b"old")
    (tmp_path / pending_key).write_bytes(b"pending")
    old_state = (tmp_path / old_key).stat()
    now = datetime.now(timezone.utc)
    reviewed = SimpleNamespace(
        id="11111111-1111-4111-8111-111111111111",
        intent_token=None,
        quarantine_key=old_key,
        size_bytes=old_state.st_size,
        source_mtime_ns=old_state.st_mtime_ns,
        source_device=old_state.st_dev,
        source_inode=old_state.st_ino,
        status="reviewed",
        reviewed_at=now - timedelta(days=8),
        reviewed_by="operator@example.test",
        quarantined_at=now - timedelta(days=8),
        purge_claimed_at=None,
        purge_token=None,
        purged_at=None,
        recovery_error=None,
    )
    pending = SimpleNamespace(
        quarantine_key=pending_key,
        status="pending_review",
        reviewed_at=None,
        quarantined_at=now - timedelta(days=30),
        purged_at=None,
    )
    db = _db_with_artifacts([reviewed, pending])

    result = scanner.purge_reviewed(db, now=now)

    assert result == {"purged": 1, "failed": 0, "restore_required": 0}
    assert reviewed.status == "purged"
    assert not (tmp_path / old_key).exists()
    assert (tmp_path / pending_key).read_bytes() == b"pending"


def _reviewed_purge_row(tmp_path, *, row_id="11111111-1111-4111-8111-111111111111"):
    token = "b" * 32
    key = f".quarantine/{row_id}.{token}"
    path = tmp_path / key
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"reviewed orphan")
    state = path.stat()
    old = datetime.now(timezone.utc) - timedelta(days=8)
    return SimpleNamespace(
        id=row_id,
        intent_token=token,
        original_key=(
            "11111111-1111-4111-8111-111111111111/"
            "22222222-2222-4222-8222-222222222222/"
            "33333333-3333-4333-8333-333333333333/"
            "44444444-4444-4444-8444-444444444444.pdf"
        ),
        quarantine_key=key,
        size_bytes=state.st_size,
        source_mtime_ns=state.st_mtime_ns,
        source_device=state.st_dev,
        source_inode=state.st_ino,
        status="reviewed",
        reviewed_at=old,
        reviewed_by="operator@example.test",
        quarantined_at=old,
        purge_claimed_at=None,
        purge_token=None,
        purged_at=None,
        recovery_error=None,
    )


def _purge_db(row):
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.with_for_update.return_value = query
    query.all.return_value = [row]
    db = MagicMock()
    db.query.return_value = query
    return db


def test_purge_crash_before_unlink_leaves_durable_purging_intent(tmp_path, monkeypatch):
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    row = _reviewed_purge_row(tmp_path)
    db = _purge_db(row)
    scanner = ArtifactOrphanScanner(
        root=tmp_path, batch_size=10, grace_seconds=300, retention_days=7
    )

    def crash(claimed):
        assert claimed.status == "purging"
        assert claimed.purge_claimed_at is not None
        assert claimed.purge_token
        assert db.commit.call_count == 1
        assert (tmp_path / claimed.quarantine_key).exists()
        raise SystemExit("crash before unlink")

    monkeypatch.setattr(scanner, "_after_purge_claim_commit", crash)
    with pytest.raises(SystemExit, match="crash before unlink"):
        scanner.purge_reviewed(db)

    assert row.status == "purging"
    assert (tmp_path / row.quarantine_key).read_bytes() == b"reviewed orphan"


def test_purge_crash_after_unlink_recovers_missing_file_as_purged(
    tmp_path, monkeypatch
):
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    row = _reviewed_purge_row(tmp_path)
    db = _purge_db(row)
    scanner = ArtifactOrphanScanner(
        root=tmp_path, batch_size=10, grace_seconds=300, retention_days=7
    )

    def crash(claimed):
        assert claimed.status == "purging"
        raise SystemExit("crash after unlink")

    monkeypatch.setattr(scanner, "_after_purge_unlink", crash)
    with pytest.raises(SystemExit, match="crash after unlink"):
        scanner.purge_reviewed(db)

    assert row.status == "purging"
    assert not (tmp_path / row.quarantine_key).exists()

    monkeypatch.setattr(scanner, "_after_purge_unlink", lambda _row: None)
    result = scanner.purge_reviewed(db)

    assert result == {"purged": 1, "failed": 0, "restore_required": 0}
    assert row.status == "purged"
    assert row.purged_at is not None


def test_purge_finalize_failure_is_retryable_after_unlink(tmp_path):
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    row = _reviewed_purge_row(tmp_path)
    db = _purge_db(row)
    db.commit.side_effect = [None, RuntimeError("finalize unavailable"), None]
    scanner = ArtifactOrphanScanner(
        root=tmp_path, batch_size=10, grace_seconds=300, retention_days=7
    )

    first = scanner.purge_reviewed(db)
    assert first["failed"] == 1
    assert row.status == "purging"
    assert not (tmp_path / row.quarantine_key).exists()

    second = scanner.purge_reviewed(db)
    assert second["purged"] == 1
    assert row.status == "purged"


def test_purge_metadata_mismatch_requires_restore_without_delete(tmp_path):
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    row = _reviewed_purge_row(tmp_path)
    path = tmp_path / row.quarantine_key
    path.write_bytes(b"replacement")
    db = _purge_db(row)

    result = ArtifactOrphanScanner(
        root=tmp_path, batch_size=10, grace_seconds=300, retention_days=7
    ).purge_reviewed(db)

    assert result["restore_required"] == 1
    assert row.status == "restore_required"
    assert row.recovery_error == "metadata_mismatch"
    assert path.read_bytes() == b"replacement"


def test_purge_symlink_requires_restore_without_following_or_deleting_target(tmp_path):
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    row = _reviewed_purge_row(tmp_path)
    path = tmp_path / row.quarantine_key
    path.unlink()
    protected = tmp_path / "protected"
    protected.write_bytes(b"keep")
    path.symlink_to(protected)
    db = _purge_db(row)

    result = ArtifactOrphanScanner(
        root=tmp_path, batch_size=10, grace_seconds=300, retention_days=7
    ).purge_reviewed(db)

    assert result["restore_required"] == 1
    assert row.status == "restore_required"
    assert row.recovery_error == "unsafe_file_type"
    assert path.is_symlink()
    assert protected.read_bytes() == b"keep"


def test_purge_claim_commit_failure_never_unlinks_reviewed_file(tmp_path):
    from src.services.artifact_orphan_quarantine import ArtifactOrphanScanner

    row = _reviewed_purge_row(tmp_path)
    db = _purge_db(row)
    db.commit.side_effect = RuntimeError("database unavailable")

    result = ArtifactOrphanScanner(
        root=tmp_path, batch_size=10, grace_seconds=300, retention_days=7
    ).purge_reviewed(db)

    assert result["failed"] == 1
    assert (tmp_path / row.quarantine_key).read_bytes() == b"reviewed orphan"


def test_maintenance_singleton_skips_when_advisory_lock_is_held():
    from src.services.durable_maintenance import DurableMaintenanceRunner

    db = MagicMock()
    db.execute.return_value.scalar_one.return_value = False
    cleanup = MagicMock()
    scanner = MagicMock()

    result = DurableMaintenanceRunner(cleanup=cleanup, orphan_scanner=scanner).run_once(
        db
    )

    assert result == {"acquired": False}
    cleanup.run_batch.assert_not_called()
    scanner.run_batch.assert_not_called()


def test_worker_schedules_bounded_artifact_maintenance():
    from src.jobs import worker

    source = inspect.getsource(worker.run_worker)
    assert "run_maintenance_loop" in source
    assert "RemediationArtifactCleanup" in inspect.getsource(
        worker.run_maintenance_loop
    )


def test_global_operator_worker_status_includes_maintenance_counts():
    from src.api.job_worker_routes import worker_status
    from src.auth.dependencies import AuthenticatedPrincipal
    from src.db.models import UserRole

    db = MagicMock()
    db.query.return_value.group_by.return_value = []
    db.query.return_value.filter.return_value.scalar.return_value = 0
    db.query.return_value.scalar.return_value = None
    principal = AuthenticatedPrincipal(
        api_key=None,
        user_id="admin-1",
        department_id="dept-1",
        user_role=UserRole.SUPER_ADMIN,
        auth_method="session",
        lti_course_id=None,
        lti_staff_role=None,
        lti_account_wide=False,
    )

    result = worker_status(principal=principal, db=db)

    assert result["maintenance"] == {"artifact_cleanup_due": 0}
    assert result["reconciliation"] == {
        "required": 0,
        "manual_required": 0,
        "failed_manual": 0,
    }
    assert result["orphans"] == {
        "pending_move": 0,
        "quarantined": 0,
        "restore_required": 0,
        "reviewed": 0,
        "purging": 0,
    }
