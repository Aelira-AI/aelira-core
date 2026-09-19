"""Run the functional LaTeX profile as the actual service user; emit JSON evidence."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.education.latex_runtime import probe_runtime  # noqa: E402

if __name__ == "__main__":
    report = probe_runtime()
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["status"] == "ready" else 1)
