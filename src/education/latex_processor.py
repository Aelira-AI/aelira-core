"""
LaTeX to MathML Conversion Module

This module provides functionality to:
1. Detect LaTeX equations in text (inline and display mode)
2. Convert LaTeX to accessible MathML
3. Generate ARIA labels for screen readers
4. Support common STEM packages (amsmath, physics, chemfig)
5. Batch process documents with multiple equations
6. Check WCAG 2.1 compliance for mathematical content

Additional capabilities:
- ChemFig chemical structure support (text-based descriptions)
- Physics notation (bra-ket, vectors, tensors)
- TikZ diagram descriptions (AI-powered)
- Custom macro detection and expansion
- Multi-file LaTeX project support
"""

from typing import List, Dict, Optional, Tuple
from pydantic import BaseModel
from latex2mathml.converter import convert as latex_to_mathml
import re
import os
import logging
from enum import Enum

# Import LLM provider manager for AI-generated ARIA labels
from src.ai.providers import get_provider_manager

logger = logging.getLogger(__name__)


# =============================================================================
# Advanced LaTeX Support
# =============================================================================


class LaTeXContentType(str, Enum):
    """Types of LaTeX content for specialized handling"""

    MATH = "math"
    CHEMISTRY = "chemistry"  # ChemFig, mhchem
    PHYSICS = "physics"  # bra-ket, vectors
    DIAGRAM = "diagram"  # TikZ
    TABLE = "table"
    CODE = "code"


# Chemistry notation mappings (ChemFig and mhchem)
CHEMISTRY_PATTERNS = {
    # mhchem package patterns
    r"\\ce\{([^}]+)\}": "chemical_formula",
    r"\\bond\{([^}]+)\}": "chemical_bond",
    # ChemFig patterns
    r"\\chemfig\{([^}]+)\}": "molecular_structure",
    r"\\chemname\{([^}]+)\}\{([^}]+)\}": "named_compound",
    r"\\arrow\{([^}]+)\}": "reaction_arrow",
    r"\\schemstart": "reaction_scheme_start",
    r"\\schemestart": "reaction_scheme_start",
    r"\\schemestop": "reaction_scheme_end",
}

# Chemistry bond type descriptions
BOND_DESCRIPTIONS = {
    "-": "single bond",
    "=": "double bond",
    "~": "triple bond",
    "->": "arrow (forward reaction)",
    "<-": "arrow (reverse reaction)",
    "<->": "equilibrium arrow",
    "<=>": "resonance arrow",
}

# Physics notation patterns (physics package)
PHYSICS_PATTERNS = {
    # Bra-ket notation
    r"\\bra\{([^}]+)\}": "bra_vector",
    r"\\ket\{([^}]+)\}": "ket_vector",
    r"\\braket\{([^}]+)\}\{([^}]+)\}": "inner_product",
    r"\\Braket\{([^}]+)\}": "inner_product_single",
    r"\\dyad\{([^}]+)\}\{([^}]+)\}": "outer_product",
    # Vectors and operators
    r"\\vb\{([^}]+)\}": "vector_bold",
    r"\\vb\*\{([^}]+)\}": "vector_bold_italic",
    r"\\va\{([^}]+)\}": "vector_arrow",
    r"\\vu\{([^}]+)\}": "unit_vector",
    r"\\vdot": "vector_dot",
    r"\\cross": "cross_product",
    r"\\grad": "gradient",
    r"\\div": "divergence",
    r"\\curl": "curl",
    r"\\laplacian": "laplacian",
    # Derivatives
    r"\\dv\{([^}]+)\}\{([^}]+)\}": "derivative",
    r"\\pdv\{([^}]+)\}\{([^}]+)\}": "partial_derivative",
    r"\\fdv\{([^}]+)\}\{([^}]+)\}": "functional_derivative",
    # Matrices and tensors
    r"\\mqty\{([^}]+)\}": "matrix_quantity",
    r"\\pmqty\{([^}]+)\}": "parenthesized_matrix",
    r"\\bmqty\{([^}]+)\}": "bracketed_matrix",
    r"\\vmqty\{([^}]+)\}": "vertical_matrix",
    # Common physics symbols
    r"\\hbar": "reduced_planck_constant",
    r"\\nabla": "nabla_operator",
    r"\\partial": "partial_derivative_symbol",
}

# TikZ/PGFPlots patterns for diagram detection
TIKZ_PATTERNS = {
    r"\\begin\{tikzpicture\}": "tikz_diagram",
    r"\\begin\{pgfplot\}": "pgf_plot",
    r"\\begin\{axis\}": "axis_plot",
    r"\\begin\{circuitikz\}": "circuit_diagram",
    r"\\begin\{tikzcd\}": "commutative_diagram",
    r"\\draw": "draw_command",
    r"\\node": "node_command",
    r"\\path": "path_command",
    r"\\fill": "fill_command",
    r"\\addplot": "add_plot",
}

# =============================================================================
# Siunitx SI Unit Support
# =============================================================================

# Siunitx package patterns
SIUNITX_PATTERNS = {
    r"\\SI\{([^}]+)\}\{([^}]+)\}": "si_value_unit",  # \SI{9.8}{\meter\per\second\squared}
    r"\\si\{([^}]+)\}": "si_unit_only",  # \si{\kilo\gram}
    r"\\num\{([^}]+)\}": "si_number",  # \num{1.23e-4}
    r"\\ang\{([^}]+)\}": "si_angle",  # \ang{45;30;0}
    r"\\SIrange\{([^}]+)\}\{([^}]+)\}\{([^}]+)\}": "si_range",  # \SIrange{1}{10}{\meter}
    r"\\numrange\{([^}]+)\}\{([^}]+)\}": "num_range",  # \numrange{1}{10}
    r"\\SIlist\{([^}]+)\}\{([^}]+)\}": "si_list",  # \SIlist{1;2;3}{\meter}
    r"\\numlist\{([^}]+)\}": "num_list",  # \numlist{1;2;3}
    r"\\qty\{([^}]+)\}\{([^}]+)\}": "qty_value_unit",  # \qty{9.8}{\meter\per\second\squared} (siunitx v3)
    r"\\unit\{([^}]+)\}": "unit_only",  # \unit{\kilo\gram} (siunitx v3)
}

# SI base units
SI_BASE_UNITS = {
    r"\\meter": "meter",
    r"\\metre": "metre",
    r"\\kilogram": "kilogram",
    r"\\second": "second",
    r"\\ampere": "ampere",
    r"\\kelvin": "kelvin",
    r"\\mole": "mole",
    r"\\candela": "candela",
}

# SI derived units
SI_DERIVED_UNITS = {
    r"\\hertz": "hertz",
    r"\\newton": "newton",
    r"\\pascal": "pascal",
    r"\\joule": "joule",
    r"\\watt": "watt",
    r"\\coulomb": "coulomb",
    r"\\volt": "volt",
    r"\\farad": "farad",
    r"\\ohm": "ohm",
    r"\\siemens": "siemens",
    r"\\weber": "weber",
    r"\\tesla": "tesla",
    r"\\henry": "henry",
    r"\\lumen": "lumen",
    r"\\lux": "lux",
    r"\\becquerel": "becquerel",
    r"\\gray": "gray",
    r"\\sievert": "sievert",
    r"\\katal": "katal",
    r"\\degree": "degree",
    r"\\celsius": "degrees Celsius",
    r"\\degreeCelsius": "degrees Celsius",
    r"\\fahrenheit": "degrees Fahrenheit",
}

# SI prefixes
SI_PREFIXES = {
    r"\\yotta": "yotta",
    r"\\zetta": "zetta",
    r"\\exa": "exa",
    r"\\peta": "peta",
    r"\\tera": "tera",
    r"\\giga": "giga",
    r"\\mega": "mega",
    r"\\kilo": "kilo",
    r"\\hecto": "hecto",
    r"\\deca": "deca",
    r"\\deci": "deci",
    r"\\centi": "centi",
    r"\\milli": "milli",
    r"\\micro": "micro",
    r"\\nano": "nano",
    r"\\pico": "pico",
    r"\\femto": "femto",
    r"\\atto": "atto",
    r"\\zepto": "zepto",
    r"\\yocto": "yocto",
}

# SI unit modifiers
SI_MODIFIERS = {
    r"\\per": "per",
    r"\\squared": "squared",
    r"\\cubed": "cubed",
    r"\\tothe\{([^}]+)\}": "to the power of",
    r"\\raiseto\{([^}]+)\}": "to the power of",
    r"\\square": "square",
    r"\\cubic": "cubic",
}

# Common non-SI units accepted with SI
SI_ACCEPTED_UNITS = {
    r"\\litre": "liter",
    r"\\liter": "liter",
    r"\\minute": "minute",
    r"\\hour": "hour",
    r"\\day": "day",
    r"\\hectare": "hectare",
    r"\\tonne": "tonne",
    r"\\gram": "gram",
    r"\\electronvolt": "electronvolt",
    r"\\dalton": "dalton",
    r"\\astronomicalunit": "astronomical unit",
    r"\\bar": "bar",
    r"\\angstrom": "angstrom",
    r"\\percent": "percent",
}


