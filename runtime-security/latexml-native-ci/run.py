#!/usr/bin/env python3
"""Native orchestration of the frozen LaTeXML caller; no release acceptance."""

from __future__ import annotations
import argparse
import ast
import hashlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tarfile
import time

ORIGIN = "https://github.com/Aelira-AI/aelira-core.git"
BASE = "ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7"
KEYRING = "80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31"
ARM_LOCK = "e59ed0151c570dc38e0f95bc75aac5fe3a8dbec66d8f31d086337d1379721e08"
SNAPSHOT_ID = "20261013T060000Z"
SNAPSHOT_RETRY_DELAYS = (2, 4, 8, 16)
SNAPSHOT_RETRY_HTTP = (500, 502, 503, 504)
SNAPSHOT_SUITES = (
    "resolute",
    "resolute-updates",
    "resolute-security",
    "resolute-backports",
)
CA_PACKAGE_PATH = "pool/main/c/ca-certificates/ca-certificates_20260601~26.04.1_all.deb"
CA_PACKAGE_SHA256 = "6077d27c6b6f8b23590cb01ff877ed8c804a67a5442cc32b5a33da10d2bd0e90"
CA_PACKAGE_SIZE = 138514
CA_BUNDLE_SHA256 = "9481fcd95f41b221f02f14d896535fe500bec539bc563c4cdca1acee483a8bdd"
CA_BUNDLE_SIZE = 182140
CA_CERTIFICATE_COUNT = 121
CALLER = "runtime-security/latexml-native-acceptance/"
WRAPPER = "runtime-security/latexml-native-ci/"
CALLER_HASHES = {
    "bootstrap.py": "d2c54ea23cb417c8c56828e70ec9f23145c527614bbbae1495e455966f41db62",
    "native_acceptance.py": "b4227668142a6044b589242d7e5778381b6939859e5dbb91bea1a7963b888717",
    "README.md": "c31543887d6f05cd6fba8834ccdc90cea8f934eaa3168099f63a0bcd6072f01d",
}
WRAPPERS = ("run.py", "evidence.py", "test-run.py", "README.md")
PACKAGE_FILES = frozenset(
    (
        "latexml-source.tar",
        "source-manifest.json",
        "latexml-runtime.tar",
        "runtime-manifest.json",
        "build-receipt.json",
    )
)
JSON_FILES = frozenset(
    (
        "status.json",
        "bounded.json",
        "acceptance.json",
        "build-receipt.json",
        "source-manifest.json",
        "runtime-manifest.json",
        "input-lock.json",
        "manifest.json",
    )
)
STAGES = (
    "source-check",
    "native-check",
    "input-capture",
    "input-pin",
    "prepare",
    "bootstrap",
    "native-run",
    "caller-export",
    "evidence-export",
    "evidence-check",
)
MAX_FILE = 200_000_000
MAX_TOTAL = 1_000_000_000
MAX_FILES = 1100
CAPTURE_CAPABILITIES = ("CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID")
CAPTURE_STAGES = ("setup", "update", "download", "select", "restore")
DOWNLOAD_WORKERS = 8
# APT output is data. Validate the complete plan before emitting NUL-delimited
# arguments; no URL, filename or index value becomes executable shell text.
DOWNLOAD_PLAN_AWK = r"""
function refuse() { bad = 1; exit 2 }
substr($0, 1, 1) == "\047" {
    if (NF != 4 || length($1) > 1026 || length($2) > 200) refuse()
    uri = substr($1, 2, length($1) - 2)
    if (substr($1, length($1), 1) != "\047" ||
        uri !~ /^https:\/\/snapshot\.ubuntu\.com\/ubuntu\/20261013T060000Z\/pool\/(main|restricted|universe|multiverse)\/[a-z0-9][a-z0-9-]*\/[a-z0-9][a-z0-9+.-]*\/[A-Za-z0-9][A-Za-z0-9+._%~-]*\.deb$/ ||
        uri ~ /\/\.\.?\//) refuse()
    encoded = uri
    gsub(/%(2[bB]|3[aA]|7[eE])/, "", encoded)
    if (encoded ~ /%/ || $2 !~ /^[A-Za-z0-9][A-Za-z0-9+._%~-]*\.deb$/ ||
        $3 !~ /^[1-9][0-9]*$/ || length($3) > 9 || $3 + 0 > 200000000 ||
        $4 !~ /^SHA256:[0-9a-f]+$/ || length($4) != 71 || seen[$2]++) refuse()
    count++; total += $3
    if (count > 1100 || total > 1000000000) refuse()
    urls[count] = uri; names[count] = $2; sizes[count] = $3; hashes[count] = $4
}
END {
    if (bad) exit 2
    if (!count) exit 2
    for (i = 1; i <= count; i++)
        printf "%s%c%s%c%s%c%s%c", urls[i], 0, names[i], 0, sizes[i], 0, hashes[i], 0
}
"""


def require(test, message="bounded check refused"):
    if not test:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pairs(rows):
    result = {}
    for key, value in rows:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def nonfinite(_):
    raise ValueError("nonfinite JSON")


def read_json(path, limit=300_000):
    regular(path, limit)
    return json.loads(
        path.read_bytes(),
        object_pairs_hook=pairs,
        parse_constant=nonfinite,
        parse_float=nonfinite,
    )


def path_check(path, new=False):
    require(
        path.is_absolute() and str(path) == str(path.resolve(strict=not new)),
        "noncanonical path",
    )
    if new:
        require(
            not path.exists()
            and not path.is_symlink()
            and path.parent.is_dir()
            and path.parent.stat().st_uid == os.getuid(),
            "output must be new and owned",
        )
    else:
        require(not path.is_symlink(), "aliased path")


def regular(path, limit=MAX_FILE):
    path_check(path)
    item = path.lstat()
    require(
        stat.S_ISREG(item.st_mode) and item.st_nlink == 1 and 0 < item.st_size <= limit,
        "unsafe regular file",
    )
    require(os.access(path, os.R_OK), "unreadable file")
    return {
        "sha256": sha(path.read_bytes()),
        "size": item.st_size,
        "mode": stat.S_IMODE(item.st_mode),
    }


