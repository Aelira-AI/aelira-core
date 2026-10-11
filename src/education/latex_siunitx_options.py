"""Bounded literal siunitx source check for the measured HTML routes.

This scanner does not expand TeX. It refuses syntax whose authored number or
option semantics cannot be established before a converter can erase them.
"""

from __future__ import annotations

import re

MAX_SOURCE = 8 * 1024 * 1024
MAX_COMMANDS = 4096
MIN_PARSE_WORK = 65536
PARSE_WORK_PER_SOURCE_CHAR = 8
QUANTITIES = {
    "qty": 2,
    "SI": 2,
    "qtyrange": 3,
    "SIrange": 3,
    "qtylist": 2,
    "SIlist": 2,
    "num": 1,
    "numrange": 2,
    "numlist": 1,
}
UNITS = {"unit": 1, "si": 1}
ANGLES = {"ang": 1}
TARGETS = QUANTITIES | UNITS | ANGLES
CONTROL_WORD = re.compile(r"\\([A-Za-z@]+)")
PACKAGE_COMMAND = re.compile(r"\\(?:usepackage|RequirePackage|PassOptionsToPackage)\b")
COMMAND = re.compile(r"\\([A-Za-z@]+|.)")
INLINE_LITERAL = re.compile(r"\\(verb\*?|lstinline|mintinline)(?![A-Za-z@])")
BEGIN_ENVIRONMENT = re.compile(r"\\begin\{([A-Za-z*]{1,32})\}")
VERBATIM = {
    "verbatim",
    "verbatim*",
    "Verbatim",
    "Verbatim*",
    "lstlisting",
    "minted",
    "comment",
}
NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:\(\d+\))?(?:[eE][+-]?\d+)?\Z")
ANGLE_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")


def _literal_angle(value: str) -> bool:
    """Only measured decimal and degree/minute/second literal forms."""
    parts = value.split(";")
    return (
        1 <= len(parts) <= 3
        and any(part.strip() for part in parts)
        and all(
            not part.strip() or ANGLE_NUMBER.fullmatch(part.strip()) for part in parts
        )
    )


class _WorkLimit(Exception):
    """The literal scanner has exhausted its source-wide work allowance."""


class _Budget:
    def __init__(self, source_chars: int):
        self.remaining = max(MIN_PARSE_WORK, source_chars * PARSE_WORK_PER_SOURCE_CHAR)
        self.line = 1

    def charge(self, amount: int = 1) -> None:
        self.remaining -= amount
        if self.remaining < 0:
            raise _WorkLimit


def _blank(text: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in text)


def _active(source: str, budget: _Budget) -> str:
    """Mask comments and common literal-text regions without moving offsets."""
    budget.charge(len(source))
    out = list(source)
    pos = 0
    while pos < len(source):
        budget.charge()
        escapes = 0
        if source[pos] == "%":
            before = pos - 1
            while before >= 0 and source[before] == "\\":
                escapes += 1
                before -= 1
        if source[pos] == "%" and escapes % 2 == 0:
            end = source.find("\n", pos)
            end = len(source) if end < 0 else end
            budget.charge(end - pos)
            out[pos:end] = _blank(source[pos:end])
            pos = end
            continue
        if source[pos] != "\\":
            pos += 1
            continue
        match = INLINE_LITERAL.match(source, pos)
        if match:
            start = match.end()
            try:
                if (
                    match[1] in {"lstinline", "mintinline"}
                    and start < len(source)
                    and source[start] == "["
                ):
                    _, start = _group(source, start, "[", "]", budget)
                if (
                    match[1] == "mintinline"
                    and start < len(source)
                    and source[start] == "{"
                ):
                    _, start = _group(source, start, "{", "}", budget)
            except ValueError:
                start = len(source)
            if start < len(source) and not source[start].isspace():
                if source[start] == "{":
                    try:
                        _, end = _group(source, start, "{", "}", budget)
                    except ValueError:
                        end = len(source)
                else:
                    end = source.find(source[start], start + 1)
                    end = len(source) if end < 0 else end + 1
                budget.charge(end - pos)
                out[pos:end] = _blank(source[pos:end])
                pos = end
                continue
        match = BEGIN_ENVIRONMENT.match(source, pos)
        if match and match[1] in VERBATIM:
            end_tag = "\\end{" + match[1] + "}"
            end = source.find(end_tag, match.end())
            end = len(source) if end < 0 else end + len(end_tag)
            budget.charge(end - pos)
            out[pos:end] = _blank(source[pos:end])
            pos = end
            continue
        pos += 2 if pos + 1 < len(source) else 1
    budget.charge(len(source))
    return "".join(out)


