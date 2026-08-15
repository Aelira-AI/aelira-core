"""Sanitization utilities for database-bound text."""

from typing import Optional


def sanitize_for_postgres(value: Optional[str]) -> Optional[str]:
    """Remove NUL bytes that PostgreSQL text columns reject."""
    if value is None:
        return None
    return value.replace("\x00", "")
