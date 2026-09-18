#!/usr/bin/env python3
"""Run bounded saved-package preservation cases; never infer conformance."""

from __future__ import annotations

import argparse
from collections import Counter
from importlib.metadata import version
import json
from pathlib import Path
import sys
import time
from zipfile import ZipFile

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.office_corpus_fixtures import generate_sources  # noqa: E402
from scripts.pdf_acceptance_corpus import sha256_file  # noqa: E402

NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}
CASE_IDS = tuple(
    f"{kind}-{mode}"
    for kind in ("docx", "pptx", "xlsx")
    for mode in ("repair", "control")
)
CLAIM_BOUNDARY = "Saved-package machine observations only; no rendered, semantic, assistive-technology or conformance claim."
MANIFEST = ROOT / "tests/fixtures/office_preservation/manifest.json"


def _package(path):
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate package members")
        return {name: archive.read(name) for name in names}


def _canonical(name, payload, kind, repair):
    if not name.endswith((".xml", ".rels")):
        return payload
    root = etree.fromstring(
        payload, etree.XMLParser(resolve_entities=False, no_network=True)
    )
    removable = []
    if name == "docProps/core.xml":
        removable += root.xpath("dcterms:modified", namespaces=NS)
        if repair and kind == "docx":
            removable += root.xpath("dc:title", namespaces=NS)
    if repair and kind == "xlsx" and name == "xl/worksheets/sheet1.xml":
        removable += root.xpath(
            "x:sheetViews/x:sheetView/x:pane | x:sheetViews/x:sheetView/x:selection",
            namespaces=NS,
        )
    if repair and kind == "pptx" and name == "ppt/slides/slide1.xml":
        for color in root.xpath(
            "p:cSld/p:spTree/p:sp[1]/p:txBody//a:rPr/a:solidFill/a:srgbClr",
            namespaces=NS,
        ):
            color.set("val", "INTENDED_COLOR")
    for element in removable:
        element.getparent().remove(element)
    return etree.tostring(root, method="c14n")


def compare_packages(source, candidate, kind, *, repair=False):
    """Compare every member, allowing only the declared repair and modified time.

    Exact member membership and binary bytes remain significant. XML attribute
    order is immaterial; text, relationships, targets and element order are not.
    The caller separately verifies the target mutation instead of masking it away.
    """
    before, after = _package(source), _package(candidate)
    failures = [f"missing:{name}" for name in sorted(before.keys() - after.keys())]
    failures += [f"added:{name}" for name in sorted(after.keys() - before.keys())]
    for name in sorted(before.keys() & after.keys()):
        if _canonical(name, before[name], kind, repair) != _canonical(
            name, after[name], kind, repair
        ):
            failures.append(f"changed:{name}")
    return failures


def _contract():
    value = json.loads(MANIFEST.read_text())
    expected = {
        "schema_version": "office-preservation-v1",
        "origin": "repository-authored synthetic content",
        "redistribution": "CC0-1.0",
        "required_cases": list(CASE_IDS),
    }
    if any(value.get(key) != data for key, data in expected.items()):
        raise ValueError("Office corpus contract drift")
    if not value.get("unavailable_evidence"):
        raise ValueError("Evidence limits must remain explicit")
    if any(
        item.get("status") != "not_run"
        or not item.get("owner")
        or not item.get("reason")
        for item in value["unavailable_evidence"]
    ):
        raise ValueError("Unavailable evidence cannot count as passing")
    return value


def _target_present(kind, path):
    if kind == "docx":
        from docx import Document

        return Document(path).core_properties.title == "Course workbook"
    if kind == "pptx":
        from pptx import Presentation

        return (
            str(
                Presentation(path)
                .slides[0]
                .shapes.title.text_frame.paragraphs[0]
                .runs[0]
                .font.color.rgb
            )
            == "696969"
        )
    from openpyxl import load_workbook

    workbook = load_workbook(path)
    try:
        sheet = workbook["Course results"]
        pane = sheet.sheet_view.pane
        selections = sheet.sheet_view.selection
        return (
            sheet.freeze_panes == "A2"
            and pane.ySplit == 1
            and pane.xSplit is None
            and pane.state == "frozen"
            and pane.activePane == "bottomLeft"
            and len(selections) == 1
            and selections[0].pane == "bottomLeft"
            and selections[0].activeCell == "A1"
            and selections[0].sqref == "A1"
        )
    finally:
        workbook.close()


