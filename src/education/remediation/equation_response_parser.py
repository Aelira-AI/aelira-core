"""Strict JSON parsing with a narrow repair for model-emitted LaTeX escapes."""

from __future__ import annotations

import json
from typing import Any


def _reject_duplicate_keys(
    pairs: list[tuple[str, Any]], rejection_type: type[ValueError]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise rejection_type("invalid_provider_response")
        result[key] = value
    return result


def _latex_has_control_characters(parsed: Any) -> bool:
    if not isinstance(parsed, dict):
        return False
    latex = parsed.get("latex")
    return isinstance(latex, str) and any(
        not character.isprintable() for character in latex
    )


def _escape_unescaped_backslashes(content: str) -> str:
    """Double odd, unescaped backslash runs inside JSON strings.

    Existing JSON escapes for quotes, backslashes, and slashes are preserved.
    Pairing runs before inspecting the following character avoids changing an
    already escaped LaTeX slash when a run has an even number of backslashes.
    """
    result: list[str] = []
    in_string = False
    index = 0

    while index < len(content):
        character = content[index]
        if character == '"':
            preceding_backslashes = 0
            cursor = index - 1
            while cursor >= 0 and content[cursor] == "\\":
                preceding_backslashes += 1
                cursor -= 1
            if preceding_backslashes % 2 == 0:
                in_string = not in_string
            result.append(character)
            index += 1
            continue

        if in_string and character == "\\":
            end = index
            while end < len(content) and content[end] == "\\":
                end += 1
            run_length = end - index
            following = content[end] if end < len(content) else None
            if (
                run_length % 2 == 1
                and following is not None
                and following not in {'"', "\\", "/"}
            ):
                run_length += 1
            result.append("\\" * run_length)
            index = end
            continue

        result.append(character)
        index += 1

    return "".join(result)


def parse_equation_response_json(
    content: str, *, rejection_type: type[ValueError]
) -> Any:
    """Parse provider JSON, repairing unescaped LaTeX only when required.

    The ordinary strict JSON parse always runs first. A retry is allowed only
    after a JSON parse failure or when the parsed latex value contains control
    characters, which can happen when a LaTeX command begins with a valid JSON
    escape such as backslash-f or backslash-n.
    """

    def loads(value: str) -> Any:
        return json.loads(
            value,
            object_pairs_hook=lambda pairs: _reject_duplicate_keys(
                pairs, rejection_type
            ),
        )

    try:
        parsed = loads(content)
    except rejection_type:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        original_error: Exception | None = exc
        parsed = None
    else:
        original_error = None
        if not _latex_has_control_characters(parsed):
            return parsed

    repaired = _escape_unescaped_backslashes(content)
    if repaired == content:
        raise rejection_type("invalid_provider_response") from original_error

    try:
        parsed = loads(repaired)
    except rejection_type:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise rejection_type("invalid_provider_response") from exc

    if _latex_has_control_characters(parsed):
        raise rejection_type("invalid_provider_response")
    return parsed
