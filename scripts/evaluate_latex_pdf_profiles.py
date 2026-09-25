"""Isolated, byte-bound PDF profile research. Never enables application delivery."""

import argparse
from collections import Counter
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.latex_profile_contract import inspect_pdf, parse_verapdf_report

FIXTURES = ROOT / "tests/fixtures/latex_profiles"
CORPUS = ROOT / "tests/fixtures/latex_research"
PROFILES = ("untagged", "existing-ua1", "modern-ua1", "modern-ua2")
PACKAGES = (
    "latex.ltx",
    "tagpdf.sty",
    "luamml.sty",
    "unicode-math.sty",
    "amsmath.sty",
    "amssymb.sty",
    "hyperref.sty",
    "babel.sty",
    "ngerman.ldf",
    "siunitx.sty",
    "physics.sty",
    "titlesec.sty",
    "tikz.sty",
    "article.cls",
    "latex-lab-testphase-math.sty",
    "latex-lab-testphase-table.sty",
)
RUNTIME_PROBE = r"""
import hashlib,json,pathlib,re,subprocess,sys
out={"engine":subprocess.check_output(["lualatex","--version"],text=True).splitlines()[0],"packages":{}}
for name in sys.argv[1:]:
 p=subprocess.run(["kpsewhich",name],capture_output=True,text=True,check=True).stdout.strip()
 b=pathlib.Path(p).read_bytes()
 declaration=re.search(r"\\Provides(?:Expl)?(?:Package|File|Class)\s*\{[^}]+\}\s*(?:\[[^\]]+\]|\{[^}]+\}\s*\{[^}]+\})?",b.decode())
 out["packages"][name]={"sha256":hashlib.sha256(b).hexdigest(),"declaration":re.sub(r"\s+"," ",declaration.group(0)).strip() if declaration else None}
 if name=="latex.ltx":
  out["latex_release"]=re.search(r"\\edef\\fmtversion\s*\{([^}]+)\}",b.decode()).group(1)
print(json.dumps(out,sort_keys=True))
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(command, *, timeout=120, cwd=None):
    return subprocess.run(command, capture_output=True, timeout=timeout, cwd=cwd)


def checked(command, **kwargs):
    result = run(command, **kwargs)
    if result.returncode:
        raise RuntimeError(
            f"Required command failed: {command[0]} ({result.returncode}): "
            + result.stderr.decode(errors="replace")[-2000:]
        )
    return result.stdout


def image_identity(image):
    info = json.loads(checked(["docker", "image", "inspect", image]))[0]
    return {
        "id": info["Id"],
        "architecture": info["Architecture"],
        "os": info["Os"],
        "base_index": info["Config"].get("Labels", {}).get("org.aelira.research.base"),
    }


def runtime_identity(image_id):
    return json.loads(
        checked(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,size=128m",
                "--entrypoint",
                "python3",
                image_id,
                "-c",
                RUNTIME_PROBE,
                *PACKAGES,
            ]
        )
    )


def source_contexts(lock):
    manifest_bytes = (CORPUS / "corpus.json").read_bytes()
    if digest(manifest_bytes) != lock["corpus_manifest_sha256"]:
        raise ValueError("Corpus manifest changed")
    manifest = json.loads(manifest_bytes)
    if (manifest["case_count"], manifest["source_count"]) != (26, 28):
        raise ValueError("Unexpected research corpus inventory")
    contexts = []
    for row in manifest["cases"]:
        files = {}
        for source in row["sources"]:
            path = Path(source["path"])
            if path.is_absolute() or ".." in path.parts or path.parts[0] != row["id"]:
                raise ValueError("Unsafe corpus path")
            data = (CORPUS / path).read_bytes()
            if digest(data) != source["sha256"]:
                raise ValueError("Corpus source changed")
            files[str(path)] = data
        contexts.append(
            {
                "id": row["id"],
                "entrypoint": row["entrypoint"],
                "files": files,
                "language": "de" if row["id"] == "P04" else "en",
            }
        )
    for name, expected in lock["controls"].items():
        if not re.fullmatch(r"S0[1-4]", name):
            raise ValueError("Unexpected control")
        data = (FIXTURES / f"{name}.tex").read_bytes()
        if digest(data) != expected:
            raise ValueError("Profile control changed")
        contexts.append(
            {
                "id": name,
                "entrypoint": f"{name}/main.tex",
                "files": {f"{name}/main.tex": data},
                "language": "en",
            }
        )
    if len(contexts) != 30 or len({c["id"] for c in contexts}) != 30:
        raise ValueError("Profile experiment requires all 30 contexts")
    return contexts


class ProfileRefused(ValueError):
    """The existing application declined to generate a profile variant."""


def profile_source(source, profile, language):
    """Explicit experiment variants; never repairs inferred from document content."""
    if profile not in PROFILES or language not in ("en", "de"):
        raise ValueError("Unknown experiment profile or language")
    if r"\DocumentMetadata" in source or source.count(r"\begin{document}") != 1:
        raise ValueError("Expected a source without a pre-existing profile")
    if profile == "untagged":
        return source
    if profile == "existing-ua1":
        # Execute the current application's injection, rather than a copied approximation.
        from src.education.remediation.latex_remediator import LatexRemediator
        from src.education.remediation.base import RemediationConfig

        declared = source.replace(
            r"\begin{document}",
            "\\usepackage{hyperref}\n\\hypersetup{pdflang={"
            + language
            + "}}\n"
            + r"\begin{document}",
        )
        with tempfile.TemporaryDirectory(prefix="latex-profile-") as scratch:
            source_path = Path(scratch) / "source.tex"
            source_path.write_text(declared)
            remediator = LatexRemediator(
                str(source_path), [], RemediationConfig(use_ai=False)
            )
            remediator._load_document()
            if not remediator._apply_structure_fix("accessibility"):
                raise ProfileRefused("application_structure_profile_refused")
            candidate = remediator._modified_content
    else:
        target, pdf_version, math = (
            ("ua-1", "1.7", "mathml-AF")
            if profile == "modern-ua1"
            else ("ua-2", "2.0", "{mathml-SE,mathml-AF}")
        )
        metadata = (
            "\\DocumentMetadata{lang={"
            + language
            + "},pdfstandard="
            + target
            + ",pdfversion="
            + pdf_version
            + ",tagging=on,tagging-setup={math/setup="
            + math
            + "}}\n"
        )
        candidate = metadata + source.replace(
            r"\begin{document}",
            "\\usepackage{unicode-math}\n\\usepackage{hyperref}\n"
            + r"\begin{document}",
        )
    # The experimental preamble may change; the authored body must remain byte-exact.
    if (
        candidate.split(r"\begin{document}", 1)[1]
        != source.split(r"\begin{document}", 1)[1]
    ):
        raise ValueError("Profile application changed the authored body")
    return candidate


def compile_pdf(image, directory, entrypoint, cache):
    build = directory / "build"
    build.mkdir()
    passes = []
    for number in (1, 2):
        pdf = build / "candidate.pdf"
        if pdf.exists():
            # Only the previous pass's owned output, never a source or external file.
            pdf.unlink()
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--pids-limit",
            "256",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--tmpfs",
            "/tmp:rw,nosuid,size=256m",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--mount",
            f"type=bind,source={directory},target=/work",
            "--mount",
            f"type=bind,source={cache},target=/cache",
            "--workdir",
            "/work/source/" + str(Path(entrypoint).parent),
            "--env",
            "TEXMFCACHE=/cache",
            "--env",
            "TEXMFVAR=/cache",
            "--entrypoint",
            "lualatex",
            image,
            "-no-shell-escape",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-recorder",
            "-jobname=candidate",
            "-output-directory=/work/build",
            Path(entrypoint).name,
        ]
        result = run(command)
        if result.returncode < 0 or result.returncode >= 125:
            raise RuntimeError(
                f"Compiler container execution failed ({result.returncode})"
            )
        log = result.stdout + result.stderr
        (build / f"pass-{number}.log").write_bytes(log)
        passes.append(
            {
                "number": number,
                "exit_code": result.returncode,
                "log_sha256": digest(log),
            }
        )
        if result.returncode:
            diagnostics = [
                line[:500]
                for line in log.decode(errors="replace").splitlines()
                if line.startswith("!") or re.match(r"l\.\d+", line)
            ][:20]
            return None, {
                "status": "failed",
                "passes": passes,
                "diagnostics": diagnostics,
                "partial_pdf_sha256": (
                    digest(pdf.read_bytes()) if pdf.exists() else None
                ),
            }
        data = pdf.read_bytes()
        if not data.startswith(b"%PDF-"):
            raise ValueError("Compiler success without PDF bytes")
        passes[-1]["pdf_sha256"] = digest(data)
        (build / f"pass-{number}.pdf").write_bytes(data)
    return pdf, {
        "status": "completed",
        "passes": passes,
        "unresolved_references": bool(
            re.search(
                rb"undefined (?:references|citations)|(?:Reference|Citation)[^\n]*undefined",
                log,
                re.I,
            )
        ),
    }


def validate_pdf(java, jar, jar_hash, pdf, flavour, compiled_hash):
    before = pdf.read_bytes()
    if digest(before) != compiled_hash:
        raise ValueError("Candidate differs from final compiler output")
    if digest(jar.read_bytes()) != jar_hash:
        raise ValueError("Validator implementation changed")
    result = run(
        [str(java), "-jar", str(jar), "--format", "xml", "--flavour", flavour, str(pdf)]
    )
    (pdf.parent / "verapdf.xml").write_bytes(result.stdout)
    (pdf.parent / "verapdf.stderr").write_bytes(result.stderr)
    if pdf.read_bytes() != before:
        raise ValueError("Candidate changed during independent validation")
    if digest(jar.read_bytes()) != jar_hash:
        raise ValueError("Validator implementation changed during execution")
    receipt = parse_verapdf_report(result.stdout, flavour, pdf, len(before))
    if result.returncode != (0 if receipt["status"] == "passed" else 1):
        raise ValueError("Validator exit disagrees with its report")
    if any(v != "1.30.2" for v in receipt["validator_versions"].values()):
        raise ValueError("Unexpected validator component version")
    return {
        **receipt,
        "candidate_sha256": digest(before),
        "report_sha256": digest(result.stdout),
        "stderr_sha256": digest(result.stderr),
        "exit_code": result.returncode,
    }


def harness_identity():
    paths = [
        "scripts/evaluate_latex_pdf_profiles.py",
        "scripts/latex_profile_contract.py",
        "docker/latex-profiles.Dockerfile",
        "tests/fixtures/latex_profiles/runtime.json",
        "tests/fixtures/latex_research/corpus.json",
        "src/education/remediation/latex_remediator.py",
    ]
    return {p: digest((ROOT / p).read_bytes()) for p in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        required=True,
        help="Built research image; resolved to immutable ID before use",
    )
    parser.add_argument("--verapdf-jar", required=True, type=Path)
    parser.add_argument("--java", default="java", help="Java 21 executable")
    parser.add_argument(
        "--output", required=True, type=Path, help="New evidence directory"
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        help="Exploration only; explicitly marks the report incomplete",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    jar = args.verapdf_jar.resolve(strict=True)
    java = Path(shutil.which(args.java) or args.java).resolve(strict=True)
    lock = json.loads((FIXTURES / "runtime.json").read_text())
    image = image_identity(args.image)
    if image["base_index"] != lock["base_index"]:
        raise ValueError("Research image is not based on the pinned runtime")
    runtime = runtime_identity(image["id"])
    if runtime != lock["tex_runtime"]:
        raise ValueError("Compiler/package identities differ from the reviewed lock")
    if digest(jar.read_bytes()) != lock["verapdf_cli_sha256"]:
        raise ValueError("Validator implementation hash changed")
    java_version = checked([str(java), "--version"]).decode().strip()
    validator_version = checked([str(java), "-jar", str(jar), "--version"]).decode()
    if not validator_version.startswith("veraPDF 1.30.2\n"):
        raise ValueError("Unpinned independent validator")
    hashes = harness_identity()
    revision = checked(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    tracked_diff = digest(checked(["git", "diff", "HEAD", "--binary"], cwd=ROOT))
    contexts = source_contexts(lock)
    if args.cases:
        if not set(args.cases) <= {c["id"] for c in contexts}:
            raise ValueError("Unknown requested cases")
        contexts = [c for c in contexts if c["id"] in args.cases]
    cache = output / "tex-cache"
    cache.mkdir()
    rows, failures = [], []
    from src.education.latex_equation_provenance import source_provenance
    from src.education.remediation.latex_pdf_validation import inspect_pdf_structure
    import pymupdf

    for context in contexts:
        for profile in PROFILES:
            row = {
                "case": context["id"],
                "profile": profile,
                "declared_target": (
                    None
                    if profile == "untagged"
                    else ("ua2" if profile == "modern-ua2" else "ua1")
                ),
                "requested_validator_profile": (
                    "ua2" if profile == "modern-ua2" else "ua1"
                ),
                "language": context["language"],
                "semantic_fidelity": "not_assessed",
                "human_review": "not_run",
                "assistive_technology": "not_run",
                "reader_support": "not_run",
                "source_to_pdf_association": "unmapped",
                "original_files": {
                    name: digest(data) for name, data in context["files"].items()
                },
                "variant_files": {},
                "inspection": None,
                "independent_validation": None,
                "compilation": {"status": "not_run"},
            }
            directory = output / context["id"] / profile
            directory.mkdir(parents=True)
            try:
                for name, original in context["files"].items():
                    variant = original
                    if name == context["entrypoint"]:
                        row["source_provenance"] = source_provenance(
                            original.decode()
                        ).model_dump(mode="json")
                        variant = profile_source(
                            original.decode(), profile, context["language"]
                        ).encode()
                    row["variant_files"][name] = digest(variant)
                    target = directory / "source" / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(variant)
                pdf, row["compilation"] = compile_pdf(
                    image["id"], directory, context["entrypoint"], cache
                )
                row["inspection"] = row["independent_validation"] = None
                if pdf:
                    row["inspection"] = inspect_pdf(pdf)
                    row["application_structure_check"] = inspect_pdf_structure(
                        pdf.read_bytes()
                    )
                    with pymupdf.open(pdf) as saved:
                        text = "\n".join(page.get_text() for page in saved)
                    (pdf.parent / "text.txt").write_text(text)
                    row["text_sha256"] = digest(text.encode())
                    row["independent_validation"] = validate_pdf(
                        java,
                        jar,
                        lock["verapdf_cli_sha256"],
                        pdf,
                        row["requested_validator_profile"],
                        row["compilation"]["passes"][-1]["pdf_sha256"],
                    )
                    if (
                        row["inspection"]["sha256"]
                        != row["independent_validation"]["candidate_sha256"]
                    ):
                        raise ValueError(
                            "Inspection and validator measured different bytes"
                        )
                else:
                    row["unavailable_reason"] = "compiler_failed_no_accepted_pdf"
                print(
                    f"{context['id']} {profile}: compile={row['compilation']['status']} validator="
                    f"{row['independent_validation']['status'] if row['independent_validation'] else 'not_run'}",
                    flush=True,
                )
            except ProfileRefused as error:
                row["generation"] = {"status": "refused", "reason": str(error)}
                row["unavailable_reason"] = "application_profile_refused"
                print(
                    f"{context['id']} {profile}: application profile refused before compilation",
                    flush=True,
                )
            except Exception as error:
                # Runtime/integrity failures are distinct from measured incompatibility.
                row["independent_validation"] = None
                row["execution_error"] = type(error).__name__
                failures.append(f"{context['id']}/{profile}: {error}")
                print(f"ERROR {context['id']} {profile}: {error}", flush=True)
            rows.append(row)
    if harness_identity() != hashes:
        failures.append("Harness changed during execution")
    if checked(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip() != revision:
        failures.append("Repository revision changed during execution")
    # Establish both positive and negative validator paths before interpreting other cases.
    if "S01" in {c["id"] for c in contexts}:
        for profile in ("untagged", "modern-ua1", "modern-ua2"):
            row = next(
                r for r in rows if r["case"] == "S01" and r["profile"] == profile
            )
            expected = "failed" if profile == "untagged" else "passed"
            if (row.get("independent_validation") or {}).get("status") != expected:
                failures.append(
                    f"Positive/negative validator control failed: S01/{profile}"
                )
    if len(rows) != len(contexts) * len(PROFILES):
        failures.append("Incomplete experiment matrix")
    report = {
        "schema_version": 1,
        "issue": 454,
        "revision": revision,
        "tracked_diff_sha256": tracked_diff,
        "harness_sha256": hashes,
        "image": image,
        "tex_runtime": runtime,
        "validator": {
            "version": validator_version.strip(),
            "cli_sha256": lock["verapdf_cli_sha256"],
            "java_version": java_version,
        },
        "inspection_runtime": {
            "python": sys.version,
            "pikepdf": version("pikepdf"),
            "pymupdf": version("pymupdf"),
        },
        "complete_matrix": not bool(args.cases),
        "profiles": list(PROFILES),
        "cases": rows,
        "failures": failures,
        "summary": dict(
            Counter(
                f"{r['profile']}/{(r.get('independent_validation') or {}).get('status', 'not_run')}"
                for r in rows
            )
        ),
        "adoption": "not_approved",
        "scope": "Research observations, not accessibility or semantic certification",
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"rows": len(rows), "failures": failures, "summary": report["summary"]},
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
