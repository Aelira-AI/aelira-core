"""Literal equation identities and representation observations, not fidelity proof.

The source scanner does not expand TeX. Offsets are half-open UTF-8 byte spans
in the original source. A TeX annotation associates content only: it cannot
certify the accompanying presentation tree, operator meaning or accessibility.
"""

from collections import defaultdict
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Annotated, Literal
from zipfile import BadZipFile, ZipFile

from bs4 import BeautifulSoup
from defusedxml import ElementTree as SafeET
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_CANDIDATE_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 128
MAX_NODES = 128
MAX_TREE_NODES = 50000
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Issue = Literal[
    "source_limit",
    "record_limit",
    "unterminated_math",
    "unsupported_source",
    "candidate_missing",
    "candidate_unreadable",
    "candidate_limit",
    "candidate_malformed",
    "node_limit",
    "tree_limit",
    "association_unavailable",
]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(digest, kind, start, end):
    return _sha(f"{digest}:{kind}:{start}:{end}".encode())


class _Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always"
    )


class SourceSpan(_Record):
    start_byte: int = Field(ge=0, strict=True)
    end_byte: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_byte < self.start_byte:
            raise ValueError("Invalid source span")
        return self


class EquationRow(_Record):
    row_id: Hash
    source_span: SourceSpan
    content_sha256: Hash


class SourceExpression(_Record):
    expression_id: Hash
    source_span: SourceSpan
    source_sha256: Hash
    content_span: SourceSpan
    content_sha256: Hash
    form: Literal["inline", "display", "environment"]
    rows: list[EquationRow] = Field(default_factory=list, max_length=MAX_RECORDS)


class SourceRelationship(_Record):
    relationship_id: Hash
    kind: Literal["label", "reference", "citation", "bibliography"]
    source_span: SourceSpan
    source_sha256: Hash
    target_sha256: Hash
    expression_id: Hash | None = None
    row_id: Hash | None = None
    target_label_ids: list[Hash] = Field(default_factory=list, max_length=MAX_RECORDS)
    command_span: SourceSpan | None = None
    command_sha256: Hash | None = None
    target_bibliography_ids: list[Hash] = Field(
        default_factory=list, max_length=MAX_RECORDS
    )
    target_status: Literal[
        "not_assessed", "authored_target_match", "unresolved", "ambiguous"
    ] = "not_assessed"