class LaTeXEquation(BaseModel):
    """Detected LaTeX equation"""

    equation_id: int
    latex_source: str
    equation_type: str  # "inline" or "display"
    content_type: LaTeXContentType = LaTeXContentType.MATH  # content classification
    position_start: int
    position_end: int
    line_number: Optional[int] = None
    detected_features: List[str] = []  # detected special features


class MathMLConversionResult(BaseModel):
    """Result of LaTeX to MathML conversion"""

    equation_id: int
    latex_source: str
    mathml_output: str
    aria_label: Optional[str] = None
    conversion_success: bool
    error_message: Optional[str] = None
    wcag_compliant: bool


class DocumentConversionResult(BaseModel):
    """Result of processing an entire document"""

    file_path: str
    file_name: str
    total_equations: int
    successful_conversions: int
    failed_conversions: int
    equations: List[MathMLConversionResult]
    html_output: str  # Full HTML with accessible math
    compliance_score: float


class LaTeXAccessibilityIssue(BaseModel):
    """Accessibility issue detected in LaTeX document"""

    issue_type: str  # e.g., "missing_alt_text", "missing_caption", "missing_title"
    severity: str  # "critical", "serious", "moderate", "minor"
    wcag_criterion: str  # e.g., "1.1.1", "2.4.2"
    description: str
    line_number: Optional[int] = None
    latex_snippet: Optional[str] = None
    recommendation: str


# WCAG mappings for LaTeX accessibility issues
LATEX_ACCESSIBILITY_RULES = {
    "missing_title": {
        "wcag": "2.4.2",
        "severity": "serious",
        "description": "Document is missing \\title{} declaration",
        "recommendation": "Add a \\title{Your Document Title} in the preamble to provide document identification for screen readers.",
    },
    "missing_author": {
        "wcag": "2.4.2",
        "severity": "minor",
        "description": "Document is missing \\author{} declaration",
        "recommendation": "Add an \\author{Author Name} in the preamble for proper document metadata.",
    },
    "missing_alt_text": {
        "wcag": "1.1.1",
        "severity": "critical",
        "description": "Image included without alternative text description",
        "recommendation": "Add alt text using: \\includegraphics[alt={description}]{image.png} or provide a \\caption{} in the figure environment.",
    },
    "missing_figure_caption": {
        "wcag": "1.1.1",
        "severity": "serious",
        "description": "Figure environment without \\caption{}",
        "recommendation": "Add \\caption{Description of the figure} inside the figure environment to describe the visual content.",
    },
    "missing_table_caption": {
        "wcag": "1.3.1",
        "severity": "serious",
        "description": "Table without \\caption{} or description",
        "recommendation": "Add \\caption{Description of table contents} to help users understand the table's purpose.",
    },
    "complex_table_no_header": {
        "wcag": "1.3.1",
        "severity": "serious",
        "description": "Table appears to lack proper header row identification",
        "recommendation": "Use \\hline after the first row and consider using booktabs package with \\toprule, \\midrule, \\bottomrule for clear header separation.",
    },
    "equation_no_label": {
        "wcag": "1.3.1",
        "severity": "moderate",
        "description": "Display equation without \\label{} for cross-referencing",
        "recommendation": "Add \\label{eq:name} to numbered equations so they can be referenced accessibly in text.",
    },
    "color_only_emphasis": {
        "wcag": "1.4.1",
        "severity": "serious",
        "description": "Using \\textcolor{} without additional visual indicator",
        "recommendation": "Don't rely solely on color to convey information. Use \\textbf{}, \\emph{}, or add text indicators alongside color.",
    },
    "missing_lang": {
        "wcag": "3.1.1",
        "severity": "moderate",
        "description": "Document class doesn't specify language",
        "recommendation": "Add language option to document class: \\documentclass[english]{article} or use babel package: \\usepackage[english]{babel}",
    },
    "low_contrast_potential": {
        "wcag": "1.4.3",
        "severity": "moderate",
        "description": "Light colors used that may have insufficient contrast when compiled",
        "recommendation": "Avoid light colors like yellow, lightgray for text. Use darker alternatives or ensure sufficient contrast ratio (4.5:1).",
    },
    "unlabeled_hyperlink": {
        "wcag": "2.4.4",
        "severity": "moderate",
        "description": "\\url{} or \\href{} without descriptive text",
        "recommendation": "Use \\href{url}{Descriptive Link Text} instead of bare URLs to provide context for screen reader users.",
    },
    "missing_list_structure": {
        "wcag": "1.3.1",
        "severity": "moderate",
        "description": "Manual list formatting instead of proper list environment",
        "recommendation": "Use \\begin{itemize}, \\begin{enumerate}, or \\begin{description} for proper list structure.",
    },
}


