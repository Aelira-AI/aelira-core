from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from xml.etree import ElementTree

import pytest
from pydantic import ValidationError

from src.education.chemical_formula import (
    ELEMENT_NAMES,
    ELEMENT_SYMBOLS,
    MAX_GROUP_DEPTH,
    MAX_SOURCE_LENGTH,
    ChemicalFormulaRejected,
    ChemicalFormulaV1,
    ChemicalSpeciesV1,
    ElementTermV1,
    chemical_mathml,
    chemical_speech,
    parse_chemical_notation,
    serialize_chemical_notation,
    verify_chemical_notation,
)

CORPUS_PATH = Path(__file__).parent / "fixtures" / "chemical_formula" / "manifest.json"
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def test_exact_periodic_table_allowlist_accepts_all_and_rejects_unknown_symbols():
    assert len(ELEMENT_SYMBOLS) == 118
    assert set(ELEMENT_NAMES) == set(ELEMENT_SYMBOLS)
    for symbol in ELEMENT_SYMBOLS:
        parsed = parse_chemical_notation(symbol)
        assert parsed.species.terms[0].symbol == symbol

    for invalid in ("X", "Xx", "h", "HE", "Uue"):
        with pytest.raises(ChemicalFormulaRejected):
            parse_chemical_notation(invalid)

    with pytest.raises(TypeError):
        ELEMENT_NAMES["H"] = "water"


@pytest.mark.parametrize("invalid", [0, 1000, True, 2.5, "2"])
def test_element_counts_are_strict_bounded_integers(invalid):
    with pytest.raises(ValidationError):
        ElementTermV1(term_kind="element", symbol="H", count=invalid)


def test_element_models_are_exact_and_frozen():
    with pytest.raises(ValidationError):
        ElementTermV1(term_kind="element", symbol="H", count=1, surprise=True)

    term = ElementTermV1(term_kind="element", symbol="H", count=1)
    with pytest.raises(ValidationError):
        term.count = 2


@pytest.mark.parametrize("case", CORPUS["supported"], ids=lambda case: case["id"])
def test_reviewed_supported_corpus_round_trips_with_exact_speech(case):
    verified = verify_chemical_notation(case["source"])

    assert verified.canonical_notation == case["canonical"]
    assert verified.speech == case["speech"]
    assert serialize_chemical_notation(verified.notation) == case["canonical"]
    assert parse_chemical_notation(case["canonical"]) == verified.notation
    assert verify_chemical_notation(case["canonical"]).semantic_sha256 == (
        verified.semantic_sha256
    )


@pytest.mark.parametrize("case", CORPUS["rejected"], ids=lambda case: case["id"])
def test_reviewed_refusal_corpus_returns_no_partial_semantics(case):
    with pytest.raises(ChemicalFormulaRejected):
        verify_chemical_notation(case["source"])


def test_deterministic_adversarial_strings_either_reject_or_round_trip():
    generator = random.Random(225)
    alphabet = "HCONaCl()^+-<>[]=;0123456789 abcxyz\\{}.*"
    for _ in range(1_000):
        source = "".join(
            generator.choice(alphabet) for _ in range(generator.randint(1, 96))
        )
        try:
            parsed = parse_chemical_notation(source)
        except ChemicalFormulaRejected:
            continue
        canonical = serialize_chemical_notation(parsed)
        assert parse_chemical_notation(canonical) == parsed


def test_allowed_presentation_whitespace_changes_only_source_identity():
    compact = verify_chemical_notation("N2 + 3H2 <=>[heat;Fe] 2NH3")
    spaced = verify_chemical_notation("  N2+ 3 H2 <=> [ heat ; Fe ] 2 NH3  ")

    assert compact.notation == spaced.notation
    assert compact.canonical_notation == spaced.canonical_notation
    assert compact.semantic_sha256 == spaced.semantic_sha256
    assert compact.source_sha256 != spaced.source_sha256


def test_numeric_and_collection_limits_accept_the_edge_and_reject_overflow():
    for source in ("H999", "^999H", "999H", "H^16+", "(H)999"):
        assert serialize_chemical_notation(parse_chemical_notation(source)) == source

    for source in ("H1000", "^1000H", "1000H", "H^17+", "(H)1000"):
        with pytest.raises(ChemicalFormulaRejected):
            parse_chemical_notation(source)

    assert len(parse_chemical_notation("H" * 256).species.terms) == 256
    with pytest.raises(ChemicalFormulaRejected, match="term limit"):
        parse_chemical_notation("H" * 257)

    eight_species = " + ".join(["H"] * 8)
    assert len(parse_chemical_notation(f"{eight_species} -> H").left) == 8
    with pytest.raises(ChemicalFormulaRejected, match="species"):
        parse_chemical_notation(f"{eight_species} + H -> H")