class SourceEquationProvenance(_Record):
    schema_version: Literal[1] = 1
    source_sha256: Hash
    source_bytes: int = Field(ge=0, strict=True)
    coverage: Literal["literal-source-spans-only"] = "literal-source-spans-only"
    expressions: list[SourceExpression] = Field(
        default_factory=list, max_length=MAX_RECORDS
    )
    relationships: list[SourceRelationship] = Field(
        default_factory=list, max_length=MAX_RECORDS
    )
    issues: list[Issue] = Field(default_factory=list, max_length=16)
    fidelity: Literal["not_assessed"] = "not_assessed"

    @model_validator(mode="after")
    def bound_spans(self):
        expressions = {e.expression_id: e for e in self.expressions}
        rows = {r.row_id: e.expression_id for e in self.expressions for r in e.rows}
        row_count = sum(len(e.rows) for e in self.expressions)
        if row_count > MAX_RECORDS or len(rows) != row_count:
            raise ValueError("Invalid aggregate row count")
        if len(expressions) != len(self.expressions):
            raise ValueError("Duplicate expression identity")
        for e in self.expressions:
            if e.expression_id != _identity(
                self.source_sha256,
                "expression",
                e.source_span.start_byte,
                e.source_span.end_byte,
            ):
                raise ValueError("Expression identity does not bind source span")
            if not (
                e.source_span.start_byte
                <= e.content_span.start_byte
                <= e.content_span.end_byte
                <= e.source_span.end_byte
                <= self.source_bytes
            ):
                raise ValueError("Expression span outside source")
            for row in e.rows:
                if row.row_id != _identity(
                    self.source_sha256,
                    "row",
                    row.source_span.start_byte,
                    row.source_span.end_byte,
                ):
                    raise ValueError("Row identity does not bind source span")
                if not (
                    e.content_span.start_byte
                    <= row.source_span.start_byte
                    <= row.source_span.end_byte
                    <= e.content_span.end_byte
                ):
                    raise ValueError("Row span outside expression")
        labels = {
            r.relationship_id: r.target_sha256
            for r in self.relationships
            if r.kind == "label"
        }
        bibliography = {
            r.relationship_id: r.target_sha256
            for r in self.relationships
            if r.kind == "bibliography"
        }
        for r in self.relationships:
            if r.relationship_id != _identity(
                self.source_sha256,
                "relationship",
                r.source_span.start_byte,
                r.source_span.end_byte,
            ):
                raise ValueError("Relationship identity does not bind source span")
            if r.source_span.end_byte > self.source_bytes:
                raise ValueError("Relationship span outside source")
            if r.expression_id is not None and r.expression_id not in expressions:
                raise ValueError("Unknown expression identity")
            if r.row_id is not None and rows.get(r.row_id) != r.expression_id:
                raise ValueError("Unknown row identity")
            if any(
                labels.get(label) != r.target_sha256 for label in r.target_label_ids
            ):
                raise ValueError("Unknown label identity")
            if r.command_span is not None:
                if (
                    not (
                        r.command_span.start_byte
                        <= r.source_span.start_byte
                        <= r.source_span.end_byte
                        <= r.command_span.end_byte
                        <= self.source_bytes
                    )
                    or r.command_sha256 is None
                ):
                    raise ValueError("Invalid relationship command span")
            elif r.command_sha256 is not None:
                raise ValueError("Command hash requires a command span")
            if r.kind == "citation":
                expected = {
                    i for i, target in bibliography.items() if target == r.target_sha256
                }
                if set(r.target_bibliography_ids) != expected:
                    raise ValueError("Citation omits or invents bibliography targets")
                status = (
                    "authored_target_match"
                    if len(expected) == 1
                    else "ambiguous" if expected else "unresolved"
                )
                if r.target_status != status:
                    raise ValueError("Citation target status does not match evidence")
            elif r.target_bibliography_ids or r.target_status != "not_assessed":
                raise ValueError("Bibliography target status belongs to citations")
        return self


class RepresentationNode(_Record):
    node_index: int = Field(ge=0, lt=MAX_NODES, strict=True)
    node_sha256: Hash
    annotation_sha256: list[Hash] = Field(default_factory=list, max_length=8)
    structure_sha256: Hash
    intermediate_semantics_sha256: list[Hash] = Field(
        default_factory=list, max_length=64
    )
    operator_scope_sha256: list[Hash] = Field(default_factory=list, max_length=64)
    script_attachment_sha256: list[Hash] = Field(default_factory=list, max_length=64)
    observations_truncated: bool = Field(default=False, strict=True)
    source_ids: list[Hash] = Field(default_factory=list, max_length=MAX_RECORDS)
    association: Literal["exact_annotation", "ambiguous", "unmapped"]
    fidelity: Literal["not_assessed"] = "not_assessed"

    @model_validator(mode="after")
    def association_evidence(self):
        if self.association == "exact_annotation" and (
            len(self.source_ids) != 1 or len(self.annotation_sha256) != 1
        ):
            raise ValueError("Exact association requires a unique annotated source")
        if self.association == "ambiguous" and len(self.source_ids) < 2:
            raise ValueError("Ambiguity requires multiple source identities")
        if self.association == "unmapped" and self.source_ids:
            raise ValueError("Unmapped nodes cannot claim a source identity")
        return self


