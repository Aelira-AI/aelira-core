"""Bounded authored relationships, never inferred visual or tabular meaning.

Only literal graphicx alternatives/artifacts and rectangular tabular data with
explicit adjacent tagpdf header declarations are supported. Saved HTML is
checked against that source contract; PDF relationships are not yet supported.
"""

import base64
from collections import Counter
from dataclasses import dataclass, field
import hashlib
from io import BytesIO
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from PIL import Image

MAX_BYTES = 16 * 1024 * 1024


def _group(text, pos, opening="{", closing="}"):
    while pos < len(text) and text[pos].isspace():
        pos += 1
    if pos >= len(text) or text[pos] != opening:
        raise ValueError("missing group")
    start, depth = pos + 1, 1
    braces = 0
    for i in range(start, len(text)):
        char = text[i]
        if i and text[i - 1] == "\\":
            continue
        if opening == "[":
            braces += (char == "{") - (char == "}")
        if not braces:
            depth += (char == opening) - (char == closing)
        if depth == 0:
            return text[start:i], i + 1
    raise ValueError("unclosed group")


def _options(text):
    parts = re.split(r",(?=(?:[^{}]*\{[^{}]*\})*[^{}]*$)", text)
    result = {}
    for part in parts:
        key, _, value = part.strip().partition("=")
        key = key.strip()
        if not key or key in result:
            raise ValueError("ambiguous options")
        value = value.strip()
        if value.startswith("{"):
            value, end = _group(value, 0)
            if end != len(part.strip().partition("=")[2].strip()):
                raise ValueError("trailing option content")
        result[key.strip()] = value
    return result


def _literal(value):
    if re.search(r"[\\$#^_~&]", value):
        raise ValueError("nonliteral text")
    # Braces group literal words; they do not add semantic content.
    return " ".join(value.replace("{", "").replace("}", "").split())


@dataclass(frozen=True)
class Graphic:
    asset: str
    alternative: str | None
    decorative: bool
    start: int
    end: int


@dataclass(frozen=True)
class Table:
    rows: tuple[tuple[str, ...], ...]
    header_rows: tuple[int, ...]
    header_columns: tuple[int, ...]
    start: int
    end: int


@dataclass
class SourceSemantics:
    graphics: list[Graphic] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    issues: set[str] = field(default_factory=set)


