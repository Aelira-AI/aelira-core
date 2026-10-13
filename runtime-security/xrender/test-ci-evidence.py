#!/usr/bin/env python3
"""Exercise closed Xrender evidence export using a real completed build."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'repo', 'scratch'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--arch', required=True)
    args = parser.parse_args()
    args.scratch.mkdir(mode=0o700)
    revision = subprocess.check_output(['git', '-C', str(args.repo), 'rev-parse', 'HEAD'], text=True).strip()
    tree = subprocess.check_output(['git', '-C', str(args.repo), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
    exporter = Path(__file__).with_name('ci-evidence.py').resolve()
    rows = []

    def snapshot(name):
        dest = args.scratch / (name + '-input')
        def link(source, target):
            try:
                os.link(source, target)
            except PermissionError:
                shutil.copyfile(source, target)
        shutil.copytree(args.source, dest, copy_function=link, symlinks=True)
        (dest / 'unlisted-private.txt').write_text('SYNTHETIC-PRIVATE-FIELD\n')
        return dest

    def invoke(name, source, expected, build_exit=0, arch=None, selected_revision=None, selected_tree=None):
        dest = args.scratch / (name + '-export')
        p = subprocess.run([sys.executable, '-B', str(exporter), '--source', str(source),
            '--dest', str(dest), '--repo', str(args.repo), '--arch', arch or args.arch,
            '--revision', selected_revision or revision, '--tree', selected_tree or tree, '--build-exit', str(build_exit)],
            capture_output=True, timeout=240)
        receipt = json.loads((dest / 'component.json').read_text())
        assert p.returncode == (0 if expected else 1), {
            'case': name, 'stage': receipt['validation_stage'], 'errors': receipt['errors']}
        assert receipt['status'] == ('pass' if expected else 'fail')
        assert 'unlisted-private.txt' not in receipt['files']
        assert 'SYNTHETIC-PRIVATE-FIELD' not in json.dumps(receipt)
        rows.append(name + ('-accepted' if expected else '-refused'))
        return receipt

    source = snapshot('completed-native')
    invoke('completed-native', source, True)
    invoke('failed-process', source, False, build_exit=7)
    for name, changed in [('invalid-arch', {'arch': 'SYNTHETIC-PRIVATE-FIELD'}),
                          ('invalid-revision', {'selected_revision': 'SYNTHETIC-PRIVATE-FIELD'}),
                          ('invalid-tree', {'selected_tree': 'SYNTHETIC-PRIVATE-FIELD'})]:
        invoke(name, source, False, **changed)
    for case, relative in [('changed-recipe', 'frozen-recipe/verify-provenance.py'),
                           ('missing-native', 'build-a/patched-O2.log'),
                           ('source-symlink', 'source-input/libxrender_0.9.12-1build1.dsc'),
                           ('changed-keyring', 'provenance/ubuntu-archive-keyring.gpg'),
                           ('changed-signed-release', None),
                           ('changed-source', 'source-input/libxrender_0.9.12.orig.tar.gz'),
                           ('changed-builder', 'provenance/builder-id.txt'),
                           ('changed-base', 'provenance/base-image.txt'),
                           ('changed-inventory', 'provenance/toolchain-packages.tsv'),
                           ('changed-base-inventory', 'provenance/base-packages.tsv'),
                           ('changed-manifest', 'provenance/recipe-sha256.txt'),
                           ('changed-package', 'build-a/libxrender1_0.9.12-1build1+aelira1_' + args.arch + '.deb'),
                           ('changed-loader', 'installed/xrender-client.log'),
                           ('changed-loaded-digest', 'installed/loaded-library.sha256'),
                           ('missing-builder', 'provenance/builder.tar'),
                           ('private-phase', 'build-status.json'),
                           ('boolean-exit', 'build-status.json'),
                           ('float-exit', 'build-status.json'),
                           ('duplicate-json-key', 'build-status.json'),
                           ('absolute-checksum-path', 'reproducible-sha256.txt')]:
        source = snapshot(case)
        if relative is None:
            path = next((source / 'provenance/apt-lists').glob('*_InRelease'))
        else:
            path = source / relative
        data = path.read_bytes() if case != 'missing-builder' else b''
        path.unlink()
        if case in ('missing-native', 'missing-builder'):
            pass
        elif case == 'source-symlink':
            path.symlink_to(args.source / relative)
        elif case == 'private-phase':
            value = json.loads(data)
            value['phase'] = 'SYNTHETIC-PRIVATE-FIELD'
            path.write_text(json.dumps(value))
        elif case in ('boolean-exit', 'float-exit'):
            value = json.loads(data)
            value['exit'] = False if case == 'boolean-exit' else 0.0
            path.write_text(json.dumps(value))
        elif case == 'duplicate-json-key':
            path.write_bytes(data.replace(b'{', b'{"phase":"SYNTHETIC-PRIVATE-FIELD",', 1))
        elif case == 'absolute-checksum-path':
            path.write_bytes(data.replace(b'  libxrender1_', b'  /private/synthetic/libxrender1_'))
        elif case in ('changed-builder', 'changed-base', 'changed-inventory', 'changed-base-inventory', 'changed-manifest', 'changed-loader', 'changed-loaded-digest'):
            path.write_text('SYNTHETIC-PRIVATE-FIELD\n')
        else:
            changed = bytearray(data)
            changed[0] ^= 1
            path.write_bytes(changed)
        invoke(case, source, False)
    alias = args.scratch / 'source-alias'
    alias.symlink_to(args.source, target_is_directory=True)
    invoke('source-alias', alias, False)
    source = snapshot('unreferenced-release')
    extra = source / 'provenance/apt-lists/security.ubuntu.com_ubuntu_dists_resolute-backports_InRelease'
    assert not extra.exists()
    extra.write_text('SYNTHETIC-PRIVATE-FIELD\n')
    receipt = invoke('unreferenced-release', source, True)
    assert str(extra.relative_to(source)) not in receipt['files']
    for case in ('changed-builder-trailer', 'metadata-only-builder', 'changed-builder-member'):
        source = snapshot(case)
        path = source / 'provenance/builder.tar'
        changed = path.with_name('changed-builder.tmp')
        shutil.copyfile(path, changed)
        path.unlink()
        changed.rename(path)
        with path.open('r+b') as stream:
            if case == 'changed-builder-trailer':
                stream.seek(-1, os.SEEK_END)
                byte = stream.read(1)
                stream.seek(-1, os.SEEK_END)
                stream.write(bytes([byte[0] ^ 1]))
            elif case == 'metadata-only-builder':
                header = bytearray(stream.read(512))
                header[136:148] = b'00000000001\0'
                header[148:156] = b'        '
                header[148:156] = (format(sum(header), '06o') + '\0 ').encode()
                stream.seek(0)
                stream.write(header)
            else:
                with tarfile.open(path, mode='r:') as archive:
                    offset = next(member for member in archive if member.isfile() and member.size).offset_data
                stream.seek(offset)
                byte = stream.read(1)
                stream.seek(offset)
                stream.write(bytes([byte[0] ^ 1]))
        invoke(case, source, case == 'metadata-only-builder')
    print(json.dumps({'schema': 'aelira-xrender-evidence-controls-v1', 'status': 'pass',
                      'count': len(rows), 'controls': rows}, sort_keys=True))


if __name__ == '__main__':
    main()