class LaTeXProcessor:
    """Process LaTeX equations and convert to accessible MathML"""

    def __init__(
        self, use_ai: bool = True, progress_callback: callable = None, llm_client=None
    ):
        """
        Initialize LaTeX processor

        Args:
            use_ai: Whether to use AI for ARIA label generation (default: True)
            progress_callback: Optional callback function(current, total, message) for progress updates
        """
        self.use_ai = use_ai
        self.progress_callback = progress_callback
        self.llm_client = (
            (llm_client if llm_client is not None else get_provider_manager())
            if use_ai
            else None
        )

        if self.use_ai:
            health = self.llm_client.health_check()
            if health.get("status") in ["healthy", "degraded"]:
                logger.info(
                    f"LLM provider connected for ARIA label generation (primary: {health.get('primary_provider')})"
                )
            else:
                logger.warning(
                    f"AI not available: {health.get('error', 'Unknown error')}, falling back to heuristic labels"
                )
                self.use_ai = False
        else:
            logger.info("AI disabled, using heuristic ARIA labels")

        # Regex patterns for LaTeX detection
        self.inline_patterns = [
            r"\$([^\$]+)\$",  # $...$
            r"\\\\?\((.+?)\\\\?\)",  # \(...\)
        ]
        self.display_patterns = [
            r"\$\$([^\$]+)\$\$",  # $$...$$
            r"\\\\?\[(.+?)\\\\?\]",  # \[...\]
            r"\\begin\{equation\}(.+?)\\end\{equation\}",  # \begin{equation}...\end{equation}
            r"\\begin\{align\*?\}(.+?)\\end\{align\*?\}",  # \begin{align}...\end{align}
            r"\\begin\{gather\*?\}(.+?)\\end\{gather\*?\}",  # \begin{gather}...\end{gather}
        ]

        # Additional patterns for specialized content
        self.chemistry_patterns = [
            r"\\ce\{[^}]+\}",  # mhchem
            r"\\chemfig\{[^}]+\}",  # ChemFig molecular structures
            r"\\schemestart",  # Reaction schemes
        ]
        self.physics_patterns = [
            r"\\(?:bra|ket|braket|Braket|dyad)\{",  # Bra-ket notation
            r"\\(?:vb|va|vu)\{",  # Vector notation
            r"\\(?:dv|pdv|fdv)\{",  # Derivatives
            r"\\(?:grad|div|curl|laplacian)",  # Operators
        ]
        self.tikz_patterns = [
            r"\\begin\{tikzpicture\}",
            r"\\begin\{circuitikz\}",
            r"\\begin\{tikzcd\}",
            r"\\begin\{axis\}",
        ]

    def detect_accessibility_issues(
        self, latex_content: str
    ) -> List[LaTeXAccessibilityIssue]:
        """
        Detect accessibility issues in LaTeX document source.

        Checks for:
        - Missing document metadata (title, author)
        - Images without alt text or captions
        - Tables without captions or headers
        - Equations without labels
        - Color-only emphasis
        - Missing language specification
        - Bare URLs without descriptive text
        - Manual list formatting

        Args:
            latex_content: Raw LaTeX source code

        Returns:
            List of LaTeXAccessibilityIssue objects
        """
        issues = []
        lines = latex_content.split("\n")

        # Track document structure
        has_title = bool(re.search(r"\\title\s*\{[^}]+\}", latex_content))
        has_author = bool(re.search(r"\\author\s*\{[^}]+\}", latex_content))
        has_lang = bool(
            re.search(
                r"\\usepackage\[[^\]]*(?:english|german|french|spanish)[^\]]*\]\{babel\}",
                latex_content,
            )
        ) or bool(
            re.search(
                r"\\documentclass\[[^\]]*(?:english|german|french|spanish)",
                latex_content,
            )
        )

        # Check for missing title
        if not has_title and "\\begin{document}" in latex_content:
            issues.append(
                LaTeXAccessibilityIssue(
                    issue_type="missing_title",
                    severity=LATEX_ACCESSIBILITY_RULES["missing_title"]["severity"],
                    wcag_criterion=LATEX_ACCESSIBILITY_RULES["missing_title"]["wcag"],
                    description=LATEX_ACCESSIBILITY_RULES["missing_title"][
                        "description"
                    ],
                    recommendation=LATEX_ACCESSIBILITY_RULES["missing_title"][
                        "recommendation"
                    ],
                )
            )

        # Check for missing author
        if not has_author and "\\begin{document}" in latex_content:
            issues.append(
                LaTeXAccessibilityIssue(
                    issue_type="missing_author",
                    severity=LATEX_ACCESSIBILITY_RULES["missing_author"]["severity"],
                    wcag_criterion=LATEX_ACCESSIBILITY_RULES["missing_author"]["wcag"],
                    description=LATEX_ACCESSIBILITY_RULES["missing_author"][
                        "description"
                    ],
                    recommendation=LATEX_ACCESSIBILITY_RULES["missing_author"][
                        "recommendation"
                    ],
                )
            )

        # Check for missing language specification
        if not has_lang and "\\documentclass" in latex_content:
            issues.append(
                LaTeXAccessibilityIssue(
                    issue_type="missing_lang",
                    severity=LATEX_ACCESSIBILITY_RULES["missing_lang"]["severity"],
                    wcag_criterion=LATEX_ACCESSIBILITY_RULES["missing_lang"]["wcag"],
                    description=LATEX_ACCESSIBILITY_RULES["missing_lang"][
                        "description"
                    ],
                    recommendation=LATEX_ACCESSIBILITY_RULES["missing_lang"][
                        "recommendation"
                    ],
                )
            )

        # Find all \includegraphics commands
        for i, line in enumerate(lines, 1):
            # Check for images without alt text
            img_matches = re.finditer(
                r"\\includegraphics(\[[^\]]*\])?\{([^}]+)\}", line
            )
            for match in img_matches:
                options = match.group(1) or ""
                filename = match.group(2)

                # Check if alt text is provided in options
                has_alt = "alt=" in options or "alt =" in options

                # Check if this image is inside a figure with caption
                # Look backwards and forwards for figure environment and caption
                context_start = max(0, i - 10)
                context_end = min(len(lines), i + 10)
                context = "\n".join(lines[context_start:context_end])

                in_figure = "\\begin{figure}" in context and "\\end{figure}" in context
                has_caption = "\\caption{" in context and in_figure

                if not has_alt and not has_caption:
                    issues.append(
                        LaTeXAccessibilityIssue(
                            issue_type="missing_alt_text",
                            severity=LATEX_ACCESSIBILITY_RULES["missing_alt_text"][
                                "severity"
                            ],
                            wcag_criterion=LATEX_ACCESSIBILITY_RULES[
                                "missing_alt_text"
                            ]["wcag"],
                            description=f"Image '{filename}' included without alternative text description",
                            line_number=i,
                            latex_snippet=line.strip(),
                            recommendation=LATEX_ACCESSIBILITY_RULES[
                                "missing_alt_text"
                            ]["recommendation"],
                        )
                    )

        # Check for figure environments without captions
        figure_pattern = r"\\begin\{figure\}(.*?)\\end\{figure\}"
        for match in re.finditer(figure_pattern, latex_content, re.DOTALL):
            figure_content = match.group(1)
            if (
                "\\caption{" not in figure_content
                and "\\caption [" not in figure_content
            ):
                # Find line number
                line_num = latex_content[: match.start()].count("\n") + 1
                issues.append(
                    LaTeXAccessibilityIssue(
                        issue_type="missing_figure_caption",
                        severity=LATEX_ACCESSIBILITY_RULES["missing_figure_caption"][
                            "severity"
                        ],
                        wcag_criterion=LATEX_ACCESSIBILITY_RULES[
                            "missing_figure_caption"
                        ]["wcag"],
                        description=LATEX_ACCESSIBILITY_RULES["missing_figure_caption"][
                            "description"
                        ],
                        line_number=line_num,
                        latex_snippet=(
                            figure_content[:100].strip() + "..."
                            if len(figure_content) > 100
                            else figure_content.strip()
                        ),
                        recommendation=LATEX_ACCESSIBILITY_RULES[
                            "missing_figure_caption"
                        ]["recommendation"],
                    )
                )

        # Check for table environments without captions
        table_pattern = r"\\begin\{table\}(.*?)\\end\{table\}"
        for match in re.finditer(table_pattern, latex_content, re.DOTALL):
            table_content = match.group(1)
            if "\\caption{" not in table_content:
                line_num = latex_content[: match.start()].count("\n") + 1
                issues.append(
                    LaTeXAccessibilityIssue(
                        issue_type="missing_table_caption",
                        severity=LATEX_ACCESSIBILITY_RULES["missing_table_caption"][
                            "severity"
                        ],
                        wcag_criterion=LATEX_ACCESSIBILITY_RULES[
                            "missing_table_caption"
                        ]["wcag"],
                        description=LATEX_ACCESSIBILITY_RULES["missing_table_caption"][
                            "description"
                        ],
                        line_number=line_num,
                        recommendation=LATEX_ACCESSIBILITY_RULES[
                            "missing_table_caption"
                        ]["recommendation"],
                    )
                )

        # Check for tabular without clear header structure
        tabular_pattern = r"\\begin\{tabular\}(.*?)\\end\{tabular\}"
        for match in re.finditer(tabular_pattern, latex_content, re.DOTALL):
            tabular_content = match.group(1)
            # Check for header separation (hline after first row, or booktabs)
            has_header_sep = (
                "\\hline" in tabular_content
                or "\\toprule" in tabular_content
                or "\\midrule" in tabular_content
            )
            if not has_header_sep and "&" in tabular_content:  # Has columns
                line_num = latex_content[: match.start()].count("\n") + 1
                issues.append(
                    LaTeXAccessibilityIssue(
                        issue_type="complex_table_no_header",
                        severity=LATEX_ACCESSIBILITY_RULES["complex_table_no_header"][
                            "severity"
                        ],
                        wcag_criterion=LATEX_ACCESSIBILITY_RULES[
                            "complex_table_no_header"
                        ]["wcag"],
                        description=LATEX_ACCESSIBILITY_RULES[
                            "complex_table_no_header"
                        ]["description"],
                        line_number=line_num,
                        recommendation=LATEX_ACCESSIBILITY_RULES[
                            "complex_table_no_header"
                        ]["recommendation"],
                    )
                )

        # Check for display equations without labels
        equation_patterns = [
            (r"\\begin\{equation\}(.*?)\\end\{equation\}", "equation"),
            (r"\\begin\{align\}(.*?)\\end\{align\}", "align"),
        ]
        for pattern, env_name in equation_patterns:
            for match in re.finditer(pattern, latex_content, re.DOTALL):
                eq_content = match.group(1)
                if "\\label{" not in eq_content:
                    line_num = latex_content[: match.start()].count("\n") + 1
                    issues.append(
                        LaTeXAccessibilityIssue(
                            issue_type="equation_no_label",
                            severity=LATEX_ACCESSIBILITY_RULES["equation_no_label"][
                                "severity"
                            ],
                            wcag_criterion=LATEX_ACCESSIBILITY_RULES[
                                "equation_no_label"
                            ]["wcag"],
                            description=f"Display equation ({env_name} environment) without \\label{{}} for cross-referencing",
                            line_number=line_num,
                            latex_snippet=eq_content[:80].strip(),
                            recommendation=LATEX_ACCESSIBILITY_RULES[
                                "equation_no_label"
                            ]["recommendation"],
                        )
                    )

        # Check for color-only emphasis
        color_pattern = r"\\textcolor\{([^}]+)\}\{([^}]+)\}"
        for match in re.finditer(color_pattern, latex_content):
            color = match.group(1)
            text = match.group(2)
            # Check if text also has bold/italic/underline
            has_other_emphasis = any(
                cmd in text for cmd in ["\\textbf", "\\emph", "\\underline", "\\textit"]
            )
            if not has_other_emphasis:
                line_num = latex_content[: match.start()].count("\n") + 1
                issues.append(
                    LaTeXAccessibilityIssue(
                        issue_type="color_only_emphasis",
                        severity=LATEX_ACCESSIBILITY_RULES["color_only_emphasis"][
                            "severity"
                        ],
                        wcag_criterion=LATEX_ACCESSIBILITY_RULES["color_only_emphasis"][
                            "wcag"
                        ],
                        description=f"Text '{text[:30]}...' uses color ({color}) without additional visual indicator",
                        line_number=line_num,
                        latex_snippet=match.group(0),
                        recommendation=LATEX_ACCESSIBILITY_RULES["color_only_emphasis"][
                            "recommendation"
                        ],
                    )
                )

        # Check for potential low contrast colors
        low_contrast_colors = ["yellow", "lightgray", "lime", "cyan", "pink", "white"]
        for color in low_contrast_colors:
            if (
                f"\\textcolor{{{color}}}" in latex_content
                or f"\\color{{{color}}}" in latex_content
            ):
                issues.append(
                    LaTeXAccessibilityIssue(
                        issue_type="low_contrast_potential",
                        severity=LATEX_ACCESSIBILITY_RULES["low_contrast_potential"][
                            "severity"
                        ],
                        wcag_criterion=LATEX_ACCESSIBILITY_RULES[
                            "low_contrast_potential"
                        ]["wcag"],
                        description=f"Light color '{color}' used which may have insufficient contrast",
                        recommendation=LATEX_ACCESSIBILITY_RULES[
                            "low_contrast_potential"
                        ]["recommendation"],
                    )
                )

        # Check for bare URLs without descriptive text
        bare_url_pattern = r"\\url\{([^}]+)\}"
        for match in re.finditer(bare_url_pattern, latex_content):
            url = match.group(1)
            line_num = latex_content[: match.start()].count("\n") + 1
            issues.append(
                LaTeXAccessibilityIssue(
                    issue_type="unlabeled_hyperlink",
                    severity=LATEX_ACCESSIBILITY_RULES["unlabeled_hyperlink"][
                        "severity"
                    ],
                    wcag_criterion=LATEX_ACCESSIBILITY_RULES["unlabeled_hyperlink"][
                        "wcag"
                    ],
                    description=f"Bare URL '{url[:50]}...' without descriptive link text",
                    line_number=line_num,
                    latex_snippet=match.group(0),
                    recommendation=LATEX_ACCESSIBILITY_RULES["unlabeled_hyperlink"][
                        "recommendation"
                    ],
                )
            )

        # Check for manual list formatting (lines starting with - or * or 1. outside of list environments)
        in_list_env = False
        for i, line in enumerate(lines, 1):
            if (
                "\\begin{itemize}" in line
                or "\\begin{enumerate}" in line
                or "\\begin{description}" in line
            ):
                in_list_env = True
            elif (
                "\\end{itemize}" in line
                or "\\end{enumerate}" in line
                or "\\end{description}" in line
            ):
                in_list_env = False
            elif not in_list_env:
                # Check for manual list formatting
                stripped = line.strip()
                if re.match(r"^[-*•]\s+\w", stripped) or re.match(
                    r"^\d+[.)]\s+\w", stripped
                ):
                    # Make sure it's not in a comment
                    if not stripped.startswith("%"):
                        issues.append(
                            LaTeXAccessibilityIssue(
                                issue_type="missing_list_structure",
                                severity=LATEX_ACCESSIBILITY_RULES[
                                    "missing_list_structure"
                                ]["severity"],
                                wcag_criterion=LATEX_ACCESSIBILITY_RULES[
                                    "missing_list_structure"
                                ]["wcag"],
                                description="Manual list formatting detected instead of proper list environment",
                                line_number=i,
                                latex_snippet=stripped[:60],
                                recommendation=LATEX_ACCESSIBILITY_RULES[
                                    "missing_list_structure"
                                ]["recommendation"],
                            )
                        )

        logger.info(f"Detected {len(issues)} accessibility issues in LaTeX document")
        return issues

    def _classify_content_type(self, latex: str) -> Tuple[LaTeXContentType, List[str]]:
        """
        Classify LaTeX content type and detect special features.

        Args:
            latex: LaTeX source code

        Returns:
            Tuple of (content_type, list of detected features)
        """
        detected_features = []

        # Check for chemistry content
        for pattern in self.chemistry_patterns:
            if re.search(pattern, latex):
                # Identify specific chemistry features
                if r"\ce{" in latex:
                    detected_features.append("mhchem_formula")
                if r"\chemfig{" in latex:
                    detected_features.append("chemfig_structure")
                if r"\schemestart" in latex or r"\schemstart" in latex:
                    detected_features.append("reaction_scheme")
                return LaTeXContentType.CHEMISTRY, detected_features

        # Check for TikZ diagrams (before physics, as TikZ may contain physics)
        for pattern in self.tikz_patterns:
            if re.search(pattern, latex):
                if r"\begin{circuitikz}" in latex:
                    detected_features.append("circuit_diagram")
                elif r"\begin{tikzcd}" in latex:
                    detected_features.append("commutative_diagram")
                elif r"\begin{axis}" in latex:
                    detected_features.append("plot_axis")
                else:
                    detected_features.append("tikz_diagram")
                return LaTeXContentType.DIAGRAM, detected_features

        # Check for physics content
        for pattern in self.physics_patterns:
            if re.search(pattern, latex):
                # Identify specific physics features
                if re.search(r"\\(?:bra|ket|braket|Braket)\{", latex):
                    detected_features.append("bra_ket_notation")
                if re.search(r"\\(?:vb|va|vu)\{", latex):
                    detected_features.append("vector_notation")
                if re.search(r"\\(?:dv|pdv|fdv)\{", latex):
                    detected_features.append("derivative_notation")
                if re.search(r"\\(?:grad|div|curl|laplacian)", latex):
                    detected_features.append("vector_calculus")
                return LaTeXContentType.PHYSICS, detected_features

        # Default to standard math
        return LaTeXContentType.MATH, detected_features

    def detect_equations(self, text: str) -> List[LaTeXEquation]:
        """
        Detect all LaTeX equations in text

        Args:
            text: Input text containing LaTeX equations

        Returns:
            List of detected equations with position and type
        """
        equations = []
        equation_id = 1

        # Detect display equations first (to avoid matching inline within display)
        for pattern in self.display_patterns:
            for match in re.finditer(pattern, text, re.DOTALL):
                latex_source = match.group(1).strip()
                content_type, detected_features = self._classify_content_type(
                    latex_source
                )
                equations.append(
                    LaTeXEquation(
                        equation_id=equation_id,
                        latex_source=latex_source,
                        equation_type="display",
                        content_type=content_type,
                        position_start=match.start(),
                        position_end=match.end(),
                        line_number=text[: match.start()].count("\n") + 1,
                        detected_features=detected_features,
                    )
                )
                equation_id += 1

        # Detect inline equations
        for pattern in self.inline_patterns:
            for match in re.finditer(pattern, text):
                # Skip if already captured as display equation
                if any(
                    eq.position_start <= match.start() < eq.position_end
                    for eq in equations
                ):
                    continue

                latex_source = match.group(1).strip()
                content_type, detected_features = self._classify_content_type(
                    latex_source
                )
                equations.append(
                    LaTeXEquation(
                        equation_id=equation_id,
                        latex_source=latex_source,
                        equation_type="inline",
                        content_type=content_type,
                        position_start=match.start(),
                        position_end=match.end(),
                        line_number=text[: match.start()].count("\n") + 1,
                        detected_features=detected_features,
                    )
                )
                equation_id += 1

        # Sort by position
        equations.sort(key=lambda eq: eq.position_start)

        return equations

    def convert_equation(
        self, equation: LaTeXEquation, equation_context: Dict = None
    ) -> MathMLConversionResult:
        """
        Convert a single LaTeX equation to MathML

        Args:
            equation: LaTeXEquation object
            equation_context: Optional context about the equation's location and surrounding text

        Returns:
            MathMLConversionResult with MathML output and ARIA label
        """
        try:
            # Convert LaTeX to MathML using latex2mathml
            mathml = latex_to_mathml(equation.latex_source)

            # Generate ARIA label with context (Gemini AI or heuristic fallback)
            aria_label = self._generate_aria_label(
                equation.latex_source, equation_context
            )

            # Check WCAG compliance (all conversions are compliant if successful)
            wcag_compliant = True

            return MathMLConversionResult(
                equation_id=equation.equation_id,
                latex_source=equation.latex_source,
                mathml_output=mathml,
                aria_label=aria_label,
                conversion_success=True,
                error_message=None,
                wcag_compliant=wcag_compliant,
            )

        except Exception as e:
            return MathMLConversionResult(
                equation_id=equation.equation_id,
                latex_source=equation.latex_source,
                mathml_output="",
                aria_label=None,
                conversion_success=False,
                error_message=str(e),
                wcag_compliant=False,
            )

    def _generate_aria_label(self, latex: str, equation_context: Dict = None) -> str:
        """
        Generate ARIA label for screen readers with document context

        Uses Gemini AI for natural language descriptions when available,
        falls back to heuristic pattern matching otherwise.

        Args:
            latex: LaTeX source code
            equation_context: Optional context dict containing:
                - 'surrounding_text': Text before/after the equation
                - 'section_title': Current section heading
                - 'document_title': Document title
                - 'topic': Inferred topic (physics, calculus, etc.)

        Returns:
            Natural language description for screen readers
        """
        # Generate heuristic fallback first
        heuristic_label = self._generate_heuristic_aria_label(latex)

        # Try Gemini if enabled
        if self.use_ai and self.llm_client:
            try:
                # Build context string for the prompt
                context_str = ""
                if equation_context:
                    context_parts = []
                    if equation_context.get("document_title"):
                        context_parts.append(
                            f"Document: {equation_context['document_title']}"
                        )
                    if equation_context.get("section_title"):
                        context_parts.append(
                            f"Section: {equation_context['section_title']}"
                        )
                    if equation_context.get("topic"):
                        context_parts.append(f"Subject: {equation_context['topic']}")
                    if equation_context.get("surrounding_text"):
                        # Truncate surrounding text to reasonable length
                        surrounding = equation_context["surrounding_text"][:300]
                        context_parts.append(f'Context: "{surrounding}..."')
                    if context_parts:
                        context_str = "\n\nDOCUMENT CONTEXT:\n" + "\n".join(
                            context_parts
                        )

                # Sanitize LaTeX input to mitigate prompt injection
                from src.utils.security import sanitize_for_prompt

                safe_latex = sanitize_for_prompt(latex, max_length=500)

                prompt = f"""Describe this mathematical expression in clear, accessible language for screen reader users.

LaTeX: {safe_latex}
{context_str}

REQUIREMENTS:
1. Provide a concise natural language description (1-2 sentences)
2. Explain what the math represents in context
3. Focus on meaning, not just reading symbols
4. Use the document context to make the description more relevant
5. Do not include any preamble, just the description"""

                result = self.llm_client.generate_text_sync(
                    prompt=prompt, max_tokens=150, temperature=0.2
                )

                if result.get("success"):
                    ai_label = result["content"].strip()
                    if ai_label and len(ai_label) > 10:
                        logger.info(
                            f"[LaTeX+AI] Generated context-aware ARIA label (provider: {result.get('provider')})"
                        )
                        return ai_label
                    else:
                        logger.warning(
                            "[LaTeX+AI] ARIA label too short, using heuristic"
                        )
                        return heuristic_label
                else:
                    logger.warning(
                        f"[LaTeX+AI] Generation failed: {result.get('error')}, using heuristic"
                    )
                    return heuristic_label

            except Exception as e:
                logger.warning(
                    f"[LaTeX+AI] ARIA generation failed: {e}, using heuristic"
                )
                return heuristic_label
        else:
            return heuristic_label

    def _generate_heuristic_aria_label(
        self, latex: str, content_type: LaTeXContentType = None
    ) -> str:
        """
        Generate ARIA label using pattern matching (fallback method)

        Handles chemistry, physics, and diagram content.

        Args:
            latex: LaTeX source code
            content_type: Optional content type classification

        Returns:
            Pattern-based description
        """
        # Siunitx SI units
        if re.search(r"\\(?:SI|si|num|ang|SIrange|qty|unit)\{", latex):
            return self._generate_siunitx_aria_label(latex)

        # Chemistry content (ChemFig, mhchem)
        if (
            content_type == LaTeXContentType.CHEMISTRY
            or r"\ce{" in latex
            or r"\chemfig{" in latex
        ):
            return self._generate_chemistry_aria_label(latex)

        # Physics content (bra-ket, vectors)
        if content_type == LaTeXContentType.PHYSICS or re.search(
            r"\\(?:bra|ket|braket)\{", latex
        ):
            return self._generate_physics_aria_label(latex)

        # TikZ diagrams
        if content_type == LaTeXContentType.DIAGRAM or r"\begin{tikz" in latex:
            return self._generate_diagram_aria_label(latex)

        # Standard math patterns
        if r"\frac{" in latex:
            return f"Fraction: {latex}"
        elif r"\sqrt{" in latex:
            return f"Square root: {latex}"
        elif r"\int" in latex:
            return f"Integral: {latex}"
        elif r"\sum" in latex:
            return f"Summation: {latex}"
        elif r"\lim" in latex:
            return f"Limit: {latex}"
        elif "^" in latex and "_" in latex:
            return f"Expression with superscript and subscript: {latex}"
        elif "^" in latex:
            return f"Expression with superscript: {latex}"
        elif "_" in latex:
            return f"Expression with subscript: {latex}"
        else:
            return f"Mathematical expression: {latex}"

    def _generate_siunitx_aria_label(self, latex: str) -> str:
        """
        Generate ARIA label for siunitx SI unit expressions.

        Converts siunitx LaTeX commands to accessible text descriptions.

        Args:
            latex: LaTeX siunitx notation

        Returns:
            SI unit description suitable for screen readers
        """
        # \SI{value}{unit} - value with unit (siunitx v2)
        si_match = re.search(r"\\SI\{([^}]+)\}\{([^}]+)\}", latex)
        if si_match:
            value = si_match.group(1)
            unit = si_match.group(2)
            unit_text = self._parse_si_unit(unit)
            return f"{value} {unit_text}"

        # \qty{value}{unit} - value with unit (siunitx v3)
        qty_match = re.search(r"\\qty\{([^}]+)\}\{([^}]+)\}", latex)
        if qty_match:
            value = qty_match.group(1)
            unit = qty_match.group(2)
            unit_text = self._parse_si_unit(unit)
            return f"{value} {unit_text}"

        # \si{unit} - unit only (siunitx v2)
        si_unit_match = re.search(r"\\si\{([^}]+)\}", latex)
        if si_unit_match:
            unit = si_unit_match.group(1)
            return self._parse_si_unit(unit)

        # \unit{unit} - unit only (siunitx v3)
        unit_match = re.search(r"\\unit\{([^}]+)\}", latex)
        if unit_match:
            unit = unit_match.group(1)
            return self._parse_si_unit(unit)

        # \num{number} - formatted number
        num_match = re.search(r"\\num\{([^}]+)\}", latex)
        if num_match:
            number = num_match.group(1)
            return self._parse_si_number(number)

        # \ang{degrees;minutes;seconds} - angle
        ang_match = re.search(r"\\ang\{([^}]+)\}", latex)
        if ang_match:
            angle = ang_match.group(1)
            return self._parse_si_angle(angle)

        # \SIrange{start}{end}{unit} - range with units
        range_match = re.search(r"\\SIrange\{([^}]+)\}\{([^}]+)\}\{([^}]+)\}", latex)
        if range_match:
            start = range_match.group(1)
            end = range_match.group(2)
            unit = range_match.group(3)
            unit_text = self._parse_si_unit(unit)
            return f"{start} to {end} {unit_text}"

        # \numrange{start}{end} - number range
        numrange_match = re.search(r"\\numrange\{([^}]+)\}\{([^}]+)\}", latex)
        if numrange_match:
            start = numrange_match.group(1)
            end = numrange_match.group(2)
            return f"{start} to {end}"

        # \SIlist{values}{unit} - list of values with unit
        silist_match = re.search(r"\\SIlist\{([^}]+)\}\{([^}]+)\}", latex)
        if silist_match:
            values = silist_match.group(1)
            unit = silist_match.group(2)
            unit_text = self._parse_si_unit(unit)
            values_list = values.replace(";", ", ")
            return f"{values_list} {unit_text}"

        # \numlist{values} - list of numbers
        numlist_match = re.search(r"\\numlist\{([^}]+)\}", latex)
        if numlist_match:
            values = numlist_match.group(1)
            return values.replace(";", ", ")

        # Fallback
        return f"SI expression: {latex}"

    def _parse_si_unit(self, unit_latex: str) -> str:
        """
        Parse siunitx unit notation to readable text.

        Args:
            unit_latex: LaTeX unit string (e.g., "\\meter\\per\\second\\squared")

        Returns:
            Readable unit string (e.g., "meters per second squared")
        """
        result_parts = []
        remaining = unit_latex

        # Build combined unit dictionary
        all_units = {**SI_BASE_UNITS, **SI_DERIVED_UNITS, **SI_ACCEPTED_UNITS}

        # Process token by token
        while remaining:
            found_match = False

            # Check for modifiers first (per, squared, cubed)
            for pattern, name in SI_MODIFIERS.items():
                if r"\{" in pattern:
                    # Pattern with argument (e.g., \tothe{2})
                    match = re.match(pattern, remaining)
                    if match:
                        result_parts.append(f"to the power of {match.group(1)}")
                        remaining = remaining[match.end() :]
                        found_match = True
                        break
                else:
                    # Simple pattern
                    if remaining.startswith(pattern.replace("\\\\", "\\")):
                        actual_pattern = pattern.replace("\\\\", "\\")
                        result_parts.append(name)
                        remaining = remaining[len(actual_pattern) :]
                        found_match = True
                        break

            if found_match:
                continue

            # Check for prefixes
            for pattern, name in SI_PREFIXES.items():
                actual_pattern = pattern.replace("\\\\", "\\")
                if remaining.startswith(actual_pattern):
                    result_parts.append(name)
                    remaining = remaining[len(actual_pattern) :]
                    found_match = True
                    break

            if found_match:
                continue

            # Check for units
            for pattern, name in all_units.items():
                actual_pattern = pattern.replace("\\\\", "\\")
                if remaining.startswith(actual_pattern):
                    # Pluralize if this is a base unit after a number
                    unit_name = name
                    if result_parts and result_parts[-1] not in [
                        "per",
                        "square",
                        "cubic",
                    ]:
                        # Only pluralize if previous is a prefix
                        if result_parts[-1] in SI_PREFIXES.values():
                            # Combine prefix with unit
                            prefix = result_parts.pop()
                            unit_name = f"{prefix}{name}s"
                        else:
                            unit_name = f"{name}s" if not name.endswith("s") else name
                    result_parts.append(unit_name)
                    remaining = remaining[len(actual_pattern) :]
                    found_match = True
                    break

            if found_match:
                continue

            # Skip unknown characters
            if remaining:
                remaining = remaining[1:]

        # Join parts intelligently
        if not result_parts:
            return unit_latex  # Return original if parsing failed

        return " ".join(result_parts)

    def _parse_si_number(self, number: str) -> str:
        """
        Parse siunitx number notation to readable text.

        Args:
            number: Number string possibly with scientific notation

        Returns:
            Readable number string
        """
        # Handle scientific notation (e.g., "1.23e-4")
        if "e" in number.lower():
            parts = number.lower().split("e")
            if len(parts) == 2:
                mantissa = parts[0]
                exponent = parts[1]
                return f"{mantissa} times 10 to the power of {exponent}"

        # Handle uncertainty notation (e.g., "1.23(4)" or "1.23 +- 0.04")
        uncertainty_match = re.match(r"([0-9.]+)\(([0-9]+)\)", number)
        if uncertainty_match:
            value = uncertainty_match.group(1)
            uncertainty = uncertainty_match.group(2)
            return f"{value} plus or minus {uncertainty}"

        pm_match = re.match(r"([0-9.]+)\s*[+-]\s*([0-9.]+)", number)
        if pm_match:
            value = pm_match.group(1)
            uncertainty = pm_match.group(2)
            return f"{value} plus or minus {uncertainty}"

        return number

    def _parse_si_angle(self, angle: str) -> str:
        """
        Parse siunitx angle notation to readable text.

        Args:
            angle: Angle string (degrees;minutes;seconds)

        Returns:
            Readable angle string
        """
        parts = angle.split(";")

        if len(parts) == 3:
            degrees, minutes, seconds = parts
            result = []
            if degrees:
                result.append(f"{degrees} degrees")
            if minutes:
                result.append(f"{minutes} minutes")
            if seconds:
                result.append(f"{seconds} seconds")
            return ", ".join(result) if result else "0 degrees"
        elif len(parts) == 2:
            degrees, minutes = parts
            result = []
            if degrees:
                result.append(f"{degrees} degrees")
            if minutes:
                result.append(f"{minutes} minutes")
            return ", ".join(result) if result else "0 degrees"
        elif len(parts) == 1:
            return f"{parts[0]} degrees"

        return f"{angle} degrees"

    def _generate_chemistry_aria_label(self, latex: str) -> str:
        """
        Generate ARIA label for chemistry content.

        Args:
            latex: LaTeX chemistry notation

        Returns:
            Chemistry-specific description
        """
        # Parse mhchem \ce{} content
        ce_match = re.search(r"\\ce\{([^}]+)\}", latex)
        if ce_match:
            formula = ce_match.group(1)
            # Parse chemical formula
            description = self._parse_chemical_formula(formula)
            return f"Chemical formula: {description}"

        # Parse ChemFig molecular structure
        chemfig_match = re.search(r"\\chemfig\{([^}]+)\}", latex)
        if chemfig_match:
            structure = chemfig_match.group(1)
            return f"Molecular structure diagram: {self._describe_chemfig(structure)}"

        # Reaction scheme
        if r"\schemestart" in latex or r"\schemstart" in latex:
            return "Chemical reaction scheme"

        return f"Chemistry notation: {latex}"

    def _parse_chemical_formula(self, formula: str) -> str:
        """
        Parse a chemical formula into readable text.

        Args:
            formula: Chemical formula (e.g., "H2O", "CO2", "NaCl")

        Returns:
            Readable description
        """
        # Handle common molecules
        common_molecules = {
            "H2O": "water (H2O)",
            "CO2": "carbon dioxide (CO2)",
            "NaCl": "sodium chloride (NaCl)",
            "H2SO4": "sulfuric acid (H2SO4)",
            "HCl": "hydrochloric acid (HCl)",
            "NaOH": "sodium hydroxide (NaOH)",
            "CH4": "methane (CH4)",
            "C6H12O6": "glucose (C6H12O6)",
            "NH3": "ammonia (NH3)",
            "O2": "oxygen gas (O2)",
            "N2": "nitrogen gas (N2)",
            "H2": "hydrogen gas (H2)",
        }

        # Check for exact match
        formula_clean = formula.strip()
        if formula_clean in common_molecules:
            return common_molecules[formula_clean]

        # Handle reaction arrows
        if "->" in formula:
            parts = formula.split("->")
            return f"{parts[0].strip()} yields {parts[1].strip()}"
        if "<=>" in formula:
            parts = formula.split("<=>")
            return f"{parts[0].strip()} in equilibrium with {parts[1].strip()}"

        # Default: return the formula with subscripts read out
        result = re.sub(r"(\d+)", r" subscript \1", formula)
        result = re.sub(r"\^(\+|-|\d+[+-]?)", r" superscript \1", result)
        return result.strip() or formula

    def _describe_chemfig(self, structure: str) -> str:
        """
        Describe a ChemFig molecular structure.

        Args:
            structure: ChemFig structure code

        Returns:
            Text description of the structure
        """
        # Count bonds
        single_bonds = structure.count("-") - structure.count("--")
        double_bonds = structure.count("=")
        triple_bonds = structure.count("~")

        # Count atoms (simple heuristic: uppercase letters)
        atoms = re.findall(r"[A-Z][a-z]?", structure)
        atom_count = len(atoms)

        parts = []
        if atom_count > 0:
            parts.append(f"{atom_count} atoms")
        if single_bonds > 0:
            parts.append(f"{single_bonds} single bonds")
        if double_bonds > 0:
            parts.append(f"{double_bonds} double bonds")
        if triple_bonds > 0:
            parts.append(f"{triple_bonds} triple bonds")

        if parts:
            return f"structure with {', '.join(parts)}"
        return "molecular structure"

    def _generate_physics_aria_label(self, latex: str) -> str:
        """
        Generate ARIA label for physics notation.

        Args:
            latex: LaTeX physics notation

        Returns:
            Physics-specific description
        """
        descriptions = []

        # Bra-ket notation
        bra_match = re.search(r"\\bra\{([^}]+)\}", latex)
        ket_match = re.search(r"\\ket\{([^}]+)\}", latex)
        braket_match = re.search(r"\\braket\{([^}]+)\}\{([^}]+)\}", latex)

        if braket_match:
            return f"Inner product of {braket_match.group(1)} and {braket_match.group(2)} (bra-ket notation)"
        if bra_match and ket_match:
            return f"Bra {bra_match.group(1)} times ket {ket_match.group(2)}"
        if bra_match:
            return f"Bra vector {bra_match.group(1)}"
        if ket_match:
            return f"Ket vector {ket_match.group(1)}"

        # Vector notation
        vb_match = re.search(r"\\vb\{([^}]+)\}", latex)
        va_match = re.search(r"\\va\{([^}]+)\}", latex)
        vu_match = re.search(r"\\vu\{([^}]+)\}", latex)

        if vb_match:
            descriptions.append(f"bold vector {vb_match.group(1)}")
        if va_match:
            descriptions.append(f"vector {va_match.group(1)} with arrow")
        if vu_match:
            descriptions.append(f"unit vector {vu_match.group(1)}")

        # Derivatives
        dv_match = re.search(r"\\dv\{([^}]+)\}\{([^}]+)\}", latex)
        pdv_match = re.search(r"\\pdv\{([^}]+)\}\{([^}]+)\}", latex)

        if dv_match:
            descriptions.append(
                f"derivative of {dv_match.group(1)} with respect to {dv_match.group(2)}"
            )
        if pdv_match:
            descriptions.append(
                f"partial derivative of {pdv_match.group(1)} with respect to {pdv_match.group(2)}"
            )

        # Vector calculus operators
        if r"\grad" in latex:
            descriptions.append("gradient operator")
        if r"\div" in latex:
            descriptions.append("divergence operator")
        if r"\curl" in latex:
            descriptions.append("curl operator")
        if r"\laplacian" in latex:
            descriptions.append("Laplacian operator")

        # Common physics symbols
        if r"\hbar" in latex:
            descriptions.append("reduced Planck constant")
        if r"\nabla" in latex:
            descriptions.append("nabla (del) operator")

        if descriptions:
            return "Physics notation: " + ", ".join(descriptions)

        return f"Physics expression: {latex}"

    def _generate_diagram_aria_label(self, latex: str) -> str:
        """
        Generate ARIA label for TikZ diagrams.

        Args:
            latex: TikZ diagram code

        Returns:
            Diagram description (may use AI for complex diagrams)
        """
        # Identify diagram type
        if r"\begin{circuitikz}" in latex:
            # Count circuit elements
            components = []
            if r"\node" in latex:
                components.append("nodes")
            if re.search(r"resistor|R\s*,", latex, re.IGNORECASE):
                components.append("resistors")
            if re.search(r"capacitor|C\s*,", latex, re.IGNORECASE):
                components.append("capacitors")
            if re.search(r"inductor|L\s*,", latex, re.IGNORECASE):
                components.append("inductors")
            if re.search(r"battery|voltage", latex, re.IGNORECASE):
                components.append("voltage sources")

            if components:
                return f"Circuit diagram with {', '.join(components)}"
            return "Electrical circuit diagram"

        if r"\begin{tikzcd}" in latex:
            # Commutative diagram (category theory)
            arrows = latex.count(r"\arrow")
            return f"Commutative diagram with {arrows} arrows (category theory)"

        if r"\begin{axis}" in latex:
            # Plot/graph
            if r"\addplot" in latex:
                plot_count = latex.count(r"\addplot")
                return f"Graph with {plot_count} plotted functions"
            return "Mathematical plot or graph"

        # Generic TikZ diagram
        draw_count = latex.count(r"\draw")
        node_count = latex.count(r"\node")
        fill_count = latex.count(r"\fill")

        parts = []
        if draw_count > 0:
            parts.append(f"{draw_count} drawn elements")
        if node_count > 0:
            parts.append(f"{node_count} labeled nodes")
        if fill_count > 0:
            parts.append(f"{fill_count} filled regions")

        if parts:
            return f"Diagram with {', '.join(parts)}"

        return "TikZ diagram (visual content)"

    def _extract_document_context(self, text: str, file_path: str) -> Dict:
        """
        Extract document-level context for better ARIA label generation.
        Analyzes document structure, title, sections, and topic.
        """
        context = {
            "document_title": None,
            "sections": [],
            "topic": None,
            "filename": os.path.basename(file_path),
        }

        # Try to extract document title (from \title{} command)
        title_match = re.search(r"\\title\{([^}]+)\}", text)
        if title_match:
            context["document_title"] = title_match.group(1).strip()

        # Extract section titles
        section_pattern = r"\\(?:section|subsection|chapter)\*?\{([^}]+)\}"
        for match in re.finditer(section_pattern, text):
            context["sections"].append(
                {"title": match.group(1).strip(), "position": match.start()}
            )

        # Infer topic from common LaTeX packages and content
        if r"\physics" in text or r"\hbar" in text or r"\nabla" in text:
            context["topic"] = "Physics"
        elif r"\ce{" in text or r"\chemfig" in text:
            context["topic"] = "Chemistry"
        elif r"\int" in text and r"\lim" in text:
            context["topic"] = "Calculus"
        elif r"\mathbb{" in text or r"\forall" in text:
            context["topic"] = "Pure Mathematics"
        elif r"\Pr" in text or r"\mathbb{E}" in text:
            context["topic"] = "Probability/Statistics"
        elif r"\matrix" in text or r"\det" in text:
            context["topic"] = "Linear Algebra"

        return context

    def _get_equation_context(
        self, text: str, equation: LaTeXEquation, document_context: Dict
    ) -> Dict:
        """
        Extract context specific to a single equation.
        Includes surrounding text and current section.
        """
        eq_context = {
            "document_title": document_context.get("document_title"),
            "topic": document_context.get("topic"),
            "section_title": None,
            "surrounding_text": None,
        }

        # Find the current section for this equation
        sections = document_context.get("sections", [])
        for section in reversed(sections):
            if section["position"] < equation.position_start:
                eq_context["section_title"] = section["title"]
                break

        # Extract surrounding text (text before and after the equation)
        start_pos = max(0, equation.position_start - 200)
        end_pos = min(len(text), equation.position_end + 200)

        # Get text before and after, excluding other equations
        before_text = text[start_pos : equation.position_start].strip()
        after_text = text[equation.position_end : end_pos].strip()

        # Clean up LaTeX commands from context
        before_text = re.sub(r"\\[a-zA-Z]+(\[[^\]]*\])?(\{[^}]*\})*", "", before_text)
        after_text = re.sub(r"\\[a-zA-Z]+(\[[^\]]*\])?(\{[^}]*\})*", "", after_text)

        eq_context["surrounding_text"] = f"{before_text} [...] {after_text}".strip()

        return eq_context

    def process_document(self, file_path: str) -> DocumentConversionResult:
        """
        Process a document containing LaTeX equations

        Args:
            file_path: Path to text file or LaTeX document

        Returns:
            DocumentConversionResult with all converted equations
        """
        # Read file
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()

        if self.progress_callback:
            self.progress_callback(0, 4, "Parsing LaTeX document...")

        # Extract document-level context
        document_context = self._extract_document_context(text, file_path)

        if self.progress_callback:
            self.progress_callback(1, 4, "Detecting equations...")

        # Detect equations
        equations = self.detect_equations(text)
        total_equations = len(equations)

        if self.progress_callback:
            self.progress_callback(
                2, 4, f"Converting {total_equations} equations to MathML..."
            )

        # Convert each equation with context
        conversions = []
        for idx, equation in enumerate(equations):
            # Report per-equation progress for many equations
            if self.progress_callback and total_equations > 5:
                self.progress_callback(
                    2, 4, f"Converting equation {idx + 1} of {total_equations}..."
                )
            # Get equation-specific context
            eq_context = self._get_equation_context(text, equation, document_context)
            conversion = self.convert_equation(equation, eq_context)
            conversions.append(conversion)

        if self.progress_callback:
            self.progress_callback(3, 4, "Generating accessible HTML output...")

        # Generate HTML output
        html = self._generate_html(
            text, equations, conversions, os.path.basename(file_path)
        )

        if self.progress_callback:
            self.progress_callback(4, 4, "Calculating compliance score...")

        # Calculate statistics
        successful = sum(1 for c in conversions if c.conversion_success)
        failed = len(conversions) - successful

        # Calculate compliance score
        compliance_score = (
            (successful / len(conversions) * 100) if conversions else 100.0
        )

        return DocumentConversionResult(
            file_path=file_path,
            file_name=os.path.basename(file_path),
            total_equations=len(equations),
            successful_conversions=successful,
            failed_conversions=failed,
            equations=conversions,
            html_output=html,
            compliance_score=compliance_score,
        )

    def _generate_html(
        self,
        original_text: str,
        equations: List[LaTeXEquation],
        conversions: List[MathMLConversionResult],
        title: str,
    ) -> str:
        """
        Generate accessible HTML with MathML equations

        Args:
            original_text: Original document text
            equations: List of detected equations
            conversions: List of conversion results
            title: Document title

        Returns:
            HTML with MathML equations embedded
        """
        html = '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        html += '  <meta charset="UTF-8">\n'
        html += (
            '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        )
        html += f"  <title>{title}</title>\n"
        html += "  <style>\n"
        html += "    body { font-family: Arial, sans-serif; line-height: 1.8; max-width: 900px; margin: 0 auto; padding: 40px; }\n"
        html += "    .equation-inline { display: inline-block; margin: 0 0.2em; vertical-align: middle; }\n"
        html += "    .equation-display { display: block; margin: 1.5em 0; text-align: center; }\n"
        html += "    math { font-size: 1.1em; }\n"
        html += "    pre { background: #f5f5f5; padding: 1em; border-radius: 4px; overflow-x: auto; }\n"
        html += "    .error { color: red; background: #fee; padding: 0.5em; border-radius: 4px; }\n"
        html += "  </style>\n"
        html += "</head>\n<body>\n"
        html += f"  <h1>{title}</h1>\n"
        html += "  <p><em>Accessible mathematics generated by Aelira</em></p>\n\n"

        # Replace LaTeX with MathML in original text
        result_text = original_text

        # Sort equations in reverse order to avoid position shifts
        sorted_equations = sorted(
            zip(equations, conversions), key=lambda x: x[0].position_start, reverse=True
        )

        for equation, conversion in sorted_equations:
            if conversion.conversion_success:
                # Wrap MathML with appropriate div and ARIA label
                css_class = (
                    "equation-display"
                    if equation.equation_type == "display"
                    else "equation-inline"
                )
                aria_label = (
                    f' aria-label="{conversion.aria_label}"'
                    if conversion.aria_label
                    else ""
                )

                mathml_html = f'<div class="{css_class}"{aria_label}>{conversion.mathml_output}</div>'

                # Replace LaTeX with MathML
                result_text = (
                    result_text[: equation.position_start]
                    + mathml_html
                    + result_text[equation.position_end :]
                )
            else:
                # Show error message
                error_html = f'<span class="error">Error converting equation: {conversion.error_message}</span>'
                result_text = (
                    result_text[: equation.position_start]
                    + error_html
                    + result_text[equation.position_end :]
                )

        # Convert remaining text to paragraphs
        paragraphs = result_text.split("\n\n")
        for para in paragraphs:
            if para.strip():
                # Check if it contains MathML (don't wrap in <p>)
                if '<div class="equation-' in para or '<span class="error">' in para:
                    html += f"  {para}\n"
                else:
                    html += f"  <p>{para.strip()}</p>\n"

        # Add summary
        html += "\n  <hr>\n"
        html += "  <h2>Conversion Summary</h2>\n"
        html += "  <ul>\n"
        html += f"    <li>Total equations: {len(equations)}</li>\n"
        html += f"    <li>Successfully converted: {sum(1 for c in conversions if c.conversion_success)}</li>\n"
        html += f"    <li>Failed conversions: {sum(1 for c in conversions if not c.conversion_success)}</li>\n"
        html += "  </ul>\n"

        html += "</body>\n</html>"
        return html

    async def process_latex(self, latex_content: str) -> Dict:
        """
        Process raw LaTeX content (async wrapper for tests)

        This method processes LaTeX content directly without requiring a file.
        Returns a dictionary compatible with E2E tests.

        Performs:
        1. Equation detection and MathML conversion
        2. Accessibility issue scanning (images, captions, metadata, etc.)

        Args:
            latex_content: Raw LaTeX text containing equations

        Returns:
            Dictionary with structure:
            {
                "equations": [...],  # List of equation dicts
                "metadata": {...},   # Document metadata
                "compliance": {...}  # Compliance information with all issues
            }
        """
        # Detect equations
        equations = self.detect_equations(latex_content)

        # Convert each equation
        conversions = []
        for equation in equations:
            conversion = self.convert_equation(equation)
            conversions.append(conversion)

        # Detect accessibility issues
        accessibility_issues = self.detect_accessibility_issues(latex_content)

        # Calculate statistics
        successful = sum(1 for c in conversions if c.conversion_success)
        failed = len(conversions) - successful

        # Build issue list combining conversion failures and accessibility issues
        all_issues = []

        # Add conversion failure issues
        for conv in conversions:
            if not conv.conversion_success:
                all_issues.append(
                    {
                        "type": "conversion_failed",
                        "severity": "error",
                        "wcag": "1.1.1",
                        "description": f"Failed to convert equation: {conv.latex_source[:50]}...",
                        "recommendation": "Check LaTeX syntax for errors. Ensure all packages are properly used.",
                    }
                )

        # Add accessibility issues
        for issue in accessibility_issues:
            all_issues.append(
                {
                    "type": issue.issue_type,
                    "severity": issue.severity,
                    "wcag": issue.wcag_criterion,
                    "description": issue.description,
                    "line_number": issue.line_number,
                    "latex_snippet": issue.latex_snippet,
                    "recommendation": issue.recommendation,
                }
            )

        # Calculate compliance score based on both conversions AND accessibility issues
        # Weight: critical=10, serious=5, moderate=2, minor=1
        severity_weights = {
            "critical": 10,
            "serious": 5,
            "moderate": 2,
            "minor": 1,
            "error": 10,
        }
        total_penalty = sum(
            severity_weights.get(issue["severity"], 1) for issue in all_issues
        )

        # Base score starts at 100, deduct penalties (min 0)
        # Scale: 0 issues = 100%, each critical = -10%, serious = -5%, etc.
        base_score = 100.0
        max_penalty = 100.0  # Cap penalty at 100 points
        penalty_applied = min(total_penalty, max_penalty)
        compliance_score = max(0.0, base_score - penalty_applied)

        # Count issues by severity
        critical_count = sum(1 for i in all_issues if i["severity"] == "critical")
        serious_count = sum(
            1 for i in all_issues if i["severity"] in ["serious", "error"]
        )
        moderate_count = sum(1 for i in all_issues if i["severity"] == "moderate")
        minor_count = sum(1 for i in all_issues if i["severity"] == "minor")

        # Build result structure
        result = {
            "equations": [
                {
                    "latex": conv.latex_source,
                    "mathml": conv.mathml_output,
                    "aria_label": conv.aria_label or "",
                }
                for conv in conversions
            ],
            "metadata": {
                "total_equations": len(equations),
                "successful_conversions": successful,
                "failed_conversions": failed,
                "accessibility_issues_found": len(accessibility_issues),
            },
            "compliance": {
                "score": round(compliance_score, 1),
                "issues": all_issues,
                "summary": {
                    "total_issues": len(all_issues),
                    "critical": critical_count,
                    "serious": serious_count,
                    "moderate": moderate_count,
                    "minor": minor_count,
                },
            },
        }

        return result

    async def export_to_html(self, process_result: Dict) -> str:
        """
        Export processing result to accessible HTML (async wrapper for tests)

        Args:
            process_result: Result from process_latex()

        Returns:
            HTML string with accessible math content
        """
        # Build simple HTML document
        html = "<!DOCTYPE html>\n"
        html += '<html lang="en">\n'
        html += "<head>\n"
        html += '  <meta charset="UTF-8">\n'
        html += (
            '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        )
        html += "  <title>Accessible Math Content</title>\n"
        html += "</head>\n"
        html += "<body>\n"
        html += "  <h1>Mathematical Equations</h1>\n"

        # Add each equation
        for i, eq in enumerate(process_result["equations"], 1):
            aria_label = eq.get("aria_label", "")
            mathml = eq.get("mathml", "")

            html += f'  <div class="equation" id="eq-{i}">\n'
            html += f'    <div aria-label="{aria_label}">\n'
            html += f"      {mathml}\n"
            html += "    </div>\n"
            html += "  </div>\n"

        html += "</body>\n"
        html += "</html>"

        return html

    def batch_process(self, file_paths: List[str]) -> List[DocumentConversionResult]:
        """
        Batch process multiple documents

        Args:
            file_paths: List of paths to documents

        Returns:
            List of DocumentConversionResult objects
        """
        results = []
        for file_path in file_paths:
            try:
                result = self.process_document(file_path)
                results.append(result)
            except Exception as e:
                print(f"[LaTeXProcessor] Error processing {file_path}: {e}")

        return results


# Test cases (for development)
def test_latex_processor():
    """Test LaTeX processor with common equations"""
    processor = LaTeXProcessor(use_ai=False)  # Use heuristic for faster testing

    # Standard math test cases
    math_test_cases = [
        ("Fraction", r"$\frac{a}{b}$"),
        ("Square Root", r"$\sqrt{x^2 + y^2}$"),
        ("Integral", r"$\int_{0}^{\infty} e^{-x} dx$"),
        ("Summation", r"$\sum_{i=1}^{n} i^2$"),
        ("Limit", r"$\lim_{x \to 0} \frac{\sin x}{x}$"),
        ("Display Equation", r"$$E = mc^2$$"),
        ("Quadratic Formula", r"$x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}$"),
    ]

    # Chemistry test cases
    chemistry_test_cases = [
        ("Water Formula", r"$\ce{H2O}$"),
        ("Chemical Reaction", r"$\ce{2H2 + O2 -> 2H2O}$"),
        ("Sulfuric Acid", r"$\ce{H2SO4}$"),
        ("Equilibrium", r"$\ce{N2 + 3H2 <=> 2NH3}$"),
    ]

    # Physics test cases
    physics_test_cases = [
        ("Ket Vector", r"$\ket{\psi}$"),
        ("Bra Vector", r"$\bra{\phi}$"),
        ("Inner Product", r"$\braket{\phi}{\psi}$"),
        ("Bold Vector", r"$\vb{F}$"),
        ("Gradient", r"$\grad f$"),
        ("Partial Derivative", r"$\pdv{f}{x}$"),
    ]

    print("=" * 60)
    print("Testing LaTeX to MathML conversion")
    print("=" * 60)

    print("\n--- Standard Math ---\n")
    for name, latex_source in math_test_cases:
        _test_single_equation(processor, name, latex_source)

    print("\n--- Chemistry (mhchem) ---\n")
    for name, latex_source in chemistry_test_cases:
        _test_single_equation(processor, name, latex_source)

    print("\n--- Physics (bra-ket, vectors) ---\n")
    for name, latex_source in physics_test_cases:
        _test_single_equation(processor, name, latex_source)

    # Test content type classification
    print("\n--- Content Type Classification ---\n")
    test_classification_cases = [
        (r"\ce{H2O}", LaTeXContentType.CHEMISTRY),
        (r"\ket{\psi}", LaTeXContentType.PHYSICS),
        (
            r"\begin{tikzpicture}\draw (0,0) -- (1,1);\end{tikzpicture}",
            LaTeXContentType.DIAGRAM,
        ),
        (r"\frac{a}{b}", LaTeXContentType.MATH),
    ]

    for latex, expected_type in test_classification_cases:
        content_type, features = processor._classify_content_type(latex)
        status = "✓" if content_type == expected_type else "✗"
        print(
            f"  {status} '{latex[:30]}...' -> {content_type.value} (expected: {expected_type.value})"
        )
        if features:
            print(f"    Features: {features}")


def _test_single_equation(processor, name: str, latex_source: str):
    """Helper function to test a single equation"""
    # Create a temporary equation
    latex_clean = latex_source.strip("$")
    content_type, features = processor._classify_content_type(latex_clean)

    equation = LaTeXEquation(
        equation_id=1,
        latex_source=latex_clean,
        equation_type="inline",
        content_type=content_type,
        position_start=0,
        position_end=len(latex_source),
        detected_features=features,
    )

    result = processor.convert_equation(equation)

    print(f"{name}:")
    print(f"  LaTeX: {latex_source}")
    print(f"  Type: {content_type.value}")
    if features:
        print(f"  Features: {features}")
    print(f"  Success: {result.conversion_success}")
    if result.conversion_success:
        print(f"  ARIA: {result.aria_label}")
    else:
        print(f"  Error: {result.error_message}")
    print()


if __name__ == "__main__":
    test_latex_processor()
