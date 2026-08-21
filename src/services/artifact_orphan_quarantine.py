"""Descriptor-confined orphan discovery, quarantine, and reviewed purge."""

from __future__ import annotations

import ctypes
from datetime import datetime, timedelta, timezone
import errno
import logging
import os
from pathlib import Path, PurePosixPath
import stat
import time
import uuid
from typing import Any

from src.db.models import (
    ArtifactOrphanQuarantine,
    MaintenanceCursor,
    RemediationArtifact,
)

logger = logging.getLogger(__name__)
_DIRECTORY_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_ALLOWED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".tex",
    ".html",
    ".htm",
    ".png",
    ".jpg",
    ".jpeg",
}


def _rename_noreplace(
    source_fd: int, source_name: str, destination_fd: int, destination_name: str
) -> None:
    """Atomically rename within open directories without replacing a target."""
    libc = ctypes.CDLL(None, use_errno=True)
    source = os.fsencode(source_name)
    destination = os.fsencode(destination_name)
    if hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(source_fd, source, destination_fd, destination, 1)
    elif hasattr(libc, "renameatx_np"):
        rename = libc.renameatx_np
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(source_fd, source, destination_fd, destination, 0x00000004)
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), source_name)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _canonical_uuid(value: str) -> bool:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        return False
    return str(parsed) == value.lower() and parsed.variant == uuid.RFC_4122


def _valid_storage_key(value: Any) -> bool:
    if not isinstance(value, str) or "\\" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 4 or ".." in path.parts:
        return False
    if not all(_canonical_uuid(part) for part in path.parts[:3]):
        return False
    leaf = PurePosixPath(path.parts[-1])
    return _canonical_uuid(leaf.stem) and leaf.suffix.lower() in _ALLOWED_EXTENSIONS