def directories(files):
    result = set()
    for name in files:
        p = Path(name).parent
        while p.as_posix() != ".":
            result.add(p.as_posix())
            p = p.parent
    return result


def expected_directory_modes(files, root_mode=0o755):
    require(
        type(root_mode) is int and root_mode in (0o700, 0o755),
        "directory root contract",
    )
    return {
        name: {"mode": root_mode if name == "." else 0o755}
        for name in {"."} | directories(files)
    }


def directory_modes(root, files, root_mode=0o755):
    """Bind every declared parent and output root to literal directory mode 0755."""
    result = {}
    for name in sorted({"."} | directories(files)):
        path = root if name == "." else root / name
        path_check(path)
        metadata = path.lstat()
        require(
            stat.S_ISDIR(metadata.st_mode)
            and stat.S_IMODE(metadata.st_mode) == (root_mode if name == "." else 0o755)
            and os.access(path, os.R_OK | os.X_OK),
            "directory mode/type/readability differs",
        )
        result[name] = {"mode": stat.S_IMODE(metadata.st_mode)}
    return result


def inventory(root, files, root_mode=0o755):
    path_check(root)
    require(root.is_dir(), "directory missing")
    found = set()
    folders = set()
    total = 0
    result = {}

    def walk(folder):
        nonlocal total
        require(os.access(folder, os.R_OK | os.X_OK), "unreadable directory")
        with os.scandir(folder) as entries:
            for entry in entries:
                p = folder / entry.name
                name = p.relative_to(root).as_posix()
                item = entry.stat(follow_symlinks=False)
                require(
                    name in files or name in directories(files), "undeclared member"
                )
                if stat.S_ISDIR(item.st_mode):
                    folders.add(name)
                    walk(p)
                else:
                    require(name in files, "undeclared file")
                    result[name] = regular(p)
                    found.add(name)
                    total += result[name]["size"]
                    require(
                        total <= MAX_TOTAL and len(found) <= MAX_FILES,
                        "evidence bounds",
                    )

    walk(root)
    require(
        found == set(files) and folders == directories(files), "exact closure differs"
    )
    require(
        directory_modes(root, files, root_mode)
        == expected_directory_modes(files, root_mode),
        "directory inventory differs",
    )
    return result


class ProcessFailure(ValueError):
    def __init__(self, code):
        super().__init__("original process failed")
        self.code = code


class CaptureUnavailable(OSError):
    """Container tracking or cleanup cannot be established."""


def command(args, timeout=60):
    p = subprocess.run(
        args,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if p.returncode != 0:
        raise ProcessFailure(p.returncode)
    return p.stdout


def cleanup_capture(cidfile):
    """Stop only the exact Docker-created ID and require successful absence."""
    try:
        regular(cidfile, 128)
        require(
            cidfile.stat().st_uid == os.getuid(),
            "capture container identity owner differs",
        )
        cid = cidfile.read_text()
        require(
            re.fullmatch(r"[0-9a-f]{64}\n?", cid), "capture container identity refused"
        )
        cid = cid.strip()
        query = [
            "/usr/bin/docker",
            "ps",
            "--all",
            "--quiet",
            "--no-trunc",
            "--filter",
            "id=" + cid,
        ]
        present = command(query, timeout=10)
        require(
            present in (b"", (cid + "\n").encode()), "capture container query differs"
        )
        if present:
            command(["/usr/bin/docker", "stop", "--time", "5", cid], timeout=15)
            for attempt in range(5):
                present = command(query, timeout=10)
                require(
                    present in (b"", (cid + "\n").encode()),
                    "capture container query differs",
                )
                if not present:
                    break
                if attempt < 4:
                    time.sleep(0.2)
        require(not present, "capture container cleanup unavailable")
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        raise CaptureUnavailable("capture container cleanup unavailable") from error


def git(repo, *args):
    return command(["/usr/bin/git", "-C", str(repo), *args])


def blob(repo, ref, name):
    fields = git(repo, "ls-tree", "-z", ref, "--", name).split(b"\0")
    require(len(fields) == 2 and fields[1] == b"", "Git member closure")
    header, sep, path = fields[0].partition(b"\t")
    tokens = header.split()
    require(
        sep == b"\t"
        and path.decode() == name
        and len(tokens) == 3
        and tokens[0] in (b"100644", b"100755")
        and tokens[1] == b"blob",
        "regular Git blob required",
    )
    return git(repo, "cat-file", "blob", tokens[2].decode())


def selected(a):
    path_check(a.operator_repo)
    require(
        a.operator_repo.is_dir()
        and git(a.operator_repo, "rev-parse", "--show-toplevel").decode().strip()
        == str(a.operator_repo),
        "operator root",
    )
    require(
        re.fullmatch("[0-9a-f]{40}", a.operator_ref)
        and re.fullmatch("[0-9a-f]{40}", a.operator_tree),
        "Git OID syntax",
    )
    require(
        git(a.operator_repo, "remote", "get-url", "origin").decode().strip() == ORIGIN,
        "approved origin",
    )
    require(
        git(a.operator_repo, "rev-parse", a.operator_ref + "^{commit}").decode().strip()
        == a.operator_ref
        and git(a.operator_repo, "rev-parse", a.operator_ref + "^{tree}")
        .decode()
        .strip()
        == a.operator_tree,
        "selected commit/tree",
    )
    if a.source_kind == "public":
        require(
            a.remote_ref is not None
            and re.fullmatch(
                r"refs/(?:heads|tags|pull)/[A-Za-z0-9_./-]+", a.remote_ref
            ),
            "public ref syntax",
        )
        require(
            git(a.operator_repo, "ls-remote", "--exit-code", "origin", a.remote_ref)
            .decode()
            .splitlines()
            == [a.operator_ref + "\t" + a.remote_ref],
            "actual public ref",
        )
    else:
        require(a.remote_ref is None, "synthetic remote ref forbidden")
    members = {
        CALLER + n: blob(a.operator_repo, a.operator_ref, CALLER + n)
        for n in CALLER_HASHES
    }
    for n, h in CALLER_HASHES.items():
        require(sha(members[CALLER + n]) == h, "frozen caller differs")
    tree = ast.parse(members[CALLER + "native_acceptance.py"])
    recipe = next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "RECIPE" for t in n.targets)
    )
    require(type(recipe) is dict and len(recipe) == 10, "ten recipe files required")
    for n, h in recipe.items():
        name = "runtime-security/latexml/" + n
        members[name] = blob(a.operator_repo, a.operator_ref, name)
        require(sha(members[name]) == h, "frozen recipe differs")
    for n in WRAPPERS:
        name = WRAPPER + n
        members[name] = blob(a.operator_repo, a.operator_ref, name)
        require(
            members[name] == Path(__file__).with_name(n).read_bytes(),
            "executing wrapper source differs",
        )
    return members


