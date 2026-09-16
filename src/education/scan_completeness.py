"""Track required-check failures across one document scan without global state."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional


class IncompleteScanError(RuntimeError):
    """A numeric accessibility score is unavailable after a required check failed."""


INCOMPLETE_SCAN_MESSAGE = (
    "Required accessibility checks could not be completed. No score is available. "
    "Review the document manually before relying on its accessibility."
)


def public_scan_failure_message(error: Exception) -> str:
    """Expose a known refusal without publishing exception text or check details."""
    if isinstance(error, IncompleteScanError):
        return INCOMPLETE_SCAN_MESSAGE
    return "Processing encountered an error. Please try again."


_failures: ContextVar[Optional[set[str]]] = ContextVar("scan_failures", default=None)


def complete_scan_requested() -> bool:
    return _failures.get() is not None


def record_incomplete_check(check: str) -> None:
    failures = _failures.get()
    if failures is not None:
        failures.add(check)


@contextmanager
def require_complete_scan(enabled: bool) -> Iterator[None]:
    failures: Optional[set[str]] = set() if enabled else None
    token = _failures.set(failures)
    try:
        yield
        if failures:
            raise IncompleteScanError(
                "Required accessibility checks did not complete: "
                + ", ".join(sorted(failures))
            )
    finally:
        _failures.reset(token)