class ArtifactOrphanScanner:
    """Scan only the canonical four-component artifact layout."""

    def __init__(
        self,
        *,
        root: str | Path,
        batch_size: int,
        grace_seconds: int,
        retention_days: int,
        max_visited_entries: int | None = None,
        max_visited_directories: int | None = None,
        max_directory_entries: int | None = None,
        max_seconds: float = 5.0,
    ) -> None:
        self.root = Path(root)
        if (
            not self.root.is_absolute()
            or ".." in self.root.parts
            or min(batch_size, grace_seconds, retention_days) < 1
        ):
            raise ValueError("orphan scanner bounds are invalid")
        self.batch_size = batch_size
        self.grace_seconds = grace_seconds
        self.retention_days = retention_days
        self.max_visited_entries = max_visited_entries or max(100, batch_size * 20)
        self.max_visited_directories = max_visited_directories or max(
            25, batch_size * 5
        )
        self.max_directory_entries = max_directory_entries or min(
            self.max_visited_entries - 1, max(25, batch_size * 5)
        )
        self.max_seconds = max_seconds
        if (
            min(
                self.max_visited_entries,
                self.max_visited_directories,
                self.max_directory_entries,
                max_seconds,
            )
            <= 0
            or self.max_directory_entries >= self.max_visited_entries
        ):
            raise ValueError("orphan scanner visit bounds are invalid")

    def _is_known_key(self, db: Any, key: str) -> bool:
        """Query only the indexed storage key for one filesystem candidate."""
        canonical = key.removesuffix(".partial")
        row = (
            db.query(RemediationArtifact.storage_key)
            .filter(RemediationArtifact.storage_key == canonical)
            .first()
        )
        if row is None:
            return False
        value = getattr(row, "storage_key", None)
        if value is None and isinstance(row, tuple) and row:
            value = row[0]
        return value == canonical

    @staticmethod
    def _open_child(parent_fd: int, name: str) -> int | None:
        try:
            return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except OSError:
            logger.warning("Ignoring unsafe artifact directory entry")
            return None

    @staticmethod
    def _entries(directory_fd: int) -> Any:
        return os.scandir(directory_fd)

    def _budget_exhausted(self, result: dict[str, Any]) -> bool:
        return (
            result["visited_entries"] + result["processed_entries"]
            >= self.max_visited_entries
            or result["visited_directories"] >= self.max_visited_directories
            or time.monotonic() >= self._deadline
        )

    def _open_relative(self, root_fd: int, relative: str) -> int | None:
        current = os.dup(root_fd)
        if not relative:
            return current
        try:
            for component in PurePosixPath(relative).parts:
                if not _canonical_uuid(component):
                    return None
                child = self._open_child(current, component)
                os.close(current)
                current = -1
                if child is None:
                    return None
                current = child
            return current
        finally:
            if (
                current >= 0
                and relative
                and not all(
                    _canonical_uuid(part) for part in PurePosixPath(relative).parts
                )
            ):
                os.close(current)

    @staticmethod
    def _directory_signature(directory_fd: int) -> tuple[int, int, int]:
        state = os.fstat(directory_fd)
        return state.st_dev, state.st_ino, state.st_mtime_ns

    def _snapshot_directory(
        self, directory_fd: int, result: dict[str, Any]
    ) -> tuple[list[str] | None, bool]:
        """Return one bounded sorted snapshot; never retain a partial enumeration."""
        remaining = self.max_visited_entries - (
            result["visited_entries"] + result["processed_entries"]
        )
        if remaining < self.max_directory_entries + 1:
            return None, False
        names: list[str] = []
        with self._entries(directory_fd) as entries:
            for entry in entries:
                if time.monotonic() >= self._deadline:
                    return None, False
                result["visited_entries"] += 1
                names.append(entry.name)
                if len(names) > self.max_directory_entries:
                    return None, True
        names.sort()
        return names, False

    def run_batch(self, db: Any, *, now: datetime | None = None) -> dict[str, Any]:
        now = _utc(now or datetime.now(timezone.utc))
        cutoff = now - timedelta(seconds=self.grace_seconds)
        result: dict[str, Any] = {
            "quarantined": 0,
            "ignored_special": 0,
            "failed": 0,
            "visited_entries": 0,
            "processed_entries": 0,
            "visited_directories": 0,
            "overflow_manual": 0,
            "mutation_restarts": 0,
            "complete": True,
        }
        cursor = db.get(MaintenanceCursor, "artifact_orphan_scan")
        if not isinstance(cursor, MaintenanceCursor):
            cursor = MaintenanceCursor(key="artifact_orphan_scan", cursor_json={})
            db.add(cursor)
            db.flush()
        cursor_data = cursor.cursor_json if isinstance(cursor.cursor_json, dict) else {}
        self._deadline = time.monotonic() + self.max_seconds
        self._stopped = False
        self._database_failed = False
        generation = int(cursor_data.get("generation") or 1)
        queue = [
            value for value in cursor_data.get("queue", [""]) if isinstance(value, str)
        ]
        if not queue and not isinstance(cursor_data.get("active"), dict):
            queue = [""]
        active = cursor_data.get("active")
        active = dict(active) if isinstance(active, dict) else None
        overflow_paths: list[str] = []
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_state = self.root.lstat()
        if stat.S_ISLNK(root_state.st_mode) or not stat.S_ISDIR(root_state.st_mode):
            raise ValueError("artifact root must be a nonsymlink directory")
        root_fd = os.open(self.root, _DIRECTORY_FLAGS)
        quarantine_fd = -1
        try:
            opened = os.fstat(root_fd)
            if (opened.st_dev, opened.st_ino) != (root_state.st_dev, root_state.st_ino):
                raise ValueError("artifact root changed while opening")
            try:
                os.mkdir(".quarantine", 0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            quarantine_fd = os.open(".quarantine", _DIRECTORY_FLAGS, dir_fd=root_fd)
            while active is not None or queue:
                if self._budget_exhausted(result):
                    self._stopped = True
                    break
                if active is None:
                    relative = queue.pop(0)
                    directory_fd = self._open_relative(root_fd, relative)
                    if directory_fd is None:
                        result["ignored_special"] += 1
                        continue
                    result["visited_directories"] += 1
                    try:
                        signature = self._directory_signature(directory_fd)
                        names, overflow = self._snapshot_directory(directory_fd, result)
                    finally:
                        os.close(directory_fd)
                    if overflow:
                        overflow_paths.append(relative)
                        result["overflow_manual"] += 1
                        logger.error(
                            "Artifact orphan directory requires manual review",
                            extra={"relative_directory": relative or "."},
                        )
                        continue
                    if names is None:
                        queue.insert(0, relative)
                        self._stopped = True
                        break
                    active = {
                        "relative": relative,
                        "names": names,
                        "index": 0,
                        "signature": list(signature),
                    }

                relative = str(active["relative"])
                directory_fd = self._open_relative(root_fd, relative)
                if directory_fd is None:
                    active = None
                    result["ignored_special"] += 1
                    continue
                try:
                    if list(self._directory_signature(directory_fd)) != active.get(
                        "signature"
                    ):
                        queue.insert(0, relative)
                        active = None
                        result["mutation_restarts"] += 1
                        continue
                    names = active.get("names")
                    index = int(active.get("index") or 0)
                    if not isinstance(names, list) or index >= len(names):
                        active = None
                        continue
                    name = names[index]
                    active["index"] = index + 1
                    result["processed_entries"] += 1
                    depth = 0 if not relative else len(PurePosixPath(relative).parts)
                    if depth < 3:
                        if depth == 0 and name == ".quarantine":
                            continue
                        if not _canonical_uuid(name):
                            result["ignored_special"] += 1
                            continue
                        child = name if not relative else f"{relative}/{name}"
                        queue.append(child)
                    else:
                        key = f"{relative}/{name}"
                        self._consider_file(
                            db,
                            directory_fd=directory_fd,
                            quarantine_fd=quarantine_fd,
                            name=name,
                            key=key,
                            cutoff=cutoff,
                            now=now,
                            result=result,
                        )
                        if self._stopped or result["quarantined"] >= self.batch_size:
                            self._stopped = True
                            break
                finally:
                    os.close(directory_fd)
        finally:
            if quarantine_fd >= 0:
                os.close(quarantine_fd)
            os.close(root_fd)
        result["complete"] = not self._stopped and not overflow_paths
        if result["complete"]:
            cursor.cursor_json = {"generation": generation + 1, "queue": [""]}
        else:
            cursor.cursor_json = {
                "generation": generation,
                "queue": overflow_paths + queue,
                "active": active,
                "status": "overflow_manual" if overflow_paths else "running",
            }
        try:
            db.commit()
        except Exception:
            db.rollback()
            if not self._database_failed:
                result["failed"] += 1
            result["complete"] = False
        return result

    def _after_intent_commit(self, _row: ArtifactOrphanQuarantine) -> None:
        """Crash-injection seam: the move must always follow a durable intent."""

    def _after_move(self, _row: ArtifactOrphanQuarantine) -> None:
        """Crash-injection seam: the durable intent still describes a moved file."""

    @staticmethod
    def _matches_source(state: os.stat_result, row: ArtifactOrphanQuarantine) -> bool:
        return (
            stat.S_ISREG(state.st_mode)
            and state.st_size == row.size_bytes
            and state.st_mtime_ns == row.source_mtime_ns
            and state.st_dev == row.source_device
            and state.st_ino == row.source_inode
        )

    @staticmethod
    def _safe_target_name(row: ArtifactOrphanQuarantine) -> str | None:
        target = PurePosixPath(row.quarantine_key)
        expected = f"{row.id}.{row.intent_token}"
        if target.parts == (".quarantine", expected):
            return expected
        return None

    def _consider_file(
        self,
        db: Any,
        *,
        directory_fd: int,
        quarantine_fd: int,
        name: str,
        key: str,
        cutoff: datetime,
        now: datetime,
        result: dict[str, Any],
    ) -> None:
        try:
            state = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError:
            result["failed"] += 1
            return
        if not stat.S_ISREG(state.st_mode):
            result["ignored_special"] += 1
            logger.warning("Ignoring symlink or special artifact entry")
            return
        final_name = name.removesuffix(".partial")
        leaf = PurePosixPath(final_name)
        if not (
            _canonical_uuid(leaf.stem) and leaf.suffix.lower() in _ALLOWED_EXTENSIONS
        ):
            result["ignored_special"] += 1
            logger.warning("Ignoring file outside expected artifact layout")
            return
        if (
            self._is_known_key(db, key)
            or datetime.fromtimestamp(state.st_mtime, timezone.utc) > cutoff
        ):
            return
        row_id = str(uuid.uuid4())
        intent_token = uuid.uuid4().hex
        target_name = f"{row_id}.{intent_token}"
        row = ArtifactOrphanQuarantine(
            id=row_id,
            intent_token=intent_token,
            original_key=key,
            quarantine_key=f".quarantine/{target_name}",
            size_bytes=state.st_size,
            source_mtime=datetime.fromtimestamp(state.st_mtime, timezone.utc),
            source_mtime_ns=state.st_mtime_ns,
            source_device=state.st_dev,
            source_inode=state.st_ino,
            kind="regular_file",
            status="pending_move",
            reason="not_in_canonical_database_snapshot",
        )
        try:
            db.add(row)
            db.flush()
            db.commit()
        except Exception:
            db.rollback()
            self._database_failed = True
            self._stopped = True
            result["failed"] += 1
            return

        self._after_intent_commit(row)
        try:
            _rename_noreplace(directory_fd, name, quarantine_fd, target_name)
            moved_state = os.stat(
                target_name, dir_fd=quarantine_fd, follow_symlinks=False
            )
            if (
                not stat.S_ISREG(moved_state.st_mode)
                or moved_state.st_dev != state.st_dev
                or moved_state.st_ino != state.st_ino
                or moved_state.st_size != state.st_size
                or moved_state.st_mtime_ns != state.st_mtime_ns
            ):
                raise OSError("artifact changed during quarantine move")
            os.fsync(quarantine_fd)
            os.fsync(directory_fd)
            self._after_move(row)
            row.status = "quarantined"
            row.quarantined_at = now
            row.recovery_error = None
            db.commit()
            result["quarantined"] += 1
        except Exception:
            db.rollback()
            result["failed"] += 1

    def _mark_restore_required(
        self, db: Any, row: ArtifactOrphanQuarantine, error: str
    ) -> None:
        row.status = "restore_required"
        row.recovery_error = error
        db.commit()

    def recover_pending(
        self, db: Any, *, now: datetime | None = None
    ) -> dict[str, int]:
        """Boundedly reconcile committed move intents and legacy quarantine files."""
        now = _utc(now or datetime.now(timezone.utc))
        result = {
            "finalized": 0,
            "moved": 0,
            "restore_required": 0,
            "intaken": 0,
            "failed": 0,
            "overflow_manual": 0,
        }
        pending = (
            db.query(ArtifactOrphanQuarantine)
            .filter(ArtifactOrphanQuarantine.status == "pending_move")
            .order_by(ArtifactOrphanQuarantine.id)
            .limit(self.batch_size)
            .with_for_update(skip_locked=True)
            .all()
        )
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_fd = os.open(self.root, _DIRECTORY_FLAGS)
        quarantine_fd = -1
        try:
            try:
                os.mkdir(".quarantine", 0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            quarantine_fd = os.open(".quarantine", _DIRECTORY_FLAGS, dir_fd=root_fd)
            quarantine_names = []
            with self._entries(quarantine_fd) as entries:
                for entry in entries:
                    quarantine_names.append(entry.name)
                    if len(quarantine_names) > self.max_directory_entries:
                        quarantine_names = []
                        result["overflow_manual"] = 1
                        logger.error(
                            "Artifact quarantine directory requires manual review"
                        )
                        break
            quarantine_names.sort()
            pending_ids = {row.id for row in pending}
            for name in quarantine_names:
                if len(pending) >= self.batch_size or "." not in name:
                    break
                base = name.split(".", 1)[0]
                try:
                    row_id = str(uuid.UUID(base))
                except ValueError:
                    continue
                tracked = db.get(ArtifactOrphanQuarantine, row_id)
                if (
                    tracked is not None
                    and tracked.status == "pending_move"
                    and tracked.id not in pending_ids
                ):
                    pending.append(tracked)
                    pending_ids.add(tracked.id)
            for row in pending:
                target_name = self._safe_target_name(row)
                key = row.original_key
                if target_name is None or not _valid_storage_key(key):
                    self._mark_restore_required(db, row, "invalid_intent")
                    result["restore_required"] += 1
                    continue
                parent = str(PurePosixPath(key).parent)
                source_name = PurePosixPath(key).name
                source_fd = self._open_relative(root_fd, parent)
                source_state = None
                target_state = None
                try:
                    if source_fd is not None:
                        try:
                            source_state = os.stat(
                                source_name, dir_fd=source_fd, follow_symlinks=False
                            )
                        except FileNotFoundError:
                            pass
                    try:
                        target_state = os.stat(
                            target_name, dir_fd=quarantine_fd, follow_symlinks=False
                        )
                    except FileNotFoundError:
                        pass

                    if source_state is not None and target_state is not None:
                        error = "both_exist"
                    elif source_state is None and target_state is None:
                        error = "missing"
                    elif source_state is not None:
                        if not self._matches_source(source_state, row):
                            error = "metadata_mismatch"
                        elif (
                            source_fd is None
                        ):  # pragma: no cover - defensive narrowing
                            error = "invalid_intent"
                        else:
                            _rename_noreplace(
                                source_fd, source_name, quarantine_fd, target_name
                            )
                            os.fsync(source_fd)
                            os.fsync(quarantine_fd)
                            target_state = os.stat(
                                target_name,
                                dir_fd=quarantine_fd,
                                follow_symlinks=False,
                            )
                            error = (
                                None
                                if self._matches_source(target_state, row)
                                else "metadata_mismatch"
                            )
                            if error is None:
                                result["moved"] += 1
                    else:
                        error = (
                            None
                            if self._matches_source(target_state, row)
                            else "metadata_mismatch"
                        )

                    if error is None:
                        row.status = "quarantined"
                        row.quarantined_at = now
                        row.recovery_error = None
                        db.commit()
                        result["finalized"] += 1
                    else:
                        self._mark_restore_required(db, row, error)
                        result["restore_required"] += 1
                except Exception:
                    db.rollback()
                    result["failed"] += 1
                finally:
                    if source_fd is not None:
                        os.close(source_fd)

            remaining = max(0, self.batch_size - len(pending))
            if remaining:
                for name in quarantine_names[:remaining]:
                    base = name.split(".", 1)[0]
                    try:
                        row_id = str(uuid.UUID(base))
                    except ValueError:
                        continue
                    if db.get(ArtifactOrphanQuarantine, row_id) is not None:
                        continue
                    try:
                        state = os.stat(
                            name, dir_fd=quarantine_fd, follow_symlinks=False
                        )
                        if not stat.S_ISREG(state.st_mode):
                            continue
                        row = ArtifactOrphanQuarantine(
                            id=row_id,
                            intent_token=uuid.uuid4().hex,
                            original_key=f"legacy-untracked/{name}",
                            quarantine_key=f".quarantine/{name}",
                            size_bytes=state.st_size,
                            source_mtime=datetime.fromtimestamp(
                                state.st_mtime, timezone.utc
                            ),
                            source_mtime_ns=state.st_mtime_ns,
                            source_device=state.st_dev,
                            source_inode=state.st_ino,
                            kind="regular_file",
                            status="restore_required",
                            reason="legacy_untracked_quarantine",
                            recovery_error="legacy_untracked",
                            quarantined_at=now,
                        )
                        db.add(row)
                        db.commit()
                        result["intaken"] += 1
                    except Exception:
                        db.rollback()
                        result["failed"] += 1
        finally:
            if quarantine_fd >= 0:
                os.close(quarantine_fd)
            os.close(root_fd)
        return result

    def _after_purge_claim_commit(self, _row: ArtifactOrphanQuarantine) -> None:
        """Crash seam: unlink must follow a durable purging claim."""

    def _after_purge_unlink(self, _row: ArtifactOrphanQuarantine) -> None:
        """Crash seam: a missing file is finalized by the next purging retry."""

    @staticmethod
    def _safe_purge_target_name(row: ArtifactOrphanQuarantine) -> str | None:
        path = PurePosixPath(row.quarantine_key)
        if len(path.parts) != 2 or path.parts[0] != ".quarantine":
            return None
        try:
            row_id = str(uuid.UUID(str(row.id)))
        except (AttributeError, ValueError):
            return None
        expected = {row_id}
        intent_token = getattr(row, "intent_token", None)
        if isinstance(intent_token, str) and intent_token:
            expected.add(f"{row_id}.{intent_token}")
        return path.parts[1] if path.parts[1] in expected else None

    def _finalize_purged(
        self, db: Any, row: ArtifactOrphanQuarantine, now: datetime
    ) -> bool:
        previous_status = row.status
        previous_purged_at = row.purged_at
        row.status = "purged"
        row.purged_at = now
        row.recovery_error = None
        try:
            db.commit()
        except Exception:
            db.rollback()
            # SQLAlchemy expires state on rollback; simple test doubles do not.
            row.status = previous_status
            row.purged_at = previous_purged_at
            return False
        return True

    def _mark_purge_restore_required(
        self, db: Any, row: ArtifactOrphanQuarantine, error: str
    ) -> bool:
        previous_status = row.status
        previous_error = row.recovery_error
        row.status = "restore_required"
        row.recovery_error = error
        try:
            db.commit()
        except Exception:
            db.rollback()
            row.status = previous_status
            row.recovery_error = previous_error
            return False
        return True

    def purge_reviewed(self, db: Any, *, now: datetime | None = None) -> dict[str, int]:
        """Claim reviewed rows durably, then idempotently unlink and finalize them."""
        from sqlalchemy import and_, or_

        now = _utc(now or datetime.now(timezone.utc))
        cutoff = now - timedelta(days=self.retention_days)
        candidates = (
            db.query(ArtifactOrphanQuarantine)
            .filter(
                or_(
                    ArtifactOrphanQuarantine.status == "purging",
                    and_(
                        ArtifactOrphanQuarantine.status == "reviewed",
                        ArtifactOrphanQuarantine.reviewed_at.is_not(None),
                        ArtifactOrphanQuarantine.reviewed_at <= cutoff,
                        ArtifactOrphanQuarantine.quarantined_at <= cutoff,
                    ),
                )
            )
            .order_by(
                ArtifactOrphanQuarantine.quarantined_at,
                ArtifactOrphanQuarantine.id,
            )
            .limit(self.batch_size)
            .with_for_update(skip_locked=True)
            .all()
        )
        result = {"purged": 0, "failed": 0, "restore_required": 0}
        if not candidates:
            return result

        newly_claimed = []
        for row in candidates:
            if row.status != "reviewed":
                continue
            row.status = "purging"
            row.purge_claimed_at = now
            row.purge_token = uuid.uuid4().hex
            newly_claimed.append(row)
        if newly_claimed:
            try:
                db.commit()
            except Exception:
                db.rollback()
                # Preserve truthful state for non-expiring test doubles.
                for row in newly_claimed:
                    row.status = "reviewed"
                    row.purge_claimed_at = None
                    row.purge_token = None
                result["failed"] += len(newly_claimed)
                return result
            for row in newly_claimed:
                self._after_purge_claim_commit(row)

        root_fd = os.open(self.root, _DIRECTORY_FLAGS)
        quarantine_fd = -1
        try:
            quarantine_fd = os.open(".quarantine", _DIRECTORY_FLAGS, dir_fd=root_fd)
            for row in candidates:
                if (
                    row.status != "purging"
                    or getattr(row, "purge_claimed_at", None) is None
                    or not getattr(row, "purge_token", None)
                ):
                    result["failed"] += 1
                    continue
                target_name = self._safe_purge_target_name(row)
                if target_name is None:
                    if self._mark_purge_restore_required(db, row, "invalid_intent"):
                        result["restore_required"] += 1
                    else:
                        result["failed"] += 1
                    continue
                try:
                    state = os.stat(
                        target_name, dir_fd=quarantine_fd, follow_symlinks=False
                    )
                except FileNotFoundError:
                    if self._finalize_purged(db, row, now):
                        result["purged"] += 1
                    else:
                        result["failed"] += 1
                    continue
                except OSError:
                    if self._mark_purge_restore_required(db, row, "stat_failed"):
                        result["restore_required"] += 1
                    else:
                        result["failed"] += 1
                    continue

                try:
                    matches = self._matches_source(state, row)
                except (AttributeError, TypeError):
                    matches = False
                if not matches:
                    error = (
                        "unsafe_file_type"
                        if not stat.S_ISREG(state.st_mode)
                        else "metadata_mismatch"
                    )
                    if self._mark_purge_restore_required(db, row, error):
                        result["restore_required"] += 1
                    else:
                        result["failed"] += 1
                    continue

                try:
                    os.unlink(target_name, dir_fd=quarantine_fd)
                    os.fsync(quarantine_fd)
                    self._after_purge_unlink(row)
                except Exception:
                    db.rollback()
                    result["failed"] += 1
                    continue
                if self._finalize_purged(db, row, now):
                    result["purged"] += 1
                else:
                    result["failed"] += 1
        finally:
            if quarantine_fd >= 0:
                os.close(quarantine_fd)
            os.close(root_fd)
        return result


__all__ = ["ArtifactOrphanScanner"]
