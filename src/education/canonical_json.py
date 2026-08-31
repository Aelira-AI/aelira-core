"""Bounded passive canonical JSON shared by visual semantic contracts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

_MAX_COLLECTION_ITEMS = 4_096
_MAX_CANONICAL_DEPTH = 32
_MAX_CANONICAL_INTEGER = 9_007_199_254_740_991
_MAX_CANONICAL_STRING = 131_072


def _passive_json_value(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON value without invoking arbitrary object hooks."""
    if depth > _MAX_CANONICAL_DEPTH:
        raise ValueError("canonical value exceeds the nesting limit")
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > _MAX_CANONICAL_INTEGER:
            raise ValueError("canonical integer exceeds the exact JSON range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > _MAX_CANONICAL_INTEGER:
            raise ValueError("canonical float must be bounded and finite")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_CANONICAL_STRING or not value.isprintable():
            raise ValueError("canonical text must be bounded and printable")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_COLLECTION_ITEMS:
            raise ValueError("canonical mapping exceeds the item limit")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical mapping keys must be strings")
            if not key or len(key) > 256 or key != key.strip() or not key.isprintable():
                raise ValueError(
                    "canonical mapping keys must be bounded printable text"
                )
            result[key] = _passive_json_value(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray, memoryview)
    ):
        if len(value) > _MAX_COLLECTION_ITEMS:
            raise ValueError("canonical sequence exceeds the item limit")
        return [_passive_json_value(item, depth=depth + 1) for item in value]
    raise TypeError("canonical values must contain only passive JSON data")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode bounded passive data with stable mapping and sequence semantics."""
    return json.dumps(
        _passive_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the SHA-256 digest of :func:`canonical_json_bytes`."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


__all__ = ["canonical_json_bytes", "canonical_sha256"]