def test_condition_bounds_and_speech_are_exact_at_both_supported_edges():
    single = verify_chemical_notation("H2 ->[heat] H2")
    assert single.speech == (
        "hydrogen subscript 2 yields hydrogen subscript 2 under condition heat"
    )

    four = parse_chemical_notation("H ->[a;b;c;d] H")
    assert four.conditions == ("a", "b", "c", "d")

    for source in (
        "H ->[] H",
        "H ->[a;] H",
        "H ->[a;b;c;d;e] H",
        "H ->[bad@value] H",
        f"H ->[{'x' * 65}] H",
    ):
        with pytest.raises(ChemicalFormulaRejected):
            parse_chemical_notation(source)


def test_source_and_all_derived_projections_are_digest_bound():
    verified = verify_chemical_notation("SO4^2-")
    assert verified.source_sha256 == hashlib.sha256(b"SO4^2-").hexdigest()
    assert (
        verified.speech_sha256
        == hashlib.sha256(verified.speech.encode("utf-8")).hexdigest()
    )
    assert (
        verified.mathml_sha256
        == hashlib.sha256(verified.mathml.encode("utf-8")).hexdigest()
    )

    payload = verified.model_dump(mode="json")
    for field in (
        "source_sha256",
        "semantic_sha256",
        "speech_sha256",
        "mathml_sha256",
    ):
        tampered = {**payload, field: "0" * 64}
        with pytest.raises(ValidationError):
            type(verified).model_validate(tampered)


def test_mathml_requires_a_typed_contract_and_uses_only_passive_markup():
    with pytest.raises(TypeError):
        chemical_mathml("H2O")

    verified = verify_chemical_notation("^14C + O2 ->[heat] CO2")
    assert chemical_mathml(verified.notation) == verified.mathml

    root = ElementTree.fromstring(verified.mathml)
    allowed_tags = {
        "math",
        "mrow",
        "mi",
        "mn",
        "mo",
        "msub",
        "msup",
        "mtext",
        "mmultiscripts",
        "mprescripts",
        "none",
        "mover",
    }
    for node in root.iter():
        assert node.tag.rsplit("}", 1)[-1] in allowed_tags
        assert set(node.attrib) <= {"mathvariant"}


def test_formula_mathml_and_semantic_identity_have_exact_structural_snapshots():
    verified = verify_chemical_notation("2^14C(OH)2^3-(aq)")
    assert verified.mathml == (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><mrow><mrow><msup>'
        "<mrow><mn>2</mn><mmultiscripts>"
        '<mi mathvariant="normal">C</mi><mprescripts/><none/><mn>14</mn>'
        "</mmultiscripts><msub><mrow><mo>(</mo><mrow>"
        '<mi mathvariant="normal">O</mi><mi mathvariant="normal">H</mi>'
        "</mrow><mo>)</mo></mrow><mn>2</mn></msub></mrow>"
        "<mrow><mn>3</mn><mo>−</mo></mrow></msup><mrow><mo>(</mo>"
        '<mi mathvariant="normal">aq</mi><mo>)</mo></mrow></mrow></mrow></math>'
    )
    assert (
        verified.mathml_sha256
        == "f8a674a0a7a8dc8944160bc7b5d7f714d311656e2c9486ea543003ef555e120d"
    )
    assert (
        verified.semantic_sha256
        == "04ce720f1c4b63e73a55912f3108628f21d2c76b5c278f402738a4e4cb9ab1d1"
    )


