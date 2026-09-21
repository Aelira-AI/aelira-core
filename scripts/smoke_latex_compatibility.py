"""Measured, version-scoped converter controls; never accessibility certification.

Run in the production image with no network, read-only root and writable /tmp.
Stdout is a JSON receipt; exit 1 means a control, identity or baseline mismatch.
"""

from __future__ import annotations

import ast
import argparse
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
import types
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/latex_compatibility"
CONVERTER = ROOT / "src/education/remediation/latex_converter.py"
SPEC = importlib.util.spec_from_file_location(
    "compatibility_runtime", ROOT / "src/education/latex_runtime.py"
)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def encoded(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def preprocessor():
    """Execute the exact checked-in method without importing the whole application.

    No replacement behavior or stub: fail if this no longer is a standalone method.
    This allows converter-image probes without unrelated ML dependencies.
    """
    source = CONVERTER.read_text(encoding="utf-8")
    module = ast.parse(source)
    cls = next(
        n
        for n in module.body
        if isinstance(n, ast.ClassDef) and n.name == "LaTeXConverter"
    )
    method = next(
        n
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "_preprocess_for_latexml"
    )
    namespace = {"re": re, "logger": logging.getLogger(__name__)}
    exec(
        compile(
            ast.Module(body=[method], type_ignores=[]),
            "checked_in_preprocessor",
            "exec",
        ),
        namespace,
    )
    return namespace[method.name], digest(
        ast.get_source_segment(source, method).encode()
    )


def command(args, directory, timeout=120):
    code, log, error = runtime.run_tool(args, directory, timeout)
    # Only diagnostic-bearing lines; synthetic source only. Omit runtime paths.
    diagnostics = []
    for line in log.splitlines():
        if re.search(
            r"warning|error|undefined|unrecognized|fatal|missing|failed", line, re.I
        ):
            safe = re.sub(r"(?<![\w:])/(?:[^\s<>\"']+)", "<path>", line.strip())
            if safe[:600] not in diagnostics:
                diagnostics.append(safe[:600])
    return {
        "tool": args[0],
        "returncode": code,
        "runtime_error": error,
        "diagnostics": diagnostics[:40],
        "diagnostics_truncated": len(diagnostics) > 40,
        "log_sha256": digest(log.encode()) if log else None,
    }


def versions(directory):
    result = {}
    for name in ("pandoc", "latexml", "latexmlpost", "lualatex"):
        code, log, error = runtime.run_tool(
            runtime.version_command(name), directory, 10
        )
        match = re.search(r"\b\d{1,4}(?:\.\d{1,4}){1,3}\b", log)
        texlive = re.search(r"TeX Live (\d{4})", log)
        result[name] = {
            "version": match[0] if code == 0 and not error and match else None
        }
        if texlive:
            result[name]["texlive_year"] = texlive[1]
    return result


def packages(directory):
    result = {}
    for name in ("physics", "siunitx", "babel"):
        code, log, error = runtime.run_tool(["kpsewhich", name + ".sty"], directory, 10)
        entry = {
            "status": "unchecked",
            "version": None,
            "release_date": None,
            "source_sha256": None,
        }
        path = (
            Path(log.strip()) if code == 0 and not error and len(log) < 4096 else None
        )
        if (
            path
            and path.is_absolute()
            and path.name == name + ".sty"
            and path.is_file()
            and path.stat().st_size < 2_000_000
        ):
            data = path.read_bytes()
            text = data.decode(errors="replace")
            declaration = re.search(
                r"\\Provides(?:Expl)?Package\s*\{" + re.escape(name) + r"\}(.{0,500})",
                text,
                re.S,
            )
            if declaration:
                date = re.search(r"\b(\d{4}[-/]\d{2}[-/]\d{2})\b", declaration[1])
                version = re.search(
                    r"(?<!\d)(?:v|\{)?(\d+\.\d+(?:\.\d+)?[a-z]?)(?!\d)", declaration[1]
                )
                entry.update(
                    status="observed",
                    version=version[1] if version else None,
                    release_date=date[1] if date else None,
                    source_sha256=digest(data),
                )
        result[name] = entry
    return result


def analyze(case, html: Path, xml: Path):
    soup = (
        BeautifulSoup(html.read_text(encoding="utf-8"), "html.parser")
        if html.is_file()
        else BeautifulSoup("", "html.parser")
    )
    maths = soup.find_all("math")
    math = maths[-1] if maths else None
    tokens = (
        [node.get_text().strip() for node in math.find_all(["mi", "mn", "mo", "mtext"])]
        if math
        else []
    )
    checks = {"mathml": bool(maths)}
    if case == "positive":
        checks.update(
            text="Aelira compatibility control" in soup.get_text(),
            square=bool(
                math and any(n.get_text() == "x2" for n in math.find_all("msup"))
            ),
            addition="+" in tokens and "1" in tokens,
        )
    elif case == "M10":
        checks.update(
            bra_ket=all(t in tokens for t in ("⟨", "ϕ", "|", "ψ", "⟩")),
            addition="+" in tokens,
        )
        fractions = math.find_all("mfrac") if math else []
        checks["second_partial_derivative"] = any(
            len(f.find_all("msup")) >= 2
            and any(s.get_text() in ("∂2", "∂²") for s in f.find_all("msup"))
            and any(s.get_text() in ("x2", "∂x2") for s in f.find_all("msup"))
            and "f" in f.get_text()
            for f in fractions
        )
    elif case == "M12":
        checks.update(
            mantissa="3.00" in tokens,
            scientific_exponent=bool(
                math
                and any(
                    [child.get_text() for child in n.find_all(recursive=False)]
                    == ["10", "8"]
                    for n in math.find_all("msup")
                )
            ),
            quantity_order=tokens[:4]
            in (
                ["3.00", "×", "10", "8"],
                ["3.00", "⋅", "10", "8"],
                ["3.00", "\u2062", "10", "8"],
            ),
            multiplication=any(t in tokens for t in ("×", "⋅", "\u2062")),
            metre="m" in tokens,
            per_second=bool(
                math
                and (
                    any(n.get_text() in ("s−1", "s-1") for n in math.find_all("msup"))
                    or any(
                        "m" in f.get_text() and "s" in f.get_text()
                        for f in math.find_all("mfrac")
                    )
                    or tokens[-3:] == ["m", "/", "s"]
                )
            ),
        )
    elif case == "P04":
        checks.update(
            german_text=all(
                t in soup.get_text()
                for t in ("Die Geschwindigkeit ist", "Dieser Satz ist auf Deutsch.")
            ),
            german_language=bool(
                soup.html and soup.html.get("lang", "").lower() in ("de", "de-de")
            ),
            equation=tokens == ["v", "=", "3"],
        )
    observations = {
        "error_nodes": len(soup.select(".ltx_ERROR")),
        "checks": checks,
        "math_tokens": tokens,
        "document_language": soup.html.get("lang") if soup.html else None,
    }
    xmath = []
    expressions = []
    if xml.is_file():
        try:
            tree = ET.parse(xml)
            nodes = tree.findall(".//{http://dlmf.nist.gov/LaTeXML}XMath")
            raw = b"\n".join(ET.tostring(node, encoding="utf-8") for node in nodes)
            observations["xmath_sha256"] = digest(raw)
            ids = {
                node.get("{http://www.w3.org/XML/1998/namespace}id"): node
                for node in tree.iter()
                if node.get("{http://www.w3.org/XML/1998/namespace}id")
            }

            def expression(node, depth=0):
                if depth > 30:
                    return "depth_limit"
                tag = node.tag.rsplit("}", 1)[-1]
                if tag == "XMRef":
                    target = ids.get(node.get("idref"))
                    return (
                        expression(target, depth + 1)
                        if target is not None
                        else "unresolved_reference"
                    )
                if tag == "XMTok":
                    return (
                        node.get("meaning")
                        or node.get("name")
                        or "".join(node.itertext()).strip()
                    )
                children = list(node)
                if tag in ("XMath", "XMDual") and children:
                    return expression(children[0], depth + 1)
                return [expression(child, depth + 1) for child in children]

            expressions = [expression(node) for node in nodes]
            for node in nodes:
                xmath.extend(
                    {
                        k: v
                        for k, v in token.attrib.items()
                        if k in ("role", "meaning", "name")
                    }
                    | {"text": "".join(token.itertext()).strip()}
                    for token in node.iter()
                    if token.tag.endswith("XMTok")
                )
        except ET.ParseError:
            observations["xml_parse_error"] = True
    observations["xmath_tokens"] = xmath
    observations["xmath_expressions"] = expressions
    if case == "M10":
        checks["operator_semantics"] = expressions == [
            [
                "plus",
                ["inner-product", "phi", "psi"],
                [["partial-derivative", "x", "2"], "f"],
            ]
        ]
        # Presentation alone is not evidence of correct operator interpretation.
        observations["operator_semantics"] = {
            "meaning_tokens": sorted({t["meaning"] for t in xmath if "meaning" in t}),
            "unchecked": not bool(xmath),
        }
    return observations


def measure(case, source, mode, engine, directory, preprocess):
    original = source.read_bytes()
    processed = (
        preprocess(None, original.decode()).encode()
        if mode == "preprocessed"
        else original
    )
    directory.mkdir()
    tex = directory / "input.tex"
    tex.write_bytes(processed)
    html, xml = directory / "output.html", directory / "output.xml"
    if engine == "pandoc":
        commands = [
            [
                "pandoc",
                str(tex),
                "-o",
                str(html),
                "--standalone",
                "--mathml",
                "--toc",
                "--section-divs",
            ]
        ]
    else:
        commands = [
            ["latexml", "--dest=" + str(xml), str(tex)],
            ["latexmlpost", "--dest=" + str(html), "--format=html5", str(xml)],
        ]
    stages = []
    for args in commands:
        if args[0] == "latexmlpost" and not xml.is_file():
            break
        stages.append(command(args, directory))
    observations = analyze(case, html, xml)
    successful = len(stages) == len(commands) and all(
        s["returncode"] == 0 and not s["runtime_error"] for s in stages
    )
    checks = observations["checks"]
    clean = not any(
        re.search(r"(?:^|\s)(?:Error:|Fatal:|\[ERROR\])", line)
        for stage in stages
        for line in stage["diagnostics"]
    )
    status = (
        "supported"
        if successful and clean and all(checks.values())
        else "partial" if successful and any(checks.values()) else "observed_failure"
    )
    oracle_passed = successful and clean and all(checks.values())
    if case in ("M10", "M12") and status == "supported":
        status = "partial"  # One macro control cannot establish entire package support.
    return {
        "case": case,
        "mode": mode,
        "engine": engine,
        "status": status,
        "control_oracle_passed": oracle_passed,
        "source_sha256": digest(original),
        "converter_input_sha256": digest(processed),
        "output_sha256": digest(html.read_bytes()) if html.is_file() else None,
        "intermediate_sha256": digest(xml.read_bytes()) if xml.is_file() else None,
        "analysis_sha256": digest(encoded(observations)),
        "observations": observations,
        "stages": stages,
    }


def integration(root, manifest, focused):
    sys.path.insert(0, str(ROOT))
    if focused:
        # Local lightweight image only. Actual converter and all its dependencies
        # still import normally; only unrelated remediation __init__ is bypassed.
        package = types.ModuleType("src.education.remediation")
        package.__path__ = [str(ROOT / "src/education/remediation")]
        sys.modules[package.__name__] = package
    from src.education.remediation.latex_converter import LaTeXConverter
    from src.education.latex_compatibility import decide_html, tool_versions

    converter = LaTeXConverter()
    rows, failures = [], []
    for case in manifest["cases"]:
        source = ROOT / case["source"]
        out = root / ("integrated-" + case["id"])
        receipts = {}
        result = converter.convert_to_html(
            str(source), str(out), conversion_receipts=receipts
        )
        receipt = receipts.get("html")
        serialized = receipt.model_dump(mode="json") if receipt else None
        candidates = list(out.rglob(source.stem + ".html"))
        candidate = (
            Path(result)
            if result
            else candidates[0] if len(candidates) == 1 else out / "missing.html"
        )
        intermediate = candidate.with_suffix(".xml")
        analysis = analyze(case["id"], candidate, intermediate)
        row = {
            "case": case["id"],
            "accepted": bool(result),
            "receipt": serialized,
            "output_sha256": digest(Path(result).read_bytes()) if result else None,
            "candidate_sha256": (
                digest(candidate.read_bytes()) if candidate.is_file() else None
            ),
            "intermediate_sha256": (
                digest(intermediate.read_bytes()) if intermediate.is_file() else None
            ),
            "candidate_delivered": bool(result),
            "analysis_sha256": digest(encoded(analysis)),
            "observations": analysis,
        }
        original_sha = digest(source.read_bytes())
        if (
            not receipt
            or not receipt.source_equations
            or receipt.source_equations.source_sha256 != original_sha
        ):
            failures.append(case["id"] + ":source_equations_binding_missing")
        if case["id"] == "M10":
            parse = (
                next(
                    (stage for stage in receipt.stages if stage.tool == "latexml"), None
                )
                if receipt
                else None
            )
            if (
                not parse
                or not parse.equations
                or parse.equations.status != "observed"
                or not any(
                    node.intermediate_semantics_sha256 for node in parse.equations.nodes
                )
            ):
                failures.append("M10:intermediate_semantics_trace_missing")
            if (
                not parse
                or not intermediate.is_file()
                or parse.candidate_sha256 != digest(intermediate.read_bytes())
            ):
                failures.append("M10:intermediate_bytes_not_bound")
            if (
                not receipt
                or not parse
                or not receipt.preprocessing
                or not any(
                    item.input_sha256 == original_sha
                    and item.output_sha256 == parse.input_sha256
                    for item in receipt.preprocessing
                )
            ):
                failures.append("M10:preprocessing_chain_missing")
        if case["id"] == "M12":
            trace = receipt.equations if receipt else None
            if (
                not result
                or not trace
                or trace.status != "observed"
                or not trace.nodes
                or trace.source_sha256 != original_sha
                or trace.candidate_sha256 != digest(Path(result).read_bytes())
            ):
                failures.append("M12:final_trace_binding_failed")
            if (
                not receipt
                or not receipt.decision
                or receipt.decision.source_sha256 != original_sha
                or receipt.decision.matrix_version != "latex-html-controls-v1"
            ):
                failures.append("M12:decision_binding_failed")
        expected_route = "latexml" if case["id"] == "M10" else "pandoc"
        if (
            not receipt
            or not receipt.decision
            or receipt.decision.selected_route != expected_route
        ):
            failures.append(case["id"] + ":integrated_route_mismatch")
        expected_acceptance = case.get("integrated_accepted")
        if expected_acceptance is not None and bool(result) != expected_acceptance:
            failures.append(case["id"] + ":integrated_acceptance_changed")
        expected_reason = case.get("integrated_refusal")
        if expected_reason and (
            not receipt
            or not any(
                d.code == expected_reason
                for stage in receipt.stages
                for d in stage.diagnostics
            )
        ):
            failures.append(case["id"] + ":integrated_refusal_changed")
        actual_tools = (
            {
                s.tool
                for s in receipt.stages
                if s.tool in ("latexml", "latexmlpost", "pandoc")
            }
            if receipt
            else set()
        )
        if actual_tools != (
            {"latexml", "latexmlpost"} if expected_route == "latexml" else {"pandoc"}
        ):
            failures.append(case["id"] + ":integrated_tool_inventory_mismatch")
        if result and (
            not all(analysis["checks"].values())
            or not receipt
            or receipt.status != "accepted"
        ):
            failures.append(case["id"] + ":integrated_oracle_failed")
        rows.append(row)
    physics = next(c for c in manifest["cases"] if c["id"] == "M10")
    decision = decide_html(
        (ROOT / physics["source"]).read_text(),
        available={"pandoc", "latexml"},
        versions=tool_versions({"pandoc", "latexml"}),
        project=True,
    )
    rows.append(
        {"case": "native-project-physics", "decision": decision.model_dump(mode="json")}
    )
    if (
        decision.selected_route is not None
        or "requirements_conflict" not in decision.reasons
    ):
        failures.append("native-project-physics:must_refuse")
    return rows, failures


def presentation_tree(node):
    """Normalize harmless grouping; retain script attachment and fraction scope."""
    if node.name in ("annotation", "annotation-xml", "mspace"):
        return None
    if node.name in ("mi", "mn", "mo", "mtext"):
        value = node.get_text().strip().replace("−", "-")
        return None if value in ("", "\u2062") else value
    children = [presentation_tree(child) for child in node.find_all(recursive=False)]
    children = [child for child in children if child is not None]
    if node.name in ("msup", "msub") and len(children) == 2:
        base, script = children
        if isinstance(base, list) and base[0] == (
            "subscript" if node.name == "msup" else "power"
        ):
            return [
                "scripts",
                base[1],
                base[2] if node.name == "msup" else script,
                script if node.name == "msup" else base[2],
            ]
        return ["power" if node.name == "msup" else "subscript", *children]
    if node.name == "msubsup":
        return ["scripts", *children]
    if node.name == "mfrac":
        return ["fraction", *children]
    if node.name in ("math", "semantics", "mstyle", "mrow", "mtd"):
        flattened = []
        for child in children:
            flattened.extend(
                child[1:] if isinstance(child, list) and child[0] == "row" else [child]
            )
        return (
            flattened[0]
            if len(flattened) == 1
            else ["row", *flattened] if flattened else None
        )
    return [node.name, *children]


def saved_structure(case, html, xml):
    soup = BeautifulSoup(html.read_bytes(), "html.parser")
    maths = soup.find_all("math")
    trees = [presentation_tree(math) for math in maths]
    checks = {"mathml": bool(maths)}
    details = {"presentation_trees": trees}
    if case == "M03":
        checks["exponent_contains_subscript"] = trees == [
            ["power", "x", ["subscript", "a", "b"]]
        ]
    elif case == "M04":
        checks["scripts_share_base"] = trees == [["scripts", "x", "b", "a"]]
    elif case == "M06":
        tables = [table for math in maths for table in math.find_all("mtable")]
        cells = (
            [
                [
                    re.sub(r"\s+", "", cell.get_text()).replace("−", "-")
                    for cell in row.find_all("mtd", recursive=False)
                ]
                for row in tables[0].find_all("mtr", recursive=False)
            ]
            if len(tables) == 1
            else []
        )
        checks["matrix_coordinates"] = cells == [["1", "0", "-i"], ["i", "2", "3"]]
        details["matrix_cells"] = cells
    elif case == "M14":
        tree = ET.parse(xml)
        ns = "{http://dlmf.nist.gov/LaTeXML}"
        labels = []
        row_trees = []
        links = []
        for equation in tree.findall(".//" + ns + "equation"):
            label = equation.get("labels")
            identity = equation.get("{http://www.w3.org/XML/1998/namespace}id")
            labels.append([label, identity])
            target = soup.find(id=identity)
            pieces = (
                [presentation_tree(math) for math in target.find_all("math")]
                if target
                else []
            )
            flat = []
            for piece in pieces:
                flat.extend(
                    piece[1:]
                    if isinstance(piece, list) and piece[0] == "row"
                    else [piece]
                )
            row_trees.append(["row", *flat])
            links.append(
                [a.get_text().strip() for a in soup.find_all("a", href="#" + identity)]
                if identity
                else []
            )
        checks["aligned_row_order"] = row_trees == [
            ["row", "E", "=", "m", ["power", "c", "2"]],
            ["row", ["fraction", "E", ["power", "c", "2"]], "=", "m"],
        ]
        checks["label_identity"] = [label for label, _ in labels] == [
            "LABEL:eq:energy",
            "LABEL:eq:mass",
        ] and len({identity for _, identity in labels}) == 2
        checks["exact_reference_targets"] = links == [["1"], ["2"]] and [
            ref.get("labelref") for ref in tree.findall(".//" + ns + "ref")
        ] == ["LABEL:eq:energy", "LABEL:eq:mass"]
        details.update(
            label_targets=labels, aligned_rows=row_trees, reference_text=links
        )
    elif case == "M16":
        tables = [table for math in maths for table in math.find_all("mtable")]
        rows = tables[0].find_all("mtr", recursive=False) if len(tables) == 1 else []
        fractions = [
            [presentation_tree(f) for f in row.find_all("mfrac")] for row in rows
        ]
        expected = []
        for index in range(1, 17):
            n = str(index)
            expected.append(
                [
                    "fraction",
                    [
                        "row",
                        ["subscript", "α", n],
                        ["power", "x", n],
                        "+",
                        ["subscript", "β", n],
                        ["subscript", "y", n],
                    ],
                    [
                        "row",
                        "1",
                        "+",
                        ["subscript", "γ", n],
                        ["power", "z", str(index + 1)],
                    ],
                ]
            )
        checks["term_order_and_scope"] = fractions[:4] == [
            expected[start : start + 4] for start in range(0, 16, 4)
        ]
        sentinel = [
            "fraction",
            ["row", "97", ["subscript", "q", ["row", "e", "n", "d"]]],
            ["row", "1", "+", ["power", "z", "2"]],
        ]
        checks["final_term"] = len(fractions) == 5 and fractions[-1] == [sentinel]
        checks["final_addition"] = bool(
            rows
            and [n.get_text().strip() for n in rows[-1].find_all(["mi", "mo", "mn"])]
            == ["+", "97", "q", "e", "n", "d", "1", "+", "z", "2"]
        )
        details["row_fraction_counts"] = [len(row) for row in fractions]
    return {"checks": checks, **details}


def saved_node_controls(root, manifest):
    from src.education.remediation.latex_converter import LaTeXConverter
    from src.education.latex_diagnostics import conversion_session
    from src.education.latex_equation_provenance import observe_representation

    converter = LaTeXConverter()
    rows, failures = [], []
    for case in manifest["saved_node_cases"]:
        source = ROOT / case["source"]
        original = source.read_bytes()
        directory = root / ("saved-nodes-" + case["id"])
        directory.mkdir()
        with conversion_session() as stages:
            result = getattr(converter, "_convert_with_" + case["engine"])(
                str(source), directory
            )
        candidate = directory / (source.stem + ".html")
        intermediate = candidate.with_suffix(".xml")
        if not candidate.is_file():
            failures.append(case["id"] + ":saved_candidate_missing")
            continue
        trace = observe_representation(original.decode(), candidate, "html")
        analysis = saved_structure(case["id"], candidate, intermediate)
        problems = []
        if digest(original) != case["sha256"]:
            problems.append("source_hash_mismatch")
        if not all(analysis["checks"].values()):
            problems.append("structural_oracle_failed")
        if (
            trace.status != "observed"
            or not trace.nodes
            or trace.candidate_sha256 != digest(candidate.read_bytes())
            or trace.source_sha256 != digest(original)
        ):
            problems.append("saved_trace_binding_failed")
        if bool(result) != case["accepted"]:
            problems.append("saved_acceptance_changed")
        parse = next((stage for stage in stages if stage.tool == case["engine"]), None)
        if (
            not parse
            or not parse.equations
            or parse.equations.status != "observed"
            or not parse.equations.nodes
        ):
            problems.append("parse_trace_missing")
        if case["engine"] == "latexml" and (
            not parse
            or not parse.equations
            or not any(
                node.intermediate_semantics_sha256 for node in parse.equations.nodes
            )
        ):
            problems.append("intermediate_semantics_missing")
        if case.get("refusal") and not any(
            d.code == case["refusal"] for stage in stages for d in stage.diagnostics
        ):
            problems.append("saved_refusal_changed")
        failures.extend(case["id"] + ":" + problem for problem in problems)
        rows.append(
            {
                "case": case["id"],
                "engine": case["engine"],
                "candidate_delivered": bool(result),
                "source_sha256": digest(original),
                "output_sha256": digest(candidate.read_bytes()),
                "analysis_sha256": digest(encoded(analysis)),
                "analysis": analysis,
                "trace": trace.model_dump(mode="json"),
                "stages": [stage.model_dump(mode="json") for stage in stages],
            }
        )
    if {row["case"] for row in rows} != {"M03", "M04", "M06", "M14", "M16"} or len(
        rows
    ) != 5:
        failures.append("saved_node_inventory_mismatch")
    return rows, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--focused-imports",
        action="store_true",
        help="Local lightweight image: bypass unrelated remediation package initialization; never substitute converter behavior",
    )
    args = parser.parse_args()
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    preprocess, preprocess_hash = preprocessor()
    report = {
        "schema": "aelira-latex-compatibility-v1",
        "scope": "Synthetic structural observations for installed versions only; no fidelity score, accessibility, or assistive-technology claim",
        "architecture": platform.machine(),
        "image_identity": os.environ.get("AELIRA_RUNTIME_IMAGE_ID"),
        "preprocessor_method_sha256": preprocess_hash,
        "oracle_script_sha256": digest(Path(__file__).read_bytes()),
        "manifest_sha256": digest((FIXTURES / "manifest.json").read_bytes()),
        "rows": [],
        "failures": [],
    }
    with tempfile.TemporaryDirectory(prefix="aelira-compatibility-") as temp:
        root = Path(temp)
        report["tools"], report["packages"] = versions(root), packages(root)
        control = root / "positive.tex"
        control.write_text(
            "\\documentclass{article}\n\\begin{document}\nAelira compatibility control. $x^2+1$.\n\\end{document}\n"
        )
        for engine in ("pandoc", "latexml"):
            row = measure(
                "positive",
                control,
                "raw",
                engine,
                root / ("positive-" + engine),
                preprocess,
            )
            report["rows"].append(row)
            if row["status"] != "supported":
                report["failures"].append(engine + ":positive_control_failed")
        for case in manifest["cases"]:
            source = ROOT / case["source"]
            if digest(source.read_bytes()) != case["sha256"]:
                report["failures"].append(case["id"] + ":source_hash_mismatch")
            for mode in ("raw", "preprocessed"):
                for engine in ("pandoc", "latexml"):
                    row = measure(
                        case["id"],
                        source,
                        mode,
                        engine,
                        root / (case["id"] + "-" + mode + "-" + engine),
                        preprocess,
                    )
                    report["rows"].append(row)
                    expected = case.get("expected", {}).get(mode + ":" + engine)
                    if expected is not None and row["status"] != expected:
                        report["failures"].append(
                            case["id"] + ":" + mode + ":" + engine + ":baseline_changed"
                        )
                    expected_oracle = case.get("oracle_passed", {}).get(
                        mode + ":" + engine
                    )
                    if (
                        expected_oracle is not None
                        and row["control_oracle_passed"] != expected_oracle
                    ):
                        report["failures"].append(
                            case["id"] + ":" + mode + ":" + engine + ":oracle_changed"
                        )
                    expected_diagnostic = case.get("diagnostic_contains", {}).get(
                        mode + ":" + engine
                    )
                    if expected_diagnostic and not any(
                        expected_diagnostic in diagnostic
                        for stage in row["stages"]
                        for diagnostic in stage["diagnostics"]
                    ):
                        report["failures"].append(
                            case["id"]
                            + ":"
                            + mode
                            + ":"
                            + engine
                            + ":diagnostic_changed"
                        )
                    if any(s["runtime_error"] for s in row["stages"]):
                        report["failures"].append(
                            case["id"] + ":" + mode + ":" + engine + ":runtime_error"
                        )
        if len(report["rows"]) != 14 or {c["id"] for c in manifest["cases"]} != {
            "M10",
            "M12",
            "P04",
        }:
            report["failures"].append("case_inventory_mismatch")
        for package, identity in manifest.get("package_versions", {}).items():
            for field in ("version", "release_date", "source_sha256"):
                if report["packages"].get(package, {}).get(field) != identity.get(
                    field
                ):
                    report["failures"].append(
                        package + ":" + field + ":requires_review"
                    )
        for tool, version in manifest.get("tool_versions", {}).items():
            if report["tools"].get(tool, {}).get("version") != version:
                report["failures"].append(tool + ":version_requires_review")
        report["integration_import_mode"] = (
            "focused" if args.focused_imports else "application"
        )
        report["integration"], integration_failures = integration(
            root, manifest, args.focused_imports
        )
        report["failures"].extend(integration_failures)
        report["saved_nodes"], saved_failures = saved_node_controls(root, manifest)
        report["failures"].extend(saved_failures)
        report["passed"] = not report["failures"]
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