class EquationRepresentationTrace(_Record):
    schema_version: Literal[1] = 1
    representation: Literal["latexml", "html", "mathml", "docx", "pdf"]
    source_sha256: Hash
    source: SourceEquationProvenance
    candidate_sha256: Hash | None = None
    status: Literal["observed", "not_assessed"]
    nodes: list[RepresentationNode] = Field(default_factory=list, max_length=MAX_NODES)
    unmapped_source_ids: list[Hash] = Field(
        default_factory=list, max_length=MAX_RECORDS * 2
    )
    issues: list[Issue] = Field(default_factory=list, max_length=16)
    association_method: Literal["exact-authored-tex-only"] = "exact-authored-tex-only"
    fidelity: Literal["not_assessed"] = "not_assessed"
    human_review_required: Literal[True] = True

    @model_validator(mode="after")
    def source_binding(self):
        if self.source_sha256 != self.source.source_sha256:
            raise ValueError("Trace source hash mismatch")
        identities = {
            e.expression_id: e.content_sha256 for e in self.source.expressions
        }
        identities.update(
            {
                r.row_id: r.content_sha256
                for e in self.source.expressions
                for r in e.rows
            }
        )
        mapped = set()
        for node in self.nodes:
            expected = {
                i
                for i, digest in identities.items()
                if digest in node.annotation_sha256
            }
            if node.association != "unmapped" and set(node.source_ids) != expected:
                raise ValueError("Association omits matching source identities")
            if any(
                identities.get(i) not in node.annotation_sha256 for i in node.source_ids
            ):
                raise ValueError("Annotation does not bind source identity")
            if node.association == "exact_annotation":
                mapped.update(node.source_ids)
        if set(self.unmapped_source_ids) != set(identities) - mapped:
            raise ValueError("Unmapped source coverage is inconsistent")
        if self.nodes and (self.status != "observed" or self.candidate_sha256 is None):
            raise ValueError("Observed nodes require candidate provenance")
        if self.representation in {"pdf", "docx"} and mapped:
            raise ValueError("No PDF or OMML association verifier is implemented")
        return self


_ENV = r"(?:equation\*?|align\*?|alignat\*?|flalign\*?|gather\*?|multline\*?|displaymath|math)"
_TOKENS = re.compile(r"\\(?:begin|end)\{[^{}\n]{1,64}\}|\\[\[\]()\\$%{}]|\$\$?|[{}]")
_RELATION = re.compile(r"\\(label|eqref|ref|autoref|pageref)\s*\{([^{}\\\n]{1,256})\}")
_CITATION = re.compile(
    r"\\(citep|citet|cite)\*?(?:\s*\[[^\[\]\\\n]{0,256}\]){0,2}\s*\{([^{}\\\n]{1,4096})\}|\\(bibitem)(?:\s*\[[^\[\]\\\n]{0,256}\])?\s*\{([^{}\\\n]{1,256})\}"
)
_UNSUPPORTED = re.compile(
    r"\\(?:[gex]?def|newcommand|renewcommand|providecommand|catcode|input|include|if\w*|let|bibliography|addbibresource|printbibliography)\b"
)


def _masked(source):
    # Replace comments/verbatim with equal-length blanks: original offsets survive.
    chars = list(source)
    pattern = re.compile(
        r"\\begin\{(verbatim\*?|lstlisting|minted|comment)\}|\\verb\*?(?![a-zA-Z])|\\.|%"
    )
    cursor, incomplete = 0, False
    for match in pattern.finditer(source):
        if match.start() < cursor:
            continue
        value = match[0]
        if value == "%":
            end = source.find("\n", match.end())
            end = len(source) if end < 0 else end
        elif match[1]:
            closing = r"\end{" + match[1] + "}"
            end = source.find(closing, match.end())
            incomplete |= end < 0
            end = len(source) if end < 0 else end + len(closing)
        elif value.startswith(r"\verb"):
            start = match.end()
            end = source.find(source[start], start + 1) if start < len(source) else -1
            incomplete |= end < 0
            end = len(source) if end < 0 else end + 1
        else:
            continue
        chars[match.start() : end] = [
            "\n" if c == "\n" else " " for c in source[match.start() : end]
        ]
        cursor = end
    return "".join(chars), incomplete


def _expression_spans(masked):
    spans, issues = [], set()
    start = body = None
    ending = None
    envs = []
    braces = 0
    for token in _TOKENS.finditer(masked):
        value = token[0]
        if start is None:
            if value in {"$", "$$", r"\(", r"\["}:
                start, body = token.start(), token.end()
                ending = {r"\(": r"\)", r"\[": r"\]"}.get(value, value)
            elif re.fullmatch(r"\\begin\{" + _ENV + r"\}", value):
                start, body, ending = token.start(), token.end(), "environment"
                envs = [value[7:-1]]
            continue
        close = False
        if value == "{":
            braces += 1
        elif value == "}":
            braces -= 1
            if braces < 0:
                issues.add("unsupported_source")
        elif value.startswith(r"\begin{"):
            envs.append(value[7:-1])
        elif value.startswith(r"\end{"):
            name = value[5:-1]
            if envs and envs[-1] == name:
                envs.pop()
                close = ending == "environment" and not envs and braces == 0
            else:
                issues.add("unsupported_source")
        elif value == ending and not envs and braces == 0:
            close = True
        if close:
            if len(spans) == MAX_RECORDS:
                issues.add("record_limit")
                start = None
                break
            form = (
                "environment"
                if ending == "environment"
                else ("inline" if ending in {"$", r"\)"} else "display")
            )
            spans.append((start, token.end(), body, token.start(), form))
            start = body = ending = None
    if start is not None:
        issues.add("unterminated_math")
    return spans, issues


