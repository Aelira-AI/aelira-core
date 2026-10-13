#!/usr/bin/env python3
"""Exercise evidence export against a real completed native cJSON build."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "repo", "scratch"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--arch", required=True)
    args = parser.parse_args()
    args.scratch.mkdir(mode=0o700)
    revision = subprocess.check_output(["git", "-C", str(args.repo), "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(["git", "-C", str(args.repo), "rev-parse", "HEAD^{tree}"], text=True).strip()
    exporter = Path(__file__).resolve().with_name("collect-evidence.py")
    rows = []

    def invoke(name, source, exit_code=0, require_builder=False):
        destination = args.scratch / (name + "-export")
        command = [sys.executable, "-B", str(exporter), "--source", str(source), "--dest", str(destination),
                   "--repo", str(args.repo), "--arch", args.arch, "--revision", revision, "--tree", tree,
                   "--build-exit", str(exit_code)]
        if require_builder:
            command.append("--require-builder")
        result = subprocess.run(command, capture_output=True, timeout=180)
        receipt = json.loads((destination / "component.json").read_text())
        assert "unlisted-private.txt" not in receipt["files"]
        return result.returncode, receipt

    def snapshot(name):
        destination = args.scratch / (name + "-input")
        # Hard links keep large retained signed indexes immutable and avoid
        # duplicating them. A changed fixture unlinks its file before writing.
        def copy_input(source, target):
            try:
                os.link(source, target)
            except PermissionError:
                # Online preparation files may belong to container root.
                shutil.copyfile(source, target)
        shutil.copytree(args.source, destination, copy_function=copy_input, symlinks=True)
        (destination / "unlisted-private.txt").write_text("SYNTHETIC-UNLISTED-DATA\n")
        return destination

    source = snapshot("positive")
    result, receipt = invoke("positive", source)
    assert result == 0 and receipt["status"] == "pass"
    rows.append("completed-native-export")
    result, receipt = invoke("failed-process", source, exit_code=7)
    assert result == 1 and receipt["status"] == "fail" and not receipt["files"]
    rows.append("failed-process-refused")

    for case, relative in (("changed-recipe", "frozen-recipe/build-source.py"),
                           ("missing-native-control", "build-a/native-O2.log"),
                           ("unsafe-source-link", "source-input/cjson_1.7.19-2.dsc")):
        source = snapshot(case)
        path = source / relative
        data = path.read_bytes()
        path.unlink()
        if case == "changed-recipe":
            path.write_bytes(data + b"\n# synthetic changed recipe\n")
        elif case == "unsafe-source-link":
            path.symlink_to(args.source / relative)
        result, receipt = invoke(case, source)
        assert result == 1 and receipt["status"] == "fail"
        rows.append(case + "-refused")

    source = snapshot("missing-builder")
    tar = source / "provenance/builder.tar"
    if tar.exists():
        tar.unlink()
    result, receipt = invoke("missing-builder", source, require_builder=True)
    assert result == 1 and receipt["status"] == "fail"
    rows.append("missing-required-builder-refused")
    link = args.scratch / "source-alias"
    link.symlink_to(args.source, target_is_directory=True)
    result, receipt = invoke("source-alias", link)
    assert result == 1 and receipt["status"] == "fail" and not receipt["files"]
    rows.append("source-directory-alias-refused")
    for case in ('changed-source-archive', 'changed-source-receipt', 'extra-installed-field',
                 'changed-signed-release', 'absolute-checksum-path', 'changed-binary-package', 'duplicate-receipt-key',
                 'private-phase', 'changed-recipe-manifest', 'changed-base-record', 'changed-builder-record', 'changed-toolchain-inventory'):
        source = snapshot(case)
        if case == 'changed-source-archive':
            path = source / 'source-input/cjson_1.7.19.orig.tar.gz'
            data = bytearray(path.read_bytes())
            data[-1] ^= 1
        elif case == 'changed-source-receipt':
            path = source / 'build-a/artifacts/source-receipt.json'
            value = json.loads(path.read_text())
            value['patched_sources']['cJSON_Utils.c'] = '0' * 64
            data = json.dumps(value).encode()
        elif case == 'extra-installed-field':
            path = source / 'installed-private.log'
            lines = path.read_text().splitlines()
            for index, line in enumerate(lines):
                if line.startswith('installed_cjson_receipt='):
                    value = json.loads(line.split('=', 1)[1])
                    value['unexpected'] = 'SYNTHETIC-PRIVATE-FIELD'
                    lines[index] = 'installed_cjson_receipt=' + json.dumps(value)
            data = ('\n'.join(lines) + '\n').encode()
        elif case == 'changed-signed-release':
            path = next((source / 'provenance/apt-lists').glob('*_InRelease'))
            data = bytearray(path.read_bytes())
            data[0] ^= 1
        elif case == 'absolute-checksum-path':
            path = source / 'reproducible-sha256.txt'
            data = path.read_bytes().replace(b'  libcjson1_', b'  /private/synthetic-path/libcjson1_')
        elif case == 'duplicate-receipt-key':
            path = source / 'build-status.json'
            data = path.read_bytes().replace(b'{', b'{"phase":"SYNTHETIC-PRIVATE-FIELD",', 1)
        elif case == 'private-phase':
            path = source / 'build-status.json'
            value = json.loads(path.read_text())
            value['phase'] = 'SYNTHETIC-PRIVATE-FIELD'
            data = json.dumps(value).encode()
        elif case in ('changed-recipe-manifest', 'changed-base-record', 'changed-builder-record', 'changed-toolchain-inventory'):
            name = {'changed-recipe-manifest': 'recipe-sha256.txt', 'changed-base-record': 'base-image.txt',
                    'changed-builder-record': 'builder-id.txt', 'changed-toolchain-inventory': 'toolchain-packages.tsv'}[case]
            path = source / 'provenance' / name
            data = b'SYNTHETIC-PRIVATE-FIELD\n'
        else:
            path = source / ('build-a/artifacts/libcjson1_1.7.19-2+aelira3_' + args.arch + '.deb')
            data = bytearray(path.read_bytes())
            data[-1] ^= 1
        path.unlink()
        path.write_bytes(data)
        result, receipt = invoke(case, source)
        assert result == 1 and receipt['status'] == 'fail'
        assert 'SYNTHETIC-PRIVATE-FIELD' not in json.dumps(receipt)
        assert str(path.relative_to(source)) not in receipt['files']
        rows.append(case + '-refused')
    source = snapshot('unreferenced-release')
    relative = 'provenance/apt-lists/security.ubuntu.com_ubuntu_dists_resolute-backports_InRelease'
    path = source / relative
    assert not path.exists()
    path.write_text('SYNTHETIC-PRIVATE-FIELD\n')
    result, receipt = invoke('unreferenced-release', source)
    assert result == 0 and receipt['status'] == 'pass' and relative not in receipt['files']
    rows.append('unreferenced-release-not-exported')
    source = snapshot('changed-builder-archive')
    path = source / 'provenance/builder.tar'
    if path.exists():
        path.unlink()
    path.write_text('SYNTHETIC-PRIVATE-FIELD\n')
    result, receipt = invoke('changed-builder-archive', source)
    assert result == 1 and receipt['status'] == 'fail'
    assert 'provenance/builder.tar' not in receipt['files']
    rows.append('changed-builder-archive-refused')
    print(json.dumps({"schema": "aelira-cjson-evidence-controls-v1", "status": "pass",
                      "count": len(rows), "controls": rows}, sort_keys=True))


if __name__ == "__main__":
    main()