def _space(text: str, pos: int, budget: _Budget) -> int:
    while pos < len(text) and text[pos].isspace():
        budget.charge()
        pos += 1
    return pos


def _group(
    text: str, pos: int, opening: str, closing: str, budget: _Budget
) -> tuple[str, int]:
    if pos >= len(text) or text[pos] != opening:
        raise ValueError("expected group")
    start, depth = pos + 1, 1
    pos += 1
    while pos < len(text):
        budget.charge()
        if text[pos] == "\\":
            pos += 2
            continue
        if text[pos] == opening:
            depth += 1
        elif text[pos] == closing:
            depth -= 1
            if depth == 0:
                budget.charge(pos - start)
                return text[start:pos], pos + 1
        pos += 1
    raise ValueError("unterminated group")


def _definition(
    text: str, pos: int, command: str, budget: _Budget
) -> tuple[str, int, str]:
    """Return a literal macro definition's name, end and body where known."""
    pos = _space(text, pos, budget)
    if command in {"def", "gdef", "edef", "xdef", "let"}:
        name = COMMAND.match(text, pos)
        if not name:
            raise ValueError("macro name")
        pos = name.end()
        if command == "let":
            pos = _space(text, pos, budget)
            if pos < len(text) and text[pos] == "=":
                budget.charge()
                pos = _space(text, pos + 1, budget)
            target = COMMAND.match(text, pos)
            if not target:
                raise ValueError("let target")
            return name[1], target.end(), target[0]
        opening = text.find("{", pos, min(len(text), pos + 129))
        budget.charge(129 if opening < 0 else opening - pos)
        if opening < 0:
            raise ValueError("definition body")
        body, end = _group(text, opening, "{", "}", budget)
        return name[1], end, body
    if pos < len(text) and text[pos] == "*":
        pos = _space(text, pos + 1, budget)
    if pos < len(text) and text[pos] == "{":
        name_group, pos = _group(text, pos, "{", "}", budget)
    else:
        raw_name = COMMAND.match(text, pos)
        if not raw_name:
            raise ValueError("macro name")
        name_group, pos = raw_name[0], raw_name.end()
    name = COMMAND.fullmatch(name_group.strip())
    if not name:
        raise ValueError("macro name")
    for _ in range(2):
        pos = _space(text, pos, budget)
        if pos < len(text) and text[pos] == "[":
            _, pos = _group(text, pos, "[", "]", budget)
    body, pos = _group(text, _space(text, pos, budget), "{", "}", budget)
    return name[1], pos, body


def _loads_siunitx(body: str, budget: _Budget) -> bool:
    """Recognize literal siunitx loading inside an invoked definition."""
    budget.charge(len(body))
    for match in PACKAGE_COMMAND.finditer(body):
        cursor = _space(body, match.end(), budget)
        try:
            if match[0].endswith("PassOptionsToPackage"):
                _, cursor = _group(body, cursor, "{", "}", budget)
            elif cursor < len(body) and body[cursor] == "[":
                _, cursor = _group(body, cursor, "[", "]", budget)
            packages, _ = _group(body, _space(body, cursor, budget), "{", "}", budget)
        except ValueError:
            continue
        if "siunitx" in {part.strip() for part in packages.split(",")}:
            return True
    return False


def _invoked_definition_is_ambiguous(
    name: str,
    definitions: dict[str, str],
    *,
    siunitx_active: bool,
    physics_active: bool,
    budget: _Budget,
) -> bool:
    """Follow bounded literal references; never substitute or evaluate macros."""
    pending = [name]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        if len(visited) > MAX_COMMANDS:
            raise _WorkLimit
        body = definitions[current]
        if _loads_siunitx(body, budget):
            return True
        budget.charge(len(body))
        for command in CONTROL_WORD.findall(body):
            if command in definitions and command not in visited:
                pending.append(command)
            if command == "sisetup":
                return True
            if (
                siunitx_active
                and command in TARGETS
                and not (command == "qty" and physics_active)
            ):
                return True
    return False