def _trim(text, start, end):
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _row_spans(text, masked, start, end):
    left, right = _trim(masked, start, end)
    # An aligned wrapper is one expression, with its own authored rows.
    wrapper = re.match(r"\\begin\{(aligned|gathered|split)\}", masked[left:right])
    if wrapper and masked[left:right].endswith(r"\end{" + wrapper[1] + "}"):
        left += wrapper.end()
        right -= len(r"\end{" + wrapper[1] + "}")
    spans, depth, envs, cursor = [], 0, [], left
    for token in _TOKENS.finditer(masked, left, right):
        value = token[0]
        if value.startswith(r"\begin{"):
            envs.append(value[7:-1])
        elif value.startswith(r"\end{") and envs:
            envs.pop()
        elif value == "{":
            depth += 1
        elif value == "}":
            depth -= 1
        elif value == r"\\" and not envs and depth == 0:
            spans.append(_trim(text, cursor, token.start()))
            cursor = token.end()
            if len(spans) >= MAX_RECORDS:
                return spans, True
    spans.append(_trim(text, cursor, right))
    return (
        [span for span in spans if span[0] < span[1]] if len(spans) > 1 else []
    ), False


def source_provenance(source: str) -> SourceEquationProvenance:
    """Record literal source identities without exposing authored text."""
    data = source.encode("utf-8")
    digest = _sha(data)
    if len(data) > MAX_SOURCE_BYTES:
        return SourceEquationProvenance(
            source_sha256=digest, source_bytes=len(data), issues=["source_limit"]
        )
    masked, incomplete = _masked(source)
    spans, issues = _expression_spans(masked)
    if incomplete or _UNSUPPORTED.search(masked):
        issues.add("unsupported_source")
    # One linear pass, avoiding repeated encoding of every prefix.
    offsets, position = [0], 0
    for char in source:
        position += len(char.encode("utf-8"))
        offsets.append(position)

    def span(start, end):
        return SourceSpan(start_byte=offsets[start], end_byte=offsets[end])

    def identity(kind, start, end):
        return _identity(digest, kind, offsets[start], offsets[end])

    expressions, row_count = [], 0
    for start, end, body, stop, form in spans:
        body, stop = _trim(source, body, stop)
        rows, truncated = _row_spans(source, masked, body, stop)
        if truncated or row_count + len(rows) > MAX_RECORDS:
            issues.add("record_limit")
        rows = rows[: max(0, MAX_RECORDS - row_count)]
        row_count += len(rows)
        expressions.append(
            SourceExpression(
                expression_id=identity("expression", start, end),
                source_span=span(start, end),
                source_sha256=_sha(data[offsets[start] : offsets[end]]),
                content_span=span(body, stop),
                content_sha256=_sha(data[offsets[body] : offsets[stop]]),
                form=form,
                rows=[
                    EquationRow(
                        row_id=identity("row", a, b),
                        source_span=span(a, b),
                        content_sha256=_sha(data[offsets[a] : offsets[b]]),
                    )
                    for a, b in rows
                ],
            )
        )
    relationships = []
    for match in _RELATION.finditer(masked):
        cursor = match.start() - 1
        while cursor >= 0 and masked[cursor] == "\\":
            cursor -= 1
        if (match.start() - cursor - 1) % 2:
            continue
        if len(relationships) == MAX_RECORDS:
            issues.add("record_limit")
            break
        start, end = match.span()
        location = span(start, end)
        expression = next(
            (
                e
                for e in expressions
                if e.content_span.start_byte <= location.start_byte
                and location.end_byte <= e.content_span.end_byte
            ),
            None,
        )
        row = (
            next(
                (
                    r
                    for r in expression.rows
                    if r.source_span.start_byte <= location.start_byte
                    and location.end_byte <= r.source_span.end_byte
                ),
                None,
            )
            if expression
            else None
        )
        relationships.append(
            SourceRelationship(
                relationship_id=identity("relationship", start, end),
                kind="label" if match[1] == "label" else "reference",
                source_span=location,
                source_sha256=_sha(data[offsets[start] : offsets[end]]),
                target_sha256=_sha(match[2].encode()),
                expression_id=expression.expression_id if expression else None,
                row_id=row.row_id if row else None,
            )
        )
    labels = defaultdict(list)
    for rel in relationships:
        if rel.kind == "label":
            labels[rel.target_sha256].append(rel.relationship_id)
    relationships = [
        (
            r.model_copy(update={"target_label_ids": labels[r.target_sha256]})
            if r.kind == "reference"
            else r
        )
        for r in relationships
    ]
    # Citation records identify each literal target, with the encompassing
    # command separately hashed. Target lists are never a single combined key.
    citation_commands = set()
    for match in _CITATION.finditer(masked):
        cursor = match.start() - 1
        while cursor >= 0 and masked[cursor] == "\\":
            cursor -= 1
        if (match.start() - cursor - 1) % 2:
            continue
        citation_commands.add(match.start())
        kind = "citation" if match[1] else "bibliography"
        group = 2 if match[1] else 4
        values = list(re.finditer(r"[^,]+", match[group]))
        if any(not value.strip() for value in match[group].split(",")):
            issues.add("unsupported_source")
            continue
        if kind == "bibliography" and len(values) != 1:
            issues.add("unsupported_source")
            continue
        if len(values) > 32:
            issues.add("record_limit")
        for target in values[:32]:
            if len(relationships) >= MAX_RECORDS:
                issues.add("record_limit")
                break
            start, end = _trim(
                source,
                match.start(group) + target.start(),
                match.start(group) + target.end(),
            )
            value = source[start:end]
            if not re.fullmatch(r"[A-Za-z0-9:._/+@-]{1,256}", value):
                issues.add("unsupported_source")
                continue
            location = span(start, end)
            expression = next(
                (
                    e
                    for e in expressions
                    if e.content_span.start_byte <= location.start_byte
                    and location.end_byte <= e.content_span.end_byte
                ),
                None,
            )
            row = (
                next(
                    (
                        r
                        for r in expression.rows
                        if r.source_span.start_byte <= location.start_byte
                        and location.end_byte <= r.source_span.end_byte
                    ),
                    None,
                )
                if expression
                else None
            )
            relationships.append(
                SourceRelationship(
                    relationship_id=identity("relationship", start, end),
                    kind=kind,
                    source_span=location,
                    source_sha256=_sha(data[offsets[start] : offsets[end]]),
                    target_sha256=_sha(value.encode()),
                    expression_id=expression.expression_id if expression else None,
                    row_id=row.row_id if row else None,
                    command_span=span(match.start(), match.end()),
                    command_sha256=_sha(
                        data[offsets[match.start()] : offsets[match.end()]]
                    ),
                )
            )
        if len(relationships) >= MAX_RECORDS:
            issues.add("record_limit")
            break
    for match in re.finditer(r"\\(?:citep|citet|cite|bibitem)\b", masked):
        if match.start() not in citation_commands:
            issues.add("unsupported_source")
            break
    bibliography = defaultdict(list)
    for rel in relationships:
        if rel.kind == "bibliography":
            bibliography[rel.target_sha256].append(rel.relationship_id)
    relationships = [
        (
            r.model_copy(
                update={
                    "target_bibliography_ids": bibliography[r.target_sha256],
                    "target_status": (
                        "authored_target_match"
                        if len(bibliography[r.target_sha256]) == 1
                        else (
                            "ambiguous"
                            if bibliography[r.target_sha256]
                            else "unresolved"
                        )
                    ),
                }
            )
            if r.kind == "citation"
            else r
        )
        for r in relationships
    ]
    relationships.sort(key=lambda r: r.source_span.start_byte)
    return SourceEquationProvenance(
        source_sha256=digest,
        source_bytes=len(data),
        expressions=expressions,
        relationships=relationships,
        issues=sorted(issues),
    )


