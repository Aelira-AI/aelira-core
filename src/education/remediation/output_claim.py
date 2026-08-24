"""Descriptor-bound ownership for exact remediation output bytes."""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import tempfile
import threading
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


class DescriptorBoundOutputClaim:
    """Own one read-only descriptor for an immutable output-byte claim."""

    __slots__ = (
        "_descriptor",
        "_finalizer",
        "_lock",
        "_filename",
        "_display_path",
        "_size",
        "_sha256",
        "_mime",
        "__weakref__",
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("Use DescriptorBoundOutputClaim.from_path()")

    @classmethod
    def from_path(
        cls,
        path: str | os.PathLike[str],
        *,
        display_path: str,
        mime: str,
    ) -> DescriptorBoundOutputClaim:
        """Open a path once and take ownership of its exact regular-file bytes."""
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(path, flags)
        return cls._from_owned_descriptor(
            descriptor,
            filename=Path(path).name,
            display_path=display_path,
            mime=mime,
        )

    @classmethod
    def _from_owned_descriptor(
        cls,
        descriptor: int,
        *,
        filename: str,
        display_path: str,
        mime: str,
    ) -> DescriptorBoundOutputClaim:
        """Take ownership of an already-open descriptor after validating it."""
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(
                    "Output claim descriptor must reference a regular file"
                )
            access_mode = fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
            if access_mode != os.O_RDONLY:
                raise ValueError("Output claim descriptor must be read-only")
            if metadata.st_size < 0:
                raise ValueError("Output claim descriptor has an invalid size")
            os.set_inheritable(descriptor, False)
            digest = cls._hash_descriptor(descriptor)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

        instance = object.__new__(cls)
        instance._descriptor = descriptor
        instance._lock = threading.RLock()
        instance._filename = str(filename)
        instance._display_path = str(display_path)
        instance._size = metadata.st_size
        instance._sha256 = digest
        instance._mime = str(mime)
        instance._finalizer = weakref.finalize(
            instance, cls._close_leaked_descriptor, descriptor
        )
        return instance

    @classmethod
    def _snapshot_from_owned_descriptor(
        cls,
        descriptor: int,
        *,
        filename: str,
        display_path: str,
        mime: str,
    ) -> DescriptorBoundOutputClaim:
        """Consume a validated fd into a private unlinked read-only snapshot."""
        snapshot_dir = tempfile.mkdtemp(prefix="aelira_output_claim_")
        snapshot_path = str(Path(snapshot_dir) / "claimed-output")
        writer: int | None = None
        snapshot: int | None = None
        try:
            os.chmod(snapshot_dir, 0o700)
            writer_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                writer_flags |= os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                writer_flags |= os.O_CLOEXEC
            writer = os.open(snapshot_path, writer_flags, 0o600)
            os.set_inheritable(writer, False)
            os.lseek(descriptor, 0, os.SEEK_SET)
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                offset = 0
                while offset < len(chunk):
                    written = os.write(writer, chunk[offset:])
                    if written <= 0:
                        raise RuntimeError("Output claim snapshot made no progress")
                    offset += written
            os.fsync(writer)
            os.close(writer)
            writer = None

            snapshot_flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                snapshot_flags |= os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                snapshot_flags |= os.O_CLOEXEC
            snapshot = os.open(snapshot_path, snapshot_flags)
            os.set_inheritable(snapshot, False)
            os.unlink(snapshot_path)
            os.rmdir(snapshot_dir)
            snapshot_dir = ""

            owned_snapshot = snapshot
            snapshot = None
            return cls._from_owned_descriptor(
                owned_snapshot,
                filename=filename,
                display_path=display_path,
                mime=mime,
            )
        finally:
            if writer is not None:
                try:
                    os.close(writer)
                except OSError:
                    pass
            if snapshot is not None:
                try:
                    os.close(snapshot)
                except OSError:
                    pass
            try:
                os.close(descriptor)
            except OSError:
                pass
            if snapshot_dir:
                try:
                    os.unlink(snapshot_path)
                except FileNotFoundError:
                    pass
                try:
                    os.rmdir(snapshot_dir)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _hash_descriptor(descriptor: int) -> str:
        duplicate = os.dup(descriptor)
        try:
            os.set_inheritable(duplicate, False)
            os.lseek(duplicate, 0, os.SEEK_SET)
            digest = hashlib.sha256()
            while True:
                chunk = os.read(duplicate, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            os.lseek(descriptor, 0, os.SEEK_SET)
            return digest.hexdigest()
        finally:
            os.close(duplicate)

    @staticmethod
    def _close_leaked_descriptor(descriptor: int) -> None:
        try:
            os.close(descriptor)
        except OSError:
            pass

    @property
    def filename(self) -> str:
        return self._filename

    @property
    def display_path(self) -> str:
        return self._display_path

    @property
    def size(self) -> int:
        return self._size

    @property
    def sha256(self) -> str:
        return self._sha256

    @property
    def mime(self) -> str:
        return self._mime

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._descriptor is None

    @contextmanager
    def open_stream(self) -> Iterator[BinaryIO]:
        """Borrow a CLOEXEC duplicate positioned at the start of claimed bytes."""
        with self._lock:
            descriptor = self._descriptor
            if descriptor is None:
                raise RuntimeError("Output claim is closed")
            duplicate = os.dup(descriptor)
            try:
                os.set_inheritable(duplicate, False)
                os.lseek(duplicate, 0, os.SEEK_SET)
                with os.fdopen(duplicate, "rb", closefd=True) as stream:
                    duplicate = -1
                    yield stream
            finally:
                if duplicate >= 0:
                    os.close(duplicate)

    def close(self) -> None:
        """Release the owned descriptor exactly once."""
        with self._lock:
            descriptor = self._descriptor
            if descriptor is None:
                return
            self._descriptor = None
            self._finalizer.detach()
            os.close(descriptor)

    def __enter__(self) -> DescriptorBoundOutputClaim:
        if self.closed:
            raise RuntimeError("Output claim is closed")
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @staticmethod
    def _reject_aliasing() -> None:
        raise TypeError("DescriptorBoundOutputClaim cannot be copied or pickled")

    def __copy__(self) -> DescriptorBoundOutputClaim:
        self._reject_aliasing()

    def __deepcopy__(self, memo: dict[int, object]) -> DescriptorBoundOutputClaim:
        self._reject_aliasing()

    def __reduce__(self) -> object:
        self._reject_aliasing()

    def __reduce_ex__(self, protocol: int) -> object:
        self._reject_aliasing()

    def __getstate__(self) -> object:
        self._reject_aliasing()