def extract_semantics(source: str | None) -> SourceSemantics:
    text = re.sub(
        r"(?<!\\)((?:\\\\)*)%[^\n]*",
        lambda m: m[1] + " " * (len(m[0]) - len(m[1])),
        source or "",
    )
    result = SourceSemantics()
    if len(text.encode()) > MAX_BYTES:
        result.issues.add("semantics_unsupported")
        return result
    relevant = bool(
        re.search(
            r"\\includegraphics|\\begin\{(?:figure|table|tabular|tikzpicture|picture)",
            text,
        )
    )
    if re.search(r"\\\\(?:includegraphics|begin\{(?:figure|tabular))", text):
        result.issues.add("semantics_unsupported")
    if relevant and re.search(
        r"\\(?:newcommand|renewcommand|providecommand|[gex]?def|if[a-zA-Z]*|input|include|verb|let|catcode|NewDocumentCommand|RenewDocumentCommand|ProvideDocumentCommand|DeclareDocumentCommand|newenvironment|renewenvironment|savebox|sbox|setbox)\b|\\begin\{(?:verbatim|lstlisting|minted|comment)\}",
        text,
    ):
        result.issues.add("semantics_unsupported")
    for match in re.finditer(r"\\includegraphics\b", text):
        try:
            pos = match.end()
            while pos < len(text) and text[pos].isspace():
                pos += 1
            options = {}
            if pos < len(text) and text[pos] == "[":
                value, pos = _group(text, pos, "[", "]")
                options = _options(value)
            if set(options) - {
                "alt",
                "artifact",
                "width",
                "height",
                "scale",
                "keepaspectratio",
            }:
                raise ValueError("unsupported graphic transformation")
            asset, end = _group(text, pos)
            if not asset or any(c in asset for c in "\\{}#%"):
                raise ValueError("nonliteral asset")
            decorative = "artifact" in options and options["artifact"] in {"", "true"}
            alternative = _literal(options["alt"]) if "alt" in options else None
            if (not alternative and not decorative) or (
                decorative and "alt" in options
            ):
                result.issues.add("semantics_unconfirmed")
            if "artifact" in options and options["artifact"] not in {
                "",
                "true",
                "false",
            }:
                raise ValueError("unsupported artifact option")
            result.graphics.append(
                Graphic(asset, alternative, decorative, match.start(), end)
            )
        except ValueError:
            result.issues.add("semantics_unsupported")
    # Generated drawings/subfigures and alternative tagging dialects need a
    # separate evidence-backed profile. Do not silently ignore their content.
    if re.search(
        r"\\begin\{(?:tikzpicture|picture|figure\*|tabular\*|tabularx|longtable|tabulary)\}|\\(?:subfigure|subfloat|Description|pdftooltip)\b",
        text,
    ):
        result.issues.add("semantics_unsupported")
    for figure in re.finditer(r"\\begin\{figure\}(.*?)\\end\{figure\}", text, re.S):
        if not any(figure.start() < g.start < figure.end() for g in result.graphics):
            result.issues.add("semantics_unsupported")
    tables = list(re.finditer(r"\\begin\{tabular\}(.*?)\\end\{tabular\}", text, re.S))
    if len(tables) != len(re.findall(r"\\begin\{tabular\}", text)):
        result.issues.add("semantics_unsupported")
    for match in tables:
        try:
            spec, pos = _group(match[1], 0)
            if not re.fullmatch(r"[lcr|\s]+", spec):
                raise ValueError("unsupported column layout")
            cells = re.sub(
                r"\\(?:hline|toprule|midrule|bottomrule)\b", "", match[1][pos:]
            ).strip()
            rows = tuple(
                tuple(_literal(c) for c in row.split("&"))
                for row in cells.split(r"\\")
                if row.strip()
            )
            width = len(re.sub(r"[|\s]", "", spec))
            if (
                not rows
                or sum(map(len, rows)) > 4096
                or any(len(row) != width for row in rows)
            ):
                raise ValueError("nonrectangular table")
            prefix = text[: match.start()]
            options = {}
            declarations = list(re.finditer(r"\\tagpdfsetup\s*\{", prefix))
            if declarations:
                declaration = declarations[-1]
                content, end = _group(prefix, declaration.end() - 1)
                if not prefix[end:].strip():
                    options = _options(content)
            if set(options) - {"table/header-rows", "table/header-columns"}:
                raise ValueError("unsupported table declaration")
            headers = []
            for key, count in [
                ("table/header-rows", len(rows)),
                ("table/header-columns", width),
            ]:
                value = options.get(key, "")
                if value and not re.fullmatch(
                    r"[1-9][0-9]*(?:\s*,\s*[1-9][0-9]*)*", value
                ):
                    raise ValueError("nonliteral header indices")
                indexes = (
                    tuple(int(v.strip()) for v in value.split(",")) if value else ()
                )
                if any(i > count for i in indexes) or len(set(indexes)) != len(indexes):
                    raise ValueError("invalid header indices")
                headers.append(indexes)
            if not any(headers):
                result.issues.add("semantics_unconfirmed")
            result.tables.append(
                Table(rows, headers[0], headers[1], match.start(), match.end())
            )
        except ValueError:
            result.issues.add("semantics_unsupported")
    if len(result.graphics) + len(result.tables) > 128:
        result.issues.add("semantics_unsupported")
    return result


