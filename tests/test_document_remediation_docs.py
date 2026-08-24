"""Contracts for the public document-remediation documentation."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "document-remediation"
REQUIRED_DOCS = {
    "README.md": [
        "## Format support and maturity",
        "## Scan and remediation boundaries",
        "## Deterministic and AI-assisted work",
        "## Format preservation",
        "## Managed artifacts and review",
        "## Installation and dependencies",
        "## Entry points",
        "## Limitations and human review",
        "## Source and test evidence",
    ],
    "pdf.md": [
        "## Verified capabilities",
        "## What it does not promise",
        "## Dependencies",
        "## Quick start",
        "## Output and review",
        "## Tests",
    ],
    "office.md": [
        "## DOCX",
        "## PPTX",
        "## XLSX",
        "## Dependencies",
        "## Quick start",
        "## Output and review",
        "## Tests",
    ],
    "latex.md": [
        "## Verified capabilities",
        "## What it does not promise",
        "## Dependencies",
        "## Quick start",
        "## Output and review",
        "## Tests",
    ],
}

MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_required_document_remediation_files_and_sections_exist():
    for name, sections in REQUIRED_DOCS.items():
        path = DOCS / name
        assert (
            path.is_file()
        ), f"missing canonical documentation file: {path.relative_to(ROOT)}"
        text = _text(path)
        for section in sections:
            assert section in text, f"{path.relative_to(ROOT)} missing {section!r}"


def test_readmes_link_to_the_canonical_hub_and_name_four_equal_pillars():
    root_readme = _text(ROOT / "README.md")
    examples_readme = _text(ROOT / "examples" / "README.md")
    hub = "docs/document-remediation/README.md"
    assert hub in root_readme
    assert "../docs/document-remediation/README.md" in examples_readme
    for pillar in ("documents", "LMS", "web", "media"):
        assert pillar in root_readme


def test_local_markdown_links_resolve():
    paths = [DOCS / name for name in REQUIRED_DOCS]
    paths += [ROOT / "README.md", ROOT / "examples" / "README.md"]
    failures = []
    for page in paths:
        for target in MARKDOWN_LINK.findall(_text(page)):
            target = target.strip().split()[0].strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            relative_target = target.split("#", 1)[0]
            if not relative_target:
                continue
            resolved = (page.parent / relative_target).resolve()
            if not resolved.exists():
                failures.append(f"{page.relative_to(ROOT)} -> {target}")
    assert not failures, "broken relative links:\n" + "\n".join(failures)


def test_documentation_avoids_banned_overclaims():
    corpus = "\n".join(_text(DOCS / name) for name in REQUIRED_DOCS).lower()
    banned = (
        "guarantees compliance",
        "guaranteed compliant",
        "100% accessible",
        "fully accessible output",
        "all formats have parity",
        "exactly-once",
        "unlimited scale",
    )
    for phrase in banned:
        assert phrase not in corpus, f"banned overclaim present: {phrase!r}"


def test_documented_scan_routes_exist_in_api_code():
    corpus = "\n".join(_text(DOCS / name) for name in REQUIRED_DOCS)
    route_source = _text(ROOT / "src" / "api" / "education" / "scan_routes.py")
    expected = (
        "/pdf/scan",
        "/word/scan",
        "/powerpoint/scan",
        "/excel/scan",
        "/latex/scan",
    )
    for route in expected:
        public_route = f"/education{route}"
        assert public_route in corpus, f"documentation missing API route {public_route}"
        assert f'@router.post("{route}"' in route_source


def test_documented_remediation_routes_exist_in_api_code():
    corpus = "\n".join(_text(DOCS / name) for name in REQUIRED_DOCS)
    source = _text(ROOT / "src" / "api" / "education" / "remediation_routes.py")
    expected = (
        "/remediate/{scan_id}",
        "/scans/{scan_id}/remediated",
        "/scans/{scan_id}/remediated/formats",
        "/scans/{scan_id}/artifacts/{artifact_id}",
        "/scans/{scan_id}/artifacts/{artifact_id}/download",
        "/scans/{scan_id}/artifacts/{artifact_id}/approve",
        "/scans/{scan_id}/artifacts/{artifact_id}/reject",
    )
    for route in expected:
        public_route = f"/education{route}"
        assert public_route in corpus, f"documentation missing API route {public_route}"
        assert route in source


def test_documented_cli_commands_have_current_command_sources():
    corpus = "\n".join(_text(DOCS / name) for name in REQUIRED_DOCS)
    commands = {
        "aelira scan pdf": "cli/src/commands/scan/pdf.ts",
        "aelira scan docx": "cli/src/commands/scan/docx.ts",
        "aelira scan ppt": "cli/src/commands/scan/ppt.ts",
        "aelira scan xlsx": "cli/src/commands/scan/xlsx.ts",
        "aelira scan latex": "cli/src/commands/scan/latex.ts",
        "aelira remediate": "cli/src/commands/remediate.ts",
    }
    package = _text(ROOT / "cli" / "package.json")
    assert '"aelira": "./bin/run.js"' in package
    for command, source in commands.items():
        assert command in corpus, f"documentation missing CLI command {command!r}"
        assert (ROOT / source).is_file(), f"missing CLI source for {command}: {source}"


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


def test_latex_ai_off_uses_query_parameter_not_multipart_field():
    latex = _text(DOCS / "latex.md")
    assert "?use_ollama=false" in latex
    assert not re.search(r'-F\s+["\']use_ollama=false', latex)


def test_ai_section_documents_real_defaults_and_disable_controls():
    hub = _section(
        _text(DOCS / "README.md"), "## Deterministic and AI-assisted work"
    ).lower()
    required_contracts = (
        "remediationoptions.use_ai",
        "defaults to `true`",
        "`use_ai=false`",
        "`enhance_descriptions` defaults to `true`",
        "`enhance_descriptions=false`",
        "`generate_alt_text` defaults to `false`",
        "`use_ollama` defaults to `true`",
        "`use_ollama=false`",
        "route-specific",
    )
    for contract in required_contracts:
        assert contract in hub, f"AI defaults section missing {contract!r}"


def test_managed_artifact_workflow_documents_publication_and_approval_gates():
    hub = _section(_text(DOCS / "README.md"), "## Managed artifacts and review")
    lower = hub.lower()
    publication_contracts = (
        "remediation succeeds",
        "at least one fix",
        "zero manual",
        "zero failed",
        "verification passes",
    )
    for contract in publication_contracts:
        assert contract in lower, f"artifact publication gate missing {contract!r}"

    review_contracts = (
        "/api/reviews/{scan_id}",
        "terminal",
        "at least one accepted fix",
        "approval_blockers",
        "can_approve",
    )
    for contract in review_contracts:
        assert contract in hub, f"artifact review gate missing {contract!r}"

    assert hub.index("/api/reviews/{scan_id}") < hub.index("/approve")


def test_authorization_header_inline_code_is_well_formed():
    hub = _text(DOCS / "README.md")
    assert "Authentication uses an `Authorization` header with a Bearer API key" in hub
    assert "Authentication uses `Authorization: Bearer" not in hub


def test_office_output_does_not_claim_artifacts_with_manual_or_failed_work():
    office = _section(_text(DOCS / "office.md"), "## Output and review").lower()
    assert (
        "managed artifact for review and may also report manual or failed" not in office
    )
    assert "zero manual" in office
    assert "zero failed" in office


def test_pdf_docs_describe_merged_ocr_and_immutable_original_boundaries():
    pdf = _text(DOCS / "pdf.md").lower()
    for contract in (
        "preserved in the delivered pdf",
        "per page",
        "partial direct text",
        "signed",
        "xfa",
        "english",
        "original pdf remains immutable",
        "prior valid output",
    ):
        assert contract in pdf, f"PDF OCR/original boundary missing {contract!r}"


def test_pdf_docs_describe_accessible_html_and_embedded_image_safety():
    pdf = _text(DOCS / "pdf.md").lower()
    for contract in (
        "normalized and escaped",
        "passive allowlist",
        "event attributes",
        "inline styles",
        "unsafe urls",
        "png and jpeg data urls",
        "dimension and pixel",
        "structural verification",
        "exact eof",
        "trailing data",
    ):
        assert contract in pdf, f"accessible-HTML safety boundary missing {contract!r}"


def test_managed_pdf_docs_describe_exact_claim_publication_and_cleanup():
    corpus = "\n".join(
        (
            _text(DOCS / "README.md"),
            _text(DOCS / "pdf.md"),
            _text(ROOT / "docs" / "deployment" / "self-hosting.md"),
        )
    ).lower()
    for contract in (
        "private, unlinked",
        "output claim",
        "read-only",
        "non-inheritable",
        "single owner",
        "descriptor-bound",
        "exact claimed stream",
        "size, sha-256, mime type, scan type, and filename",
        "db-first",
        "artifact id",
        "publication token",
        "cleanup warning",
        "retained path",
        "unix",
    ):
        assert contract in corpus, f"managed PDF lifecycle missing {contract!r}"


def test_operator_cleanup_recovery_names_the_real_maintenance_loop():
    self_hosting = _text(ROOT / "docs" / "deployment" / "self-hosting.md")
    for contract in (
        "python -m src.jobs.worker",
        "DURABLE_MAINTENANCE_INTERVAL_SECONDS",
        "REMEDIATION_ARTIFACT_STAGING_GRACE_SECONDS",
        "REMEDIATION_ARTIFACT_CLEANUP_BATCH_SIZE",
        "publication_cleanup_pending",
    ):
        assert contract in self_hosting
    assert "bounded artifact cleanup workflow" not in self_hosting


def test_operator_docs_describe_current_parent_cleanup_fence():
    self_hosting = _text(ROOT / "docs" / "deployment" / "self-hosting.md")
    assert "Until Task16B" not in self_hosting
    for contract in (
        "database cleanup fence",
        "publication and cleanup cannot both own",
        "do not scan the artifact directory",
    ):
        assert contract in self_hosting


def test_post_v095_pdf_hardening_is_not_claimed_as_released():
    pages = (
        ROOT / "README.md",
        ROOT / "examples" / "README.md",
        DOCS / "README.md",
        DOCS / "pdf.md",
        ROOT / "docs" / "deployment" / "self-hosting.md",
        ROOT / "SECURITY.md",
    )
    corpus = "\n".join(_text(page) for page in pages).lower()
    assert "present on `main`" in corpus
    assert "not part of v0.9.5" in corpus
    assert "future release" in corpus
    assert "no release or deployment" in corpus

    canonical_pdf = _text(DOCS / "pdf.md").lower()
    for boundary in (
        "present on `main`",
        "not part of v0.9.5",
        "future release",
        "no release or deployment",
    ):
        assert boundary in canonical_pdf