def identity(a):
    return {
        "operator_origin": ORIGIN,
        "operator_ref": a.operator_ref,
        "operator_tree": a.operator_tree,
        "source_kind": a.source_kind,
        "remote_ref": a.remote_ref,
        "architecture": a.architecture,
    }


def diagnostic_expectations(a, members, input_lock_sha256):
    """Pure expected binding from independently selected Git bytes; lock is external."""
    require(
        type(input_lock_sha256) is str
        and re.fullmatch("[0-9a-f]{64}", input_lock_sha256),
        "trusted launch lock required",
    )
    require(
        a.architecture != "arm64" or input_lock_sha256 == ARM_LOCK,
        "reviewed ARM launch lock required",
    )
    data = members[CALLER + "native_acceptance.py"]
    require(
        sha(data) == CALLER_HASHES["native_acceptance.py"],
        "trusted caller source required",
    )
    wanted = {"UPSTREAM_ORIGIN", "UPSTREAM_HEAD", "UPSTREAM_TREE", "RECIPE"}
    literals = {}
    for node in ast.parse(data).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    require(target.id not in literals, "duplicate source constant")
                    literals[target.id] = ast.literal_eval(node.value)
    require(
        set(literals) == wanted
        and type(literals["RECIPE"]) is dict
        and len(literals["RECIPE"]) == 10,
        "selected binding constants",
    )
    binding = {
        "schema": "aelira-latexml-native-source-binding-v1",
        "source_kind": a.source_kind,
        "operator_origin": ORIGIN,
        "operator_ref": a.operator_ref,
        "operator_tree": a.operator_tree,
        "remote_ref": a.remote_ref,
        "upstream_origin": literals["UPSTREAM_ORIGIN"],
        "upstream_ref": literals["UPSTREAM_HEAD"],
        "upstream_tree": literals["UPSTREAM_TREE"],
        "recipe_sha256": literals["RECIPE"],
        "runner_sha256": sha(data),
    }
    return {
        **identity(a),
        "caller_sha256": sha(data),
        "binding_sha256": sha(canonical(binding)),
        "input_lock_sha256": input_lock_sha256,
    }


def observe_capture_lock(scratch):
    """Bounded fixed-file observation only; not signed-input acceptance or export."""
    try:
        path_check(scratch)
        path = scratch / "captured-input/input-lock.json"
        path_check(path)
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            item = os.fstat(stream.fileno())
            require(
                stat.S_ISREG(item.st_mode)
                and item.st_nlink == 1
                and item.st_uid == os.getuid()
                and stat.S_IMODE(item.st_mode) == 0o644
                and 0 < item.st_size <= 300_000,
                "captured lock file",
            )
            data = stream.read(300_001)
            require(
                len(data) == item.st_size and len(data) <= 300_000,
                "captured lock bound",
            )
        return sha(data)
    except Exception:
        return None


def read_failure_projection(path, expected, caller_state, validator):
    """Failure-only private read. Missing/malformed data never masks an original exit."""
    if caller_state == {"state": "not_run", "exit": None} or caller_state == {
        "state": "executed",
        "exit": 0,
    }:
        return {"state": "not_run", "record": None}
    unavailable = {"state": "unavailable", "record": None}
    if expected is None or validator is None:
        return unavailable
    try:
        path_check(path)
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            item = os.fstat(stream.fileno())
            require(
                stat.S_ISREG(item.st_mode)
                and item.st_nlink == 1
                and item.st_uid == os.getuid()
                and stat.S_IMODE(item.st_mode) == 0o600
                and 0 < item.st_size <= 16_000,
                "private diagnostic file",
            )
            data = stream.read(16_001)
            require(
                len(data) == item.st_size and len(data) <= 16_000,
                "private diagnostic size",
            )
        candidate = {
            "state": "valid",
            "record": json.loads(
                data,
                object_pairs_hook=pairs,
                parse_constant=nonfinite,
                parse_float=nonfinite,
            ),
        }
        validator(candidate, expected, caller_state)
        return candidate
    except Exception:
        return unavailable


def selector(a):
    args = [
        "--operator-repo",
        str(a.operator_repo),
        "--operator-ref",
        a.operator_ref,
        "--operator-tree",
        a.operator_tree,
        "--source-kind",
        a.source_kind,
    ]
    if a.remote_ref is not None:
        args += ["--remote-ref", a.remote_ref]
    return args


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_stages(rows, successful=False):
    require(type(rows) is dict and set(rows) == set(STAGES), "stage inventory")
    for name, row in rows.items():
        require(
            type(row) is dict and set(row) == {"state", "exit"}, "typed stage fields"
        )
        require(
            row["state"] in ("executed", "not_run", "unavailable")
            and (
                type(row["exit"]) is int and -127 <= row["exit"] <= 255
                if row["state"] == "executed"
                else row["exit"] is None
            ),
            "original stage status",
        )
        if successful and name not in ("evidence-export", "evidence-check"):
            require(
                row == {"state": "executed", "exit": 0}, "successful stage required"
            )


