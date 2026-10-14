#!/usr/bin/env python3
"""Check real source preparation and refusal before invoking dpkg-source."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    args = parser.parse_args()
    args.scratch.mkdir(mode=0o700)
    recipe = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "xdg_source", recipe / "build-source.py"
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    originals = helper.verify_inputs(args.input, helper.INPUTS)
    repairs = helper.verify_inputs(recipe, helper.REPAIR_INPUTS)
    rows = []

    def invoke(script, inputs, output):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(script),
                "--input",
                str(inputs),
                "--out",
                str(output),
            ],
            capture_output=True,
            timeout=240,
        )

    # This is a real extraction, patch application and source-package build.
    positive = args.scratch / "positive"
    result = invoke(recipe / "build-source.py", args.input, positive)
    assert result.returncode == 0, result.stderr.decode()
    receipt = json.loads(result.stdout)
    assert (
        receipt["status"] == "pass" and receipt["version"] == "1.2.1-2ubuntu2+aelira2"
    )
    helper.verify_tree(positive / "xdg-utils-1.2.1-aelira", helper.PATCHED)
    rows.append("exact-inputs-real-source-package")

    for changed in [*originals, *repairs, "input-symlink", "missing-source"]:
        case = args.scratch / changed
        inputs = case / "inputs"
        local_recipe = case / "recipe"
        inputs.mkdir(parents=True)
        local_recipe.mkdir()
        shutil.copyfile(recipe / "build-source.py", local_recipe / "build-source.py")
        for name, data in originals.items():
            (inputs / name).write_bytes(data)
        for name, data in repairs.items():
            (local_recipe / name).write_bytes(data)
        if changed in originals or changed in repairs:
            target = (inputs if changed in originals else local_recipe) / changed
            data = bytearray(target.read_bytes())
            data[-1] ^= 1
            target.write_bytes(data)
        elif changed == "input-symlink":
            target = inputs / next(iter(originals))
            target.unlink()
            target.symlink_to(args.input / target.name)
        else:
            (inputs / next(iter(originals))).unlink()
        output = case / "output"
        result = invoke(local_recipe / "build-source.py", inputs, output)
        assert result.returncode == 1 and not output.exists(), changed
        assert json.loads(result.stderr)["status"] == "fail", changed
        rows.append("preflight-refusal:" + changed)

    before = hashlib.sha256((positive / "source-receipt.json").read_bytes()).hexdigest()
    result = invoke(recipe / "build-source.py", args.input, positive)
    assert result.returncode == 1
    assert (
        hashlib.sha256((positive / "source-receipt.json").read_bytes()).hexdigest()
        == before
    )
    rows.append("existing-output-preserved")
    result = {
        "schema": "aelira-xdg-source-controls-v1",
        "status": "pass",
        "count": len(rows),
        "controls": rows,
    }
    (args.scratch / "controls.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n"
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
