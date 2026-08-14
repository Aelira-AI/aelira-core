"""Run the issue #88 corpus through the scan processors (AI off).

Pass criteria per file:
- processor completes without raising
- result object is non-None

Usage:  python tests/fuzz/run_fuzz.py   (from backend/, venv active,
        after tests/fuzz/build_corpus.py)

Writes tests/fuzz/fuzz-report.md and exits non-zero if anything crashed,
so it can gate CI or a pre-pilot checklist.
"""

import sys
import traceback
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent))  # backend/

CORPUS = HERE / "corpus"

ROUTES = {
    ".docx": ("DocxProcessor", "process_docx", "src.education.docx_processor"),
    ".pptx": ("PowerPointProcessor", "process_pptx", "src.education.pptx_processor"),
    ".pdf": ("PDFProcessor", "process_pdf", "src.education.pdf_processor"),
    ".xlsx": ("XlsxProcessor", "process_xlsx", "src.education.xlsx_processor"),
}


def run_one(path: Path) -> dict:
    import importlib

    cls_name, method, module = ROUTES[path.suffix]
    cls = getattr(importlib.import_module(module), cls_name)
    try:
        proc = cls()  # AI flags default off
    except TypeError:
        proc = cls(generate_alt_text=False)
    try:
        result = getattr(proc, method)(str(path))
        detail = ""
        for attr in ("issues", "all_issues"):
            issues = getattr(result, attr, None)
            if issues is not None:
                detail = f"{len(issues)} issues"
                break
        score = getattr(result, "compliance_score", getattr(result, "score", None))
        if score is not None:
            detail += f", score {score}"
        return {
            "status": "COMPLETED" if result is not None else "RETURNED NONE",
            "detail": detail,
        }
    except Exception as e:
        return {
            "status": "CRASHED",
            "detail": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
        }


def main() -> int:
    if not CORPUS.exists():
        sys.exit("corpus/ missing — run tests/fuzz/build_corpus.py first")
    rows, tracebacks = [], []
    for f in sorted(CORPUS.iterdir()):
        if f.suffix not in ROUTES:
            continue
        r = run_one(f)
        rows.append((f.name, r["status"], r["detail"]))
        if "traceback" in r:
            tracebacks.append((f.name, r["traceback"]))
        print(f"{r['status']:<12} {f.name:<28} {r['detail']}")

    lines = [
        "# Issue #88 fuzz matrix — generator-diverse corpus, AI off",
        "",
        "| File | Status | Detail |",
        "|------|--------|--------|",
        *[f"| {n} | {s} | {d} |" for n, s, d in rows],
    ]
    if tracebacks:
        lines.append("\n## Tracebacks\n")
        for name, tb in tracebacks:
            lines.append(f"### {name}\n\n```\n{tb}\n```")
    (HERE / "fuzz-report.md").write_text("\n".join(lines) + "\n")
    crashed = sum(1 for _, s, _ in rows if s == "CRASHED")
    print(f"\n{len(rows)} files, {crashed} crashed. Report: tests/fuzz/fuzz-report.md")
    return 1 if crashed else 0


if __name__ == "__main__":
    sys.exit(main())