def native(a):
    machine = platform.machine()
    expected = "aarch64" if a.architecture == "arm64" else "x86_64"
    require(
        platform.system() == "Linux" and machine == expected,
        "native Linux host required",
    )
    require(
        command(["/usr/bin/docker", "info", "--format", "{{.Architecture}}"])
        .decode()
        .strip()
        in (expected, a.architecture),
        "native daemon required",
    )
    command(
        ["/usr/bin/docker", "pull", "--platform", "linux/" + a.architecture, BASE],
        timeout=600,
    )
    data = json.loads(command(["/usr/bin/docker", "image", "inspect", BASE]))
    require(
        type(data) is list
        and len(data) == 1
        and data[0]["Architecture"] == a.architecture
        and data[0]["Os"] == "linux",
        "native pinned base required",
    )


def snapshot_file(relative, limit):
    """Read one fixed vendor snapshot resource with normal TLS and no redirects."""
    require(
        relative == CA_PACKAGE_PATH
        or relative in {"dists/" + suite + "/InRelease" for suite in SNAPSHOT_SUITES},
        "undeclared snapshot resource",
    )
    for attempt in range(len(SNAPSHOT_RETRY_DELAYS) + 1):
        connection = None
        try:
            connection = http.client.HTTPSConnection(
                "snapshot.ubuntu.com", timeout=60, context=ssl.create_default_context()
            )
            connection.request("GET", "/ubuntu/" + SNAPSHOT_ID + "/" + relative)
            response = connection.getresponse()
            if response.status == 200:
                data = response.read(limit + 1)
                require(0 < len(data) <= limit, "snapshot resource bound")
                return data
            require(response.status in SNAPSHOT_RETRY_HTTP, "snapshot request refused")
            if attempt == len(SNAPSHOT_RETRY_DELAYS):
                raise ValueError("snapshot request refused")
        except ssl.SSLError as error:
            raise ValueError("snapshot TLS refused") from error
        except (OSError, http.client.HTTPException) as error:
            if attempt == len(SNAPSHOT_RETRY_DELAYS):
                raise ValueError("snapshot transport unavailable") from error
        finally:
            if connection is not None:
                connection.close()
        time.sleep(SNAPSHOT_RETRY_DELAYS[attempt])


def snapshot_ca_bundle(data):
    """Read inert certificate members; never extract or execute package payloads."""
    require(0 < len(data) <= 2_000_000, "CA package archive bound")
    certificates = {}
    prefix = "./usr/share/ca-certificates/mozilla/"
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        members = archive.getmembers()
        require(len(members) <= 1000, "CA package member bound")
        names = set()
        for member in members:
            require(member.name not in names, "duplicate CA package member")
            names.add(member.name)
            if not member.name.startswith(prefix) or not member.name.endswith(".crt"):
                continue
            relative = member.name[len(prefix) :]
            require(
                member.isfile()
                and "/" not in relative
                and relative not in (".", "..")
                and 0 < member.size <= 20_000,
                "regular bounded root certificate required",
            )
            certificate = archive.extractfile(member).read()
            require(len(certificate) == member.size, "root certificate size differs")
            ssl.PEM_cert_to_DER_cert(certificate.decode("ascii"))
            certificates[member.name] = certificate.rstrip() + b"\n"
    bundle = b"".join(certificates[name] for name in sorted(certificates))
    require(
        len(certificates) == CA_CERTIFICATE_COUNT
        and len(bundle) == CA_BUNDLE_SIZE
        and sha(bundle) == CA_BUNDLE_SHA256,
        "fixed CA inventory differs",
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=bundle.decode("ascii"))
    require(
        context.cert_store_stats()["x509_ca"] == CA_CERTIFICATE_COUNT,
        "CA store count differs",
    )
    return bundle


def download_plan(path):
    """Reconcile the same bounded plan used as worker arguments, without eval."""
    regular(path, 500_000)
    rows = []
    names = set()
    total = 0
    for line in path.read_text(encoding="ascii").splitlines():
        if not line.startswith("'"):
            continue
        match = re.fullmatch(
            r"'(https://snapshot\.ubuntu\.com/ubuntu/"
            + SNAPSHOT_ID
            + r"/pool/(?:main|restricted|universe|multiverse)/[a-z0-9][a-z0-9-]*/"
            r"[a-z0-9][a-z0-9+.-]*/[A-Za-z0-9][A-Za-z0-9+._%~-]*\.deb)' "
            r"([A-Za-z0-9][A-Za-z0-9+._%~-]*\.deb) ([1-9][0-9]{0,8}) SHA256:([0-9a-f]{64})",
            line,
        )
        require(match is not None, "download plan row refused")
        uri, name, size, digest = match.groups()
        require(
            len(uri) <= 1024
            and len(name) <= 200
            and name not in names
            and "/../" not in uri
            and "/./" not in uri
            and "%" not in re.sub(r"%(?:2b|3a|7e)", "", uri, flags=re.I)
            and int(size) <= MAX_FILE,
            "download plan identity/bound refused",
        )
        names.add(name)
        total += int(size)
        rows.append({"uri": uri, "file": name, "size": int(size), "sha256": digest})
    require(0 < len(rows) <= MAX_FILES and total <= MAX_TOTAL, "download plan bound")
    return rows


