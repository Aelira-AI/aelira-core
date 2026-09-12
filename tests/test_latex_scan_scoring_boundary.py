"""Queued source scoring must not include generated conversion diagnostics."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.education.latex_processor import LaTeXProcessor, MathMLConversionResult


@pytest.mark.parametrize("conversion_success", [False, True])
def test_persisted_latex_score_is_source_only(
    tmp_path, monkeypatch, conversion_success
):
    from src.api.education import scan_routes
    from src.db import database

    content = rb"\documentclass{article}\begin{document}$x$\end{document}"
    original = tmp_path / "original.tex"
    original.write_bytes(content)
    work_copy = tmp_path / "work.tex"
    work_copy.write_bytes(content)
    expected = LaTeXProcessor(use_ai=False).scan_source(content.decode())
    monkeypatch.setattr(
        LaTeXProcessor,
        "convert_equation",
        lambda *args: MathMLConversionResult(
            equation_id=1,
            latex_source="x",
            mathml_output="",
            conversion_success=conversion_success,
            wcag_compliant=False,
            error_message=(
                "Synthetic conversion failure" if not conversion_success else None
            ),
        ),
    )
    scan = SimpleNamespace(id="latex-scan", department_id="workspace-test")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = scan
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    scan_routes.process_latex_background(
        str(work_copy),
        content,
        original.name,
        scan.id,
        False,
        "user-test",
        "workspace-test",
    )
    assert db.add.call_count == 1
    stored = db.add.call_args.args[0]
    assert stored.compliance_score == expected["compliance_score"]
    assert stored.structure["conversion_issues"]
    assert all(
        row["type"] not in {"conversion_failed", "wcag_noncompliant"}
        for row in stored.issues
    )
    assert sum(
        getattr(stored, f"{severity}_issues")
        for severity in ("critical", "high", "medium", "low")
    ) == len(expected["issues"])
    assert original.read_bytes() == content
