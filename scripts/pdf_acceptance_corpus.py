#!/usr/bin/env python3
"""Generate and run the required synthetic PDF remediation acceptance corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tempfile
import time
from typing import Any, Literal

import pikepdf
from pikepdf import Array, Dictionary, Name, String
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_CASE_IDS = (
    "accessible-baseline",
    "metadata",
    "headings-reading-order",
    "links",
    "tables",
    "forms",
    "images-charts",
    "math-stem",
)
ALLOWED_STAGES = frozenset(
    {"scan", "remediation", "publication", "validation", "rescan"}
)
CLAIM_BOUNDARY = (
    "Machine observations only; not proof of WCAG, PDF/UA, or legal conformance."
)
MAX_MANIFEST_BYTES = 128 * 1024


class CorpusContractError(ValueError):
    """The checked-in corpus contract is incomplete, unsafe, or malformed."""


class MachineAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        "issue_type_present",
        "issue_type_absent",
        "issue_message_contains",
        "text_contains",
    ]
    value: str = Field(min_length=1, max_length=200)


class CorpusCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    fixture: str = Field(pattern=r"^[a-z0-9][a-z0-9/_-]*\.pdf$")
    stages: tuple[str, ...]
    latex_aware: bool = False
    machine_assertions: tuple[MachineAssertion, ...] = Field(min_length=1)
    remediation_issue_types: tuple[str, ...] = ()
    review_outcome: Literal["human_review_required"] | None = None

    @model_validator(mode="after")
    def validate_case(self) -> "CorpusCase":
        fixture = PurePosixPath(self.fixture)
        if fixture.is_absolute() or ".." in fixture.parts or "\\" in self.fixture:
            raise ValueError("fixture path is unsafe")
        if not self.stages or self.stages[0] != "scan":
            raise ValueError("every case must begin with scan")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("case stages must be unique")
        if any(stage not in ALLOWED_STAGES for stage in self.stages):
            raise ValueError("case stage is unknown")
        remediation_stages = {
            "remediation",
            "publication",
            "validation",
            "rescan",
        }
        declares_remediation = bool(remediation_stages & set(self.stages))
        if declares_remediation and not self.remediation_issue_types:
            raise ValueError("remediation stages require governed issue types")
        if not declares_remediation and self.remediation_issue_types:
            raise ValueError("scan-only cases cannot declare remediation issue types")
        return self


class ExtendedCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    status: Literal["quarantined", "skipped"]
    reason: Literal["live_provider_required"]


class CorpusManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["pdf-remediation-acceptance-corpus-v1"]
    corpus_version: Literal["synthetic-pdf-v1"]
    generated_by: Literal["scripts/pdf_acceptance_corpus.py"]
    origin: Literal["repository-authored synthetic content"]
    redistribution: Literal["CC0-1.0"]
    normal_ci_budget_seconds: int = Field(ge=1, le=120)
    required_cases: tuple[CorpusCase, ...]
    extended_cases: tuple[ExtendedCase, ...]

    @model_validator(mode="after")
    def validate_inventory(self) -> "CorpusManifest":
        ids = tuple(case.id for case in self.required_cases)
        if ids != REQUIRED_CASE_IDS:
            raise ValueError("required case inventory is incomplete or reordered")
        fixtures = tuple(case.fixture for case in self.required_cases)
        if len(set(fixtures)) != len(fixtures):
            raise ValueError("fixture paths must be unique")
        if not self.extended_cases:
            raise ValueError("provider-dependent cases require explicit metadata")
        return self


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CorpusContractError("manifest contains duplicate keys")
        value[key] = item
    return value


def load_manifest(path: str | Path) -> CorpusManifest:
    """Load an exact, bounded corpus manifest and fail closed on drift."""
    manifest_path = Path(path)
    try:
        raw = manifest_path.read_bytes()
    except OSError as error:
        raise CorpusContractError("manifest unavailable") from error
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise CorpusContractError("manifest size is invalid")
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        return CorpusManifest.model_validate(payload)
    except (CorpusContractError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CorpusContractError("manifest JSON is invalid") from error
    except ValidationError as error:
        raise CorpusContractError("manifest contract is invalid") from error


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pdf_text(value: str) -> bytes:
    safe = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return safe.encode("ascii")


def _xmp(title: str) -> bytes:
    return (
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '<rdf:Description rdf:about="" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/">\n'
        '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">'
        f"{title}"
        "</rdf:li></rdf:Alt></dc:title><pdfuaid:part>1</pdfuaid:part>\n"
        "</rdf:Description></rdf:RDF></x:xmpmeta>\n"
        '<?xpacket end="w"?>\n'
    ).encode("utf-8")


def _write_pdf(
    path: Path,
    *,
    text_lines: tuple[str, ...],
    title: str | None = "Synthetic accessibility acceptance fixture",
    language: str | None = "en-AU",
    tagged: bool = True,
    table: bool = False,
    form: bool = False,
    link: bool = False,
    image: bool = False,
) -> None:
    pdf = pikepdf.new()
    font = pdf.make_indirect(
        Dictionary(
            Type=Name.Font,
            Subtype=Name.Type1,
            BaseFont=Name.Helvetica,
        )
    )
    resources = Dictionary(Font=Dictionary(F1=font))
    content: list[bytes] = []
    document_lines = text_lines + (
        "Repository-authored synthetic content for deterministic acceptance scanning.",
    )
    for index, line in enumerate(document_lines):
        y = 730 - (index * 24)
        content.append(
            b"BT /F1 12 Tf 72 "
            + str(y).encode("ascii")
            + b" Td ("
            + _pdf_text(line)
            + b") Tj ET"
        )

    if table:
        content.extend(
            [
                b"72 470 360 120 re S",
                b"72 510 m 432 510 l S",
                b"72 550 m 432 550 l S",
                b"192 470 m 192 590 l S",
                b"312 470 m 312 590 l S",
                b"BT /F1 10 Tf 82 566 Td (Name) Tj ET",
                b"BT /F1 10 Tf 202 566 Td (Status) Tj ET",
                b"BT /F1 10 Tf 322 566 Td (Count) Tj ET",
                b"BT /F1 10 Tf 82 526 Td (Alpha) Tj ET",
                b"BT /F1 10 Tf 202 526 Td (Open) Tj ET",
                b"BT /F1 10 Tf 322 526 Td (3) Tj ET",
                b"BT /F1 10 Tf 82 486 Td (Beta) Tj ET",
                b"BT /F1 10 Tf 202 486 Td (Closed) Tj ET",
                b"BT /F1 10 Tf 322 486 Td (5) Tj ET",
            ]
        )

    if image:
        width, height = 32, 24
        pixels = bytearray()
        for y in range(height):
            for x in range(width):
                bar = x < 8 or (8 <= x < 16 and y > 7) or (16 <= x < 24 and y > 13)
                pixels.extend((30, 96, 180) if bar else (245, 247, 250))
        image_stream = pdf.make_stream(bytes(pixels))
        image_stream[Name.Type] = Name.XObject
        image_stream[Name.Subtype] = Name.Image
        image_stream[Name.Width] = width
        image_stream[Name.Height] = height
        image_stream[Name.ColorSpace] = Name.DeviceRGB
        image_stream[Name.BitsPerComponent] = 8
        resources[Name.XObject] = Dictionary(Im0=image_stream)
        content.append(b"q 240 0 0 180 72 430 cm /Im0 Do Q")

    stream = b"\n".join(content)
    if tagged:
        stream = b"/P <</MCID 0>> BDC\n" + stream + b"\nEMC"
    page = pikepdf.Page(
        Dictionary(
            Type=Name.Page,
            MediaBox=Array([0, 0, 612, 792]),
            Contents=pdf.make_stream(stream),
            Resources=resources,
        )
    )
    pdf.pages.append(page)
    page_obj = pdf.pages[0].obj

    if language is not None:
        pdf.Root[Name.Lang] = String(language)
    if title is not None:
        pdf.docinfo[Name.Title] = String(title)
        metadata = pdf.make_stream(_xmp(title))
        metadata[Name.Type] = Name.Metadata
        metadata[Name.Subtype] = Name.XML
        pdf.Root[Name.Metadata] = metadata
        pdf.Root[Name.ViewerPreferences] = Dictionary(DisplayDocTitle=True)

    if tagged:
        paragraph = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, K=0))
        document = pdf.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name.Document, K=Array([paragraph]))
        )
        parent_tree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([paragraph])])))
        pdf.Root[Name.StructTreeRoot] = pdf.make_indirect(
            Dictionary(
                Type=Name.StructTreeRoot,
                K=document,
                ParentTree=parent_tree,
                ParentTreeNextKey=1,
            )
        )
        pdf.Root[Name.MarkInfo] = Dictionary(Marked=True)
        page_obj[Name.StructParents] = 0

    annotations = Array()
    if link:
        annotation = pdf.make_indirect(
            Dictionary(
                Type=Name.Annot,
                Subtype=Name.Link,
                Rect=Array([72, 620, 250, 642]),
                A=Dictionary(
                    Type=Name.Action,
                    S=Name.URI,
                    URI=String("https://example.com/synthetic-guide"),
                ),
            )
        )
        annotations.append(annotation)

    if form:
        field = pdf.make_indirect(
            Dictionary(
                Type=Name.Annot,
                Subtype=Name.Widget,
                FT=Name.Tx,
                T=String("Field1"),
                Rect=Array([72, 580, 280, 610]),
                P=page_obj,
            )
        )
        annotations.append(field)
        pdf.Root[Name.AcroForm] = pdf.make_indirect(Dictionary(Fields=Array([field])))

    if annotations:
        page_obj[Name.Annots] = annotations

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(
        path,
        static_id=True,
        compress_streams=True,
        recompress_flate=True,
        object_stream_mode=pikepdf.ObjectStreamMode.disable,
        normalize_content=True,
    )


def _generate_case(case_id: str, path: Path) -> None:
    common = {
        "accessible-baseline": dict(
            text_lines=("Accessible synthetic baseline", "Plain paragraph content"),
        ),
        "metadata": dict(
            text_lines=("Synthetic metadata failure", "Title and language are absent"),
            title=None,
            language=None,
        ),
        "headings-reading-order": dict(
            text_lines=("Heading level one", "Heading level three", "Body content"),
            tagged=False,
        ),
        "links": dict(
            text_lines=(
                "Synthetic link failure",
                "A link annotation has no accessible text",
            ),
            link=True,
        ),
        "tables": dict(
            text_lines=("Synthetic table failure",),
            table=True,
        ),
        "forms": dict(
            text_lines=("Synthetic form failure", "The field has no accessible label"),
            form=True,
        ),
        "images-charts": dict(
            text_lines=(
                "Synthetic chart failure",
                "Bar chart of neutral example values",
            ),
            image=True,
        ),
        "math-stem": dict(
            text_lines=(
                "Synthetic math failure",
                "Equation formula theorem proof",
                "sin x = y = z and cos x = y = z",
                "integral sum square root infinity",
            ),
        ),
    }
    try:
        options = common[case_id]
    except KeyError as error:
        raise CorpusContractError("generator case is unknown") from error
    _write_pdf(path, **options)


def _contained_fixture(root: Path, fixture: str) -> Path:
    candidate = (root / fixture).resolve()
    resolved_root = root.resolve()
    if not candidate.is_relative_to(resolved_root):
        raise CorpusContractError("fixture escaped corpus root")
    return candidate


def generate_corpus(
    manifest: CorpusManifest, output_root: str | Path
) -> dict[str, str]:
    """Generate every required source and return its content identity."""
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for case in manifest.required_cases:
        path = _contained_fixture(root, case.fixture)
        _generate_case(case.id, path)
        hashes[case.id] = sha256_file(path)
    return hashes


def _issue_projection(issues: list[dict[str, Any]]) -> tuple[set[str], list[str]]:
    issue_types: set[str] = set()
    messages: list[str] = []
    for issue in issues:
        issue_type = issue.get("issue_type")
        if isinstance(issue_type, str) and issue_type:
            issue_types.add(issue_type)
        message = issue.get("message")
        if isinstance(message, str):
            messages.append(message)
            if "image missing alternative text" in message.casefold():
                issue_types.add("image_missing_alt")
    return issue_types, messages


def _assert_machine_observations(
    case: CorpusCase,
    *,
    issue_types: set[str],
    messages: list[str],
    text: str,
) -> list[str]:
    failures: list[str] = []
    for assertion in case.machine_assertions:
        if assertion.kind == "issue_type_present":
            passed = assertion.value in issue_types
        elif assertion.kind == "issue_type_absent":
            passed = assertion.value not in issue_types
        elif assertion.kind == "issue_message_contains":
            passed = any(
                assertion.value.casefold() in item.casefold() for item in messages
            )
        else:
            passed = assertion.value.casefold() in text.casefold()
        if not passed:
            failures.append(f"{assertion.kind}:{assertion.value}")
    return failures


def _scan(case: CorpusCase, path: Path) -> Any:
    from src.education.pdf_processor import PDFProcessor

    processor = PDFProcessor(
        generate_alt_text=False,
        validate_alt_text=False,
        latex_aware=case.latex_aware,
    )
    return processor.process_pdf(str(path), original_filename=path.name)


def _run_case(case: CorpusCase, source_root: Path, output_root: Path) -> dict[str, Any]:
    source = _contained_fixture(source_root, case.fixture)
    source_before = sha256_file(source)
    scan = _scan(case, source)
    issue_types, messages = _issue_projection(scan.issues)
    failures = _assert_machine_observations(
        case,
        issue_types=issue_types,
        messages=messages,
        text=scan.html_output or "",
    )
    stages: dict[str, str] = {"scan": "failed" if failures else "passed"}
    output_path: Path | None = None
    output_sha256: str | None = None
    new_findings: list[str] = []

    if "remediation" in case.stages and not failures:
        from src.education.remediation.base import RemediationConfig
        from src.education.remediation.pdf_remediator import PdfRemediator

        selected = [
            issue
            for issue in scan.issues
            if issue.get("issue_type") in set(case.remediation_issue_types)
        ]
        case_output = output_root / case.id
        case_output.mkdir(parents=True, exist_ok=True)
        result = PdfRemediator(
            str(source),
            selected,
            RemediationConfig(
                use_ai=False,
                verify_fixes=False,
                output_directory=str(case_output),
            ),
        ).remediate()
        if not result.success or not result.output_file:
            failures.append("remediation_failed")
            stages["remediation"] = "failed"
        else:
            stages["remediation"] = "passed"
            produced = Path(result.output_file).resolve()
            if not produced.is_relative_to(case_output.resolve()):
                failures.append("publication_escaped_output_root")
                stages["publication"] = "failed"
            else:
                output_path = produced
                stages["publication"] = "passed"

    if "validation" in case.stages and output_path is not None:
        try:
            with pikepdf.open(output_path) as output_pdf:
                valid = len(output_pdf.pages) > 0
        except pikepdf.PdfError:
            valid = False
        stages["validation"] = "passed" if valid else "failed"
        if not valid:
            failures.append("output_validation_failed")

    if "rescan" in case.stages and output_path is not None and not failures:
        rescanned = _scan(case, output_path)
        rescanned_types, _rescanned_messages = _issue_projection(rescanned.issues)
        new_findings = sorted(rescanned_types - issue_types)
        governed_remaining = sorted(set(case.remediation_issue_types) & rescanned_types)
        if governed_remaining:
            failures.extend(
                f"remediation_remaining:{item}" for item in governed_remaining
            )
        if new_findings:
            failures.extend(f"new_finding:{item}" for item in new_findings)
        stages["rescan"] = "failed" if governed_remaining or new_findings else "passed"

    source_after = sha256_file(source)
    if source_before != source_after:
        failures.append("source_mutated")
    if output_path is not None:
        output_sha256 = sha256_file(output_path)
        if source.resolve() == output_path.resolve() or source_after == output_sha256:
            failures.append("output_identity_not_distinct")
    for stage in case.stages:
        if stage not in stages:
            stages[stage] = "failed"
            failures.append(f"stage_not_executed:{stage}")

    return {
        "id": case.id,
        "status": "failed" if failures else "passed",
        "stages": stages,
        "assertion_failures": failures,
        "review_outcome": case.review_outcome,
        "source_path": source.relative_to(source_root.parent).as_posix(),
        "source_sha256_before": source_before,
        "source_sha256_after": source_after,
        "output_path": (
            output_path.relative_to(output_root.parent).as_posix()
            if output_path is not None
            else None
        ),
        "output_sha256": output_sha256,
        "new_governed_findings": new_findings,
    }


def run_required_corpus(
    manifest_path: str | Path, work_root: str | Path
) -> dict[str, Any]:
    """Run the eight-case core corpus with no provider or network dependency."""
    started = time.monotonic()
    manifest = load_manifest(manifest_path)
    work = Path(work_root)
    source_root = work / "source"
    output_root = work / "output"
    if source_root.exists() or output_root.exists():
        raise CorpusContractError("corpus run directories already exist")
    generate_corpus(manifest, source_root)
    for case in manifest.required_cases:
        _contained_fixture(source_root, case.fixture).chmod(0o444)
    cases = [
        _run_case(case, source_root, output_root) for case in manifest.required_cases
    ]
    duration = round(time.monotonic() - started, 3)
    passed = sum(case["status"] == "passed" for case in cases)
    failed = sum(case["status"] == "failed" for case in cases)
    extended_cases = [case.model_dump() for case in manifest.extended_cases]
    skipped = sum(case["status"] == "skipped" for case in extended_cases)
    quarantined = sum(case["status"] == "quarantined" for case in extended_cases)
    status = (
        "passed"
        if failed == 0 and duration <= manifest.normal_ci_budget_seconds
        else "failed"
    )
    return {
        "schema_version": "pdf-remediation-acceptance-report-v1",
        "corpus_version": manifest.corpus_version,
        "status": status,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "quarantined": quarantined,
        "duration_seconds": duration,
        "budget_seconds": manifest.normal_ci_budget_seconds,
        "provider_calls": 0,
        "claim_boundary": CLAIM_BOUNDARY,
        "cases": cases,
        "extended_cases": extended_cases,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the required synthetic PDF remediation acceptance corpus."
    )
    parser.add_argument(
        "--manifest",
        default="tests/fixtures/pdf_acceptance/manifest.json",
    )
    parser.add_argument("--work-dir")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.work_dir:
            report = run_required_corpus(args.manifest, args.work_dir)
        else:
            with tempfile.TemporaryDirectory(prefix="aelira-pdf-corpus-") as temp:
                report = run_required_corpus(args.manifest, temp)
    except CorpusContractError:
        print("corpus_contract_invalid", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