def download_worker(options):
    """Trusted fixed script; every external value is a separately quoted argument."""
    return (
        "\n".join(
            (
                'set -u; umask 022; test "$#" = 4 || exit 2',
                "uri=$1; name=$2; size=$3; hash=$4",
                'target=/capture/debs/partial/"$name"; status=/capture/status/workers/"$name"',
                'printf "1\\n" > "$status.started" || exit 2',
                # The frozen Ubuntu /bin/sh uses POSIX 512-byte units. Native controls
                # exercise this limit; at most 511 bytes above the signed size can be written.
                '( ulimit -f "$(( (size + 511) / 512 ))" || exit 2; exec /usr/lib/apt/apt-helper '
                + options
                + ' download-file "$uri" "$target" "$hash" ) > "$status.log" 2>&1',
                'rc=$?; printf "%s\\n" "$rc" > "$status.apt.exit" || exit 2',
                'if test "$rc" = 0; then',
                '  ( set -eu; test -f "$target"; test ! -L "$target"; test "$(stat -c %h -- "$target")" = 1;',
                '    test "$(stat -c %s -- "$target")" = "$size";',
                '    printf "%s  %s\\n" "${hash#SHA256:}" "$target" | sha256sum --check --status;',
                '    test ! -e /capture/debs/"$name"; test ! -L /capture/debs/"$name";',
                '    chmod 0644 -- "$target"; mv -T -- "$target" /capture/debs/"$name" )',
                "  rc=$?",
                "fi",
                'printf "%s\\n" "$rc" > "$status.exit" || exit 2; exit "$rc"',
            )
        )
        + "\n"
    )


def capture_status(directory, name):
    """A started process without a numeric terminal receipt is unavailable."""
    started, terminal = directory / (name + ".started"), directory / (name + ".exit")
    if not started.exists() and not started.is_symlink():
        require(not terminal.exists() and not terminal.is_symlink(), "unstarted status")
        return {"state": "not_run", "exit": None}
    regular(started, 2)
    require(started.read_bytes() == b"1\n", "capture start marker differs")
    if not terminal.exists() and not terminal.is_symlink():
        return {"state": "unavailable", "exit": None}
    regular(terminal, 4)
    text = terminal.read_text()
    require(
        re.fullmatch(r"(?:0|[1-9][0-9]{0,2})\n", text) and int(text) <= 255,
        "capture original status malformed",
    )
    return {"state": "executed", "exit": int(text)}


def observe_capture_process(cache, process):
    """Read stopped-container receipts, including completed stages before timeout."""
    try:
        process["stages"] = {
            n: capture_status(cache / "status", n) for n in CAPTURE_STAGES
        }
        process["plan"] = capture_status(cache / "status", "plan")
        process["archive_check"] = capture_status(cache / "status", "archive-check")
        if process["plan"] == {"state": "executed", "exit": 0}:
            plan_path = cache / "download-plan.txt"
            rows = download_plan(plan_path)
            process["download_plan_sha256"] = regular(plan_path, 500_000)["sha256"]
            expected_args = b"".join(
                str(value).encode("ascii") + b"\0"
                for row in rows
                for value in (
                    row["uri"],
                    row["file"],
                    row["size"],
                    "SHA256:" + row["sha256"],
                )
            )
            regular(cache / "download-args", 500_000)
            require(
                (cache / "download-args").read_bytes() == expected_args,
                "worker arguments differ",
            )
            for row in rows:
                state = capture_status(cache / "status/workers", row["file"])
                apt_path = cache / "status/workers" / (row["file"] + ".apt.exit")
                apt = {
                    "state": (
                        "not_run" if state["state"] == "not_run" else "unavailable"
                    ),
                    "exit": None,
                }
                if apt_path.exists() or apt_path.is_symlink():
                    require(state["state"] != "not_run", "unstarted APT worker")
                    regular(apt_path, 4)
                    text = apt_path.read_text()
                    require(
                        re.fullmatch(r"(?:0|[1-9][0-9]{0,2})\n", text)
                        and int(text) <= 255,
                        "APT worker original status malformed",
                    )
                    apt = {"state": "executed", "exit": int(text)}
                process["downloads"].append(
                    {k: row[k] for k in ("file", "size", "sha256")}
                    | {"apt": apt, "result": state}
                )
    except (OSError, ValueError, UnicodeError) as error:
        raise CaptureUnavailable("capture process receipt unavailable") from error


