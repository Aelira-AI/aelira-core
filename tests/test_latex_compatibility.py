"""Package decisions are source-bound observations, not fidelity guarantees."""

import pytest
from pydantic import ValidationError

from src.education.latex_compatibility import ConversionDecision, decide_html

VERSIONS = {"latexml": "0.8.8", "pandoc": "3.1.11.1"}


def decide(source, **kwargs):
    return decide_html(
        source, available={"latexml", "pandoc"}, versions=VERSIONS, **kwargs
    )


def test_physics_is_not_silently_routed_to_pandoc():
    result = decide(r"\documentclass{article}\usepackage{physics}")
    assert result.selected_route == "latexml"
    physics = next(r for r in result.requirements if r.known_name == "physics")
    assert physics.pandoc == "observed_failure"
    refused = decide_html(
        r"\usepackage{physics}", available={"pandoc"}, versions=VERSIONS
    )
    assert refused.selected_route is None
    assert "package_route_unavailable" in refused.reasons


def test_conflicting_relationship_route_is_refused():
    result = decide(r"\usepackage{physics}", prefer_pandoc=True)
    assert result.selected_route is None
    assert "requirements_conflict" in result.reasons


def test_project_profile_never_launches_latexml():
    result = decide(r"\usepackage{physics}", project=True)
    assert result.selected_route is None
    assert "project_sandbox_required" in result.reasons


def test_siunitx_and_babel_selection_have_explicit_routes():
    assert decide(r"\usepackage{siunitx}").selected_route == "pandoc"
    assert (
        decide(r"\usepackage{babel}\selectlanguage{ngerman}").selected_route == "pandoc"
    )
    assert decide(r"\usepackage{physics,siunitx}").selected_route is None


def test_unknown_requirements_stay_unchecked_and_do_not_expose_names():
    source = r"\documentclass{privatecourse}\usepackage{privatepackage}\newcommand{\privateauthor}{x}"
    result = decide(source)
    assert len(result.requirements) == 3
    assert all(r.known_name is None for r in result.requirements)
    assert all(r.latexml == r.pandoc == "unchecked" for r in result.requirements)
    assert "private" not in result.model_dump_json()
    assert "unchecked_requirements" in result.reasons


def test_versions_outside_observed_matrix_are_unchecked():
    result = decide_html(
        r"\usepackage{amsmath}", available={"pandoc"}, versions={"pandoc": "99.1"}
    )
    assert result.requirements[0].pandoc == "unchecked"
    assert "unmeasured_tool_version" in result.reasons


def test_no_tools_and_bounded_malformed_source():
    assert decide_html("Text", available=set(), versions={}).selected_route is None
    assert decide(r"\usepackage{" * 1000).selected_route is None
    assert (
        decide("".join(r"\usepackage{p%d}" % i for i in range(70))).selected_route
        is None
    )


def test_comments_do_not_change_route_and_literal_lists_are_inventory():
    result = decide(
        "% \\usepackage{physics}\n"
        + r"\documentclass{article}\usepackage{amsmath,amssymb}"
    )
    assert {r.known_name for r in result.requirements} == {
        "article",
        "amsmath",
        "amssymb",
    }
    assert all(r.kind != "macro" for r in result.requirements)


def test_public_decision_rejects_raw_logs_and_unbounded_versions():
    value = decide("Text").model_dump()
    with pytest.raises(ValidationError):
        ConversionDecision.model_validate({**value, "stderr": "/private/data"})
    with pytest.raises(ValidationError):
        ConversionDecision.model_validate(
            {**value, "tool_versions": {"pandoc": "/private/path"}}
        )


def test_matrix_lookup_binds_requirement_kind():
    result = decide(r"\usepackage{article}\documentclass{physics}")
    assert all(r.known_name is None for r in result.requirements)
    assert all(r.evidence == "none" for r in result.requirements)


def test_custom_macro_definitions_are_unchecked_and_source_bound():
    first = decide(r"\newcommand{\f}{x}")
    second = decide(r"\newcommand{\f}{changed}")
    assert first.source_sha256 != second.source_sha256
    assert first.requirements[0].latexml == "unchecked"
    assert (
        first.requirements[0].declaration_prefix_sha256
        == second.requirements[0].declaration_prefix_sha256
    )
