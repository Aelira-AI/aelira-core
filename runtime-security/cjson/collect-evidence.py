#!/usr/bin/env python3
"""Export declared public cJSON build inputs and typed component receipts."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import io

MAX_FILE = 2 * 1024 * 1024 * 1024
MAX_TOTAL = 3 * 1024 * 1024 * 1024
INDEX = re.compile(r"(?:archive\.ubuntu\.com_ubuntu|ports\.ubuntu\.com_ubuntu-ports|security\.ubuntu\.com_ubuntu)_dists_resolute(?:-updates|-security|-backports)?_(?:InRelease|(?:main|universe|restricted|multiverse)_source_Sources(?:\.lz4)?)")
RECIPE = ("README.md", "build.sh", "build-in-container.sh", "prepare-native.sh", "test-installed.sh",
          "build-source.py", "test-source.py", "verify-ubuntu-source.py", "test-native.c",
          "changelog-entry.txt", "ubuntu-1.7.19-security-backport.patch")
BASE = 'ubuntu@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7'
SOURCE_CONTROLS = ["exact-inputs-real-source-package", "preflight-refusal:cjson_1.7.19-2.dsc",
                   "preflight-refusal:cjson_1.7.19.orig.tar.gz", "preflight-refusal:cjson_1.7.19-2.debian.tar.xz",
                   "preflight-refusal:ubuntu-1.7.19-security-backport.patch", "preflight-refusal:changelog-entry.txt",
                   "preflight-refusal:input-symlink", "preflight-refusal:missing-source", "existing-output-preserved"]


def regular(root, relative):
    current = root
    for part in Path(relative).parts:
        if part in ("..", "."):
            raise ValueError("Unsafe evidence path")
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("Evidence symlink")
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
        raise ValueError("Invalid evidence file")
    return current


def small(root, relative):
    path = regular(root, relative)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Receipt exceeds bound")
    return path.read_bytes()


def decode_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate receipt key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def export(args):
    args.dest.mkdir(mode=0o700)
    rows = {}
    total = 0
    errors = []
    receipt = {"schema": "aelira-cjson-native-evidence-v1", "status": "fail",
               "architecture": args.arch, "revision": args.revision, "tree": args.tree,
               "build_exit": args.build_exit, "scope": "Native cJSON component; final release images require separate acceptance"}

    def copy(relative, expected=None):
        nonlocal total
        path = regular(args.source, relative)
        checked_data = None
        if expected is not None:
            if path.stat().st_size > 128 * 1024 * 1024:
                raise ValueError('Verified input exceeds bound')
            checked_data = path.read_bytes()
            if hashlib.sha256(checked_data).hexdigest() != expected:
                raise ValueError("Declared input or artifact hash mismatch")
        total += path.stat().st_size
        if total > MAX_TOTAL or len(rows) >= 200:
            raise ValueError("Evidence bundle exceeds bound")
        destination = args.dest / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        sha = hashlib.sha256()
        if checked_data is not None:
            destination.write_bytes(checked_data)
            sha.update(checked_data)
        else:
            with path.open("rb") as incoming, destination.open("xb") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    sha.update(chunk)
                    outgoing.write(chunk)
        rows[relative] = {"sha256": sha.hexdigest(), "size": destination.stat().st_size}

    try:
        if args.arch not in ("amd64", "arm64"):
            raise ValueError("Invalid architecture")
        if not args.source.is_absolute() or args.source.resolve() != args.source or not args.source.is_dir():
            raise ValueError("Source directory must be canonical")
        for ref, expected in (("HEAD", args.revision), ("HEAD^{tree}", args.tree)):
            if not re.fullmatch(r"[a-f0-9]{40}", expected):
                raise ValueError("Invalid checkout identity")
            actual = subprocess.check_output(['git', '-C', str(args.repo), 'rev-parse', ref], text=True).strip()
            if actual != expected:
                raise ValueError("Checkout identity mismatch")
        dirty = subprocess.check_output(['git', '-C', str(args.repo), 'status', '--porcelain', '--',
                                         'runtime-security/cjson', '.github/workflows/ci.yml'], text=True)
        if dirty:
            raise ValueError("Recipe checkout is dirty")
        status = decode_json(small(args.source, "build-status.json"))
        receipt["stage"] = 'complete' if status == {"schema": "aelira-cjson-build-status-v1", "phase": "complete", "exit": 0} else 'incomplete'
        if status != {"schema": "aelira-cjson-build-status-v1", "phase": "complete", "exit": 0} or args.build_exit != 0:
            raise ValueError("Native build did not complete")
        copy("build-status.json")
        reproducible = {}
        for line in small(args.source, "reproducible-sha256.txt").decode().splitlines():
            match = re.fullmatch(r"([a-f0-9]{64})  (cjson_1\.7\.19-2\+aelira3\.(?:dsc|debian\.tar\.xz)|libcjson(?:1|-dev)_1\.7\.19-2\+aelira3_(?:amd64|arm64)\.deb)", line)
            if match is None or match[2] in reproducible:
                raise ValueError("Noncanonical reproducibility manifest")
            reproducible[match[2]] = match[1]
        expected_artifacts = {"cjson_1.7.19-2+aelira3.dsc", "cjson_1.7.19-2+aelira3.debian.tar.xz",
                              "libcjson1_1.7.19-2+aelira3_" + args.arch + ".deb",
                              "libcjson-dev_1.7.19-2+aelira3_" + args.arch + ".deb"}
        if set(reproducible) != expected_artifacts:
            raise ValueError("Incomplete reproducibility manifest")
        copy("reproducible-sha256.txt")
        for name in RECIPE:
            relative = "frozen-recipe/" + name
            expected = hashlib.sha256((args.repo / "runtime-security/cjson" / name).read_bytes()).hexdigest()
            copy(relative, expected)
        spec = importlib.util.spec_from_file_location('cjson_source', args.repo / 'runtime-security/cjson/build-source.py')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        source_identity = {name: {'sha256': digest, 'size': size} for name, (size, digest) in helper.INPUTS.items()}
        repair_identity = {name: {'sha256': digest, 'size': size} for name, (size, digest) in helper.REPAIR_INPUTS.items()}
        for name in ("cjson_1.7.19-2.dsc", "cjson_1.7.19.orig.tar.gz", "cjson_1.7.19-2.debian.tar.xz"):
            copy("source-input/" + name, source_identity[name]['sha256'])
        recipe_manifest = {}
        for line in small(args.source, 'provenance/recipe-sha256.txt').decode().splitlines():
            match = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9_.-]+)', line)
            if match is None or match[2] in recipe_manifest:
                raise ValueError('Invalid executed recipe manifest')
            recipe_manifest[match[2]] = match[1]
        if recipe_manifest != {name: rows['frozen-recipe/' + name]['sha256'] for name in RECIPE}:
            raise ValueError('Executed recipe manifest mismatch')
        copy('provenance/recipe-sha256.txt')
        if small(args.source, 'provenance/base-image.txt') != (BASE + '\n').encode():
            raise ValueError('Pinned base record mismatch')
        copy('provenance/base-image.txt')
        builder_bytes = small(args.source, "provenance/builder-id.txt")
        if not re.fullmatch(rb'sha256:[a-f0-9]{64}\n', builder_bytes):
            raise ValueError('Invalid frozen builder ID')
        builder = builder_bytes.decode().strip()
        copy('provenance/builder-id.txt')
        builder_arch = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Architecture}}', builder], text=True, timeout=30).strip()
        if builder_arch != args.arch:
            raise ValueError('Frozen builder architecture mismatch')
        base_layers = decode_json(subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{json .RootFS.Layers}}', BASE], timeout=30))
        builder_layers = decode_json(subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{json .RootFS.Layers}}', builder], timeout=30))
        if not base_layers or builder_layers[:len(base_layers)] != base_layers:
            raise ValueError('Builder does not descend from pinned base')
        inventory = small(args.source, 'provenance/toolchain-packages.tsv')
        seen_packages = set()
        for line in inventory.decode().splitlines():
            match = re.fullmatch(r'([a-z0-9][a-z0-9+.-]*)\t([A-Za-z0-9.+:~\-]+)\t(all|amd64|arm64)', line)
            if match is None or match[1] in seen_packages or match[3] not in ('all', args.arch):
                raise ValueError('Invalid toolchain package inventory')
            seen_packages.add(match[1])
        if not seen_packages or len(seen_packages) > 2000:
            raise ValueError('Invalid toolchain package count')
        actual_inventory = subprocess.check_output(['docker', 'run', '--rm', '--entrypoint', '/usr/bin/dpkg-query',
            '--user', f'{os.getuid()}:{os.getgid()}', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--memory', '256m', '--pids-limit', '64', builder,
            '-W', '-f=${Package}\t${Version}\t${Architecture}\n'], timeout=30)
        if inventory != actual_inventory:
            raise ValueError('Toolchain inventory differs from frozen builder')
        copy('provenance/toolchain-packages.tsv')
        spec = importlib.util.spec_from_file_location('cjson_auth', args.repo / 'runtime-security/cjson/verify-ubuntu-source.py')
        authenticator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(authenticator)
        copy('provenance/ubuntu-archive-keyring.gpg', authenticator.KEYRING_SHA256)
        receipt["builder_id"] = builder
        indexes = args.source / "provenance/apt-lists"
        if indexes.is_symlink() or not indexes.is_dir():
            raise ValueError("Unsafe index directory")
        current_source_chain = authenticator.verify(indexes, args.source / 'source-input', args.source / 'provenance/ubuntu-archive-keyring.gpg')
        for attempt in 'ab':
            if decode_json(small(args.source, 'build-' + attempt + '/ubuntu-source.json')) != current_source_chain:
                raise ValueError('Source authentication differs from current signed inputs')
        for name, identity in sorted(current_source_chain['authenticated_files'].items()):
            if not INDEX.fullmatch(name):
                raise ValueError("Unexpected index")
            copy("provenance/apt-lists/" + name, identity['sha256'])
        if not any(name.endswith('_InRelease') for name in rows):
            raise ValueError("Signed index missing")
        for attempt in "ab":
            prefix = "build-" + attempt + "/"
            controls = decode_json(small(args.source, prefix + "source-controls.json"))
            if controls != {"schema": "aelira-cjson-source-controls-v1", "status": "pass", "count": 9, "controls": SOURCE_CONTROLS}:
                raise ValueError("Source controls incomplete")
            copy(prefix + "source-controls.json")
            authenticated = decode_json(small(args.source, prefix + "ubuntu-source.json"))
            if authenticated != current_source_chain:
                raise ValueError('Source authentication differs from current signed inputs')
            if set(authenticated) != {'schema', 'status', 'records', 'keyring_sha256', 'authenticated_files'} or authenticated["schema"] != "aelira-cjson-ubuntu-source-v1" or authenticated["status"] != "pass" or not authenticated["records"]:
                raise ValueError("Source authentication incomplete")
            if authenticated['keyring_sha256'] != rows['provenance/ubuntu-archive-keyring.gpg']['sha256']:
                raise ValueError('Authenticated keyring mismatch')
            index_hashes = {row['sha256'] for name, row in rows.items() if '/apt-lists/' in name and not name.endswith('_InRelease')}
            release_hashes = {row['sha256'] for name, row in rows.items() if '/apt-lists/' in name and name.endswith('_InRelease')}
            for record in authenticated['records']:
                if set(record) != {'index_sha256', 'release_sha256', 'files'} or record['files'] != source_identity or record['index_sha256'] not in index_hashes or record['release_sha256'] not in release_hashes:
                    raise ValueError('Authenticated source record mismatch')
            copy(prefix + "ubuntu-source.json")
            for level in ("0", "2"):
                log = small(args.source, prefix + "native-O" + level + ".log").decode()
                if log != "borrowed_parent_allocation_controls=pass successes=574 refusals=194\ncJSON native controls passed\n":
                    raise ValueError("Native sanitizer controls incomplete")
                copy(prefix + "native-O" + level + ".log")
            build_log = small(args.source, prefix + "package-build-private.log").decode()
            if "100% tests passed, 0 tests failed out of 22\n" not in build_log:
                raise ValueError("Upstream tests incomplete")
            names = ("cjson_1.7.19-2+aelira3.dsc", "cjson_1.7.19-2+aelira3.debian.tar.xz", "source-receipt.json")
            for name in names:
                if name != 'source-receipt.json':
                    copy(prefix + "artifacts/" + name, reproducible[name])
            source_receipt = decode_json(small(args.source, prefix + 'artifacts/source-receipt.json'))
            artifact_identity = {name: rows[prefix + 'artifacts/' + name] for name in names[:2]}
            artifact_identity['cjson_1.7.19.orig.tar.gz'] = source_identity['cjson_1.7.19.orig.tar.gz']
            if source_receipt != {'schema': 'aelira-cjson-source-v1', 'status': 'pass', 'version': '1.7.19-2+aelira3',
                                  'inputs': {**source_identity, **repair_identity}, 'patched_sources': helper.PATCHED,
                                  'artifacts': artifact_identity, 'scope': 'Exact source preparation; signed-index authentication and native acceptance are separate'}:
                raise ValueError('Source preparation receipt mismatch')
            copy(prefix + 'artifacts/source-receipt.json')
            for package in ("libcjson1", "libcjson-dev"):
                name = package + "_1.7.19-2+aelira3_" + args.arch + ".deb"
                copy(prefix + "artifacts/" + name, reproducible[name])
                if attempt == "b" and rows[prefix + "artifacts/" + name]["sha256"] != rows["build-a/artifacts/" + name]["sha256"]:
                    raise ValueError("Package reproducibility mismatch")
            if attempt == "b":
                for name in names[:2]:
                    if rows[prefix + "artifacts/" + name]["sha256"] != rows["build-a/artifacts/" + name]["sha256"]:
                        raise ValueError("Source reproducibility mismatch")
        installed_log = small(args.source, "installed-private.log").decode()
        match = re.findall(r"^installed_cjson_receipt=(.+)$", installed_log, re.MULTILINE)
        if len(match) != 1 or f"installed_cjson_acceptance=pass architecture={args.arch} version=1.7.19-2+aelira3\n" not in installed_log:
            raise ValueError("Installed controls incomplete")
        installed = decode_json(match[0])
        if set(installed) != {'schema', 'status', 'architecture', 'version', 'libraries'} or installed["schema"] != "aelira-cjson-installed-v1" or installed["status"] != "pass" or installed["architecture"] != args.arch or installed["version"] != "1.7.19-2+aelira3":
            raise ValueError("Installed receipt mismatch")
        if set(installed['libraries']) != {'libcjson.so.1', 'libcjson_utils.so.1'}:
            raise ValueError('Installed libraries mismatch')
        multiarch = {'arm64': 'aarch64-linux-gnu', 'amd64': 'x86_64-linux-gnu'}[args.arch]
        package = args.source / ('build-a/artifacts/libcjson1_1.7.19-2+aelira3_' + args.arch + '.deb')
        payload = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', str(package)], timeout=30)
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            for soname, baseline, count in [('libcjson.so.1', 'baseline-core.so', 78), ('libcjson_utils.so.1', 'baseline-utils.so', 14)]:
                library = installed['libraries'][soname]
                if set(library) != {'sha256', 'baseline_sha256', 'public_symbols', 'loader_path'}:
                    raise ValueError('Unexpected installed metadata')
                filename = '/usr/lib/' + multiarch + '/' + soname + '.7.19'
                if library['loader_path'] != filename:
                    raise ValueError('Installed loader path mismatch')
                member = archive.getmember('.' + filename)
                if not member.isfile() or member.size > 1024 * 1024:
                    raise ValueError('Invalid packaged library')
                if hashlib.sha256(archive.extractfile(member).read()).hexdigest() != library['sha256']:
                    raise ValueError('Installed library differs from package')
                copy('build-a/' + baseline, library['baseline_sha256'])
                symbols = library['public_symbols']
                expected_symbols = sorted([line.split()[:2] for line in subprocess.check_output(['nm', '-D', '--defined-only', '--format=posix', str(args.source / ('build-a/' + baseline))], text=True, timeout=30).splitlines()])
                if symbols != expected_symbols or len(symbols) != count:
                    raise ValueError('Installed symbol contract mismatch')
        receipt["installed"] = installed
        receipt["upstream_tests_per_build"] = 22
        receipt["allocation_outcomes_per_native_run"] = 768
        tar = args.source / "provenance/builder.tar"
        receipt["builder_exported"] = tar.exists()
        if args.require_builder and not tar.exists():
            raise ValueError("Frozen builder export missing")
        if tar.exists():
            copy("provenance/builder.tar")
        receipt["status"] = "pass"
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as error:
        errors.append(type(error).__name__)
    receipt["errors"] = errors
    receipt["files"] = rows
    (args.dest / "component.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    print(json.dumps({key: receipt[key] for key in ("schema", "status", "architecture", "build_exit", "errors")}))
    return 0 if receipt["status"] == "pass" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "dest", "repo"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("arch", "revision", "tree"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--build-exit", required=True, type=int)
    parser.add_argument("--require-builder", action="store_true")
    sys.exit(export(parser.parse_args()))