def test_reaction_mathml_and_semantic_identity_have_exact_structural_snapshots():
    verified = verify_chemical_notation("N2 + 3H2 <=>[heat;Fe] 2NH3")
    assert verified.mathml == (
        '<math xmlns="http://www.w3.org/1998/Math/MathML"><mrow><mrow><mrow>'
        '<msub><mi mathvariant="normal">N</mi><mn>2</mn></msub></mrow></mrow>'
        '<mo>+</mo><mrow><mrow><mn>3</mn><msub><mi mathvariant="normal">H</mi>'
        "<mn>2</mn></msub></mrow></mrow><mover><mo>⇌</mo><mrow>"
        "<mtext>heat</mtext><mo>;</mo><mtext>Fe</mtext></mrow></mover>"
        '<mrow><mrow><mn>2</mn><mi mathvariant="normal">N</mi><msub>'
        '<mi mathvariant="normal">H</mi><mn>3</mn></msub></mrow></mrow>'
        "</mrow></math>"
    )
    assert (
        verified.mathml_sha256
        == "c25796c0143d9a95b2f4894f3fd9e48699ca37b07979b3355e9b2dda1fb7ce9e"
    )
    assert (
        verified.semantic_sha256
        == "1b9dd9073cedbbf03707cb27261d3e22e5fbc9e55301a1d4272f235188ba351f"
    )


def test_projection_boundary_revalidates_even_bypassed_pydantic_instances():
    forged = ChemicalFormulaV1.model_construct(
        notation_kind="chemical_formula_v1", species="H2O"
    )
    with pytest.raises(ValidationError):
        chemical_mathml(forged)


def test_speech_requires_a_typed_contract_and_never_uses_common_names():
    with pytest.raises(TypeError):
        chemical_speech("H2O")

    speech = chemical_speech(parse_chemical_notation("H2O + NaCl -> CO2"))
    assert "water" not in speech.lower()
    assert "salt" not in speech.lower()
    assert "carbon dioxide" not in speech.lower()


def test_parser_rejects_resource_overflow_before_recursive_model_validation():
    over_depth = "(" * (MAX_GROUP_DEPTH + 1) + "H" + ")" * (MAX_GROUP_DEPTH + 1)
    with pytest.raises(ChemicalFormulaRejected, match="depth"):
        parse_chemical_notation(over_depth)

    with pytest.raises(ChemicalFormulaRejected, match="length"):
        parse_chemical_notation("H" * (MAX_SOURCE_LENGTH + 1))

    with pytest.raises(ChemicalFormulaRejected, match="species"):
        parse_chemical_notation(" + ".join(["H"] * 9) + " -> H")


def test_raw_typed_contract_rejects_over_depth_before_recursive_construction():
    term = {"term_kind": "element", "symbol": "H", "count": 1}
    for _ in range(MAX_GROUP_DEPTH + 1):
        term = {"term_kind": "group", "terms": [term], "multiplier": 1}

    with pytest.raises(ValidationError, match="depth"):
        ChemicalSpeciesV1.model_validate(
            {
                "species_kind": "chemical_species_v1",
                "coefficient": 1,
                "terms": [term],
            }
        )


def test_corpus_manifest_covers_every_supported_and_refusal_class():
    supported = {
        coverage for case in CORPUS["supported"] for coverage in case["covers"]
    }
    assert supported == {
        "element",
        "count",
        "formula",
        "isotope",
        "positive_charge",
        "negative_charge",
        "state",
        "group",
        "group_multiplier",
        "combined_construct",
        "coefficient",
        "reaction",
        "forward_arrow",
        "plus_separator",
        "equilibrium_arrow",
        "conditions",
        "reversible_arrow",
        "nested_group",
    }
    assert {case["class"] for case in CORPUS["rejected"]} == {
        "empty",
        "unknown_element",
        "case_invalid",
        "ambiguous_charge",
        "coefficient_bound",
        "count_bound",
        "empty_group",
        "group_bound",
        "unsupported_hydrate",
        "unicode",
        "unsupported_smiles",
        "unsupported_mhchem",
        "multiple_arrows",
        "empty_side",
        "unsafe_condition",
        "condition_bound",
        "unsupported_state",
        "malformed_group",
        "malformed_isotope",
        "free_condition",
    }


def test_public_verified_result_is_ready_for_later_source_evidence_without_reparse():
    verified = verify_chemical_notation("2H2 + O2 -> 2H2O")
    schema = type(verified).model_json_schema()
    assert set(schema["properties"]) == {
        "verification_kind",
        "notation",
        "source_notation",
        "source_sha256",
        "canonical_notation",
        "semantic_sha256",
        "speech",
        "speech_sha256",
        "mathml_decision",
        "mathml",
        "mathml_sha256",
    }

    later_evidence = {
        "chemistry": verified,
        "source_object": "page-1-object-7",
        "saved_artifact_sha256": "f" * 64,
    }
    assert later_evidence["chemistry"].semantic_sha256 == verified.semantic_sha256
