"""Research experiment boundaries; no compiler or conformance result is mocked."""

import copy
import json

import pytest

from scripts import evaluate_latex_pdf_profiles as profiles


@pytest.fixture
def control():
    return (profiles.FIXTURES / "S01.tex").read_text()


@pytest.mark.parametrize("profile", profiles.PROFILES)
def test_profile_preserves_authored_body(control, profile):
    variant = profiles.profile_source(control, profile, "en")
    assert (
        variant.split(r"\begin{document}", 1)[1]
        == control.split(r"\begin{document}", 1)[1]
    )
    if profile == "untagged":
        assert variant == control
    else:
        assert r"\DocumentMetadata" in variant


@pytest.mark.parametrize("profile", ("modern-ua1", "modern-ua2"))
def test_german_metadata_is_explicit_and_preserved(profile):
    source = (profiles.CORPUS / "P04/main.tex").read_text()
    variant = profiles.profile_source(source, profile, "de")
    assert "lang={de}" in variant
    assert r"\usepackage[ngerman]{babel}" in variant
    assert r"\selectlanguage{ngerman}" in variant
    assert "lang={en}" not in variant


def test_existing_profile_refusal_is_not_bypassed_for_german_control():
    source = (profiles.CORPUS / "P04/main.tex").read_text()
    with pytest.raises(
        profiles.ProfileRefused, match="application_structure_profile_refused"
    ):
        profiles.profile_source(source, "existing-ua1", "de")


def test_unknown_profile_language_and_preexisting_metadata_refused(control):
    for profile, language in (("auto", "en"), ("modern-ua2", "unknown")):
        with pytest.raises(ValueError):
            profiles.profile_source(control, profile, language)
    with pytest.raises(ValueError):
        profiles.profile_source(r"\DocumentMetadata{}" + control, "modern-ua2", "en")


def test_complete_context_preserves_multifile_project_and_negatives():
    lock = json.loads((profiles.FIXTURES / "runtime.json").read_text())
    rows = profiles.source_contexts(lock)
    assert len(rows) == 30
    project = next(row for row in rows if row["id"] == "P01")
    assert set(project["files"]) == {
        "P01/main.tex",
        "P01/macros.tex",
        "P01/chapters/one.tex",
    }
    missing = next(row for row in rows if row["id"] == "N02")
    assert set(missing["files"]) == {"N02/main.tex"}
    assert b"chapters/absent" in missing["files"]["N02/main.tex"]


def test_changed_control_or_inventory_cannot_satisfy_experiment():
    lock = json.loads((profiles.FIXTURES / "runtime.json").read_text())
    mutated = copy.deepcopy(lock)
    mutated["controls"]["S01"] = "0" * 64
    with pytest.raises(ValueError, match="changed"):
        profiles.source_contexts(mutated)
    del lock["controls"]["S04"]
    with pytest.raises(ValueError, match="30 contexts"):
        profiles.source_contexts(lock)


def test_replacement_between_compiler_and_validator_is_refused(tmp_path, monkeypatch):
    pdf = tmp_path / "candidate.pdf"
    pdf.write_bytes(b"replacement")
    monkeypatch.setattr(
        profiles, "run", lambda *a, **k: pytest.fail("Must not launch validator")
    )
    with pytest.raises(ValueError, match="final compiler output"):
        profiles.validate_pdf(
            "java",
            tmp_path / "cli.jar",
            "0" * 64,
            pdf,
            "ua2",
            profiles.digest(b"original"),
        )


def test_changed_validator_implementation_is_refused(tmp_path, monkeypatch):
    pdf = tmp_path / "candidate.pdf"
    pdf.write_bytes(b"original")
    jar = tmp_path / "cli.jar"
    jar.write_bytes(b"different implementation")
    monkeypatch.setattr(
        profiles, "run", lambda *a, **k: pytest.fail("Must not launch validator")
    )
    with pytest.raises(ValueError, match="implementation changed"):
        profiles.validate_pdf(
            "java", jar, "0" * 64, pdf, "ua2", profiles.digest(pdf.read_bytes())
        )