def _image(source: Path, graphic: Graphic):
    # One literal PNG/JPEG within the source directory. No network, directory
    # traversal, symlinks or implicit extension selection.
    path = source.parent / graphic.asset
    if path.is_symlink() or not path.resolve().is_relative_to(source.parent.resolve()):
        raise ValueError("asset outside source")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("asset too large")
    data = path.read_bytes()
    with Image.open(BytesIO(data)) as image:
        if image.format not in {"PNG", "JPEG"}:
            raise ValueError("unsupported image")
        mime = "image/png" if image.format == "PNG" else "image/jpeg"
        image.verify()
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def _cell_id(source_hash, table, row, column):
    return f"aelira-{source_hash[:16]}-{table}-{row}-{column}"


def _relations(table, row, column):
    # Column header rows above a cell and row header columns to its left.
    return [(r, column) for r in table.header_rows if r < row] + [
        (row, c) for c in table.header_columns if c < column
    ]


def _visible_semantics(soup):
    """Conservative structural gate; no CSS rendering/accessibility claim."""
    if soup.find(["script", "style"]) or soup.find("link", rel="stylesheet"):
        return False
    if soup.find(["picture", "source"]) or soup.find(attrs={"srcset": True}):
        return False
    for element in soup.find_all(True):
        if any(
            key.lower().startswith("on") or key == "srcdoc" for key in element.attrs
        ):
            return False
        if any(
            str(element.get(key, "")).strip().lower().startswith("javascript:")
            for key in ("href", "src", "action")
        ):
            return False
    for node in soup.find_all(["img", "table"]):
        targets = [node, *node.parents]
        if node.name == "table":
            targets.extend(node.find_all(True))
        for target in targets:
            if target.name == "[document]":
                continue
            if target.name in {"template", "noscript", "head", "dialog", "details"}:
                return False
            if any(target.has_attr(k) for k in ("hidden", "inert", "aria-hidden")):
                return False
            if (
                target is not node
                and target.has_attr("role")
                and target.get("role") not in {"main"}
            ):
                return False
            if target.name == "table" and target.has_attr("role"):
                return False
            if (
                any(target.has_attr(k) for k in ("aria-label", "aria-labelledby"))
                and target.name != "img"
            ):
                return False
            style = target.get("style", "").strip()
            if style and not (
                target.name in {"td", "th"}
                and re.fullmatch(r"text-align:\s*(?:left|right|center);?", style)
            ):
                return False
    return True


def save_html_semantics(source: Path, candidate: Path) -> bool:
    """Translate explicit authored declarations only after exact content mapping.

    Reopen the saved bytes and verify every asset, cell, role and header edge.
    A caption or inferred first-row TH never supplies the source declaration.
    """
    original = source.read_bytes()
    contract = extract_semantics(original.decode())
    if contract.issues or candidate.stat().st_size > MAX_BYTES:
        return False
    soup = BeautifulSoup(candidate.read_text(), "html.parser")
    if (contract.graphics or contract.tables) and not _visible_semantics(soup):
        return False
    images, tables = soup.find_all("img"), soup.find_all("table")
    if len(images) != len(contract.graphics) or len(tables) != len(contract.tables):
        return False
    # Other graphical representations cannot be counted as an inspected image.
    if soup.find(["svg", "object", "canvas", "embed"]):
        return False
    assets = [g.asset for g in contract.graphics]
    if len(set(assets)) != len(assets):
        return False
    embedded_size = candidate.stat().st_size
    for graphic in contract.graphics:
        matching = [
            img
            for img in images
            if unquote(urlsplit(img.get("src", "")).path) == graphic.asset
            and not urlsplit(img.get("src", "")).scheme
            and not urlsplit(img.get("src", "")).netloc
        ]
        if len(matching) != 1:
            return False
        img = matching[0]
        data = _image(source, graphic)
        embedded_size += len(data)
        if embedded_size > MAX_BYTES:
            return False
        img["src"] = data
        img["alt"] = "" if graphic.decorative else graphic.alternative
        for key in (
            "aria-label",
            "aria-labelledby",
            "aria-describedby",
            "title",
            "role",
            "aria-hidden",
        ):
            img.attrs.pop(key, None)
        if graphic.decorative:
            img["role"] = "presentation"
    source_hash = hashlib.sha256(original).hexdigest()
    for index, (table, node) in enumerate(zip(contract.tables, tables)):
        html_rows = node.find_all("tr")
        cells = [row.find_all(["td", "th"], recursive=False) for row in html_rows]
        if len(cells) != len(table.rows) or any(
            tuple(" ".join(c.get_text().split()) for c in row) != expected
            for row, expected in zip(cells, table.rows)
        ):
            return False
        if node.find_parent("table") or any(
            c.get("rowspan", "1") != "1"
            or c.get("colspan", "1") != "1"
            or c.find(["img", "table", "math"])
            for row in cells
            for c in row
        ):
            return False
        for row, line in enumerate(cells, 1):
            for column, cell in enumerate(line, 1):
                header = row in table.header_rows or column in table.header_columns
                cell.name = "th" if header else "td"
                for key in (
                    "scope",
                    "headers",
                    "role",
                    "aria-label",
                    "aria-labelledby",
                    "aria-hidden",
                ):
                    cell.attrs.pop(key, None)
                cell["id"] = _cell_id(source_hash, index, row, column)
                relations = _relations(table, row, column)
                if relations:
                    cell["headers"] = [
                        _cell_id(source_hash, index, r, c) for r, c in relations
                    ]
    candidate.write_text(str(soup), encoding="utf-8")
    return verify_html_semantics(source, candidate)