def _inspect_siunitx(source: str, budget: _Budget) -> tuple[str, int] | None:
    text = _active(source, budget)
    definitions: dict[str, str] = {}
    siunitx_active = False
    physics_active = False
    package_context_unknown = False
    pos = 0
    commands = 0
    counted_to = 0
    line = 1
    while pos < len(text):
        budget.charge()
        if text[pos] != "\\":
            pos += 1
            continue
        match = COMMAND.match(text, pos)
        if not match:
            pos += 1
            continue
        name, start = match[1], pos
        pos = match.end()
        commands += 1
        line += text.count("\n", counted_to, start)
        counted_to = start
        budget.line = line
        if commands > MAX_COMMANDS:
            return "siunitx_source_unbounded", line
        if name in {
            "newcommand",
            "renewcommand",
            "providecommand",
            "DeclareRobustCommand",
            "def",
            "gdef",
            "edef",
            "xdef",
            "let",
        }:
            try:
                defined, end, body = _definition(text, pos, name, budget)
            except ValueError:
                # Leave malformed unrelated definitions to the converter's
                # normal parser. Their following source is still scanned.
                continue
            definitions[defined] = body
            pos = end
            continue
        if name in definitions:
            if name in TARGETS and siunitx_active:
                return "siunitx_syntax_ambiguous", line
            if _invoked_definition_is_ambiguous(
                name,
                definitions,
                siunitx_active=siunitx_active,
                physics_active=physics_active,
                budget=budget,
            ):
                return "siunitx_syntax_ambiguous", line
        if name == "sisetup":
            return "siunitx_options_unverified", line
        if name in {"usepackage", "RequirePackage", "PassOptionsToPackage"}:
            try:
                cursor = _space(text, pos, budget)
                options = ""
                if name == "PassOptionsToPackage":
                    options, cursor = _group(text, cursor, "{", "}", budget)
                elif cursor < len(text) and text[cursor] == "[":
                    options, cursor = _group(text, cursor, "[", "]", budget)
                packages, cursor = _group(
                    text, _space(text, cursor, budget), "{", "}", budget
                )
                package_names = {item.strip() for item in packages.split(",")}
                if name != "PassOptionsToPackage" and "\\" in packages:
                    package_context_unknown = True
                if "siunitx" in package_names:
                    if options.strip():
                        return "siunitx_options_unverified", line
                    if name != "PassOptionsToPackage":
                        siunitx_active = True
                if "physics" in package_names and name != "PassOptionsToPackage":
                    physics_active = True
            except ValueError:
                pass  # The regular package route handles malformed declarations.
            continue
        if name not in TARGETS:
            continue
        if name == "qty" and physics_active:
            continue
        if not siunitx_active:
            if package_context_unknown:
                return "siunitx_syntax_ambiguous", line
            continue
        cursor = _space(text, pos, budget)
        try:
            if cursor < len(text) and text[cursor] == "[":
                return "siunitx_options_unverified", line
            arguments = []
            for index in range(TARGETS[name]):
                cursor = _space(text, cursor, budget)
                argument, cursor = _group(text, cursor, "{", "}", budget)
                arguments.append(argument)
                if index == 0 and name in {"SI", "SIrange", "SIlist"}:
                    cursor = _space(text, cursor, budget)
                    if cursor < len(text) and text[cursor] == "[":
                        return "siunitx_syntax_ambiguous", line
            if name in QUANTITIES:
                numbers = (
                    arguments[:2]
                    if name in {"qtyrange", "SIrange", "numrange"}
                    else arguments[:1]
                )
                if name in {"qtylist", "SIlist", "numlist"}:
                    numbers = arguments[0].split(";")
                if any(not NUMBER.fullmatch(number.strip()) for number in numbers):
                    return "siunitx_number_unverified", line
            if name in ANGLES and not _literal_angle(arguments[0]):
                return "siunitx_number_unverified", line
            # Keep scanning the parsed arguments for nested siunitx commands.
        except ValueError:
            # A physics-style \qty(...) is not a siunitx quantity.
            if name == "qty" and cursor < len(text) and text[cursor] in "([{":
                if text[cursor] == "(":
                    continue
            return "siunitx_syntax_ambiguous", line
    return None


def inspect_siunitx(source: str) -> tuple[str, int] | None:
    """Return public diagnostic code and line for uncertain authored semantics."""
    if len(source.encode("utf-8")) > MAX_SOURCE:
        return "siunitx_source_unbounded", 1
    budget = _Budget(len(source))
    try:
        return _inspect_siunitx(source, budget)
    except _WorkLimit:
        return "siunitx_source_unbounded", budget.line
