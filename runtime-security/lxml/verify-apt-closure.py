#!/usr/bin/env python3
"""Compare the pinned base's offline APT plan with authenticated cached DEBs."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

INSTALL = re.compile(r"Inst (\S+)(?: \[[^]]+\])? \((\S+) .* \[([^]]+)\]\)\Z")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: verify-apt-closure.py APT_SIMULATION AUTHENTICATED_PACKAGES"
        )
    simulation = Path(sys.argv[1]).read_text()
    if (
        "Reading package lists..." not in simulation
        or "Solving dependencies..." not in simulation
    ):
        raise SystemExit("incomplete APT simulation")
    selected: set[tuple[str, str, str]] = set()
    for line in simulation.splitlines():
        if not line.startswith("Inst "):
            continue
        match = INSTALL.fullmatch(line)
        if not match or match.groups() in selected:
            raise SystemExit("unrecognized or duplicate APT selection")
        selected.add(match.groups())
    if not selected:
        raise SystemExit("empty APT selection")
    authenticated = json.loads(Path(sys.argv[2]).read_text())
    cached = {
        (row["package"], row["version"], row["architecture"])
        for row in authenticated["debs"]
    }
    if len(cached) != len(authenticated["debs"]) or selected != cached:
        raise SystemExit(
            "authenticated DEBs differ from pinned base's exact APT closure"
        )
    print(
        json.dumps(
            {"selected_packages": len(selected), "exact_closure": True}, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
