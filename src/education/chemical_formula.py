"""Bounded chemical notation semantics, speech, and passive MathML."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from html import escape
from types import MappingProxyType
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from src.education.canonical_json import canonical_sha256

MAX_SOURCE_LENGTH = 4_096
MAX_GROUP_DEPTH = 8
MAX_TERMS = 256
MAX_TERMS_PER_GROUP = 32
MAX_SPECIES_PER_SIDE = 8
MAX_CONDITIONS = 4
MAX_CONDITION_LENGTH = 64

_MAX_SMALL_INTEGER = 999
_MAX_CHARGE = 16
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_STATE_SPEECH = {"s": "solid", "l": "liquid", "g": "gas", "aq": "aqueous"}
_ARROW_SPEECH = {
    "forward": "yields",
    "equilibrium": "is in equilibrium with",
    "reversible": "is reversible with",
}
_ARROW_TEXT = {"forward": "->", "equilibrium": "<=>", "reversible": "<->"}
_ARROW_KIND = {value: key for key, value in _ARROW_TEXT.items()}
_ARROW_MATHML = {"forward": "→", "equilibrium": "⇌", "reversible": "↔"}
_CONDITION_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .,:/_+-"
)

_ELEMENT_DATA = (
    ("H", "hydrogen"),
    ("He", "helium"),
    ("Li", "lithium"),
    ("Be", "beryllium"),
    ("B", "boron"),
    ("C", "carbon"),
    ("N", "nitrogen"),
    ("O", "oxygen"),
    ("F", "fluorine"),
    ("Ne", "neon"),
    ("Na", "sodium"),
    ("Mg", "magnesium"),
    ("Al", "aluminum"),
    ("Si", "silicon"),
    ("P", "phosphorus"),
    ("S", "sulfur"),
    ("Cl", "chlorine"),
    ("Ar", "argon"),
    ("K", "potassium"),
    ("Ca", "calcium"),
    ("Sc", "scandium"),
    ("Ti", "titanium"),
    ("V", "vanadium"),
    ("Cr", "chromium"),
    ("Mn", "manganese"),
    ("Fe", "iron"),
    ("Co", "cobalt"),
    ("Ni", "nickel"),
    ("Cu", "copper"),
    ("Zn", "zinc"),
    ("Ga", "gallium"),
    ("Ge", "germanium"),
    ("As", "arsenic"),
    ("Se", "selenium"),
    ("Br", "bromine"),
    ("Kr", "krypton"),
    ("Rb", "rubidium"),
    ("Sr", "strontium"),
    ("Y", "yttrium"),
    ("Zr", "zirconium"),
    ("Nb", "niobium"),
    ("Mo", "molybdenum"),
    ("Tc", "technetium"),
    ("Ru", "ruthenium"),
    ("Rh", "rhodium"),
    ("Pd", "palladium"),
    ("Ag", "silver"),
    ("Cd", "cadmium"),
    ("In", "indium"),
    ("Sn", "tin"),
    ("Sb", "antimony"),
    ("Te", "tellurium"),
    ("I", "iodine"),
    ("Xe", "xenon"),
    ("Cs", "cesium"),
    ("Ba", "barium"),
    ("La", "lanthanum"),
    ("Ce", "cerium"),
    ("Pr", "praseodymium"),
    ("Nd", "neodymium"),
    ("Pm", "promethium"),
    ("Sm", "samarium"),
    ("Eu", "europium"),
    ("Gd", "gadolinium"),
    ("Tb", "terbium"),
    ("Dy", "dysprosium"),
    ("Ho", "holmium"),
    ("Er", "erbium"),
    ("Tm", "thulium"),
    ("Yb", "ytterbium"),
    ("Lu", "lutetium"),
    ("Hf", "hafnium"),
    ("Ta", "tantalum"),
    ("W", "tungsten"),
    ("Re", "rhenium"),
    ("Os", "osmium"),
    ("Ir", "iridium"),
    ("Pt", "platinum"),
    ("Au", "gold"),
    ("Hg", "mercury"),
    ("Tl", "thallium"),
    ("Pb", "lead"),
    ("Bi", "bismuth"),
    ("Po", "polonium"),
    ("At", "astatine"),
    ("Rn", "radon"),
    ("Fr", "francium"),
    ("Ra", "radium"),
    ("Ac", "actinium"),
    ("Th", "thorium"),
    ("Pa", "protactinium"),
    ("U", "uranium"),
    ("Np", "neptunium"),
    ("Pu", "plutonium"),
    ("Am", "americium"),
    ("Cm", "curium"),
    ("Bk", "berkelium"),
    ("Cf", "californium"),
    ("Es", "einsteinium"),
    ("Fm", "fermium"),
    ("Md", "mendelevium"),
    ("No", "nobelium"),
    ("Lr", "lawrencium"),
    ("Rf", "rutherfordium"),
    ("Db", "dubnium"),
    ("Sg", "seaborgium"),
    ("Bh", "bohrium"),
    ("Hs", "hassium"),
    ("Mt", "meitnerium"),
    ("Ds", "darmstadtium"),
    ("Rg", "roentgenium"),
    ("Cn", "copernicium"),
    ("Nh", "nihonium"),
    ("Fl", "flerovium"),
    ("Mc", "moscovium"),
    ("Lv", "livermorium"),
    ("Ts", "tennessine"),
    ("Og", "oganesson"),
)
ELEMENT_NAMES: Mapping[str, str] = MappingProxyType(dict(_ELEMENT_DATA))
ELEMENT_SYMBOLS = tuple(ELEMENT_NAMES)
_ELEMENT_SYMBOL_SET = frozenset(ELEMENT_SYMBOLS)

SmallPositiveInt = Annotated[int, Field(strict=True, ge=1, le=_MAX_SMALL_INTEGER)]
ChargeInt = Annotated[int, Field(strict=True, ge=-_MAX_CHARGE, le=_MAX_CHARGE)]


class ChemicalFormulaRejected(ValueError):
    """A bounded rejection that never carries partial chemistry semantics."""


class _FrozenContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always"
    )


def _check_raw_term_tree(value: Any, *, initial_depth: int = 0) -> Any:
    """Bound exact built-in containers before Pydantic recursively constructs them."""
    if type(value) not in (list, tuple):
        return value
    stack: list[tuple[list[Any] | tuple[Any, ...], int]] = [(value, initial_depth)]
    total = 0
    while stack:
        terms, depth = stack.pop()
        if len(terms) > MAX_TERMS_PER_GROUP and depth > 0:
            raise ValueError("group exceeds the term limit")
        for term in terms:
            total += 1
            if total > MAX_TERMS:
                raise ValueError("notation exceeds the term limit")
            if type(term) is not dict or term.get("term_kind") != "group":
                continue
            if depth >= MAX_GROUP_DEPTH:
                raise ValueError("group depth exceeds the supported limit")
            children = term.get("terms")
            if type(children) not in (list, tuple):
                continue
            stack.append((children, depth + 1))
    return value


class ElementTermV1(_FrozenContractModel):
    """One case-correct element token and its notation-bound modifiers."""

    term_kind: Literal["element"]
    symbol: str = Field(min_length=1, max_length=2)
    count: SmallPositiveInt = 1
    isotope: SmallPositiveInt | None = None

    @field_validator("symbol")
    @classmethod
    def _known_element_symbol(cls, value: str) -> str:
        if value not in _ELEMENT_SYMBOL_SET:
            raise ValueError("unknown or case-invalid element symbol")
        return value


class GroupTermV1(_FrozenContractModel):
    """One bounded parenthesized sequence whose order is semantic."""

    term_kind: Literal["group"]
    terms: tuple[
        Annotated[ElementTermV1 | GroupTermV1, Field(discriminator="term_kind")],
        ...,
    ] = Field(min_length=1, max_length=MAX_TERMS_PER_GROUP)
    multiplier: SmallPositiveInt = 1

    @field_validator("terms", mode="before")
    @classmethod
    def _bound_raw_terms(cls, value: Any) -> Any:
        return _check_raw_term_tree(value, initial_depth=1)

    @model_validator(mode="after")
    def _bound_constructed_terms(self) -> GroupTermV1:
        stack: list[tuple[tuple[ElementTermV1 | GroupTermV1, ...], int]] = [
            (self.terms, 1)
        ]
        total = 0
        while stack:
            terms, depth = stack.pop()
            for term in terms:
                total += 1
                if total > MAX_TERMS:
                    raise ValueError("notation exceeds the term limit")
                if not isinstance(term, GroupTermV1):
                    continue
                if depth >= MAX_GROUP_DEPTH:
                    raise ValueError("group depth exceeds the supported limit")
                stack.append((term.terms, depth + 1))
        return self


ChemicalTermV1: TypeAlias = Annotated[
    ElementTermV1 | GroupTermV1,
    Field(discriminator="term_kind"),
]


class ChemicalSpeciesV1(_FrozenContractModel):
    """One formula species, separate from its stoichiometric coefficient."""

    species_kind: Literal["chemical_species_v1"]
    coefficient: SmallPositiveInt = 1
    terms: tuple[ChemicalTermV1, ...] = Field(min_length=1, max_length=MAX_TERMS)
    charge: ChargeInt | None = None
    state: Literal["s", "l", "g", "aq"] | None = None

    @field_validator("terms", mode="before")
    @classmethod
    def _bound_raw_terms(cls, value: Any) -> Any:
        return _check_raw_term_tree(value)

    @field_validator("charge")
    @classmethod
    def _nonzero_charge(cls, value: int | None) -> int | None:
        if value == 0:
            raise ValueError("charge must be nonzero")
        return value


class ChemicalFormulaV1(_FrozenContractModel):
    """One supported formula without reaction semantics."""

    notation_kind: Literal["chemical_formula_v1"]
    species: ChemicalSpeciesV1


class ChemicalReactionV1(_FrozenContractModel):
    """Two bounded ordered species sides joined by one explicit arrow."""

    notation_kind: Literal["chemical_reaction_v1"]
    left: tuple[ChemicalSpeciesV1, ...] = Field(
        min_length=1, max_length=MAX_SPECIES_PER_SIDE
    )
    arrow: Literal["forward", "equilibrium", "reversible"]
    conditions: tuple[str, ...] = Field(default=(), max_length=MAX_CONDITIONS)
    right: tuple[ChemicalSpeciesV1, ...] = Field(
        min_length=1, max_length=MAX_SPECIES_PER_SIDE
    )

    @field_validator("conditions")
    @classmethod
    def _safe_conditions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if (
                not value
                or value != value.strip()
                or len(value) > MAX_CONDITION_LENGTH
                or not value.isascii()
                or not value.isprintable()
                or not set(value) <= _CONDITION_CHARS
            ):
                raise ValueError("conditions must be bounded safe ASCII text")
        return values


ChemicalNotationV1: TypeAlias = Annotated[
    ChemicalFormulaV1 | ChemicalReactionV1,
    Field(discriminator="notation_kind"),
]


def _validated_notation(notation: Any) -> ChemicalFormulaV1 | ChemicalReactionV1:
    if type(notation) is ChemicalFormulaV1:
        return ChemicalFormulaV1.model_validate(notation)
    if type(notation) is ChemicalReactionV1:
        return ChemicalReactionV1.model_validate(notation)
    raise TypeError("projection requires an exact typed chemical notation")


class VerifiedChemicalNotationV1(_FrozenContractModel):
    """One parsed notation with every accessible projection digest-bound."""

    verification_kind: Literal["verified_chemical_notation_v1"]
    notation: ChemicalNotationV1
    source_notation: str = Field(min_length=1, max_length=MAX_SOURCE_LENGTH)
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    canonical_notation: str = Field(min_length=1, max_length=MAX_SOURCE_LENGTH)
    semantic_sha256: str = Field(pattern=_SHA256_PATTERN)
    speech: str = Field(min_length=1, max_length=16_384)
    speech_sha256: str = Field(pattern=_SHA256_PATTERN)
    mathml_decision: Literal["generated_from_validated_contract"]
    mathml: str = Field(min_length=1, max_length=65_536)
    mathml_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("source_notation", "canonical_notation", "speech", "mathml")
    @classmethod
    def _printable_text(cls, value: str) -> str:
        if not value.isprintable():
            raise ValueError("verified text must be printable")
        return value

    @model_validator(mode="after")
    def _projections_match_contract(self) -> VerifiedChemicalNotationV1:
        expected_canonical = serialize_chemical_notation(self.notation)
        expected_speech = chemical_speech(self.notation)
        expected_mathml = chemical_mathml(self.notation)
        if self.canonical_notation != expected_canonical:
            raise ValueError("canonical notation does not match the typed contract")
        if self.semantic_sha256 != canonical_sha256(self.notation):
            raise ValueError("semantic digest does not match the typed contract")
        if self.source_sha256 != _text_sha256(self.source_notation):
            raise ValueError("source digest does not match source notation")
        if self.speech != expected_speech or self.speech_sha256 != _text_sha256(
            expected_speech
        ):
            raise ValueError("speech projection or digest does not match")
        if self.mathml != expected_mathml or self.mathml_sha256 != _text_sha256(
            expected_mathml
        ):
            raise ValueError("MathML projection or digest does not match")
        return self


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _NotationParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0
        self.term_count = 0

    def _skip_spaces(self) -> None:
        while self.index < len(self.text) and self.text[self.index] == " ":
            self.index += 1

    def _read_digits(self) -> int | None:
        start = self.index
        while self.index < len(self.text) and self.text[self.index].isdigit():
            self.index += 1
        if self.index == start:
            return None
        value = int(self.text[start : self.index])
        if not 1 <= value <= _MAX_SMALL_INTEGER:
            raise ChemicalFormulaRejected("integer is outside the supported bounds")
        return value

    def _looks_like_isotope(self) -> bool:
        if self.index >= len(self.text) or self.text[self.index] != "^":
            return False
        cursor = self.index + 1
        while cursor < len(self.text) and self.text[cursor].isdigit():
            cursor += 1
        return (
            cursor > self.index + 1
            and cursor < len(self.text)
            and self.text[cursor].isupper()
        )

    def _state_at_species_end(self) -> str | None:
        if self.index >= len(self.text) or self.text[self.index] != "(":
            return None
        for state in ("(aq)", "(s)", "(l)", "(g)"):
            if not self.text.startswith(state, self.index):
                continue
            cursor = self.index + len(state)
            while cursor < len(self.text) and self.text[cursor] == " ":
                cursor += 1
            if cursor == len(self.text) or self.text[cursor] == "+":
                return state[1:-1]
        return None

    def _track_term(self) -> None:
        self.term_count += 1
        if self.term_count > MAX_TERMS:
            raise ChemicalFormulaRejected("notation exceeds the term limit")

    def _parse_element(self, isotope: int | None = None) -> ElementTermV1:
        if self.index >= len(self.text) or not self.text[self.index].isupper():
            raise ChemicalFormulaRejected("isotope must be followed by an element")
        start = self.index
        self.index += 1
        if self.index < len(self.text) and self.text[self.index].islower():
            self.index += 1
        symbol = self.text[start : self.index]
        if symbol not in _ELEMENT_SYMBOL_SET:
            raise ChemicalFormulaRejected("unknown or case-invalid element symbol")
        count = self._read_digits() or 1
        self._track_term()
        return ElementTermV1(
            term_kind="element", symbol=symbol, count=count, isotope=isotope
        )

    def _parse_group(self, depth: int) -> GroupTermV1:
        if depth >= MAX_GROUP_DEPTH:
            raise ChemicalFormulaRejected("group depth exceeds the supported limit")
        self.index += 1
        terms = self._parse_terms(depth=depth + 1, in_group=True)
        if not terms:
            raise ChemicalFormulaRejected("groups cannot be empty")
        if self.index >= len(self.text) or self.text[self.index] != ")":
            raise ChemicalFormulaRejected("group is not closed")
        self.index += 1
        multiplier = self._read_digits() or 1
        self._track_term()
        try:
            return GroupTermV1(term_kind="group", terms=terms, multiplier=multiplier)
        except ValidationError as exc:
            raise ChemicalFormulaRejected(
                "group is outside the supported bounds"
            ) from exc

    def _parse_terms(
        self, *, depth: int, in_group: bool = False
    ) -> tuple[ChemicalTermV1, ...]:
        terms: list[ChemicalTermV1] = []
        while True:
            self._skip_spaces()
            if self.index >= len(self.text):
                break
            char = self.text[self.index]
            if in_group and char == ")":
                break
            if not in_group and self._state_at_species_end() is not None:
                break
            if char.isupper():
                terms.append(self._parse_element())
            elif char == "^" and self._looks_like_isotope():
                self.index += 1
                isotope = self._read_digits()
                if isotope is None:
                    raise ChemicalFormulaRejected("isotope mass is required")
                terms.append(self._parse_element(isotope))
            elif char == "(":
                terms.append(self._parse_group(depth))
            else:
                break
            if len(terms) > MAX_TERMS_PER_GROUP and in_group:
                raise ChemicalFormulaRejected("group exceeds the term limit")
        return tuple(terms)

    def _parse_charge(self) -> int | None:
        self._skip_spaces()
        if self.index >= len(self.text) or self.text[self.index] != "^":
            return None
        self.index += 1
        magnitude = self._read_digits() or 1
        if self.index >= len(self.text) or self.text[self.index] not in "+-":
            raise ChemicalFormulaRejected("charge requires a caret and terminal sign")
        sign = self.text[self.index]
        self.index += 1
        charge = magnitude if sign == "+" else -magnitude
        if abs(charge) > _MAX_CHARGE:
            raise ChemicalFormulaRejected("charge exceeds the supported bound")
        return charge

    def parse_species(self) -> ChemicalSpeciesV1:
        self._skip_spaces()
        coefficient = self._read_digits() or 1
        self._skip_spaces()
        terms = self._parse_terms(depth=0)
        if not terms:
            raise ChemicalFormulaRejected("species must contain at least one term")
        charge = self._parse_charge()
        self._skip_spaces()
        state = self._state_at_species_end()
        if state is not None:
            self.index += len(state) + 2
        self._skip_spaces()
        try:
            return ChemicalSpeciesV1(
                species_kind="chemical_species_v1",
                coefficient=coefficient,
                terms=terms,
                charge=charge,
                state=state,
            )
        except ValidationError as exc:
            raise ChemicalFormulaRejected(
                "species is outside the supported bounds"
            ) from exc

    def parse_side(self) -> tuple[ChemicalSpeciesV1, ...]:
        species: list[ChemicalSpeciesV1] = []
        while True:
            species.append(self.parse_species())
            if len(species) > MAX_SPECIES_PER_SIDE:
                raise ChemicalFormulaRejected("reaction side exceeds the species limit")
            self._skip_spaces()
            if self.index == len(self.text):
                break
            if self.text[self.index] != "+":
                raise ChemicalFormulaRejected("unsupported trailing notation")
            self.index += 1
            self._skip_spaces()
            if self.index == len(self.text):
                raise ChemicalFormulaRejected("reaction side ends with a separator")
        return tuple(species)


def _validate_source(source: Any) -> str:
    if not isinstance(source, str):
        raise TypeError("chemical notation source must be a string")
    if not source:
        raise ChemicalFormulaRejected("chemical notation is empty")
    if len(source) > MAX_SOURCE_LENGTH:
        raise ChemicalFormulaRejected(
            "chemical notation exceeds the source length limit"
        )
    if not source.isascii() or not source.isprintable():
        raise ChemicalFormulaRejected("chemical notation must be printable ASCII")
    if not source.strip():
        raise ChemicalFormulaRejected("chemical notation is empty")
    return source


def _find_arrow(source: str) -> tuple[int, str] | None:
    matches: list[tuple[int, str]] = []
    cursor = 0
    arrows = ("<=>", "<->", "->")
    while cursor < len(source):
        matched = next(
            (arrow for arrow in arrows if source.startswith(arrow, cursor)), None
        )
        if matched is None:
            cursor += 1
            continue
        matches.append((cursor, matched))
        cursor += len(matched)
    if len(matches) > 1:
        raise ChemicalFormulaRejected("reaction must contain exactly one arrow")
    return matches[0] if matches else None


def _parse_conditions_and_right(tail: str) -> tuple[tuple[str, ...], str]:
    stripped = tail.lstrip(" ")
    if not stripped.startswith("["):
        return (), tail
    close = stripped.find("]")
    if close < 0:
        raise ChemicalFormulaRejected("reaction conditions are not closed")
    raw_conditions = stripped[1:close]
    values = tuple(" ".join(item.strip().split()) for item in raw_conditions.split(";"))
    if not 1 <= len(values) <= MAX_CONDITIONS or any(not value for value in values):
        raise ChemicalFormulaRejected("reaction condition count is outside the bounds")
    if any(
        len(value) > MAX_CONDITION_LENGTH
        or not value.isascii()
        or not value.isprintable()
        or not set(value) <= _CONDITION_CHARS
        for value in values
    ):
        raise ChemicalFormulaRejected("reaction condition text is unsupported")
    return values, stripped[close + 1 :]


def parse_chemical_notation(source: str) -> ChemicalFormulaV1 | ChemicalReactionV1:
    """Parse the complete supported ASCII subset or reject without partial output."""
    source = _validate_source(source)
    arrow = _find_arrow(source)
    try:
        if arrow is None:
            parser = _NotationParser(source)
            species = parser.parse_side()
            if len(species) != 1:
                raise ChemicalFormulaRejected("a formula contains exactly one species")
            return ChemicalFormulaV1(
                notation_kind="chemical_formula_v1", species=species[0]
            )

        arrow_index, arrow_text = arrow
        left_text = source[:arrow_index]
        conditions, right_text = _parse_conditions_and_right(
            source[arrow_index + len(arrow_text) :]
        )
        left = _NotationParser(left_text).parse_side()
        right = _NotationParser(right_text).parse_side()
        return ChemicalReactionV1(
            notation_kind="chemical_reaction_v1",
            left=left,
            arrow=_ARROW_KIND[arrow_text],
            conditions=conditions,
            right=right,
        )
    except ValidationError as exc:
        raise ChemicalFormulaRejected("notation violates the typed contract") from exc


def _serialize_term(term: ChemicalTermV1) -> str:
    if isinstance(term, ElementTermV1):
        isotope = f"^{term.isotope}" if term.isotope is not None else ""
        count = str(term.count) if term.count != 1 else ""
        return f"{isotope}{term.symbol}{count}"
    body = "".join(_serialize_term(child) for child in term.terms)
    multiplier = str(term.multiplier) if term.multiplier != 1 else ""
    return f"({body}){multiplier}"


def _serialize_species(species: ChemicalSpeciesV1) -> str:
    coefficient = str(species.coefficient) if species.coefficient != 1 else ""
    body = "".join(_serialize_term(term) for term in species.terms)
    charge = ""
    if species.charge is not None:
        magnitude = abs(species.charge)
        sign = "+" if species.charge > 0 else "-"
        charge = f"^{magnitude if magnitude != 1 else ''}{sign}"
    state = f"({species.state})" if species.state is not None else ""
    return f"{coefficient}{body}{charge}{state}"


def serialize_chemical_notation(
    notation: ChemicalFormulaV1 | ChemicalReactionV1,
) -> str:
    """Return the one canonical ASCII spelling for a typed notation."""
    notation = _validated_notation(notation)
    if isinstance(notation, ChemicalFormulaV1):
        return _serialize_species(notation.species)
    left = " + ".join(_serialize_species(species) for species in notation.left)
    right = " + ".join(_serialize_species(species) for species in notation.right)
    conditions = f"[{';'.join(notation.conditions)}]" if notation.conditions else ""
    return f"{left} {_ARROW_TEXT[notation.arrow]}{conditions} {right}"


def _term_speech(term: ChemicalTermV1) -> str:
    if isinstance(term, ElementTermV1):
        words = ELEMENT_NAMES[term.symbol]
        if term.isotope is not None:
            words += f" isotope {term.isotope}"
        if term.count != 1:
            words += f" subscript {term.count}"
        return words
    parts = ", ".join(_term_speech(child) for child in term.terms)
    words = f"open group, {parts}, close group"
    if term.multiplier != 1:
        words += f" subscript {term.multiplier}"
    return words


def _species_speech(species: ChemicalSpeciesV1) -> str:
    parts = [_term_speech(term) for term in species.terms]
    if species.coefficient != 1:
        parts.insert(0, f"coefficient {species.coefficient}")
    if species.charge is not None:
        sign = "positive" if species.charge > 0 else "negative"
        parts.append(f"charge {abs(species.charge)} {sign}")
    if species.state is not None:
        parts.append(f"state {_STATE_SPEECH[species.state]}")
    return ", ".join(parts)


def chemical_speech(notation: ChemicalFormulaV1 | ChemicalReactionV1) -> str:
    """Project only facts present in a validated notation into deterministic speech."""
    notation = _validated_notation(notation)
    if isinstance(notation, ChemicalFormulaV1):
        return _species_speech(notation.species)
    left = " plus ".join(_species_speech(species) for species in notation.left)
    right = " plus ".join(_species_speech(species) for species in notation.right)
    speech = f"{left} {_ARROW_SPEECH[notation.arrow]} {right}"
    if notation.conditions:
        label = "condition" if len(notation.conditions) == 1 else "conditions"
        speech += f" under {label} {'; '.join(notation.conditions)}"
    return speech


def _term_mathml(term: ChemicalTermV1) -> str:
    if isinstance(term, ElementTermV1):
        node = f'<mi mathvariant="normal">{term.symbol}</mi>'
        if term.isotope is not None:
            node = (
                f"<mmultiscripts>{node}<mprescripts/><none/>"
                f"<mn>{term.isotope}</mn></mmultiscripts>"
            )
        if term.count != 1:
            node = f"<msub>{node}<mn>{term.count}</mn></msub>"
        return node
    body = "".join(_term_mathml(child) for child in term.terms)
    node = f"<mrow><mo>(</mo><mrow>{body}</mrow><mo>)</mo></mrow>"
    if term.multiplier != 1:
        node = f"<msub>{node}<mn>{term.multiplier}</mn></msub>"
    return node


def _species_mathml(species: ChemicalSpeciesV1) -> str:
    nodes = []
    if species.coefficient != 1:
        nodes.append(f"<mn>{species.coefficient}</mn>")
    nodes.extend(_term_mathml(term) for term in species.terms)
    body = f"<mrow>{''.join(nodes)}</mrow>"
    if species.charge is not None:
        magnitude = abs(species.charge)
        sign = "+" if species.charge > 0 else "−"
        magnitude_node = f"<mn>{magnitude}</mn>" if magnitude != 1 else ""
        body = f"<msup>{body}<mrow>{magnitude_node}<mo>{sign}</mo></mrow></msup>"
    if species.state is not None:
        body += (
            "<mrow><mo>(</mo>"
            f'<mi mathvariant="normal">{species.state}</mi>'
            "<mo>)</mo></mrow>"
        )
    return f"<mrow>{body}</mrow>"


def _arrow_mathml(reaction: ChemicalReactionV1) -> str:
    arrow = f"<mo>{_ARROW_MATHML[reaction.arrow]}</mo>"
    if not reaction.conditions:
        return arrow
    condition_nodes = "<mo>;</mo>".join(
        f"<mtext>{escape(value)}</mtext>" for value in reaction.conditions
    )
    return f"<mover>{arrow}<mrow>{condition_nodes}</mrow></mover>"


def chemical_mathml(notation: ChemicalFormulaV1 | ChemicalReactionV1) -> str:
    """Render passive canonical MathML from a validated contract only."""
    notation = _validated_notation(notation)
    if isinstance(notation, ChemicalFormulaV1):
        body = _species_mathml(notation.species)
    else:
        left = "<mo>+</mo>".join(_species_mathml(species) for species in notation.left)
        right = "<mo>+</mo>".join(
            _species_mathml(species) for species in notation.right
        )
        body = f"{left}{_arrow_mathml(notation)}{right}"
    return (
        f'<math xmlns="http://www.w3.org/1998/Math/MathML"><mrow>{body}</mrow></math>'
    )


def verify_chemical_notation(source: str) -> VerifiedChemicalNotationV1:
    """Parse once and bind source, semantics, speech, and MathML to exact digests."""
    source = _validate_source(source)
    notation = parse_chemical_notation(source)
    canonical = serialize_chemical_notation(notation)
    speech = chemical_speech(notation)
    mathml = chemical_mathml(notation)
    return VerifiedChemicalNotationV1(
        verification_kind="verified_chemical_notation_v1",
        notation=notation,
        source_notation=source,
        source_sha256=_text_sha256(source),
        canonical_notation=canonical,
        semantic_sha256=canonical_sha256(notation),
        speech=speech,
        speech_sha256=_text_sha256(speech),
        mathml_decision="generated_from_validated_contract",
        mathml=mathml,
        mathml_sha256=_text_sha256(mathml),
    )


__all__ = [
    "ELEMENT_NAMES",
    "ELEMENT_SYMBOLS",
    "MAX_GROUP_DEPTH",
    "MAX_SOURCE_LENGTH",
    "ChemicalFormulaRejected",
    "ChemicalFormulaV1",
    "ChemicalReactionV1",
    "ChemicalSpeciesV1",
    "ChemicalTermV1",
    "ElementTermV1",
    "GroupTermV1",
    "VerifiedChemicalNotationV1",
    "chemical_mathml",
    "chemical_speech",
    "parse_chemical_notation",
    "serialize_chemical_notation",
    "verify_chemical_notation",
]
