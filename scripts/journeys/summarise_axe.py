"""Print a Markdown table of the axe results the journey suite recorded."""

import json
import sys
from pathlib import Path


def main(path: str) -> None:
    file = Path(path)
    if not file.is_file():
        print("No accessibility results were recorded.")
        return
    rows = [json.loads(line) for line in file.read_text().splitlines() if line.strip()]
    blocking = sum(row["blocking"] for row in rows)
    print("### Accessibility checks (axe, WCAG 2.1 A and AA)\n")
    print(f"{len(rows)} pages checked, {blocking} serious or critical violations.\n")
    print("| Page | Serious or critical | Moderate or minor |")
    print("| --- | ---: | ---: |")
    for row in rows:
        print(f"| {row['page']} | {row['blocking']} | {row['minorOrModerate']} |")


if __name__ == "__main__":
    main(sys.argv[1])