def _name(tag):
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def _tree_digest(node, *, presentation=False):
    # Hash a canonical event stream (including attributes and exact token text).
    # Explicit stack keeps hostile nested XML from recursing in Python.
    events, stack = [], [(node, False)]
    while stack:
        element, closing = stack.pop()
        if presentation and _name(element.tag) in {"annotation", "annotation-xml"}:
            continue
        if closing:
            events.append(["end", element.tag, element.tail or ""])
            continue
        attrs = {
            k: v
            for k, v in element.attrib.items()
            if not (presentation and _name(k) in {"tex", "alttext"})
        }
        events.append(["start", element.tag, sorted(attrs.items()), element.text or ""])
        stack.append((element, True))
        stack.extend((child, False) for child in reversed(list(element)))
    return _sha(json.dumps(events, ensure_ascii=True, separators=(",", ":")).encode())


def _observed_node(node, index, identities, kind):
    annotations = []
    for key in ("tex", "alttext"):
        if node.get(key) is not None:
            annotations.append(node.get(key))
    all_nodes = list(node.iter())
    for child in all_nodes:
        if _name(child.tag) == "annotation" and child.get("encoding", "").lower() in {
            "application/x-tex",
            "application/x-latex",
            "text/x-tex",
        }:
            annotations.append("".join(child.itertext()))
    distinct_annotations = sorted({_sha(text.encode()) for text in annotations})
    annotation_hashes = distinct_annotations[:8]
    matched = (
        sorted({i for h in annotation_hashes for i in identities.get(h, [])})
        if kind not in {"docx", "pdf"} and len(distinct_annotations) == 1
        else []
    )
    if len(matched) > MAX_RECORDS:
        matched = []  # A clipped candidate set must never become a unique mapping.
    return RepresentationNode(
        node_index=index,
        node_sha256=_tree_digest(node),
        structure_sha256=_tree_digest(node, presentation=True),
        annotation_sha256=annotation_hashes,
        source_ids=matched,
        association=(
            "exact_annotation"
            if len(matched) == 1
            else "ambiguous" if matched else "unmapped"
        ),
        observations_truncated=len(distinct_annotations) > 8
        or any(
            sum(_name(n.tag) in names for n in all_nodes) > 64
            for names in (
                {"XMath"},
                {"XMApp", "apply", "nary"},
                {"msub", "msup", "msubsup", "mmultiscripts", "sSub", "sSup", "sSubSup"},
            )
        ),
        intermediate_semantics_sha256=[
            _tree_digest(n)
            for n in [n for n in all_nodes if _name(n.tag) == "XMath"][:64]
        ],
        operator_scope_sha256=[
            _tree_digest(n)
            for n in [
                n for n in all_nodes if _name(n.tag) in {"XMApp", "apply", "nary"}
            ][:64]
        ],
        script_attachment_sha256=[
            _tree_digest(n)
            for n in [
                n
                for n in all_nodes
                if _name(n.tag)
                in {
                    "msub",
                    "msup",
                    "msubsup",
                    "mmultiscripts",
                    "sSub",
                    "sSup",
                    "sSubSup",
                }
            ][:64]
        ],
    )


