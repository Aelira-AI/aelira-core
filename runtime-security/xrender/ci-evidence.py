#!/usr/bin/env python3
"""Export a closed, authenticated native Xrender component evidence bundle."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile

MAX_FILE = 2 * 1024**3
MAX_TOTAL = 3 * 1024**3
BASE = 'ubuntu@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7'
KEYRING = '80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31'
RECIPE = ('README.md', 'build.sh', 'ci-native.sh', 'prepare-native.sh', 'build-in-container.sh',
          'verify-provenance.py', 'inputs.sha256', 'patch.sha256', 'upstream-0564bce.patch',
          'changelog-entry.txt', 'generate-control.py', 'control-template.c', 'test-installed.sh', 'xrender-client.c')
INDEX = re.compile(r'(?:archive\.ubuntu\.com_ubuntu|ports\.ubuntu\.com_ubuntu-ports|security\.ubuntu\.com_ubuntu)_dists_resolute(?:-updates|-security|-backports)?_(?:InRelease|(?:main|universe|restricted|multiverse)_(?:source_Sources|binary-(?:amd64|arm64)_Packages)(?:\.lz4)?)')


def saved_image_contents(path):
    """Compare all image bytes, allowing tar header timestamps/order to vary."""
    contents = {}
    total = 0
    with tarfile.open(path, mode='r:') as archive:
        for member in archive:
            parts = Path(member.name).parts
            if member.name.startswith('/') or not parts or any(part in ('.', '..') for part in parts):
                raise ValueError('Unsafe saved image member')
            if member.name in contents or len(contents) >= 4096:
                raise ValueError('Duplicate or excess saved image member')
            if member.isdir():
                if member.size != 0:
                    raise ValueError('Invalid saved image directory')
                contents[member.name] = ('directory', 0, None)
            elif member.isfile():
                total += member.size
                if member.size < 0 or total > MAX_FILE:
                    raise ValueError('Saved image payload exceeds bound')
                digest = hashlib.sha256()
                read_size = 0
                stream = archive.extractfile(member)
                while chunk := stream.read(1024 * 1024):
                    read_size += len(chunk)
                    digest.update(chunk)
                if read_size != member.size:
                    raise ValueError('Truncated saved image member')
                contents[member.name] = ('file', member.size, digest.hexdigest())
            else:
                raise ValueError('Unsupported saved image member')
        end = archive.offset
    # tar readers normally ignore bytes after their end marker. Refuse nonzero
    # trailers so a changed full-size input is not silently accepted there.
    with path.open('rb') as stream:
        stream.seek(end)
        trailer_size = 0
        while chunk := stream.read(1024 * 1024):
            trailer_size += len(chunk)
            if any(chunk) or trailer_size > 1024 * 1024:
                raise ValueError('Invalid saved image trailer')
    if not contents or trailer_size < 1024 or trailer_size % 512:
        raise ValueError('Incomplete saved image archive')
    return contents

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
    stage = 'checkout'
    receipt = {'schema': 'aelira-xrender-native-evidence-v1', 'status': 'fail',
               'architecture': args.arch, 'revision': args.revision, 'tree': args.tree,
               'build_exit': args.build_exit,
               'scope': 'Native Xrender source/package component; final release images require separate acceptance'}

    def copy(name, expected=None, verified_file=None):
        nonlocal total
        path = verified_file or regular(args.source, name)
        total += path.stat().st_size
        if total > MAX_TOTAL or len(rows) >= 1000:
            raise ValueError('Evidence bundle exceeds bound')
        target = args.dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        if expected is not None:
            if path.stat().st_size > 128 * 1024**2:
                raise ValueError('Authenticated input exceeds bound')
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('Authenticated input mismatch')
            target.write_bytes(data)
            digest.update(data)
        else:
            with path.open('rb') as incoming, target.open('xb') as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    digest.update(chunk)
                    outgoing.write(chunk)
        rows[name] = {'sha256': digest.hexdigest(), 'size': target.stat().st_size}

    try:
        if args.arch not in ('arm64', 'amd64'):
            raise ValueError('Invalid architecture')
        if not args.source.is_absolute() or args.source.resolve() != args.source or not args.source.is_dir():
            raise ValueError('Noncanonical source directory')
        for ref, expected in [('HEAD', args.revision), ('HEAD^{tree}', args.tree)]:
            if not re.fullmatch('[a-f0-9]{40}', expected):
                raise ValueError('Invalid checkout identity')
            if subprocess.check_output(['git', '-C', str(args.repo), 'rev-parse', ref], text=True).strip() != expected:
                raise ValueError('Checkout identity mismatch')
        if subprocess.check_output(['git', '-C', str(args.repo), 'status', '--porcelain', '--',
                                    'runtime-security/xrender', '.github/workflows/ci.yml'], text=True):
            raise ValueError('Dirty recipe checkout')
        stage = 'build-status'
        status = decode_json(small(args.source, 'build-status.json'))
        if args.build_exit != 0 or status != {'schema': 'aelira-xrender-build-status-v1', 'phase': 'complete', 'exit': 0}:
            raise ValueError('Native build incomplete')
        copy('build-status.json')
        stage = 'recipe'
        for name in RECIPE:
            expected = hashlib.sha256((args.repo / 'runtime-security/xrender' / name).read_bytes()).hexdigest()
            copy('frozen-recipe/' + name, expected)
        expected_recipe = ''.join(rows['frozen-recipe/' + name]['sha256'] + '  ' + name + '\n'
                                  for name in sorted(RECIPE)).encode()
        if small(args.source, 'provenance/recipe-sha256.txt') != expected_recipe:
            raise ValueError('Executed recipe mismatch')
        copy('provenance/recipe-sha256.txt')
        if small(args.source, 'provenance/base-image.txt') != (BASE + '\n').encode():
            raise ValueError('Pinned base mismatch')
        copy('provenance/base-image.txt')
        stage = 'builder-identity'
        builder_bytes = small(args.source, 'provenance/builder-id.txt')
        if not re.fullmatch(rb'sha256:[a-f0-9]{64}\n', builder_bytes):
            raise ValueError('Invalid builder identity')
        builder = builder_bytes.decode().strip()
        copy('provenance/builder-id.txt')
        if subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Architecture}}', builder], text=True).strip() != args.arch:
            raise ValueError('Builder architecture mismatch')
        base_layers = decode_json(subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{json .RootFS.Layers}}', BASE]))
        layers = decode_json(subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{json .RootFS.Layers}}', builder]))
        if not base_layers or layers[:len(base_layers)] != base_layers:
            raise ValueError('Builder base ancestry mismatch')
        stage = 'toolchain'
        inventory = small(args.source, 'provenance/toolchain-packages.tsv')
        actual = subprocess.check_output(['docker', 'run', '--rm', '--entrypoint', '/usr/bin/dpkg-query',
            '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--user', f'{os.getuid()}:{os.getgid()}', '--memory', '256m', '--pids-limit', '64', builder,
            '-W', '-f=${Package}\t${Version}\t${Architecture}\n'], timeout=30)
        if inventory != actual:
            raise ValueError('Toolchain inventory mismatch')
        base_inventory = small(args.source, 'provenance/base-packages.tsv')
        actual_base = subprocess.check_output(['docker', 'run', '--rm', '--entrypoint', '/usr/bin/dpkg-query',
            '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--user', f'{os.getuid()}:{os.getgid()}', '--memory', '256m', '--pids-limit', '64', BASE,
            '-W', '-f=${Package}\t${Version}\t${Architecture}\n'], timeout=30)
        if base_inventory != actual_base:
            raise ValueError('Base package inventory mismatch')

        def packages(data):
            result = {}
            for line in data.decode().splitlines():
                match = re.fullmatch(r'([a-z0-9][a-z0-9+.-]*)\t([A-Za-z0-9.+:~\-]+)\t(all|amd64|arm64)', line)
                if not match or match[1] in result or match[3] not in ('all', args.arch):
                    raise ValueError('Invalid package inventory')
                result[match[1]] = (match[2], match[3])
            if not result or len(result) > 2000:
                raise ValueError('Invalid package inventory count')
            return result

        base_packages = packages(base_inventory)
        builder_packages = packages(inventory)
        delta = {(name, version, arch) for name, (version, arch) in builder_packages.items()
                 if base_packages.get(name) != (version, arch)}
        copy('provenance/toolchain-packages.tsv')
        copy('provenance/base-packages.tsv')
        copy('provenance/ubuntu-archive-keyring.gpg', KEYRING)
        stage = 'signed-inputs'
        verified = subprocess.check_output(['docker', 'run', '--rm', '--network', 'none', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--user', f'{os.getuid()}:{os.getgid()}',
            '--memory', '1g', '--pids-limit', '64', '--tmpfs', '/tmp:rw,nosuid,nodev,size=16m', '-e', 'HOME=/tmp',
            '-v', str(args.source / 'frozen-recipe') + ':/recipe:ro',
            '-v', str(args.source / 'source-input') + ':/source-input:ro',
            '-v', str(args.source / 'provenance') + ':/provenance:ro', builder,
            'python3', '-B', '/recipe/verify-provenance.py'], timeout=120, stderr=subprocess.PIPE)
        signed = decode_json(verified)
        if set(signed) != {'schema', 'status', 'source_files', 'source_stanzas', 'toolchain_files', 'authenticated_files', 'keyring_sha256'} or signed['schema'] != 'aelira-xrender-ubuntu-inputs-v1' or signed['status'] != 'pass' or signed['keyring_sha256'] != KEYRING:
            raise ValueError('Signed input receipt mismatch')
        pins = {}
        for line in small(args.source, 'frozen-recipe/inputs.sha256').decode().splitlines():
            match = re.fullmatch(r'([a-f0-9]{64})  (libxrender_0\.9\.12(?:\.orig\.tar\.gz(?:\.asc)?|-1build1\.(?:diff\.gz|dsc)))', line)
            if not match or match[2] in pins:
                raise ValueError('Invalid source pin')
            pins[match[2]] = match[1]
        if len(pins) != 4 or set(pins) != set(signed['source_files']):
            raise ValueError('Signed source closure mismatch')
        for name, row in signed['source_files'].items():
            if set(row) != {'sha256', 'size'} or row['sha256'] != pins[name]:
                raise ValueError('Signed source identity mismatch')
            copy('source-input/' + name, row['sha256'])
            if rows['source-input/' + name]['size'] != row['size']:
                raise ValueError('Signed source size mismatch')
        for name, row in signed['authenticated_files'].items():
            if not INDEX.fullmatch(name) or set(row) != {'sha256', 'size'}:
                raise ValueError('Unexpected signed index')
            copy('provenance/apt-lists/' + name, row['sha256'])
            if rows['provenance/apt-lists/' + name]['size'] != row['size']:
                raise ValueError('Signed index size mismatch')
        if not 1 <= len(signed['toolchain_files']) <= 2000:
            raise ValueError('Toolchain archive count mismatch')
        signed_delta = {(row['package'], row['version'], row['architecture'])
                        for row in signed['toolchain_files'].values()}
        if signed_delta != delta or len(signed_delta) != len(signed['toolchain_files']):
            raise ValueError('Signed toolchain differs from actual base/builder delta')
        for name, row in signed['toolchain_files'].items():
            if not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*_[A-Za-z0-9.+:%~\-]+_(?:arm64|amd64|all)\.deb', name) or set(row) != {'package', 'version', 'architecture', 'sha256', 'size'} or row['architecture'] not in ('all', args.arch):
                raise ValueError('Unexpected toolchain archive')
            copy('provenance/toolchain-debs/' + name, row['sha256'])
            if rows['provenance/toolchain-debs/' + name]['size'] != row['size']:
                raise ValueError('Toolchain archive size mismatch')
        stage = 'native-controls'
        expected_control = {'sourceSha256': 'c5c93efd29a9e13cabe4f6fe99863c13a14d6c006e90267f747e9da9a8b800c8',
                            'functionSha256': 'a771bbd3fa6bcc1d8db72a064145387a90f5d5661285f9bb01261bcfb75834f6',
                            'baselineTransformation': 'Remove only upstream rep.numSubpixel <= rep.numScreens guard',
                            'scope': 'Exact final source function with mocked protocol reply; installed consumer is separate'}
        for attempt in ('a', 'b'):
            prefix = 'build-' + attempt + '/'
            if decode_json(small(args.source, prefix + 'signed-chain.log')) != signed:
                raise ValueError('Cold signed-input receipt mismatch')
            copy(prefix + 'signed-chain.log')
            if decode_json(small(args.source, prefix + 'control-source.json')) != expected_control:
                raise ValueError('Exact source control mismatch')
            copy(prefix + 'control-source.json')
            for level in ('0', '2'):
                name = prefix + 'patched-O' + level + '.log'
                if small(args.source, name) != b'Exact-source Xrender bounds, valid versions and allocation controls passed\n':
                    raise ValueError('Native sanitizer controls incomplete')
                copy(name)
            baseline = int(small(args.source, prefix + 'baseline-exit-status.txt'))
            if not 1 <= baseline <= 255 or b'AddressSanitizer: heap-buffer-overflow' not in small(args.source, prefix + 'baseline-failure.log'):
                raise ValueError('Distinguishing baseline incomplete')
            copy(prefix + 'baseline-exit-status.txt')
        stage = 'packages'
        candidates = {'libxrender1_0.9.12-1build1+aelira1_' + args.arch + '.deb',
                      'libxrender-dev_0.9.12-1build1+aelira1_' + args.arch + '.deb',
                      'libxrender_0.9.12-1build1+aelira1.dsc', 'libxrender_0.9.12-1build1+aelira1.diff.gz'}
        checksums = {}
        for line in small(args.source, 'reproducible-sha256.txt').decode().splitlines():
            match = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9_+.-]+)', line)
            if not match or match[2] not in candidates or match[2] in checksums:
                raise ValueError('Noncanonical package manifest')
            checksums[match[2]] = match[1]
        if set(checksums) != candidates:
            raise ValueError('Package closure mismatch')
        copy('reproducible-sha256.txt')
        for name, expected in checksums.items():
            for attempt in ('a', 'b'):
                copy('build-' + attempt + '/' + name, expected)
        stage = 'installed-consumer'
        installed_log = small(args.source, 'installed-private.log')
        if installed_log != ('installed_xrender_acceptance=pass architecture=' + args.arch + ' version=1:0.9.12-1build1+aelira1\n').encode():
            raise ValueError('Installed acceptance incomplete')
        triplet = {'arm64': 'aarch64-linux-gnu', 'amd64': 'x86_64-linux-gnu'}[args.arch]
        client = small(args.source, 'installed/xrender-client.log').decode()
        if not re.fullmatch('Loaded XRender library: /(?:usr/)?lib/' + triplet + r'/libXrender\.so\.1\nInstalled Xrender 0\.11 rendered red pixels; subpixel=[0-5]\n', client):
            raise ValueError('Loaded consumer/pixel acceptance mismatch')
        installed_hash = small(args.source, 'installed/installed-library.sha256').decode().split()[0]
        if small(args.source, 'installed/loaded-library.sha256').decode().split()[0] != installed_hash:
            raise ValueError('Loaded library differs from installed package')
        originals = [name for name, row in signed['toolchain_files'].items()
                     if (row['package'], row['version'], row['architecture']) ==
                     ('libxrender1', '1:0.9.12-1build1', args.arch)]
        if len(originals) != 1:
            raise ValueError('Original library package missing')
        original_deb = originals[0]
        runtime_deb = 'libxrender1_0.9.12-1build1+aelira1_' + args.arch + '.deb'
        symbols = []
        with tempfile.TemporaryDirectory(prefix='aelira-xrender-abi-') as work:
            for label, package in [('original', args.source / 'provenance/toolchain-debs' / original_deb),
                                   ('candidate', args.source / 'build-a' / runtime_deb)]:
                payload = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', str(package)], timeout=30)
                with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
                    library_dir = './usr/lib/' + triplet + '/'
                    link = archive.getmember(library_dir + 'libXrender.so.1')
                    if not link.issym() or not re.fullmatch(r'libXrender\.so\.1\.[0-9]+\.[0-9]+', link.linkname):
                        raise ValueError('Invalid packaged SONAME link')
                    member = archive.getmember(library_dir + link.linkname)
                    if not member.isfile() or member.size > 2 * 1024**2:
                        raise ValueError('Invalid packaged library')
                    data = archive.extractfile(member).read()
                path = Path(work) / (label + '.so')
                path.write_bytes(data)
                header = subprocess.check_output(['readelf', '-h', str(path)], text=True, timeout=30)
                machine = {'arm64': 'AArch64', 'amd64': 'Advanced Micro Devices X86-64'}[args.arch]
                if re.findall(r'Machine:\s+([^\n]+)', header) != [machine] or re.findall(r'Class:\s+([^\n]+)', header) != ['ELF64']:
                    raise ValueError('Packaged library architecture mismatch')
                if label == 'candidate' and hashlib.sha256(data).hexdigest() != installed_hash:
                    raise ValueError('Installed library differs from package')
                dynamic = subprocess.check_output(['readelf', '-d', str(path)], text=True, timeout=30)
                if re.findall(r'\(SONAME\).*\[([^]]+)\]', dynamic) != ['libXrender.so.1']:
                    raise ValueError('Packaged SONAME mismatch')
                symbols.append(sorted([line.split()[:2] for line in subprocess.check_output(
                    ['nm', '-D', '--defined-only', '--format=posix', str(path)], text=True, timeout=30).splitlines()]))
        if symbols[0] != symbols[1] or len(symbols[0]) != 47:
            raise ValueError('Public ABI mismatch')
        receipt['installed'] = {'version': '1:0.9.12-1build1+aelira1', 'library_sha256': installed_hash,
                                'loader_path': '/usr/lib/' + triplet + '/libXrender.so.1',
                                'soname': 'libXrender.so.1', 'public_symbols': symbols[0], 'pixel_readback': 'pass'}
        stage = 'builder-archive'
        saved = regular(args.source, 'provenance/builder.tar')
        with tempfile.TemporaryDirectory(prefix='aelira-xrender-builder-') as work:
            fresh = Path(work) / 'builder.tar'
            subprocess.run(['docker', 'save', builder, '-o', str(fresh)], check=True, capture_output=True, timeout=180)
            if saved_image_contents(saved) != saved_image_contents(fresh):
                raise ValueError('Saved builder image mismatch')
            copy('provenance/builder.tar', verified_file=fresh)
        receipt['builder_id'] = builder
        receipt['status'] = 'pass'
        stage = 'complete'
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError) as error:
        receipt['errors'] = [type(error).__name__]
    receipt.setdefault('errors', [])
    receipt['validation_stage'] = stage
    receipt['files'] = rows
    (args.dest / 'component.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    print(json.dumps({key: receipt[key] for key in ('schema', 'status', 'architecture', 'build_exit', 'validation_stage', 'errors')}))
    return 0 if receipt['status'] == 'pass' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'dest', 'repo'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('arch', 'revision', 'tree'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--build-exit', type=int, required=True)
    raise SystemExit(export(parser.parse_args()))