def verify_html_semantics(source: Path, candidate: Path) -> bool:
    """Verify saved HTML independently of the transformation's in-memory nodes."""
    original = source.read_bytes()
    contract = extract_semantics(original.decode())
    if contract.issues or candidate.stat().st_size > MAX_BYTES:
        return False
    expected_images = [
        (_image(source, g), "" if g.decorative else g.alternative, g.decorative)
        for g in contract.graphics
    ]
    soup = BeautifulSoup(candidate.read_text(), "html.parser")
    id_counts = Counter(node["id"] for node in soup.find_all(id=True))
    if (contract.graphics or contract.tables) and not _visible_semantics(soup):
        return False
    images, tables = soup.find_all("img"), soup.find_all("table")
    if len(images) != len(expected_images) or len(tables) != len(contract.tables):
        return False
    if soup.find(["svg", "object", "canvas", "embed"]):
        return False
    for data, alt, decorative in expected_images:
        matching = [img for img in images if img.get("src") == data]
        if len(matching) != 1 or matching[0].get("alt") != alt:
            return False
        img = matching[0]
        if img.get("role") != ("presentation" if decorative else None) or any(
            img.has_attr(k) for k in ("aria-label", "aria-labelledby", "aria-hidden")
        ):
            return False
    source_hash = hashlib.sha256(original).hexdigest()
    for index, (table, node) in enumerate(zip(contract.tables, tables)):
        cells = [
            row.find_all(["td", "th"], recursive=False) for row in node.find_all("tr")
        ]
        if len(cells) != len(table.rows):
            return False
        for row, (line, expected) in enumerate(zip(cells, table.rows), 1):
            if tuple(" ".join(c.get_text().split()) for c in line) != expected:
                return False
            for column, cell in enumerate(line, 1):
                if (
                    cell.get("rowspan", "1") != "1"
                    or cell.get("colspan", "1") != "1"
                    or cell.find(["img", "table", "math"])
                ):
                    return False
                header = row in table.header_rows or column in table.header_columns
                relations = [
                    _cell_id(source_hash, index, r, c)
                    for r, c in _relations(table, row, column)
                ]
                if (
                    cell.name != ("th" if header else "td")
                    or cell.get("headers", []) != relations
                    or cell.get("id") != _cell_id(source_hash, index, row, column)
                ):
                    return False
                if any(id_counts[target] != 1 for target in [cell["id"], *relations]):
                    return False
    return True
