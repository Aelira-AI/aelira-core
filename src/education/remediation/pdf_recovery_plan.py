"""Bounded transport for explicitly supplied internal PDF recovery reviews.

This format records caller provenance, not approval. The font and semantic
compilers independently bind their input bytes to the two source hashes; the
semantic source must be the exact saved output of the font compiler. Transport
validation neither infers a mapping nor certifies a document's accessibility.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
import re
from typing import Any, NoReturn

from .pdf_reviewed_semantics import (
    ReviewedOccurrence,
    ReviewedSemanticManifest,
    ReviewedSemanticNode,
)
from .pdf_verified_font_recovery import (
    FontRecoveryManifest,
    ReviewedFontMap,
    ReviewedTextRun,
)

MAX_RECOVERY_JSON_BYTES = 4 * 1024 * 1024
_MAX_ITEMS = 600_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ROLES = frozenset(
    {"H1", "H2", "H3", "H4", "H5", "H6", "P", "L", "LI", "Lbl", "LBody", "Figure"}
)


class PDFRecoveryPlanError(ValueError):
    """Content-free refusal of a malformed or excessive review bundle."""


def _fail() -> NoReturn:
    raise PDFRecoveryPlanError("invalid_pdf_recovery_plan")


@dataclass(frozen=True)
class ReviewedPDFRecovery:
    font_manifest: FontRecoveryManifest
    semantic_manifest: ReviewedSemanticManifest


_DATACLASSES = frozenset(
    {
        ReviewedPDFRecovery,
        FontRecoveryManifest,
        ReviewedFontMap,
        ReviewedTextRun,
        ReviewedSemanticManifest,
        ReviewedSemanticNode,
        ReviewedOccurrence,
    }
)


def _object(value: Any, keys: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(keys.split()):
        _fail()
    return value


def _array(value: Any, maximum: int, minimum: int = 0) -> list[Any]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        _fail()
    return value


def _integer(value: Any, maximum: int, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()
    return value


def _string(value: Any, maximum: int, *, empty: bool = False) -> str:
    if (
        type(value) is not str
        or len(value) > maximum
        or (not empty and not value.strip())
        or any(
            (ord(char) < 32 and char not in "\t\n\r")
            or 0xD800 <= ord(char) <= 0xDFFF
            or char == "\ufffd"
            for char in value
        )
    ):
        _fail()
    return value


def _optional_string(value: Any, maximum: int) -> str | None:
    return None if value is None else _string(value, maximum)


def _sha(value: Any) -> str:
    result = _string(value, 64)
    if _SHA256.fullmatch(result) is None:
        _fail()
    return result


def _occurrence(value: Any) -> ReviewedOccurrence:
    obj = _object(value, "page_index operator_index text image_sha256")
    text = None if obj["text"] is None else _string(obj["text"], 200_000, empty=True)
    image_sha = None if obj["image_sha256"] is None else _sha(obj["image_sha256"])
    if (text is None) == (image_sha is None):
        _fail()
    return ReviewedOccurrence(
        _integer(obj["page_index"], 99),
        _integer(obj["operator_index"], 99_999),
        text,
        image_sha,
    )


def _font_manifest(value: Any) -> FontRecoveryManifest:
    obj = _object(value, "source_sha256 reviewer review_reference fonts runs")
    fonts = []
    used_fonts: set[tuple[int, int]] = set()
    total_cids = 0
    for value in _array(obj["fonts"], 100, 1):
        font = _object(value, "objgen fingerprint mappings")
        identity = _array(font["objgen"], 2, 2)
        objgen = (_integer(identity[0], 2**31 - 1, 1), _integer(identity[1], 65535))
        if objgen in used_fonts:
            _fail()
        used_fonts.add(objgen)
        mappings = []
        cids: set[int] = set()
        for value in _array(font["mappings"], 65536, 1):
            pair = _array(value, 2, 2)
            cid = _integer(pair[0], 65535)
            if cid in cids:
                _fail()
            cids.add(cid)
            text = _string(pair[1], 16, empty=True)
            if not text:
                _fail()
            mappings.append((cid, text))
        total_cids += len(mappings)
        if total_cids > 200_000:
            _fail()
        fonts.append(
            ReviewedFontMap(objgen, _sha(font["fingerprint"]), tuple(mappings))
        )
    runs = []
    used_runs: set[tuple[int, int, int]] = set()
    for value in _array(obj["runs"], 10_000, 1):
        run = _object(value, "page_index start end text")
        location = (
            _integer(run["page_index"], 99),
            _integer(run["start"], 99_999),
            _integer(run["end"], 99_999),
        )
        if location[1] >= location[2] or location in used_runs:
            _fail()
        used_runs.add(location)
        runs.append(
            ReviewedTextRun(*location, _string(run["text"], 200_000, empty=True))
        )
    return FontRecoveryManifest(
        _sha(obj["source_sha256"]),
        _string(obj["reviewer"], 1024),
        _string(obj["review_reference"], 2048),
        tuple(fonts),
        tuple(runs),
    )


def _semantic_manifest(value: Any) -> ReviewedSemanticManifest:
    obj = _object(
        value,
        "source_sha256 reviewer review_reference title language nodes root_ids artifacts vector_artifacts_reviewed",
    )
    nodes = []
    ids: set[str] = set()
    used: set[tuple[int, int]] = set()

    def references(value: Any) -> tuple[ReviewedOccurrence, ...]:
        items = tuple(_occurrence(item) for item in _array(value, 100_000))
        for item in items:
            key = (item.page_index, item.operator_index)
            if key in used:
                _fail()
            used.add(key)
        if len(used) > 100_000:
            _fail()
        return items

    for value in _array(obj["nodes"], 20_000, 1):
        node = _object(value, "node_id role children occurrences alt outline_title")
        node_id = _string(node["node_id"], 256)
        role = _string(node["role"], 6)
        if node_id in ids or role not in _ROLES:
            _fail()
        ids.add(node_id)
        children = tuple(_string(v, 256) for v in _array(node["children"], 20_000))
        nodes.append(
            ReviewedSemanticNode(
                node_id,
                role,
                children,
                references(node["occurrences"]),
                _optional_string(node["alt"], 8192),
                _optional_string(node["outline_title"], 2048),
            )
        )
    roots = tuple(_string(value, 256) for value in _array(obj["root_ids"], 20_000, 1))
    # A unique parent for every node rules out reused nodes and disconnected
    # cycles; the bounded traversal below also proves reachability and depth.
    parents = list(roots) + [child for node in nodes for child in node.children]
    if len(parents) != len(ids) or set(parents) != ids:
        _fail()
    by_id = {node.node_id: node for node in nodes}
    pending = [(root, 1) for root in roots]
    visited: set[str] = set()
    while pending:
        node_id, depth = pending.pop()
        if node_id in visited or depth > 40:
            _fail()
        visited.add(node_id)
        pending.extend((child, depth + 1) for child in by_id[node_id].children)
    if visited != ids or type(obj["vector_artifacts_reviewed"]) is not bool:
        _fail()
    return ReviewedSemanticManifest(
        _sha(obj["source_sha256"]),
        _string(obj["reviewer"], 1024),
        _string(obj["review_reference"], 2048),
        _string(obj["title"], 2048),
        _string(obj["language"], 64),
        tuple(nodes),
        roots,
        references(obj["artifacts"]),
        obj["vector_artifacts_reviewed"],
    )


def _decode(value: Any) -> ReviewedPDFRecovery:
    obj = _object(value, "version font_manifest semantic_manifest")
    if _integer(obj["version"], 1, 1) != 1:
        _fail()
    return ReviewedPDFRecovery(
        _font_manifest(obj["font_manifest"]),
        _semantic_manifest(obj["semantic_manifest"]),
    )


def _canonical(value: Any) -> str:
    result = json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    if len(result) > MAX_RECOVERY_JSON_BYTES:
        _fail()
    return result


def _encode(plan: ReviewedPDFRecovery) -> dict[str, Any]:
    if type(plan) is not ReviewedPDFRecovery:
        _fail()
    items = chars = 0

    def visit(value: Any, depth: int) -> Any:
        nonlocal items, chars
        items += 1
        if items > _MAX_ITEMS or depth > 16:
            _fail()
        if type(value) in _DATACLASSES:
            return {
                field.name: visit(getattr(value, field.name), depth + 1)
                for field in fields(value)
            }
        if type(value) is tuple:
            if len(value) > 200_000:
                _fail()
            return [visit(item, depth + 1) for item in value]
        if type(value) is str:
            chars += len(value)
            if chars > MAX_RECOVERY_JSON_BYTES:
                _fail()
            return value
        if value is None or type(value) is bool or type(value) is int:
            return value
        _fail()

    result: dict[str, Any] = visit(plan, 0)
    result["version"] = 1
    _decode(result)
    return result


def serialize_reviewed_pdf_recovery(plan: ReviewedPDFRecovery) -> str:
    """Validate deeply immutable typed input and return canonical bounded JSON."""
    return _canonical(_encode(plan))


def deserialize_reviewed_pdf_recovery(raw: str) -> ReviewedPDFRecovery:
    """Reject unknown keys, duplicates, nonintegral numbers, and excess depth."""
    if type(raw) is not str or len(raw) > MAX_RECOVERY_JSON_BYTES:
        _fail()
    try:
        if len(raw.encode("utf-8")) > MAX_RECOVERY_JSON_BYTES:
            _fail()
    except UnicodeError:
        _fail()
    depth = 0
    quoted = escaped = False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 16:
                _fail()
        elif char in "]}":
            depth -= 1
            if depth < 0:
                _fail()

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail()
            result[key] = value
        return result

    def invalid_number(_value: str) -> NoReturn:
        _fail()

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=invalid_number,
            parse_float=invalid_number,
        )
    except (ValueError, RecursionError):
        _fail()
    plan = _decode(value)
    # Normalize and bound the canonical form as well as the received bytes.
    _canonical(_encode(plan))
    return plan


def reviewed_pdf_recovery_receipt(plan: ReviewedPDFRecovery) -> dict[str, Any]:
    """Receipt of supplied review material, with no transcript or approval claim."""
    value = _encode(plan)
    return {
        "version": 1,
        "bundle_sha256": hashlib.sha256(_canonical(value).encode("ascii")).hexdigest(),
        "font_manifest_sha256": hashlib.sha256(
            _canonical(value["font_manifest"]).encode("ascii")
        ).hexdigest(),
        "semantic_manifest_sha256": hashlib.sha256(
            _canonical(value["semantic_manifest"]).encode("ascii")
        ).hexdigest(),
        "font_source_sha256": plan.font_manifest.source_sha256,
        "semantic_source_sha256": plan.semantic_manifest.source_sha256,
        "font_review": {
            "reviewer": plan.font_manifest.reviewer,
            "review_reference": plan.font_manifest.review_reference,
        },
        "semantic_review": {
            "reviewer": plan.semantic_manifest.reviewer,
            "review_reference": plan.semantic_manifest.review_reference,
        },
        "independent_review_pending": True,
    }