def _run_case(case_id, source, workdir):
    from src.education.remediation.base import RemediationConfig
    from src.education.remediation.docx_remediator import DocxRemediator
    from src.education.remediation.pptx_remediator import PptxRemediator
    from src.education.remediation.xlsx_remediator import XlsxRemediator
    from src.education.remediation.office_verification import scan_office

    kind, mode = case_id.split("-")
    repair = mode == "repair"
    document_type, remediator_type, category = {
        "docx": ("word", DocxRemediator, "title"),
        "pptx": ("powerpoint", PptxRemediator, "contrast"),
        "xlsx": ("excel", XlsxRemediator, "navigation"),
    }[kind]
    before_hash = sha256_file(source)
    before = scan_office(document_type, str(source))
    selected = [issue for issue in before.issues if issue.get("category") == category]
    if repair and len(selected) != 1:
        raise ValueError(
            f"{case_id}: expected exactly one governed scanner finding, got {len(selected)}"
        )
    if not repair and selected:
        raise ValueError(f"{case_id}: control still has the governed finding")
    if kind == "docx" and selected:
        selected[0] = {
            **selected[0],
            "metadata": {
                **selected[0].get("metadata", {}),
                "suggested_title": "Course workbook",
            },
        }
    config = RemediationConfig(
        use_ai=False,
        allow_legacy_nested_ai=False,
        create_backup=False,
        output_directory=str(workdir / "output" / case_id),
        output_filename=f"saved.{kind}",
    )
    result = remediator_type(str(source), selected, config).remediate()
    if not result.success or not result.output_file:
        raise ValueError(f"{case_id}: remediation did not produce a candidate")
    candidate = Path(result.output_file)
    after = scan_office(document_type, str(candidate))
    failures = compare_packages(source, candidate, kind, repair=repair)
    if sha256_file(source) != before_hash:
        failures.append("source_modified")
    if not _target_present(kind, candidate):
        failures.append("target_not_persisted")
    if result.fixed_count != int(repair):
        failures.append("unexpected_verified_fix_count")
    if not result.verification_passed:
        failures.append("saved_scanner_verification_failed")

    def counts(scan):
        return Counter(
            (item.get("category"), item.get("location")) for item in scan.issues
        )

    new = counts(after) - counts(before)
    if new:
        failures.append("new_scanner_findings")
    return {
        "id": case_id,
        "status": "failed" if failures else "passed",
        "source": str(source.relative_to(workdir)),
        "candidate": str(candidate.relative_to(workdir)),
        "source_sha256_before": before_hash,
        "source_sha256_after": sha256_file(source),
        "candidate_sha256": sha256_file(candidate),
        "preservation_failures": failures,
        "scanner_profile": f"office-{document_type}-strict-v1",
        "findings_before": len(before.issues),
        "findings_after": len(after.issues),
        "new_findings": sum(new.values()),
        "verified_fixes": result.fixed_count,
    }


def run_corpus(workdir: Path, *, revision=None):
    contract = _contract()
    start = time.monotonic()
    sources = generate_sources(workdir / "source")
    controls = generate_sources(workdir / "control", corrected=True)
    # Controls and repair sources must be independently reproducible.
    repeat = generate_sources(workdir / "repeat")
    if any(sha256_file(sources[k]) != sha256_file(repeat[k]) for k in sources):
        raise ValueError("Source generation is not reproducible")
    cases = []
    for case_id in CASE_IDS:
        kind, mode = case_id.split("-")
        source = (sources if mode == "repair" else controls)[kind]
        cases.append(_run_case(case_id, source, workdir))
    failed = sum(case["status"] == "failed" for case in cases)
    return {
        "schema_version": "office-preservation-report-v1",
        "revision": revision,
        "manifest_sha256": sha256_file(MANIFEST),
        "runner_sha256": sha256_file(Path(__file__)),
        "fixture_generator_sha256": sha256_file(
            ROOT / "scripts/office_corpus_fixtures.py"
        ),
        "dependency_versions": {
            name: version(name)
            for name in ("python-docx", "python-pptx", "openpyxl", "lxml")
        },
        "configuration": {
            "use_ai": False,
            "allow_legacy_nested_ai": False,
            "verify_fixes": True,
        },
        "status": "failed" if failed else "passed",
        "passed": len(cases) - failed,
        "failed": failed,
        "cases": cases,
        "duration_seconds": round(time.monotonic() - start, 3),
        "unavailable_evidence": contract["unavailable_evidence"],
        "claim_boundary": CLAIM_BOUNDARY,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if len(args.revision) != 40 or any(
        c not in "0123456789abcdef" for c in args.revision
    ):
        parser.error("revision must be a full lowercase commit SHA")
    # A fresh owned directory prevents overwriting previous evidence.
    args.workdir.mkdir(parents=True, exist_ok=False)
    report = run_corpus(args.workdir, revision=args.revision)
    (args.workdir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {key: report[key] for key in ("status", "passed", "failed", "revision")}
        )
    )
    return int(report["failed"] != 0)


if __name__ == "__main__":
    raise SystemExit(main())