def observe_representation(
    source: str, candidate: Path, kind: str
) -> EquationRepresentationTrace:
    """Inspect saved bytes; PDF and OMML have no trustworthy source association."""
    if kind not in {"latexml", "html", "mathml", "docx", "pdf"}:
        raise ValueError("Unsupported representation")
    provenance = source_provenance(source)
    identities = defaultdict(list)
    for expression in provenance.expressions:
        identities[expression.content_sha256].append(expression.expression_id)
        for row in expression.rows:
            identities[row.content_sha256].append(row.row_id)
    all_ids = sorted(i for values in identities.values() for i in values)
    issues, nodes, data, status = [], [], None, "not_assessed"
    try:
        with Path(candidate).open("rb") as stream:
            data = stream.read(MAX_CANDIDATE_BYTES + 1)
        if len(data) > MAX_CANDIDATE_BYTES:
            issues.append("candidate_limit")
            data = None
        elif kind == "pdf":
            issues.append("association_unavailable")
        else:
            xml = data
            if kind == "docx":
                issues.append("association_unavailable")
                with ZipFile(BytesIO(data)) as archive:
                    info = archive.getinfo("word/document.xml")
                    if info.file_size > MAX_CANDIDATE_BYTES:
                        raise OverflowError
                    with archive.open(info) as stream:
                        xml = stream.read(MAX_CANDIDATE_BYTES + 1)
                    if len(xml) > MAX_CANDIDATE_BYTES:
                        raise OverflowError
            elif kind == "html":
                # HTML is not necessarily XML; parse only bounded MathML fragments.
                soup = BeautifulSoup(data, "html.parser")
                fragments = soup.find_all("math", limit=MAX_NODES + 1)
                # Check ancestry and tree counts before serializing: otherwise
                # nested roots repeat a large subtree once per selected root.
                for fragment in fragments:
                    if (
                        fragment.find_parent("math") is not None
                        or fragment.find("math") is not None
                    ):
                        raise ValueError("Nested math roots")
                tree_count = 0
                parts, byte_count = [], len(b"<root></root>")
                for fragment in fragments:
                    for _ in fragment.descendants:
                        tree_count += 1
                        if tree_count > MAX_TREE_NODES:
                            raise OverflowError
                    part = str(fragment).encode()
                    byte_count += len(part)
                    if byte_count > MAX_CANDIDATE_BYTES:
                        raise OverflowError
                    parts.append(part)
                xml = b"<root>" + b"".join(parts) + b"</root>"
            root = SafeET.fromstring(
                xml,
                # LaTeXML may name a DTD. Expat does not fetch it; entity
                # declarations and every external reference remain forbidden.
                forbid_dtd=kind != "latexml",
                forbid_entities=True,
                forbid_external=True,
            )
            elements = []
            for element in root.iter():
                elements.append(element)
                if len(elements) > MAX_TREE_NODES:
                    raise OverflowError
            targets = [
                n
                for n in elements
                if _name(n.tag)
                == (
                    "Math"
                    if kind == "latexml"
                    else "oMath" if kind == "docx" else "math"
                )
            ]
            # Nested math roots are not separate trustworthy associations. Reject
            # them rather than repeatedly hashing overlapping hostile subtrees.
            target_tag = (
                "Math" if kind == "latexml" else "oMath" if kind == "docx" else "math"
            )
            for target in targets[:MAX_NODES]:
                descendants = list(target.iter())
                if any(_name(n.tag) == target_tag for n in descendants[1:]):
                    raise ValueError("Nested math roots")
                if (
                    len(descendants) > 4096
                    or sum(
                        len(n.text or "")
                        + len(n.tail or "")
                        + sum(len(v) for v in n.attrib.values())
                        for n in descendants
                    )
                    > 1024 * 1024
                ):
                    raise OverflowError
            if len(targets) > MAX_NODES:
                issues.append("node_limit")
            nodes = [
                _observed_node(n, i, identities, kind)
                for i, n in enumerate(targets[:MAX_NODES])
            ]
            status = "observed"
    except FileNotFoundError:
        issues.append("candidate_missing")
    except OSError:
        issues.append("candidate_unreadable")
    except OverflowError:
        issues.append("tree_limit")
    except (
        ValueError,
        KeyError,
        BadZipFile,
        SafeET.ParseError,
        SafeET.DTDForbidden,
        SafeET.EntitiesForbidden,
        SafeET.ExternalReferenceForbidden,
    ):
        issues.append("candidate_malformed")
    mapped = {
        i for n in nodes if n.association == "exact_annotation" for i in n.source_ids
    }
    return EquationRepresentationTrace(
        representation=kind,
        source_sha256=provenance.source_sha256,
        source=provenance,
        candidate_sha256=_sha(data) if data is not None else None,
        status=status,
        nodes=nodes,
        unmapped_source_ids=sorted(set(all_ids) - mapped),
        issues=issues,
    )


def public_equation_trace(value):
    """Revalidate nested objects, including model_construct/model_copy values."""
    return EquationRepresentationTrace.model_validate(value).model_dump(mode="json")
