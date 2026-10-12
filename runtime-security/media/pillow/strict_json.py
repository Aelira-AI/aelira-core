"""Strict JSON reading for retained metadata and Pillow proof receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise ValueError("nonstandard JSON constant")


def loads(raw: bytes | str) -> Any:
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)


def load(path: Path) -> Any:
    return loads(path.read_bytes())


def exact_object(value: Any, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("JSON object schema mismatch")
    return value


def integer(value: Any, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError("JSON integer type/range mismatch")
    return value


def boolean(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("JSON boolean type mismatch")
    return value