def capture(a, root, caller):
    """Capture the fixed signed snapshot using the default _apt sandbox and TLS."""
    require(caller.SNAPSHOT_ID == SNAPSHOT_ID, "capture and offline snapshot differ")
    root.mkdir()
    (root / "debs").mkdir()
    (root / "lists").mkdir()
    key = command(
        [
            "/usr/bin/docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--entrypoint",
            "/bin/cat",
            BASE,
            "/usr/share/keyrings/ubuntu-archive-keyring.gpg",
        ]
    )
    require(sha(key) == KEYRING, "pinned native keyring differs")
    (root / "ubuntu-archive-keyring.gpg").write_bytes(key)
    cache = root.parent / "capture-cache"
    path_check(cache, new=True)
    cache.mkdir()
    uid, gid = os.getuid(), os.getgid()
    require(uid != 0, "host root capture refused")
    # This package is pinned to the previously authenticated Ubuntu signed-index
    # closure. Only its public root certificates are read; no package is installed.
    ca_package = snapshot_file(CA_PACKAGE_PATH, CA_PACKAGE_SIZE)
    require(
        len(ca_package) == CA_PACKAGE_SIZE and sha(ca_package) == CA_PACKAGE_SHA256,
        "pinned CA package differs",
    )
    ca_path = cache / "ca-certificates.deb"
    ca_path.write_bytes(ca_package)
    ca_path.chmod(0o644)
    bundle = snapshot_ca_bundle(
        command(["/usr/bin/dpkg-deb", "--fsys-tarfile", str(ca_path)], timeout=30)
    )
    require(regular(ca_path)["sha256"] == CA_PACKAGE_SHA256, "CA package changed")
    (cache / "snapshot-roots.pem").write_bytes(bundle)
    (cache / "snapshot-roots.pem").chmod(0o644)
    endpoint_metadata = {}
    for suite in SNAPSHOT_SUITES:
        data = snapshot_file("dists/" + suite + "/InRelease", 10_000_000)
        endpoint = cache / (suite + ".InRelease")
        endpoint.write_bytes(data)
        endpoint.chmod(0o644)
        command(
            [
                "/usr/bin/gpgv",
                "--keyring",
                str(root / "ubuntu-archive-keyring.gpg"),
                str(endpoint),
            ],
            timeout=30,
        )
        text = data.decode("utf-8")
        for field, value in (
            ("Origin", "Ubuntu"),
            ("Label", "Ubuntu"),
            ("Codename", "resolute"),
            ("Suite", suite),
        ):
            require(
                re.findall(r"^" + field + r": (.*)$", text, re.M) == [value],
                "signed snapshot identity differs",
            )
        endpoint_metadata[suite] = data
    sources = (
        "Types: deb\nURIs: https://snapshot.ubuntu.com/ubuntu/" + SNAPSHOT_ID + "/\n"
        "Suites: " + " ".join(SNAPSHOT_SUITES) + "\n"
        "Components: main restricted universe multiverse\n"
        "Architectures: " + a.architecture + "\n"
        "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
    )
    (cache / "snapshot.sources").write_text(sources)
    (cache / "snapshot.sources").chmod(0o644)
    (cache / "sourceparts-empty").mkdir()
    options = (
        "-o Dir::Etc::sourcelist=/capture/snapshot.sources "
        "-o Dir::Etc::sourceparts=/capture/sourceparts-empty "
        "-o Dir::State::lists=/capture/lists "
        "-o Acquire::https::AllowRedirect=false "
        "-o Acquire::https::CAInfo=/capture/snapshot-roots.pem "
        "-o Acquire::https::Verify-Peer=true -o Acquire::https::Verify-Host=true "
        "-o Acquire::Retries=3 "
        "-o Acquire::https::Timeout=30 "
        "-o APT::Update::Error-Mode=any"
    )
    (cache / "download-plan.awk").write_text(DOWNLOAD_PLAN_AWK)
    (cache / "download-worker.sh").write_text(download_worker(options))
    for name in ("download-plan.awk", "download-worker.sh"):
        (cache / name).chmod(0o644)
    # Root APT needs these five ordinary filesystem/UID capabilities; no others
    # are granted. Only this new cache is mounted. Default _apt is not overridden.
    script = "\n".join(
        (
            "set -u; umask 022; export LC_ALL=C",
            "mkdir -m 0755 /capture/status || exit $?",
            'stage() { name=$1; shift; printf "1\\n" > /capture/status/"$name".started || return 2; ( set -eu; "$@" ); rc=$?; printf \'%s\\n\' "$rc" > /capture/status/"$name".exit || return 2; return "$rc"; }',
            'setup() { chown 0:0 /capture || return $?; mkdir -m 0755 /capture/debs /capture/lists || return $?; apt_uid=$(id -u _apt) || return $?; test "$apt_uid" = 42 || return $?; sandbox=$(apt-config shell user APT::Sandbox::User) || return $?; test "$sandbox" = "user=\'_apt\'" || return $?; install -d -m 0700 -o _apt -g root /capture/debs/partial /capture/lists/partial || return $?; }',
            "stage setup setup || exit $?",
            "stage update apt-get " + options + " update || exit $?",
            "download() { mkdir -m 0755 /capture/status/workers || return 2; stage plan apt-get "
            + options
            + " -o Acquire::ForceHash=SHA256 --print-uris -y --no-install-recommends --download-only -o Dir::Cache::archives=/capture/debs install "
            + " ".join(caller.PACKAGES)
            + " > /capture/download-plan.txt || return $?; "
            + "awk -f /capture/download-plan.awk /capture/download-plan.txt > /capture/download-args || return 2; "
            + "xargs -0 -r -n 4 -P "
            + str(DOWNLOAD_WORKERS)
            + " /bin/sh /capture/download-worker.sh < /capture/download-args || return $?; "
            + "stage archive-check apt-get "
            + options
            + " --no-download -y --no-install-recommends --download-only -o Dir::Cache::archives=/capture/debs install "
            + " ".join(caller.PACKAGES)
            + "; }",
            "stage download download || exit $?",
            'select_inputs() { set -- /capture/lists/*_InRelease; test "$#" = 4 || return $?; test -f "$1" || return $?; set -- /capture/lists/*_Packages.lz4; test -f "$1" || return $?; set -- /capture/debs/*.deb; test -f "$1" || return $?; }',
            "stage select select_inputs || exit $?",
            "stage restore chown -R --no-dereference "
            + str(uid)
            + ":"
            + str(gid)
            + " /capture || exit $?",
        )
    )
    cidfile = root.parent / "private/input-capture.cid"
    path_check(cidfile, new=True)
    args = [
        "/usr/bin/docker",
        "run",
        "--rm",
        "--platform",
        "linux/" + a.architecture,
        "--cap-drop",
        "ALL",
    ]
    for capability in CAPTURE_CAPABILITIES:
        args += ["--cap-add", capability]
    args += [
        "--security-opt",
        "no-new-privileges",
        "--cpus",
        "2",
        "--memory",
        "3g",
        "--pids-limit",
        "128",
        "--cidfile",
        str(cidfile),
        "--mount",
        "type=bind,src=" + str(cache) + ",dst=/capture",
        "--entrypoint",
        "/bin/sh",
        BASE,
        "-c",
        script,
    ]
    original = None
    process = {
        "schema": "aelira-latexml-capture-process-v3",
        **identity(a),
        "keyring_sha256": sha(key),
        "snapshot_id": SNAPSHOT_ID,
        "ca_package_sha256": CA_PACKAGE_SHA256,
        "ca_bundle_sha256": sha(bundle),
        "apt_sandbox_user": "_apt",
        "apt_uid": 42,
        "capabilities": list(CAPTURE_CAPABILITIES),
        "docker_exit": None,
        "container_absent": False,
        "stages": {n: {"state": "not_run", "exit": None} for n in CAPTURE_STAGES},
        "plan": {"state": "not_run", "exit": None},
        "archive_check": {"state": "not_run", "exit": None},
        "download_workers": DOWNLOAD_WORKERS,
        "download_plan_sha256": None,
        "downloads": [],
    }
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=1200,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        (root.parent / "private/input-capture.log").write_bytes(
            result.stdout + b"\n[stderr]\n" + result.stderr
        )
        original = result.returncode
        process["docker_exit"] = original
    except subprocess.TimeoutExpired as error:
        (root.parent / "private/input-capture.log").write_bytes(
            (error.stdout or b"") + b"\n[stderr]\n" + (error.stderr or b"")
        )
        raise
    finally:
        try:
            cleanup_capture(cidfile)
            process["container_absent"] = True
            observe_capture_process(cache, process)
        finally:
            (root.parent / "capture-process.json").write_bytes(canonical(process))
    if original != 0:
        raise ProcessFailure(original)
    require(
        all(
            row == {"state": "executed", "exit": 0}
            for row in process["stages"].values()
        ),
        "capture stages incomplete",
    )
    require(
        process["plan"] == process["archive_check"] == {"state": "executed", "exit": 0}
        and process["downloads"]
        and all(
            row["apt"] == row["result"] == {"state": "executed", "exit": 0}
            for row in process["downloads"]
        ),
        "capture package receipts incomplete",
    )
    for folder, suffixes in (
        ("debs", (".deb",)),
        ("lists", ("_InRelease", "_Packages.lz4")),
    ):
        candidates = sorted(
            p for p in (cache / folder).iterdir() if p.name.endswith(suffixes)
        )
        require(candidates, "capture input selection empty")
        for path in candidates:
            before = regular(path)
            target = root / folder / path.name
            shutil.copyfile(path, target)
            target.chmod(0o644)
            require(
                regular(path) == before
                and regular(target)["sha256"] == before["sha256"]
                and regular(target)["size"] == before["size"],
                "capture copied bytes differ",
            )
    releases = sorted((root / "lists").glob("*_InRelease"))
    prefix = "snapshot.ubuntu.com_ubuntu_" + SNAPSHOT_ID + "_dists_"
    require(len(releases) == len(SNAPSHOT_SUITES), "snapshot release count differs")
    for suite in SNAPSHOT_SUITES:
        matches = [p for p in releases if p.name == prefix + suite + "_InRelease"]
        require(
            len(matches) == 1 and matches[0].read_bytes() == endpoint_metadata[suite],
            "snapshot endpoint-to-input mismatch",
        )
    require(
        regular(cache / "snapshot-roots.pem")["sha256"] == CA_BUNDLE_SHA256,
        "capture CA roots changed",
    )
    require(
        (cache / "snapshot.sources").read_text() == sources
        and not list((cache / "sourceparts-empty").iterdir()),
        "capture source configuration changed",
    )
    lock = caller.verify_apt(root, a.architecture)
    files = {"ubuntu-archive-keyring.gpg", "input-lock.json"} | {
        x["name"] for x in lock["files"]
    }
    captured = inventory(root, files)
    (root.parent / "capture-manifest.json").write_bytes(
        canonical(
            {
                "schema": "aelira-latexml-captured-input-v2",
                **identity(a),
                "input_lock_sha256": captured["input-lock.json"]["sha256"],
                "snapshot_id": SNAPSHOT_ID,
                "ca_package_sha256": CA_PACKAGE_SHA256,
                "ca_bundle_sha256": CA_BUNDLE_SHA256,
                "endpoint_metadata_sha256": {
                    suite: sha(data) for suite, data in endpoint_metadata.items()
                },
                "files": captured,
                "directories": directory_modes(root, files),
                "file_count": len(files),
                "signed_releases": lock["signed_releases"],
                "packages_indexes": lock["packages_indexes"],
                "deb_count": len(lock["debs"]),
                "process_sha256": regular(root.parent / "capture-process.json")[
                    "sha256"
                ],
            }
        )
    )
    return captured["input-lock.json"]["sha256"]


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--operator-repo", type=Path, required=True)
    p.add_argument("--operator-ref", required=True)
    p.add_argument("--operator-tree", required=True)
    p.add_argument("--source-kind", choices=("public", "synthetic"), required=True)
    p.add_argument("--remote-ref")
    p.add_argument("--architecture", choices=("arm64", "amd64"), required=True)
    p.add_argument("--scratch", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--upstream-repo", type=Path, required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--apt-input", type=Path)
    group.add_argument("--capture-input", action="store_true")
    p.add_argument("--apt-lock-sha256")
    return p


