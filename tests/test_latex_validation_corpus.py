"""Bounded #444 replay: source preservation and compiler/refusal behavior.

This does not grade mathematical meaning, PDF/UA conformance or reader access.
The compiler replay is explicit opt-in; deterministic preservation always runs.
"""

import hashlib
import json
from pathlib import Path

import pytest

from src.education.remediation.base import RemediationConfig
from src.education.remediation.latex_remediator import LatexRemediator

ROOT = Path(__file__).parent / "fixtures/latex_validation"
CASES = json.loads((ROOT / "corpus.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_corpus_remediation_preserves_source_bytes(tmp_path, case):
    source = tmp_path / "source.tex"
    data = (ROOT / case["file"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == case["sha256"]
    source.write_bytes(data)
    remediator = LatexRemediator(
        str(source), [], RemediationConfig(use_ai=False, latex_output_formats=["tex"])
    )
    remediator._load_document()
    output = remediator._save_document(remediator._modified_content)
    assert Path(output) != source
    assert Path(output).read_bytes() == source.read_bytes() == data
    assert remediator.result.latex_pdf_validation is None