def main():
    a = parser().parse_args()
    os.umask(0o022)
    for path in (a.scratch, a.out):
        path_check(path, new=True)
    path_check(a.upstream_repo)
    for first, second in (
        (a.operator_repo, a.scratch),
        (a.operator_repo, a.out),
        (a.scratch, a.out),
    ):
        require(
            first != second
            and first not in second.parents
            and second not in first.parents,
            "overlapping outputs",
        )
    require(
        (a.apt_input is None and a.apt_lock_sha256 is None)
        or (
            a.apt_input is not None
            and type(a.apt_lock_sha256) is str
            and re.fullmatch("[0-9a-f]{64}", a.apt_lock_sha256)
        ),
        "explicit frozen lock required",
    )
    a.scratch.mkdir()
    logs = a.scratch / "private"
    logs.mkdir()
    stages = {n: {"state": "not_run", "exit": None} for n in STAGES}
    receipt = {
        "schema": "aelira-latexml-ci-outer-v3",
        **identity(a),
        "stages": stages,
        "captured_lock_sha256": None,
        "selected_sources": None,
        "producer_status_sha256": None,
        "failure_diagnostic": {"state": "not_run", "record": None},
        "pending": [
            "public_ci",
            "native_amd64",
            "full_application",
            "final_image",
            "all_copy",
            "advisory",
            "R10",
            "#600",
            "#579",
            "#578",
        ],
    }

    def write():
        validate_stages(stages)
        (a.scratch / "outer.json").write_bytes(canonical(receipt))

    def step(name, args, timeout=1800):
        try:
            p = subprocess.run(
                args,
                capture_output=True,
                timeout=timeout,
                check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            (logs / (name + ".log")).write_bytes(p.stdout + b"\n[stderr]\n" + p.stderr)
            stages[name] = {"state": "executed", "exit": p.returncode}
            write()
            require(p.returncode == 0, "original stage failed")
        except (OSError, subprocess.TimeoutExpired):
            stages[name] = {"state": "unavailable", "exit": None}
            write()
            raise

    diagnostic_expected = None
    caller = None
    current = "source-check"
    try:
        members = selected(a)
        receipt["selected_sources"] = {
            n: {"sha256": sha(d), "size": len(d), "mode": 420}
            for n, d in members.items()
        }
        trusted = a.scratch / "trusted"
        for n, d in members.items():
            target = trusted / n
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(d)
            target.chmod(0o644)
        inventory(trusted, set(members))
        stages[current] = {"state": "executed", "exit": 0}
        write()
        current = "native-check"
        native(a)
        stages[current] = {"state": "executed", "exit": 0}
        write()
        caller_path = trusted / CALLER / "native_acceptance.py"
        caller = load_module(caller_path, "trusted_latexml_caller")
        current = "input-capture"
        if a.capture_input:
            a.apt_input = a.scratch / "captured-input"
            a.apt_lock_sha256 = capture(a, a.apt_input, caller)
            receipt["captured_lock_sha256"] = a.apt_lock_sha256
        else:
            path_check(a.apt_input)
            require(
                regular(a.apt_input / "input-lock.json")["sha256"] == a.apt_lock_sha256,
                "input lock differs",
            )
        stages[current] = {"state": "executed", "exit": 0}
        write()
        # Do not relax the immutable caller's ARM guard for a recaptured closure.
        current = "input-pin"
        require(
            a.architecture != "arm64" or a.apt_lock_sha256 == ARM_LOCK,
            "new authenticated ARM inputs need a separately reviewed caller pin",
        )
        stages[current] = {"state": "executed", "exit": 0}
        write()
        transport = a.scratch / "transport"
        opts = selector(a)
        current = "prepare"
        step(
            current,
            [sys.executable, "-B", str(caller_path), "prepare", str(transport), *opts],
        )
        r = read_json(transport / "transport-receipt.json")
        producer = a.scratch / "producer"
        producer.mkdir()
        current = "bootstrap"
        step(
            current,
            [
                sys.executable,
                "-B",
                str(trusted / CALLER / "bootstrap.py"),
                "--transport",
                str(transport / "transport.tar"),
                "--transport-sha256",
                r["transport_sha256"],
                "--binding-sha256",
                r["binding_sha256"],
                "--runner-sha256",
                r["runner_sha256"],
                "--out",
                str(producer / "source"),
                *opts,
            ],
        )
        current = "native-run"
        try:
            context = diagnostic_expectations(a, members, a.apt_lock_sha256)
            require(
                r["runner_sha256"] == context["caller_sha256"]
                and r["binding_sha256"] == context["binding_sha256"]
                and regular(producer / "source/binding.json")["sha256"]
                == context["binding_sha256"],
                "actual bootstrap diagnostic expectation differs",
            )
            caller.validate_diagnostic_context(context)
            diagnostic_expected = context
        except Exception:
            # Insufficient trusted bootstrap/launch identity cannot produce a record.
            diagnostic_expected = None
        step(
            current,
            [
                sys.executable,
                "-B",
                str(producer / "source/native_acceptance.py"),
                "run",
                str(producer),
                *opts,
                "--architecture",
                a.architecture,
                "--upstream-repo",
                str(a.upstream_repo),
                "--apt-input",
                str(a.apt_input),
                "--apt-lock-sha256",
                a.apt_lock_sha256,
            ],
            timeout=5400,
        )
        receipt["producer_status_sha256"] = regular(producer / "status.json")["sha256"]
        write()
        exported = a.scratch / "caller-export"
        current = "caller-export"
        step(
            current,
            [
                sys.executable,
                "-B",
                str(producer / "source/native_acceptance.py"),
                "export",
                str(producer),
                "--out",
                str(exported),
                *opts,
                "--binding-sha256",
                r["binding_sha256"],
                "--runner-sha256",
                r["runner_sha256"],
                "--architecture",
                a.architecture,
            ],
        )
        current = "evidence-export"
        step(
            current,
            [
                sys.executable,
                "-B",
                str(trusted / WRAPPER / "evidence.py"),
                "export",
                "--scratch",
                str(a.scratch),
                "--out",
                str(a.out),
                *opts,
                "--architecture",
                a.architecture,
            ],
            timeout=1800,
        )
        current = "evidence-check"
        step(
            current,
            [
                sys.executable,
                "-B",
                str(trusted / WRAPPER / "evidence.py"),
                "check",
                "--evidence",
                str(a.out),
                "--verification-scratch",
                str(a.scratch / "independent-check"),
                "--manifest-sha256",
                regular(a.out / "manifest.json")["sha256"],
                *opts,
                "--architecture",
                a.architecture,
            ],
            timeout=1800,
        )
        print(
            json.dumps(
                {
                    "schema": receipt["schema"],
                    "outer_sha256": regular(a.scratch / "outer.json")["sha256"],
                    "evidence_manifest_sha256": regular(a.out / "manifest.json")[
                        "sha256"
                    ],
                    "architecture": a.architecture,
                    "source_kind": a.source_kind,
                },
                sort_keys=True,
            )
        )
    except (
        ValueError,
        OSError,
        subprocess.TimeoutExpired,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        if stages[current]["state"] == "not_run":
            stages[current] = (
                {"state": "unavailable", "exit": None}
                if isinstance(exc, (OSError, subprocess.TimeoutExpired))
                else {
                    "state": "executed",
                    "exit": exc.code if isinstance(exc, ProcessFailure) else 2,
                }
            )
        if (a.scratch / "producer/status.json").is_file():
            receipt["producer_status_sha256"] = regular(
                a.scratch / "producer/status.json"
            )["sha256"]
        receipt["failure_diagnostic"] = read_failure_projection(
            a.scratch / "producer/private/failure-diagnostic.json",
            diagnostic_expected,
            stages["native-run"],
            caller.validate_failure_projection if caller is not None else None,
        )
        write()
        raise


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        OSError,
        subprocess.TimeoutExpired,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ):
        print("native CI refused", file=sys.stderr)
        sys.exit(2)
